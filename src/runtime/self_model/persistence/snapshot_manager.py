# -*- coding: utf-8 -*-
"""
src/runtime/self_model/persistence/snapshot_manager.py

Phase 4.6: SnapshotManager —— snapshot 生命周期协调器。

职责:
- 协调 SelfModelStore + EvolutionHistoryStore
- 提供高层 API:save_snapshot / restore_snapshot / create_checkpoint / rollback
- 不直接改 SelfModelStore / EvolutionHistoryStore 的 IO 行为
- 快照不可变原则:rollback 必须产生一个新 SelfModelSnapshot(版本号+1)
- 不修改 / 截断 history(只追加,不删)

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 仅依赖 stdlib + 同包 store/history + self_model_data
- 异常隔离:任何失败 → 返回 None / False,写入 self._last_error
"""

from __future__ import annotations

import copy
import logging
import os
import threading
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.runtime.self_model.self_model_data import (
        SelfModelSnapshot,
    )
    _HAS_SNAPSHOT = True
except Exception:  # noqa: BLE001
    _HAS_SNAPSHOT = False
    SelfModelSnapshot = None  # type: ignore[assignment]


from src.runtime.self_model.persistence.self_model_store import (
    SelfModelStore,
    DEFAULT_SELF_MODEL_DIR,
    _safe_id as _store_safe_id,  # type: ignore[attr-defined]
)
from src.runtime.self_model.persistence.evolution_history_store import (
    EvolutionHistoryStore,
    DEFAULT_EVOLUTION_HISTORY_DIR,
)


logger = logging.getLogger(__name__)


SNAPSHOT_MANAGER_SCHEMA_VERSION = "1.0"
CHECKPOINT_PREFIX = "ckpt"
CHECKPOINT_FORMAT_VERSION = "1.0"


def _now_iso() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"


def _safe_id(s: str) -> str:
    return _store_safe_id(s)


class SnapshotManager:
    """Snapshot 生命周期协调器 (Phase 4.6 / v1.0)。

    典型用法:
        mgr = SnapshotManager(
            self_model_store=SelfModelStore(),
            evolution_history_store=EvolutionHistoryStore(),
        )
        mgr.save_snapshot(snap)
        snap2 = mgr.restore_snapshot(identity_id)
        mgr.create_checkpoint(snap, label="before_evolution_X")
        snap_old = mgr.rollback(identity_id, target_version=3)
    """

    name: str = "snapshot_manager"
    schema_version: str = SNAPSHOT_MANAGER_SCHEMA_VERSION

    def __init__(
        self,
        self_model_store: Optional[SelfModelStore] = None,
        evolution_history_store: Optional[EvolutionHistoryStore] = None,
        # checkpoint 子目录(相对 self_model_store.root_dir)
        checkpoint_subdir: str = "checkpoints",
    ) -> None:
        self._lock = threading.RLock()
        self._store = self_model_store or SelfModelStore()
        self._history = evolution_history_store or EvolutionHistoryStore()
        self._ckpt_subdir = str(checkpoint_subdir or "checkpoints")

        # 统计
        self._save_count: int = 0
        self._restore_count: int = 0
        self._checkpoint_count: int = 0
        self._rollback_count: int = 0
        self._last_error: Optional[str] = None
        self._last_checkpoint_id: Optional[str] = None
        self._last_rollback_version: Optional[int] = None
        self._last_saved_version: Optional[int] = None
        self._last_restored_version: Optional[int] = None

    @property
    def store(self) -> SelfModelStore:
        return self._store

    @property
    def history(self) -> EvolutionHistoryStore:
        return self._history

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # --------------------------------------------------------
    # validate
    # --------------------------------------------------------
    def _validate_snapshot(self, snapshot: Any) -> bool:
        if snapshot is None:
            return False
        if not _HAS_SNAPSHOT or SelfModelSnapshot is None:
            return hasattr(snapshot, "to_dict") and hasattr(snapshot, "from_dict")
        if not isinstance(snapshot, SelfModelSnapshot):
            return False
        return self._store.validate(snapshot)

    # --------------------------------------------------------
    # save_snapshot
    # --------------------------------------------------------
    def save_snapshot(self, snapshot: Any) -> bool:
        """保存 snapshot 到 SelfModelStore。

        返回:True 成功 / False 失败(不抛)
        """
        if not self._validate_snapshot(snapshot):
            self._last_error = "validate_failed"
            return False
        ok = self._store.save(snapshot)
        if ok:
            self._save_count += 1
            try:
                self._last_saved_version = int(getattr(snapshot, "version", 0) or 0)
            except Exception:  # noqa: BLE001
                self._last_saved_version = None
            self._last_error = None
        else:
            self._last_error = self._store.last_error or "save_failed"
        return ok

    # --------------------------------------------------------
    # restore_snapshot
    # --------------------------------------------------------
    def restore_snapshot(self, identity_id: str) -> Optional[SelfModelSnapshot]:
        """从 SelfModelStore 读取 snapshot。"""
        sid = _safe_id(identity_id)
        if not sid:
            self._last_error = "identity_id_unsafe"
            return None
        snap = self._store.load(sid)
        if snap is None:
            self._last_error = self._store.last_error or "load_failed"
            return None
        self._restore_count += 1
        try:
            self._last_restored_version = int(getattr(snap, "version", 0) or 0)
        except Exception:  # noqa: BLE001
            self._last_restored_version = None
        self._last_error = None
        return snap

    # --------------------------------------------------------
    # checkpoint(快照一个 immutable 副本到 ckpt 子目录)
    # --------------------------------------------------------
    def _checkpoint_dir(self) -> str:
        base = getattr(self._store, "root_dir", DEFAULT_SELF_MODEL_DIR)
        return os.path.join(base, self._ckpt_subdir)

    def _checkpoint_path(
        self, identity_id: str, version: int, label: str = "",
    ) -> Optional[str]:
        sid = _safe_id(identity_id)
        if not sid:
            return None
        try:
            v_int = int(version)
        except (TypeError, ValueError):
            v_int = 0
        safe_label = _safe_id(label or "")
        suffix = f".{safe_label}" if safe_label else ""
        fname = f"{sid}.v{v_int}{suffix}.{CHECKPOINT_PREFIX}.json"
        return os.path.join(self._checkpoint_dir(), fname)

    def create_checkpoint(
        self,
        snapshot: Any,
        label: str = "",
    ) -> Optional[str]:
        """创建 checkpoint。

        返回:checkpoint 路径(失败时 None)
        行为:
        - 把 snapshot 原子写到 <store_root>/checkpoints/<id>.v<n>.<label>.ckpt.json
        - 不修改 SelfModelStore 主文件
        - 不修改 history
        - snapshot 必须自带 to_dict / identity_id / version
        """
        if not self._validate_snapshot(snapshot):
            self._last_error = "validate_failed"
            return None
        try:
            identity_id = str(getattr(snapshot, "identity_id", "") or "")
            version = int(getattr(snapshot, "version", 0) or 0)
        except Exception:  # noqa: BLE001
            self._last_error = "snapshot_field_invalid"
            return None
        path = self._checkpoint_path(identity_id, version, label)
        if path is None:
            self._last_error = "identity_id_unsafe"
            return None
        try:
            os.makedirs(self._checkpoint_dir(), exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"mkdir_failed: {exc}"
            return None

        try:
            payload = snapshot.to_dict()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"to_dict_failed: {exc}"
            return None
        envelope: Dict[str, Any] = {
            "schema_version": SNAPSHOT_MANAGER_SCHEMA_VERSION,
            "checkpoint_format_version": CHECKPOINT_FORMAT_VERSION,
            "stored_at": _now_iso(),
            "snapshot": payload,
        }
        import json
        import tempfile
        try:
            text = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            self._last_error = f"json_dumps_failed: {exc}"
            return None
        encoded = text.encode("utf-8")

        with self._lock:
            try:
                dir_name = os.path.dirname(path) or "."
                fd, tmp_path = tempfile.mkstemp(
                    prefix=".ckpt_", suffix=".tmp", dir=dir_name,
                )
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(encoded)
                        try:
                            f.flush()
                            os.fsync(f.fileno())
                        except Exception:  # noqa: BLE001
                            pass
                    os.replace(tmp_path, path)
                except Exception:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:  # noqa: BLE001
                        pass
                    raise
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"write_failed: {exc}"
                logger.warning("SnapshotManager 写 checkpoint 失败: %s", exc)
                return None

        self._checkpoint_count += 1
        self._last_checkpoint_id = os.path.basename(path)
        self._last_error = None
        return path

    def list_checkpoints(self, identity_id: str) -> List[Dict[str, Any]]:
        """列出 identity_id 的所有 checkpoint(按修改时间倒序)。"""
        sid = _safe_id(identity_id)
        if not sid:
            return []
        d = self._checkpoint_dir()
        out: List[Dict[str, Any]] = []
        try:
            if not os.path.isdir(d):
                return out
            for name in os.listdir(d):
                if not (name.startswith(sid + ".") and name.endswith(f".{CHECKPOINT_PREFIX}.json")):
                    continue
                full = os.path.join(d, name)
                try:
                    stat = os.stat(full)
                except Exception:  # noqa: BLE001
                    continue
                out.append({
                    "name": name,
                    "path": full,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                })
        except Exception:  # noqa: BLE001
            return out
        out.sort(key=lambda x: float(x.get("modified_at") or 0.0), reverse=True)
        return out

    def load_checkpoint(
        self, identity_id: str, version: int, label: str = "",
    ) -> Optional[SelfModelSnapshot]:
        """从 checkpoint 还原一个 snapshot 引用(不修改主存储)。"""
        path = self._checkpoint_path(identity_id, version, label)
        if path is None or not os.path.exists(path):
            self._last_error = "checkpoint_not_found"
            return None
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"read_failed: {exc}"
            return None
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"decode_failed: {exc}"
            return None
        import json
        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"json_decode_failed: {exc}"
            return None
        if not isinstance(obj, dict):
            self._last_error = "envelope_not_dict"
            return None
        snap_data = obj.get("snapshot")
        if not isinstance(snap_data, dict):
            self._last_error = "snapshot_payload_invalid"
            return None
        if not _HAS_SNAPSHOT or SelfModelSnapshot is None:
            self._last_error = "snapshot_class_unavailable"
            return None
        try:
            return SelfModelSnapshot.from_dict(snap_data)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"from_dict_failed: {exc}"
            return None

    # --------------------------------------------------------
    # rollback
    # --------------------------------------------------------
    def rollback(
        self,
        identity_id: str,
        target_version: int,
    ) -> Optional[SelfModelSnapshot]:
        """回滚到 target_version —— 必须产生一个新的 SelfModelSnapshot(版本号+1)。

        策略:
        1) 从 history 找到 from_version = target_version 的 EvolutionRecord
        2) 加载当前 SelfModelStore 里的 latest snapshot
        3) 不可变拷贝 → 调整 version = latest_version + 1,
           updated_at = now,
           meta 里记录 {"rollback_from_version": latest_version,
                        "rollback_to_version": target_version}
        4) 写回 SelfModelStore(原 history 仍 append 追加)
        5) 把 rollback EvolutionRecord append 到 history

        返回:新 snapshot(失败 None)
        """
        sid = _safe_id(identity_id)
        if not sid:
            self._last_error = "identity_id_unsafe"
            return None
        try:
            target_v = int(target_version)
        except (TypeError, ValueError):
            self._last_error = "target_version_invalid"
            return None

        # 当前 latest
        current = self._store.load(sid)
        if current is None:
            self._last_error = "current_snapshot_missing"
            return None
        try:
            current_v = int(getattr(current, "version", 0) or 0)
        except Exception:  # noqa: BLE001
            current_v = 0

        # 找 target_version 的 evolution record
        records = self._history.list_history(sid) if self._history.exists(sid) else []
        target_snapshot: Optional[SelfModelSnapshot] = None
        for r in records:
            try:
                fv = int(r.get("from_version", 0) or 0)
                tv = int(r.get("to_version", 0) or 0)
            except Exception:  # noqa: BLE001
                continue
            if tv == target_v and fv < target_v:
                snap_data = r.get("changes", {}) if isinstance(r, dict) else None
                # changes 在 EvolutionRecord 里是 list[SelfModelChange],
                # 不是 snapshot payload,所以这里需要 history 提供
                # 单独的 snapshot payload 字段(可选)
                snap_payload = (
                    r.get("restored_snapshot")
                    if isinstance(r, dict) else None
                )
                if isinstance(snap_payload, dict):
                    if not _HAS_SNAPSHOT or SelfModelSnapshot is None:
                        self._last_error = "snapshot_class_unavailable"
                        return None
                    try:
                        target_snapshot = SelfModelSnapshot.from_dict(snap_payload)
                        break
                    except Exception:  # noqa: BLE001
                        target_snapshot = None
                # 兼容老式 history 字段(没有 restored_snapshot 字段)
                # 这种情况下,如果 history 里有 from_version 等于 target_v
                # 的 record,说明"应用 changes 之前"的快照版本就是 target_v
                # 我们取不到完整 snapshot,只能从 current 复制再做标记
                target_snapshot = None
                break

        if target_snapshot is None:
            # 没有 restore payload — 退化为: 复制 current,标记 version + 1
            # 这是"软回滚":不丢历史,但版本号推进
            try:
                restored = copy.deepcopy(current)
            except Exception:  # noqa: BLE001
                if _HAS_SNAPSHOT and SelfModelSnapshot is not None:
                    try:
                        restored = SelfModelSnapshot.from_dict(current.to_dict())
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = f"clone_failed: {exc}"
                        return None
                else:
                    self._last_error = "clone_failed"
                    return None
            try:
                restored.version = current_v + 1
            except Exception:  # noqa: BLE001
                pass
            try:
                restored.updated_at = _now_iso()
            except Exception:  # noqa: BLE001
                pass
            try:
                meta = dict(getattr(restored, "meta", {}) or {})
            except Exception:  # noqa: BLE001
                meta = {}
            meta["rollback_from_version"] = current_v
            meta["rollback_to_version"] = target_v
            meta["rollback_kind"] = "soft"
            try:
                restored.meta = meta
            except Exception:  # noqa: BLE001
                pass
        else:
            # 找到了 target snapshot
            try:
                restored = copy.deepcopy(target_snapshot)
            except Exception:  # noqa: BLE001
                if _HAS_SNAPSHOT and SelfModelSnapshot is not None:
                    try:
                        restored = SelfModelSnapshot.from_dict(
                            target_snapshot.to_dict(),
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._last_error = f"clone_failed: {exc}"
                        return None
                else:
                    self._last_error = "clone_failed"
                    return None
            try:
                restored.version = current_v + 1
            except Exception:  # noqa: BLE001
                pass
            try:
                restored.updated_at = _now_iso()
            except Exception:  # noqa: BLE001
                pass
            try:
                meta = dict(getattr(restored, "meta", {}) or {})
            except Exception:  # noqa: BLE001
                meta = {}
            meta["rollback_from_version"] = current_v
            meta["rollback_to_version"] = target_v
            meta["rollback_kind"] = "hard"
            try:
                restored.meta = meta
            except Exception:  # noqa: BLE001
                pass

        # 写回主存储
        if not self._store.save(restored):
            self._last_error = self._store.last_error or "save_after_rollback_failed"
            return None

        # 追加 rollback EvolutionRecord(轻量 dict 形式)
        rb_record: Dict[str, Any] = {
            "record_id": f"rb_{os.urandom(6).hex()}",
            "identity_id": sid,
            "timestamp": _now_iso(),
            "source_type": "rollback",
            "from_version": current_v,
            "to_version": current_v + 1,
            "schema_version": "1.0",
            "changes": [],
            "rejected_changes": [],
            "reject_reasons": [],
            "rollback": {
                "target_version": target_v,
                "kind": (
                    "hard" if target_snapshot is not None else "soft"
                ),
            },
        }
        if self._history is not None:
            try:
                self._history.append(rb_record)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SnapshotManager rollback history append 失败: %s", exc)

        self._rollback_count += 1
        self._last_rollback_version = current_v + 1
        self._last_error = None
        return restored

    # --------------------------------------------------------
    # health
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "save_count": self._save_count,
            "restore_count": self._restore_count,
            "checkpoint_count": self._checkpoint_count,
            "rollback_count": self._rollback_count,
            "last_error": self._last_error,
            "last_saved_version": self._last_saved_version,
            "last_restored_version": self._last_restored_version,
            "last_checkpoint_id": self._last_checkpoint_id,
            "last_rollback_version": self._last_rollback_version,
            "store_health": (
                self._store.health_check() if self._store is not None else None
            ),
            "history_health": (
                self._history.health_check() if self._history is not None else None
            ),
        }


__all__ = [
    "SnapshotManager",
    "SNAPSHOT_MANAGER_SCHEMA_VERSION",
    "CHECKPOINT_PREFIX",
    "CHECKPOINT_FORMAT_VERSION",
]
