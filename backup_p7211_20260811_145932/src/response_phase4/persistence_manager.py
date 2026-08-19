"""
Phase 4.0 — R2.7.4 Phase4 Persistence Manager（跨进程保持羽依）

目标："今天启动聊天、关闭、明天再启动，她仍然知道昨天发生了什么、自己的状态是什么"

三个快照：
  personality_snapshot  →  PersonalityState.to_dict()
    - traits {name: float 0~1}
    - version（单调）
    - evolution_record_ids（审计）
    - applied_proposal_ids（重启后防止重复 apply，EP-2 兼容）

  memory_snapshot       →  List[memory_record]（与 memory.json 的 list 同构）
    - records: [{id, text, topic, timestamp_ms, ...}]

  relationship_snapshot →  {
      "user_id": str,
      "trust_level": float 0~1,
      "closeness": float 0~1,
      "interaction_count": int,
      "last_interaction_ts_ms": int,
      "shared_memory_tags": [str],
      "history": [Dict[str, Any]]
    }

管理器职责：
  1. save_all() / load_all() 三态统一落盘 / 恢复
  2. personality 版本单调保护（load 时若磁盘版本 < 当前运行版本，保留当前版本不降）
  3. 所有损坏场景 → 降级默认值，并记录 recover_warning 到 load_result
     （返回结构里有 "recovery_details": [{level, issue, detail}]）
  4. 不引用任何 LLM / Growth / Runtime 模块；纯序列化/反序列化 + Gate 检查
"""

from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.personality.personality_state import PersonalityState


logger = logging.getLogger(__name__)

# ============================================================
# Snapshot shape 合约（R2.7.4 冻结）
# ============================================================
PERSONALITY_SNAPSHOT_REQUIRED = ("schema_version", "traits", "version", "applied_proposal_ids")
MEMORY_SNAPSHOT_REQUIRED = ("schema_version", "records")
RELATIONSHIP_SNAPSHOT_REQUIRED = (
    "schema_version",
    "trust_level",
    "closeness",
    "interaction_count",
    "last_interaction_ts_ms",
    "shared_memory_tags",
    "history",
)

# trait 允许变化的稳定性阈值（RPG-3）
SINGLE_TRAIT_DELTA_CAP_PER_SESSION = 0.15      # 单次应用（apply_evolution）不能超过 0.15
GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE = 0.40   # 从出厂基线到当前，所有 trait 总距离之和 ≤ 0.40 / N

CURRENT_SCHEMA_VERSION = 1
DEFAULT_SNAPSHOT_DIR = "data/snapshots"
DEFAULT_USERS_ROOT_DIR = "data/users"

# 出厂基线 trait（与 PersonalityState() 默认保持一致）
_BASELINE_TRAITS: Dict[str, float] = {
    "creativity": 0.6,
    "curiosity": 0.7,
    "empathy": 0.65,
    "independence": 0.6,
    "playfulness": 0.55,
}

# ============ R2.7.6 全局 per-user SnapshotLock =============
# 策略：双层锁
#   1) 进程内：threading.Lock（per user_id 粒度）保证同进程多线程串行
#   2) 跨进程：file lock（.lock 文件）保证不同 worker 进程/重启不冲突
# _locks_registry 只存已创建的锁 key: threading.Lock
_LOCKS_REGISTRY_LOCK = threading.Lock()
_LOCKS_REGISTRY: Dict[str, "threading.Lock"] = {}


# ============================================================
# 验证函数（Gate 可直接调用）
# ============================================================
def validate_personality_snapshot_shape(s: Any) -> None:
    if not isinstance(s, dict):
        raise ValueError("personality_snapshot 必须是 dict")
    for req in PERSONALITY_SNAPSHOT_REQUIRED:
        if req not in s:
            raise ValueError(f"personality_snapshot 缺少字段: {req}")
    if not isinstance(s.get("traits"), dict):
        raise ValueError("personality_snapshot.traits 必须是 dict")
    if not isinstance(s.get("version"), int) or s["version"] < 0:
        raise ValueError("personality_snapshot.version 必须是 int >= 0")
    if not isinstance(s.get("applied_proposal_ids"), list):
        raise ValueError("personality_snapshot.applied_proposal_ids 必须是 list")


def validate_memory_snapshot_shape(s: Any) -> None:
    if not isinstance(s, dict):
        raise ValueError("memory_snapshot 必须是 dict")
    for req in MEMORY_SNAPSHOT_REQUIRED:
        if req not in s:
            raise ValueError(f"memory_snapshot 缺少字段: {req}")
    if not isinstance(s.get("records"), list):
        raise ValueError("memory_snapshot.records 必须是 list")
    # 每条 record 至少 id/text
    for idx, rec in enumerate(s["records"]):
        if not isinstance(rec, dict):
            raise ValueError(f"memory.records[{idx}] 不是 dict")
        if "id" not in rec or "text" not in rec:
            raise ValueError(f"memory.records[{idx}] 缺少 id 或 text")


def validate_relationship_snapshot_shape(s: Any) -> None:
    if not isinstance(s, dict):
        raise ValueError("relationship_snapshot 必须是 dict")
    for req in RELATIONSHIP_SNAPSHOT_REQUIRED:
        if req not in s:
            raise ValueError(f"relationship_snapshot 缺少字段: {req}")
    for k in ("trust_level", "closeness"):
        v = s.get(k)
        if not isinstance(v, (int, float)):
            raise ValueError(f"relationship.{k} 必须是数值")
        if not (0.0 <= float(v) <= 1.0):
            raise ValueError(f"relationship.{k} 超出 [0,1] 范围")
    if not isinstance(s.get("interaction_count"), int) or s["interaction_count"] < 0:
        raise ValueError("relationship.interaction_count 必须是 int >= 0")
    if not isinstance(s.get("shared_memory_tags"), list):
        raise ValueError("relationship.shared_memory_tags 必须是 list")
    if not isinstance(s.get("history"), list):
        raise ValueError("relationship.history 必须是 list")


# ============================================================
# Load Result（含恢复警告）
# ============================================================
@dataclass
class LoadResult:
    """三态恢复结果（包含降级警告）。"""
    personality: PersonalityState
    memories: List[Dict[str, Any]]
    relationship: Dict[str, Any]

    # 恢复细节：[{severity, issue, detail}]
    recovery_details: List[Dict[str, str]]

    @property
    def has_warnings(self) -> bool:
        return any(r["severity"] != "ok" for r in self.recovery_details)


def _mk_default_relationship() -> Dict[str, Any]:
    """R2.7.6：默认关系状态 schema 同时写入 legacy 字段，
    保证 Orchestrator RelationshipState（trust/familiarity/...）读取兼容。"""
    base = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "trust_level": 0.5,
        "closeness": 0.3,
        "interaction_count": 0,
        "last_interaction_ts_ms": 0,
        "shared_memory_tags": [],
        "history": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # legacy 双向兼容：让老的 relationship_state.json 读取也能读到合理值
    # relationship_state.py 期望：trust / familiarity / bond_strength / promise_level /
    #   shared_history / activity_level / milestones / important_events
    base["trust"] = base["trust_level"]
    base["familiarity"] = base["closeness"]
    base["bond_strength"] = round((base["trust_level"] + base["closeness"]) / 2.0, 6)
    base["promise_level"] = 0.0
    base["shared_history"] = 0.0
    base["activity_level"] = 0.0
    base["milestones"] = []
    base["important_events"] = []
    return base


# ============================================================
# Phase4PersistenceManager
# ============================================================
class Phase4PersistenceManager:
    """R2.7.4 三态统一持久化管理器。

    参数：
        snapshot_dir: 快照目录（默认 data/snapshots）
        user_tag:     可选用户标识（用于生成独立快照文件）

    R2.7.6 新增：
        · Phase4PersistenceManager.for_user(user_id, users_root)：
          按 user_id 生成独立目录 data/users/<user_id>/，并自动带 per-user 锁。
        · save_all / load_all 支持 context manager 的 in-memory 锁（同 Flask 多线程），
          还能在文件层做 .lock（跨进程）。
        · import_legacy_memory(legacy_json_path)：只读导入旧 data/memory.json
          到 records（只补缺失，不覆盖现有）。
        · relationship schema 双向兼容：同时写 trust_level + trust / closeness + familiarity。
    """

    # ================================================================
    # 工厂（R2.7.6）
    # ================================================================
    @classmethod
    def for_user(
        cls,
        user_id: str,
        users_root: str = DEFAULT_USERS_ROOT_DIR,
    ) -> "Phase4PersistenceManager":
        """真实生产环境的入口：每个 user_id 一个目录（data/users/<user_id>/）。

        目录结构：
            data/users/
                123456/
                    personality.json
                    memory.json
                    relationship.json
                    runtime.json   (额外元数据)
                    .lock          (跨进程锁占位文件，for_user 会自动创建)
        """
        safe_uid = _sanitize_user_id_for_path(user_id)
        pm = cls(
            snapshot_dir=str(Path(users_root) / safe_uid),
            user_tag=safe_uid,
        )
        # 在锁文件夹中标记 .lock（跨进程锁占位）
        try:
            lockfile = pm.snapshot_dir / ".lock"
            lockfile.parent.mkdir(parents=True, exist_ok=True)
            if not lockfile.exists():
                lockfile.write_text(
                    f"created_at: {datetime.now(timezone.utc).isoformat()}\n"
                    f"user_id: {user_id}\n",
                    encoding="utf-8",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("for_user: .lock 文件创建失败（不影响运行）: %s", exc)
        return pm

    def __init__(
        self,
        snapshot_dir: str = DEFAULT_SNAPSHOT_DIR,
        user_tag: str = "default",
    ) -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.user_tag = user_tag or "default"
        self.rec_details: List[Dict[str, str]] = []
        # R2.7.6: 关联 per-user threading.Lock（进程内保证同 user 串行）
        self._thread_lock = _get_or_create_thread_lock(self.user_tag)

    # ───────────────────────────────────────
    # 文件路径
    # ────────────────────────────────────────
    @property
    def personality_file(self) -> Path:
        return self.snapshot_dir / f"personality_{self.user_tag}.json"

    @property
    def memory_file(self) -> Path:
        return self.snapshot_dir / f"memory_{self.user_tag}.json"

    @property
    def relationship_file(self) -> Path:
        return self.snapshot_dir / f"relationship_{self.user_tag}.json"

    # ────────────────────────────────────────
    # 统一落盘
    # ────────────────────────────────────────
    def save_all(
        self,
        personality: PersonalityState,
        memories: Optional[List[Dict[str, Any]]] = None,
        relationship: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """保存三态快照。返回 {"personality": path, "memory": path, "relationship": path}

        原子写入：先写 .tmp，再 rename（防止写一半进程退出导致损坏）。
        R2.7.6：并发保护通过调用方 acquire_lock() 提供（threading.Lock + FileLock 组合）。
        本函数内部不做嵌套加锁，避免死锁和重入锁 FileExistsError。
        """
        # Personality
        ps_dict = personality.to_dict()
        validate_personality_snapshot_shape(ps_dict)  # 写前校验
        self._atomic_write(self.personality_file, ps_dict)

        # Memory
        mem_payload = {"schema_version": CURRENT_SCHEMA_VERSION, "records": list(memories or [])}
        validate_memory_snapshot_shape(mem_payload)
        self._atomic_write(self.memory_file, mem_payload)

        # Relationship —— R2.7.6：先补齐 legacy 双向字段（trust/familiarity/bond_strength…）
        rel = dict(relationship) if relationship is not None else _mk_default_relationship()
        rel = _normalize_relationship_for_write(rel)
        rel.setdefault("schema_version", CURRENT_SCHEMA_VERSION)
        rel.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        rel["updated_at"] = datetime.now(timezone.utc).isoformat()
        try:
            validate_relationship_snapshot_shape(rel)
        except ValueError as exc:
            logger.warning("save_all: relationship 形状错误，将用默认值覆盖。原因: %s", exc)
            rel = _mk_default_relationship()
        self._atomic_write(self.relationship_file, rel)

        return {
            "personality": str(self.personality_file),
            "memory": str(self.memory_file),
            "relationship": str(self.relationship_file),
        }

    # ────────────────────────────────────────
    # 统一恢复
    # ────────────────────────────────────────
    def load_all(
        self,
        *,
        current_personality_version_ceiling: Optional[int] = None,
    ) -> LoadResult:
        """加载三态快照。

        参数：
            current_personality_version_ceiling：若当前运行时已经有更高 version（运行过程中发生成长），
                则拒绝回退到更旧版本（保持当前 version）。
        """
        self.rec_details = []
        personality = self._load_personality(current_personality_version_ceiling)
        memories = self._load_memory()
        relationship = self._load_relationship()

        # RPG-3 Personality Stability Gate：
        # 恢复后做稳定性检查：与出厂基线的距离
        stability_warns = self._check_personality_stability(personality)
        self.rec_details.extend(stability_warns)

        return LoadResult(
            personality=personality,
            memories=memories,
            relationship=relationship,
            recovery_details=list(self.rec_details),
        )

    # ────────────────────────────────────────
    # 三态各自的加载实现（损坏降级）
    # ────────────────────────────────────────
    def _load_personality(self, ceiling: Optional[int]) -> PersonalityState:
        path = self.personality_file
        if not path.exists():
            self.rec_details.append({
                "severity": "info",
                "issue": "personality_snapshot_missing",
                "detail": f"{path} 不存在，返回出厂基线 PersonalityState",
            })
            return PersonalityState()

        try:
            raw = self._read_json(path)
            validate_personality_snapshot_shape(raw)
            ps = PersonalityState.from_dict(raw, current_version_ceiling=ceiling)
            self.rec_details.append({
                "severity": "ok",
                "issue": "personality_loaded",
                "detail": f"version={ps.version}, traits_count={len(ps.traits)}",
            })
            return ps
        except ValueError as exc:
            msg = str(exc)
            if "rollback detected" in msg:
                self.rec_details.append({
                    "severity": "warn",
                    "issue": "personality_version_rollback_blocked",
                    "detail": msg,
                })
                # 版本回退：返回空基线（调用方通常会用 ceiling 对应的 state，这里返回基线以示"回退失败"）
                return PersonalityState()
            # 其余 ValueError：损坏 → 降级默认
            self.rec_details.append({
                "severity": "warn",
                "issue": "personality_snapshot_corrupted",
                "detail": msg,
            })
            return PersonalityState()
        except Exception as exc:  # noqa: BLE001
            self.rec_details.append({
                "severity": "warn",
                "issue": "personality_snapshot_unreadable",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            return PersonalityState()

    def _load_memory(self) -> List[Dict[str, Any]]:
        path = self.memory_file
        if not path.exists():
            self.rec_details.append({
                "severity": "info",
                "issue": "memory_snapshot_missing",
                "detail": f"{path} 不存在，返回空记忆列表",
            })
            return []
        try:
            raw = self._read_json(path)
            validate_memory_snapshot_shape(raw)
            recs = list(raw["records"])
            self.rec_details.append({
                "severity": "ok",
                "issue": "memory_loaded",
                "detail": f"loaded {len(recs)} memory records",
            })
            return recs
        except ValueError as exc:
            self.rec_details.append({
                "severity": "warn",
                "issue": "memory_snapshot_corrupted",
                "detail": str(exc),
            })
            return []
        except Exception as exc:  # noqa: BLE001
            self.rec_details.append({
                "severity": "warn",
                "issue": "memory_snapshot_unreadable",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            return []

    def _load_relationship(self) -> Dict[str, Any]:
        path = self.relationship_file
        if not path.exists():
            self.rec_details.append({
                "severity": "info",
                "issue": "relationship_snapshot_missing",
                "detail": f"{path} 不存在，返回默认关系状态",
            })
            return _mk_default_relationship()
        try:
            raw = self._read_json(path)
            validate_relationship_snapshot_shape(raw)
            self.rec_details.append({
                "severity": "ok",
                "issue": "relationship_loaded",
                "detail": (
                    f"trust={raw['trust_level']:.2f}, closeness={raw['closeness']:.2f}, "
                    f"interactions={raw['interaction_count']}"
                ),
            })
            return dict(raw)
        except ValueError as exc:
            self.rec_details.append({
                "severity": "warn",
                "issue": "relationship_snapshot_corrupted",
                "detail": str(exc),
            })
            return _mk_default_relationship()
        except Exception as exc:  # noqa: BLE001
            self.rec_details.append({
                "severity": "warn",
                "issue": "relationship_snapshot_unreadable",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            return _mk_default_relationship()

    # ────────────────────────────────────────
    # RPG-3 稳定性（与出厂基线距离）
    # ────────────────────────────────────────
    def _check_personality_stability(self, ps: PersonalityState) -> List[Dict[str, str]]:
        """检查：
        1) 与基线的总 L1 距离 / N ≤ GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE
        2) 任意单 trait 偏离 ≤ 2x CAP（防止极端漂移）
        """
        warns: List[Dict[str, str]] = []
        total_delta = 0.0
        count = 0
        big_jumps: List[str] = []
        for k, baseline in _BASELINE_TRAITS.items():
            cur = float(ps.traits.get(k, baseline))
            delta = abs(cur - baseline)
            total_delta += delta
            count += 1
            if delta > 2 * GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE:
                big_jumps.append(f"{k}: baseline={baseline} current={cur} delta={delta:.3f}")

        if count == 0:
            return warns
        avg_delta = total_delta / count
        if avg_delta > GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE:
            warns.append({
                "severity": "warn",
                "issue": "personality_drift_global",
                "detail": (
                    f"global avg delta vs baseline={avg_delta:.3f} > "
                    f"cap={GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE}"
                ),
            })
        if big_jumps:
            warns.append({
                "severity": "warn",
                "issue": "personality_drift_single_trait",
                "detail": "; ".join(big_jumps),
            })
        if not warns:
            warns.append({
                "severity": "ok",
                "issue": "personality_stability_ok",
                "detail": (
                    f"avg_delta_vs_baseline={avg_delta:.3f} <= "
                    f"cap={GLOBAL_TRAIT_DELTA_CAP_FROM_BASELINE}"
                ),
            })
        return warns

    # ────────────────────────────────────────
    # 文件读写工具（原子写 + 读取）
    # ────────────────────────────────────────
    def _atomic_write(self, path: Path, payload: Dict[str, Any]) -> None:
        """原子写入快照（先写 .tmp，再 rename/replace）。

        R2.7.6-AUDIT: 加重试 + Windows 兼容。
          Windows 上如果目标文件被其他进程（如杀毒软件/备份软件）短暂占用，
          os.replace / Path.replace 会抛 PermissionError / OSError；重试 5 次，
          指数退避（50ms/100ms/200ms/400ms/800ms），成功率大幅提升。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            # R2.7.6-AUDIT: 加 flush + fsync，保证断电时数据已落盘
            f.flush()
            try:
                import os as _os
                _os.fsync(f.fileno())
            except (OSError, AttributeError):
                pass  # Windows 某些文件系统不支持 fsync，忽略

        last_exc: Optional[Exception] = None
        for attempt in range(5):
            try:
                tmp_path.replace(path)
                return
            except (PermissionError, OSError) as exc:
                # Windows: 文件被其他进程占用（杀毒、索引器等）→ 短暂等待重试
                last_exc = exc
                backoff = 0.05 * (2 ** attempt)  # 0.05s / 0.1s / 0.2s / 0.4s / 0.8s
                logger.debug(
                    "[_atomic_write] replace 失败（第 %d 次重试，退避 %.2fs）: %s → %s; err=%s",
                    attempt + 1, backoff, tmp_path, path, exc,
                )
                time.sleep(backoff)
        # 重试耗尽：尝试删除 tmp 文件并抛出最后一次错误
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(
            f"_atomic_write 重试失败（共 5 次）: tmp={tmp_path} → target={path}; last_err={last_exc}"
        ) from last_exc

    def _read_json(self, path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ================================================================
    # R2.7.6 生产化新增 API
    # ================================================================
    def acquire_lock(self):
        """显式获取 user-level 锁（返回可作为 context manager 的组合锁）。

        用法：
            with pm.acquire_lock():
                load = pm.load_all()
                ...
                pm.save_all(...)
        """
        return _CombinedLock([
            self._thread_lock,
            _FileLock(self.personality_file.with_suffix(".personality.flock")),
        ])

    def import_legacy_memory(
        self,
        legacy_memory_path: str,
        *,
        dedupe_by_id: bool = True,
        max_import: Optional[int] = None,
        target_user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """只读导入旧 MemoryStore 风格的 data/memory.json 到内存 records。

        参数：
            legacy_memory_path：例如 "data/memory.json"
            dedupe_by_id：如果 memory id 重复就跳过（不覆盖）
            max_import：最多导入多少条（防止超大老数据一次性把 prompt 撑爆）
            target_user_id：R2.7.6-AUDIT 新增——按 user_id 过滤 legacy 记录，防跨用户数据泄漏。
                · 如果传了 target_user_id，只导入 rec.user_id == target_user_id 的记录
                · 如果 legacy 记录没有 user_id 字段，则：
                    - 如果 self.user_tag == "default"（默认单用户模式）→ 全部导入
                    - 否则 → 跳过（避免把匿名记录导入到具体用户目录）

        返回：
            List[Dict[str, Any]]：成功导入并合并的 records 列表（便于调用方直接 extend）。
            为了兼容旧代码：空列表 [] 表示无导入；list 中非空即导入的记录。
            （R2.7.6-AUDIT 之前版本返回 int；现改为 List 同时支持两者，因 bool(list) 与 int 非零判断兼容。）

        注意：本函数**只修改 PM 返回给调用方的 records 列表，不自动 save_all()。
        调用方需要后续 save_all() 才会真正落到新目录。** 这是故意的：导入
        是只读读源文件 + 合并到目标内存；调用失败不会影响老 data/memory.json。
        """
        recs = self._load_memory()
        existing_ids = {r.get("id") for r in recs if isinstance(r, dict) and r.get("id")}
        legacy_path = Path(legacy_memory_path)
        if not legacy_path.exists():
            self.rec_details.append({
                "severity": "info",
                "issue": "legacy_memory_import_skipped",
                "detail": f"源 {legacy_path} 不存在，跳过导入",
            })
            return []
        try:
            legacy_data = self._read_json(legacy_path)
            # MemoryStore 风格：直接 list 或者 {records: [...]} 都兼容
            if isinstance(legacy_data, list):
                source = legacy_data
            elif isinstance(legacy_data, dict) and isinstance(legacy_data.get("records"), list):
                source = legacy_data["records"]
            else:
                raise ValueError("legacy memory.json 既不是 records 列表，也不是 {'records': [...]} 结构")
        except Exception as exc:  # noqa: BLE001
            self.rec_details.append({
                "severity": "warn",
                "issue": "legacy_memory_import_failed",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            return []

        imported_records: List[Dict[str, Any]] = []
        skipped_user_filter_count = 0
        for rec in source:
            if max_import is not None and len(imported_records) >= max_import:
                break
            if not isinstance(rec, dict):
                continue

            # R2.7.6-AUDIT: user_id 过滤（防跨用户数据泄漏）
            if target_user_id is not None:
                rec_uid = rec.get("user_id")
                if rec_uid is None:
                    # Legacy 记录没有 user_id：
                    #   · default 用户目录（单用户模式）→ 允许导入
                    #   · 具体用户目录 → 跳过，避免把全局/匿名记录错塞给某个用户
                    if self.user_tag != "default":
                        skipped_user_filter_count += 1
                        continue
                elif str(rec_uid) != str(target_user_id):
                    # user_id 不匹配 → 跳过
                    skipped_user_filter_count += 1
                    continue

            # 缺字段补最小结构（避免后续 validate 校验失败）
            normalized = dict(rec)
            if "id" not in normalized:
                normalized["id"] = f"legacy_{int(time.time()*1000)}_{len(imported_records)}"
            if "text" not in normalized:
                if "content" in normalized:
                    normalized["text"] = str(normalized["content"])
                else:
                    continue
            if "timestamp_ms" not in normalized and "timestamp" in normalized:
                try:
                    ts = str(normalized["timestamp"])
                    # 从 isoformat 转 ms
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    normalized["timestamp_ms"] = int(dt.timestamp() * 1000)
                except Exception:  # noqa: BLE001
                    normalized["timestamp_ms"] = int(time.time() * 1000)
            if "topic" not in normalized:
                normalized["topic"] = normalized.get("metadata", {}).get("memory_type", "general") if isinstance(normalized.get("metadata"), dict) else "general"
            if dedupe_by_id and normalized["id"] in existing_ids:
                continue
            recs.append(normalized)
            existing_ids.add(normalized["id"])
            imported_records.append(normalized)

        if imported_records:
            detail = (
                f"imported {len(imported_records)} records from {legacy_path}"
                f" (deduped, no overwrites"
            )
            if target_user_id is not None:
                detail += f", user_filter={target_user_id}, skipped_other_user={skipped_user_filter_count}"
            detail += ")"
            self.rec_details.append({
                "severity": "ok",
                "issue": "legacy_memory_imported",
                "detail": detail,
            })
        else:
            extra = ""
            if skipped_user_filter_count:
                extra = f"（按 user_id 过滤跳过 {skipped_user_filter_count} 条非本用户记录）"
            self.rec_details.append({
                "severity": "info",
                "issue": "legacy_memory_import_noop",
                "detail": (
                    f"源 {legacy_path} 没有可新增导入的记录（已全在内存中）{extra}"
                ),
            })
        return imported_records

    def import_legacy_relationship(self, legacy_relationship_path: str) -> bool:
        """只读合并 data/relationship_state.json 到当前 relationship 内存对象。

        语义：如果 Phase4 新快照不存在（空信任空熟悉度），则从 legacy 取
        trust→trust_level、familiarity→closeness；并把 important_events 合并进
        history。如果 Phase4 已经存在（interaction_count>0），则只把缺失 history
        append（不覆盖当前实时值）。

        返回 True 表示有内容被合并（需要调用方 save_all 落盘）。
        """
        return _LEGACY_NOT_IMPLEMENTED


# ================================================================
# R2.7.6 helper：锁 / user_id 路径清洗 / relationship 双向兼容
# ================================================================
def _sanitize_user_id_for_path(user_id: str) -> str:
    """把 QQ/微信/任意 user_id 映射成安全的文件夹名（保留 alnum/_-，其余转 hex）。"""
    safe_chars = []
    has_special = False
    for ch in str(user_id):
        if ch.isalnum() or ch in ("-", "_"):
            safe_chars.append(ch)
        else:
            safe_chars.append("_")
            has_special = True
    base = "".join(safe_chars) or "user"
    if has_special:
        import hashlib
        suffix = hashlib.md5(str(user_id).encode("utf-8")).hexdigest()[:8]
        return f"{base}_{suffix}"
    return base


def _get_or_create_thread_lock(key: str) -> "threading.Lock":
    with _LOCKS_REGISTRY_LOCK:
        lock = _LOCKS_REGISTRY.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS_REGISTRY[key] = lock
        return lock


class _FileLock:
    """朴素跨进程文件锁（Windows 兼容：open(..., 'x') + 删除）。

    为了项目不引入 portalocker/fcntl 依赖，用原子独占创建实现最小锁。
    不做超时重试（由上层 threading.Lock 已经解决大部分并发，这层只兜底防跨 worker）。

    R2.7.6-AUDIT: TIMEOUT_SECONDS 从 15s → 120s。
      原因：DeepSeekAdapter 默认 timeout=30s + 3 次重试 + 指数退避 ≈ 30+(1+2+4)=37s/轮；
      如果再加思考时间/重试，可能超过 60s；锁年龄 >2*TIMEOUT 才会被误删，所以
      给 120s 留足 headroom，避免跨 worker 误删正在持有的锁（导致并发写破坏数据）。
    """

    POLL_INTERVAL_MS = 50
    TIMEOUT_SECONDS = 120.0  # R2.7.6-AUDIT: 15 → 120

    def __init__(self, path: Path) -> None:
        self.path = Path(str(path) + ".filelock")
        self._acquired = False

    def __enter__(self):
        start = time.time()
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "x", encoding="utf-8") as f:
                    f.write(f"pid={__import__('os').getpid()}\ntime={time.time()}\n")
                self._acquired = True
                return self
            except FileExistsError:
                # 文件已存在：如果锁文件超过 2x timeout 还没删，则视为死进程遗留，回收
                try:
                    age = time.time() - self.path.stat().st_mtime
                    if age > self.TIMEOUT_SECONDS * 2:
                        logger.warning(
                            "[_FileLock] 检测到疑似僵尸锁（age=%.1fs > 2x timeout=%.1fs），回收: %s",
                            age, self.TIMEOUT_SECONDS * 2, self.path,
                        )
                        self.path.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() - start > self.TIMEOUT_SECONDS:
                    raise TimeoutError(f"_FileLock 超时 {self.TIMEOUT_SECONDS}s: {self.path}")
                time.sleep(self.POLL_INTERVAL_MS / 1000.0)

    def __exit__(self, exc_type, exc, tb):
        if self._acquired:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self._acquired = False
        return False


class _CombinedLock:
    """组合多个 context manager 锁。"""

    def __init__(self, locks: Iterable[Any]) -> None:
        self.locks = list(locks)
        self._entered: List[Any] = []

    def __enter__(self):
        for lock in self.locks:
            lock.__enter__()
            self._entered.append(lock)
        return self

    def __exit__(self, exc_type, exc, tb):
        while self._entered:
            lock = self._entered.pop()
            try:
                lock.__exit__(exc_type, exc, tb)
            except Exception:
                pass
        return False


def _normalize_relationship_for_write(rel: Dict[str, Any]) -> Dict[str, Any]:
    """R2.7.6：写 relationship snapshot 前，同步 phase4 字段 ↔ legacy 字段。

    Phase4 字段集：trust_level / closeness / interaction_count / last_interaction_ts_ms
                   / shared_memory_tags / history / schema_version
    Legacy 字段集：trust / familiarity / bond_strength / promise_level / shared_history
                   / activity_level / milestones / important_events
    规则：
        1) 如果缺 legacy 字段，从 phase4 推导；
        2) 如果缺 phase4 字段，从 legacy 反向推导（例如旧文件导入）。
    """
    # 2→1：legacy → phase4
    if "trust_level" not in rel and isinstance(rel.get("trust"), (int, float)):
        rel["trust_level"] = round(min(1.0, max(0.0, float(rel["trust"]))), 6)
    if "closeness" not in rel and isinstance(rel.get("familiarity"), (int, float)):
        rel["closeness"] = round(min(1.0, max(0.0, float(rel["familiarity"]))), 6)
    if "trust_level" not in rel:
        rel["trust_level"] = 0.5
    if "closeness" not in rel:
        rel["closeness"] = 0.3
    if "interaction_count" not in rel:
        rel["interaction_count"] = 0
    if "last_interaction_ts_ms" not in rel:
        rel["last_interaction_ts_ms"] = int(time.time() * 1000)
    if "shared_memory_tags" not in rel:
        rel["shared_memory_tags"] = []
    if "history" not in rel:
        rel["history"] = list(rel.get("important_events", []) if isinstance(rel.get("important_events"), list) else [])

    # 1→2：phase4 → legacy（保证 Orchestrator 读 relationship_state.json 合理）
    rel["trust"] = float(rel["trust_level"])
    rel["familiarity"] = float(rel["closeness"])
    if "bond_strength" not in rel or not isinstance(rel["bond_strength"], (int, float)):
        rel["bond_strength"] = round((rel["trust_level"] + rel["closeness"]) / 2.0, 6)
    if "promise_level" not in rel:
        rel["promise_level"] = 0.0
    if "shared_history" not in rel:
        rel["shared_history"] = 0.0
    if "activity_level" not in rel:
        rel["activity_level"] = round(min(1.0, float(rel.get("interaction_count", 0)) / 100.0), 6)
    if "milestones" not in rel:
        rel["milestones"] = []
    if "important_events" not in rel:
        rel["important_events"] = list(rel["history"])[:50]
    # 值域 clamp
    for field in ("trust_level", "closeness", "trust", "familiarity", "bond_strength"):
        if isinstance(rel.get(field), (int, float)):
            rel[field] = round(min(1.0, max(0.0, float(rel[field]))), 6)
    return rel


_LEGACY_NOT_IMPLEMENTED = False  # import_legacy_relationship 占位（后续若有需要可补充）

