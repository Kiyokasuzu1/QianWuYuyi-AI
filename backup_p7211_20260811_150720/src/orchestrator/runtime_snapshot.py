# -*- coding: utf-8 -*-
"""
src/orchestrator/runtime_snapshot.py

Phase 5.0-C: RuntimeSnapshot —— Runtime 运行时状态快照独立模块。

本模块从 runtime_bootstrap.py 抽出,提供三层:
1. RuntimeSnapshot          数据类(纯数据,不 IO)
2. RuntimeSnapshotStore     JSON 文件持久化(原子写)
3. RuntimeSnapshotBuilder   业务编排(合并 LongLoop / PR / Audit 状态)

职责边界(严格遵守):
- 不修改 LongLoop / Orchestrator / SelfModelOrchestrator / PersistenceRuntime
  / RuntimeAuditLogger / main.py 任何代码
- 不接管事件循环;不创建 EventBus / MessageQueue / Scheduler
- 不引入 LLM SDK
- 不持有 Orchestrator 引用(单向)
- 全部异常隔离,不抛

依赖关系:
    RuntimeBootstrap → RuntimeSnapshotBuilder → RuntimeSnapshotStore
    RuntimeSnapshotStore ↔ RuntimeSnapshot (双向弱依赖,Store 不知道 Builder)
    反向引用:无

字段集(向后兼容 v1.0):
    schema_version, identity_id, created_at, updated_at, source,
    last_turn_count, last_checkpoint_count,
    last_started_at, last_stopped_at,
    last_self_model_version, last_self_model_identity,
    evolution_record_count,
    last_audit_log_offset, last_audit_log_path,
    boot_count, last_boot_mode, last_boot_at
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
RUNTIME_SNAPSHOT_SCHEMA_VERSION = "1.0"

DEFAULT_RUNTIME_SNAPSHOT_PATH = "data/runtime_snapshot.json"
DEFAULT_IDENTITY_ID = "yuyi_default"

# 文件大小保护(防巨型 JSON 爆炸)
_RUNTIME_SNAPSHOT_MAX_BYTES = 1 * 1024 * 1024  # 1 MB

# source 字段取值常量
SOURCE_INITIAL = "initial"
SOURCE_FRESH = "fresh"
SOURCE_RESTORE = "restore"
SOURCE_PERIODIC = "periodic"
SOURCE_FINAL = "final"
SOURCE_CHECKPOINT = "checkpoint"


# ============================================================
# 辅助
# ============================================================
def _now_iso() -> str:
    """返回 ISO 8601 UTC 时间字符串。失败回退到 naive utcnow。"""
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        try:
            from datetime import datetime as _dt
            return _dt.utcnow().isoformat() + "Z"
        except Exception:  # noqa: BLE001
            return ""


def _safe_optional_int(value: Any) -> Optional[int]:
    """安全 int 转换(用于 Optional[int] 字段)。失败或非数字 → None。"""
    if value is None:
        return None
    try:
        # bool / int / float / str(数字) → 接受
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        return None
    except Exception:  # noqa: BLE001
        return None


def _safe_optional_str(value: Any) -> Optional[str]:
    """安全字符串转换(用于 Optional[str] 字段)。失败 → None。"""
    if value is None:
        return None
    try:
        s = str(value)
        return s
    except Exception:  # noqa: BLE001
        return None


def _safe_str(value: Any, max_len: int = 240) -> str:
    """安全字符串化,过长截断。"""
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


def _safe_int(value: Any, default: int = 0) -> int:
    """安全 int 转换。"""
    try:
        return int(value or 0)
    except Exception:  # noqa: BLE001
        return default


# ============================================================
# 数据层:RuntimeSnapshot
# ============================================================
class RuntimeSnapshot:
    """Runtime 运行时快照(Phase 5.0-C / v1.0)。

    字段集合与旧 _SnapshotData(v1.0)完全一致,to_dict() 输出 key 集合
    与顺序严格保持,保证已落盘的旧 snapshot 文件可被新版本加载。

    关键不变量:
    - to_dict() 字段集合 17 个 key,顺序固定
    - from_dict() 对未知字段/缺字段/类型错误一律容错(不抛)
    - mark_* 方法不修改 schema_version / created_at
    - 所有方法线程安全(无内部状态可变,__slots__)
    """

    # __slots__ 顺序即 to_dict() 输出顺序
    __slots__ = (
        "schema_version",
        "identity_id",
        "created_at",
        "updated_at",
        "source",
        "last_turn_count",
        "last_checkpoint_count",
        "last_started_at",
        "last_stopped_at",
        "last_self_model_version",
        "last_self_model_identity",
        "evolution_record_count",
        "last_audit_log_offset",
        "last_audit_log_path",
        "boot_count",
        "last_boot_mode",
        "last_boot_at",
    )

    def __init__(
        self,
        *,
        identity_id: str = DEFAULT_IDENTITY_ID,
        source: str = SOURCE_INITIAL,
        created_at: Optional[str] = None,
    ) -> None:
        """构造 RuntimeSnapshot。

        参数:
        - identity_id: 根标识(默认 "yuyi_default")。
        - source: 当前 source 取值(默认 "initial")。
        - created_at: 外部注入时间戳(默认 None → 用 _now_iso())。
        """
        self.schema_version: str = RUNTIME_SNAPSHOT_SCHEMA_VERSION
        self.identity_id: str = str(identity_id or DEFAULT_IDENTITY_ID)
        self.created_at: str = str(created_at) if created_at else _now_iso()
        self.updated_at: str = self.created_at
        self.source: str = str(source or SOURCE_INITIAL)

        self.last_turn_count: int = 0
        self.last_checkpoint_count: int = 0
        self.last_started_at: Optional[str] = None
        self.last_stopped_at: Optional[str] = None

        self.last_self_model_version: Optional[int] = None
        self.last_self_model_identity: Optional[str] = None
        self.evolution_record_count: Optional[int] = None

        self.last_audit_log_offset: Optional[int] = None
        self.last_audit_log_path: Optional[str] = None

        self.boot_count: int = 0
        self.last_boot_mode: str = SOURCE_FRESH
        self.last_boot_at: Optional[str] = None

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """序列化为 dict。字段顺序与 __slots__ 一致。"""
        return {
            "schema_version": self.schema_version,
            "identity_id": self.identity_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
            "last_turn_count": self.last_turn_count,
            "last_checkpoint_count": self.last_checkpoint_count,
            "last_started_at": self.last_started_at,
            "last_stopped_at": self.last_stopped_at,
            "last_self_model_version": self.last_self_model_version,
            "last_self_model_identity": self.last_self_model_identity,
            "evolution_record_count": self.evolution_record_count,
            "last_audit_log_offset": self.last_audit_log_offset,
            "last_audit_log_path": self.last_audit_log_path,
            "boot_count": self.boot_count,
            "last_boot_mode": self.last_boot_mode,
            "last_boot_at": self.last_boot_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "RuntimeSnapshot":
        """从 dict 反序列化。容错优先,从不抛异常。

        行为:
        - 非 dict 输入 → 返回新实例(默认值)
        - 未知字段 → 静默忽略
        - 缺字段 → 使用默认值
        - 类型错误 → _safe_int / str() 兜底
        """
        s = cls()
        if not isinstance(data, dict):
            return s
        try:
            s.schema_version = str(data.get("schema_version", s.schema_version))
        except Exception:  # noqa: BLE001
            pass
        try:
            s.identity_id = str(data.get("identity_id", s.identity_id))
        except Exception:  # noqa: BLE001
            pass
        try:
            s.created_at = str(data.get("created_at", s.created_at))
        except Exception:  # noqa: BLE001
            pass
        try:
            s.updated_at = str(data.get("updated_at", s.updated_at))
        except Exception:  # noqa: BLE001
            pass
        try:
            s.source = str(data.get("source", s.source))
        except Exception:  # noqa: BLE001
            pass

        s.last_turn_count = _safe_int(data.get("last_turn_count", 0))
        s.last_checkpoint_count = _safe_int(data.get("last_checkpoint_count", 0))

        s.last_started_at = _safe_optional_str(data.get("last_started_at"))
        s.last_stopped_at = _safe_optional_str(data.get("last_stopped_at"))

        s.last_self_model_version = _safe_optional_int(data.get("last_self_model_version"))
        s.last_self_model_identity = _safe_optional_str(data.get("last_self_model_identity"))
        s.evolution_record_count = _safe_optional_int(data.get("evolution_record_count"))
        s.last_audit_log_offset = _safe_optional_int(data.get("last_audit_log_offset"))
        s.last_audit_log_path = _safe_optional_str(data.get("last_audit_log_path"))

        s.boot_count = _safe_int(data.get("boot_count", 0))

        try:
            s.last_boot_mode = str(data.get("last_boot_mode", SOURCE_FRESH))
        except Exception:  # noqa: BLE001
            s.last_boot_mode = SOURCE_FRESH

        s.last_boot_at = _safe_optional_str(data.get("last_boot_at"))

        return s

    # --------------------------------------------------------
    # schema 兼容
    # --------------------------------------------------------
    def is_schema_compatible(self) -> bool:
        """判定 schema_version 是否与当前版本兼容。

        v1.0 策略:严格相等。
        v1.1+ 预留:可放开为接受同主版本号的次版本。
        """
        return self.schema_version == RUNTIME_SNAPSHOT_SCHEMA_VERSION

    # --------------------------------------------------------
    # 语义辅助
    # --------------------------------------------------------
    def mark_boot(self, boot_mode: str, *, now_iso: Optional[str] = None) -> int:
        """更新 last_boot_* 字段并 boot_count+=1,返回新值。

        不修改 schema_version / created_at。
        """
        now = str(now_iso) if now_iso else _now_iso()
        self.last_boot_mode = str(boot_mode or SOURCE_FRESH)
        self.last_boot_at = now
        self.updated_at = now
        self.boot_count = _safe_int(self.boot_count) + 1
        return self.boot_count

    def mark_stopped(self, stopped_at: str) -> None:
        """更新 last_stopped_at 与 updated_at。"""
        try:
            self.last_stopped_at = str(stopped_at) if stopped_at else None
        except Exception:  # noqa: BLE001
            self.last_stopped_at = None
        self.updated_at = _now_iso()

    def touch(self) -> None:
        """仅更新 updated_at(用于周期性写盘)。"""
        self.updated_at = _now_iso()

    def copy(self) -> "RuntimeSnapshot":
        """深拷贝(字段都是标量,直接构造新实例即可)。"""
        d = self.to_dict()
        s = RuntimeSnapshot.from_dict(d)
        return s

    def __repr__(self) -> str:
        try:
            return (
                f"RuntimeSnapshot("
                f"schema_version={self.schema_version!r}, "
                f"identity_id={self.identity_id!r}, "
                f"source={self.source!r}, "
                f"boot_count={self.boot_count!r}, "
                f"last_turn_count={self.last_turn_count!r}, "
                f"last_self_model_version={self.last_self_model_version!r})"
            )
        except Exception:  # noqa: BLE001
            return "RuntimeSnapshot(<unprintable>)"


# ============================================================
# IO 层:RuntimeSnapshotStore
# ============================================================
class RuntimeSnapshotStore:
    """RuntimeSnapshot JSON 文件持久化层(Phase 5.0-C / v1.0)。

    后端:JSON 文件 —— <path>
    原子写:tmp + os.replace
    损坏隔离:解析失败 → 返回 None + 记录 last_error
    """

    name: str = "runtime_snapshot_store"
    schema_version: str = RUNTIME_SNAPSHOT_SCHEMA_VERSION

    def __init__(
        self,
        path: str = DEFAULT_RUNTIME_SNAPSHOT_PATH,
        *,
        auto_create_dir: bool = True,
        max_bytes: int = _RUNTIME_SNAPSHOT_MAX_BYTES,
    ) -> None:
        """构造 RuntimeSnapshotStore。

        参数:
        - path: snapshot 落盘路径。
        - auto_create_dir: 写入时若目录不存在是否自动创建(默认 True)。
        - max_bytes: 文件大小保护,超过则拒绝写入(默认 1 MB)。
        """
        self._lock = threading.RLock()

        self._path: str = str(path or DEFAULT_RUNTIME_SNAPSHOT_PATH)
        try:
            self._max_bytes = int(max_bytes)
        except (TypeError, ValueError):
            self._max_bytes = _RUNTIME_SNAPSHOT_MAX_BYTES
        if self._max_bytes < 1024:
            self._max_bytes = _RUNTIME_SNAPSHOT_MAX_BYTES
        self._auto_create = bool(auto_create_dir)

        # 统计
        self._save_count: int = 0
        self._save_failures: int = 0
        self._load_count: int = 0
        self._load_failures: int = 0
        self._delete_count: int = 0
        self._last_error: Optional[str] = None
        self._last_saved_at: Optional[str] = None
        self._last_loaded_at: Optional[str] = None

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def path(self) -> str:
        return self._path

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def save_count(self) -> int:
        with self._lock:
            return self._save_count

    @property
    def save_failures(self) -> int:
        with self._lock:
            return self._save_failures

    @property
    def load_count(self) -> int:
        with self._lock:
            return self._load_count

    @property
    def load_failures(self) -> int:
        with self._lock:
            return self._load_failures

    @property
    def delete_count(self) -> int:
        with self._lock:
            return self._delete_count

    # --------------------------------------------------------
    # IO
    # --------------------------------------------------------
    def exists(self) -> bool:
        try:
            return os.path.exists(self._path)
        except Exception:  # noqa: BLE001
            return False

    def load(self) -> Optional[RuntimeSnapshot]:
        """读取 snapshot。失败时返回 None + 设置 last_error。

        行为:
        - 文件不存在 → 返回 None(不记录错误)
        - 解析失败   → 返回 None + 记录 last_error
        - schema 不匹配 → 返回 None(不隔离,避免误删可能修复的数据)
        - 任何 IO 异常 → 返回 None + 记录 last_error
        """
        with self._lock:
            path = self._path
            try:
                if not os.path.exists(path):
                    self._last_error = None
                    return None
                size = os.path.getsize(path)
                if size > self._max_bytes:
                    self._load_failures += 1
                    self._last_error = f"file_too_large:{size}"
                    logger.warning("RuntimeSnapshotStore 文件过大: %d > %d", size, self._max_bytes)
                    return None
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
            except Exception as exc:  # noqa: BLE001
                self._load_failures += 1
                self._last_error = f"read_failed: {exc}"
                logger.warning("RuntimeSnapshotStore 读盘失败: %s", exc)
                return None

        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._load_failures += 1
                self._last_error = f"json_decode_failed: {exc}"
            logger.warning("RuntimeSnapshotStore JSON 解析失败: %s", exc)
            return None

        if not isinstance(obj, dict):
            with self._lock:
                self._load_failures += 1
                self._last_error = "envelope_not_dict"
            return None

        snap = RuntimeSnapshot.from_dict(obj)
        # schema 不匹配视为 fresh(不抛)
        if not snap.is_schema_compatible():
            with self._lock:
                self._load_failures += 1
                self._last_error = f"schema_mismatch:{snap.schema_version}"
            return None

        with self._lock:
            self._load_count += 1
            self._last_loaded_at = _now_iso()
            self._last_error = None
        return snap

    def save(self, snap: RuntimeSnapshot) -> bool:
        """原子保存 snapshot。

        步骤:
        1) 校验 isinstance(snap, RuntimeSnapshot)
        2) 写 <path>.tmp
        3) os.replace 原子替换
        4) 失败 → 清理 tmp + 返回 False
        """
        if not isinstance(snap, RuntimeSnapshot):
            with self._lock:
                self._save_failures += 1
                self._last_error = "invalid_snapshot_type"
            return False

        with self._lock:
            path = self._path

        try:
            payload = snap.to_dict()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._save_failures += 1
                self._last_error = f"to_dict_failed: {exc}"
            logger.warning("RuntimeSnapshotStore.to_dict 失败: %s", exc)
            return False

        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as exc:
            with self._lock:
                self._save_failures += 1
                self._last_error = f"json_dumps_failed: {exc}"
            logger.warning("RuntimeSnapshotStore JSON 序列化失败: %s", exc)
            return False

        encoded = text.encode("utf-8")
        if len(encoded) > self._max_bytes:
            with self._lock:
                self._save_failures += 1
                self._last_error = f"file_too_large:{len(encoded)}"
            logger.warning(
                "RuntimeSnapshotStore snapshot 过大: %d > %d",
                len(encoded), self._max_bytes,
            )
            return False

        with self._lock:
            try:
                if self._auto_create:
                    folder = os.path.dirname(path)
                    if folder and not os.path.exists(folder):
                        try:
                            os.makedirs(folder, exist_ok=True)
                        except Exception:  # noqa: BLE001
                            pass
                # 写临时文件
                tmp_path = path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.write(text)
                    try:
                        f.flush()
                        os.fsync(f.fileno())
                    except Exception:  # noqa: BLE001
                        pass
                # 原子替换
                try:
                    os.replace(tmp_path, path)
                except Exception:  # noqa: BLE001
                    # 回退:删旧换新
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                        os.rename(tmp_path, path)
                    except Exception as exc:  # noqa: BLE001
                        try:
                            if os.path.exists(tmp_path):
                                os.remove(tmp_path)
                        except Exception:  # noqa: BLE001
                            pass
                        self._save_failures += 1
                        self._last_error = f"replace_failed: {exc}"
                        logger.warning("RuntimeSnapshotStore 落盘失败: %s", exc)
                        return False
            except Exception as exc:  # noqa: BLE001
                self._save_failures += 1
                self._last_error = f"write_failed: {exc}"
                logger.warning("RuntimeSnapshotStore 写盘异常: %s", exc)
                return False

        with self._lock:
            self._save_count += 1
            self._last_saved_at = _now_iso()
            self._last_error = None
        return True

    def delete(self) -> bool:
        """删除 snapshot 文件(不存在不算失败)。"""
        with self._lock:
            try:
                if os.path.exists(self._path):
                    os.remove(self._path)
                # 同时清理 .tmp / .corrupt
                for suf in (".tmp", ".corrupt"):
                    p2 = self._path + suf
                    if os.path.exists(p2):
                        try:
                            os.remove(p2)
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"delete_failed: {exc}"
                return False
            self._delete_count += 1
        return True

    # --------------------------------------------------------
    # 健康检查
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            file_exists = False
            file_size = 0
            try:
                if os.path.exists(self._path):
                    file_exists = True
                    file_size = os.path.getsize(self._path)
            except Exception:  # noqa: BLE001
                pass
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "path": self._path,
                "file_exists": file_exists,
                "file_size": file_size,
                "max_bytes": self._max_bytes,
                "save_count": self._save_count,
                "save_failures": self._save_failures,
                "load_count": self._load_count,
                "load_failures": self._load_failures,
                "delete_count": self._delete_count,
                "last_saved_at": self._last_saved_at,
                "last_loaded_at": self._last_loaded_at,
                "last_error": self._last_error,
            }


# ============================================================
# 业务编排层:RuntimeSnapshotBuilder
# ============================================================
class RuntimeSnapshotBuilder:
    """RuntimeSnapshot 业务编排层(Phase 5.0-C / v1.0)。

    职责:
    - 构造 initial / fresh snapshot
    - 合并 LongLoop state + PR + Audit 到 snapshot
    - 落盘 + 写审计(单一入口)
    """

    name: str = "runtime_snapshot_builder"
    schema_version: str = RUNTIME_SNAPSHOT_SCHEMA_VERSION

    def __init__(
        self,
        *,
        identity_id: str = DEFAULT_IDENTITY_ID,
        store: Optional[RuntimeSnapshotStore] = None,
        persistence_runtime: Any = None,
        audit_logger: Any = None,
    ) -> None:
        """构造 RuntimeSnapshotBuilder。

        参数:
        - identity_id: 根标识(默认 "yuyi_default")。
        - store: 持久化 Store(None → 内部自建,使用默认路径)。
        - persistence_runtime: 可选,用于合并 last_persisted_version /
          current_identity_id / evolution_count(默认 None → 字段退化)。
        - audit_logger: 可选,用于合并 write_count / log_path(默认 None → 字段退化)。
        """
        self._lock = threading.RLock()
        self._identity_id: str = str(identity_id or DEFAULT_IDENTITY_ID)
        self._store: RuntimeSnapshotStore = store or RuntimeSnapshotStore()
        # 弱类型,避免循环依赖
        self._persistence: Any = persistence_runtime
        self._audit: Any = audit_logger

        # 上一次 commit 的 snapshot(供下一次 build_from_state 增量合并)
        self._last_snapshot: Optional[RuntimeSnapshot] = None

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def identity_id(self) -> str:
        with self._lock:
            return self._identity_id

    @property
    def store(self) -> RuntimeSnapshotStore:
        with self._lock:
            return self._store

    @property
    def last_snapshot(self) -> Optional[RuntimeSnapshot]:
        with self._lock:
            return self._last_snapshot

    # --------------------------------------------------------
    # 构造
    # --------------------------------------------------------
    def build_initial(self) -> RuntimeSnapshot:
        """构造全新 snapshot(identity_id + 17 字段默认值)。"""
        return RuntimeSnapshot(identity_id=self._identity_id, source=SOURCE_INITIAL)

    def build_from_state(
        self,
        state: Optional[Dict[str, Any]],
        *,
        previous: Optional[RuntimeSnapshot] = None,
    ) -> RuntimeSnapshot:
        """从 LongLoop state + PR + Audit 合并构造新 snapshot。

        参数:
        - state: LongLoop._collect_state() 输出,可为 None。
        - previous: 已有的 snapshot(为 None 时使用 self._last_snapshot)。

        行为:
        1) 若 previous 为 None → build_initial
        2) 合并 state 字段(last_turn_count / last_started_at / source)
        3) 合并 PR 状态
        4) 合并 Audit 状态
        5) 推断 source
        6) 递增 last_checkpoint_count
        7) 更新 updated_at
        异常全部隔离,降级为安全默认值。
        """
        with self._lock:
            base = previous if previous is not None else self._last_snapshot
            if base is not None:
                snap = base.copy()
            else:
                snap = self.build_initial()

            # 1) 合并 LongLoop state
            if isinstance(state, dict):
                try:
                    snap.last_turn_count = _safe_int(
                        state.get("turn_count", snap.last_turn_count),
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    started_at = state.get("started_at")
                    if started_at is not None:
                        snap.last_started_at = str(started_at)
                except Exception:  # noqa: BLE001
                    pass
                # 2) 推断 source
                loop_state_value = None
                try:
                    loop_state_value = state.get("state")
                except Exception:  # noqa: BLE001
                    loop_state_value = None
                if loop_state_value == "STOPPED":
                    snap.source = SOURCE_FINAL
                elif loop_state_value == "RUNNING":
                    snap.source = SOURCE_PERIODIC
                else:
                    snap.source = SOURCE_CHECKPOINT
            else:
                # state 为 None/非 dict → 保持 previous.source(或 initial)
                if base is None:
                    snap.source = SOURCE_CHECKPOINT

            # 3) 合并 PersistenceRuntime 状态
            if self._persistence is not None:
                try:
                    v = getattr(self._persistence, "last_persisted_version", None)
                    if v is not None:
                        snap.last_self_model_version = _safe_int(v)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    iid = getattr(self._persistence, "current_identity_id", None)
                    if iid:
                        snap.last_self_model_identity = str(iid)
                    else:
                        snap.last_self_model_identity = self._identity_id
                except Exception:  # noqa: BLE001
                    snap.last_self_model_identity = self._identity_id
                try:
                    cnt = self._persistence.get_evolution_count()
                    if cnt is not None:
                        snap.evolution_record_count = _safe_int(cnt)
                except Exception:  # noqa: BLE001
                    pass

            # 4) 合并 Audit 状态
            if self._audit is not None:
                try:
                    snap.last_audit_log_offset = _safe_int(
                        getattr(self._audit, "write_count", 0),
                    )
                except Exception:  # noqa: BLE001
                    pass
                try:
                    snap.last_audit_log_path = getattr(
                        self._audit, "log_path", snap.last_audit_log_path,
                    )
                except Exception:  # noqa: BLE001
                    pass

            # 5) 递增 last_checkpoint_count
            snap.last_checkpoint_count = _safe_int(snap.last_checkpoint_count) + 1
            # 6) 更新时间戳
            snap.touch()
            snap.identity_id = self._identity_id

            return snap

    def commit(
        self,
        snap: RuntimeSnapshot,
        *,
        reason: str = SOURCE_CHECKPOINT,
    ) -> bool:
        """落盘 snapshot + 写 audit.log_checkpoint。

        步骤:
        1) store.save(snap)
        2) 写 audit.log_checkpoint(reason, state)
        3) 更新 self._last_snapshot
        4) 异常全部隔离,返回 False
        """
        if not isinstance(snap, RuntimeSnapshot):
            return False
        with self._lock:
            saved = False
            try:
                saved = self._store.save(snap)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RuntimeSnapshotBuilder.commit 落盘失败(已隔离): %s", exc)
                saved = False

            if saved:
                # 成功后更新 last_snapshot
                self._last_snapshot = snap.copy()

            # 写 audit(异常隔离)
            if self._audit is not None:
                try:
                    self._audit.log_checkpoint(
                        reason=str(reason or snap.source),
                        state={
                            "turn_count": snap.last_turn_count,
                            "checkpoint_count": snap.last_checkpoint_count,
                            "self_model_version": snap.last_self_model_version,
                            "source": snap.source,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass

            return saved

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "schema_version": self.schema_version,
                "identity_id": self._identity_id,
                "store": self._store.health_check() if self._store is not None else None,
                "has_persistence_runtime": self._persistence is not None,
                "has_audit_logger": self._audit is not None,
                "has_last_snapshot": self._last_snapshot is not None,
            }


__all__ = [
    "RuntimeSnapshot",
    "RuntimeSnapshotStore",
    "RuntimeSnapshotBuilder",
    "RUNTIME_SNAPSHOT_SCHEMA_VERSION",
    "DEFAULT_RUNTIME_SNAPSHOT_PATH",
    "DEFAULT_IDENTITY_ID",
    "SOURCE_INITIAL",
    "SOURCE_FRESH",
    "SOURCE_RESTORE",
    "SOURCE_PERIODIC",
    "SOURCE_FINAL",
    "SOURCE_CHECKPOINT",
]
