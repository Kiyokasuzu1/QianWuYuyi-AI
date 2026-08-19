# -*- coding: utf-8 -*-
"""
src/runtime/self_model/persistence/persistence_runtime.py

Phase 4.6: PersistenceRuntime —— 持久化协调器。

职责:
- 协调 SelfModelStore + EvolutionHistoryStore + SnapshotManager
- 提供高层 API:initialize / load_current_self_model / persist_evolution / restore_on_startup
- 生命周期:Runtime.start() 之后调用 restore_on_startup
- 每次 SELF_MODEL_PERSISTENCE 阶段调用 persist_evolution(result)
- 异常隔离:任何步骤失败不抛,记录 self._last_error

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 仅依赖 stdlib + 同包 self_model / evolution 模块
- 不可变:不修改输入 snapshot / record
"""

from __future__ import annotations

import logging
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


try:
    from src.runtime.self_model.evolution.evolution_record import (
        SelfModelEvolutionResult,
        EvolutionRecord,
    )
    _HAS_EVOLUTION = True
except Exception:  # noqa: BLE001
    _HAS_EVOLUTION = False
    SelfModelEvolutionResult = None  # type: ignore[assignment]
    EvolutionRecord = None  # type: ignore[assignment]


from src.runtime.self_model.persistence.self_model_store import (
    SelfModelStore,
    DEFAULT_SELF_MODEL_DIR,
)
from src.runtime.self_model.persistence.evolution_history_store import (
    EvolutionHistoryStore,
    DEFAULT_EVOLUTION_HISTORY_DIR,
)
from src.runtime.self_model.persistence.snapshot_manager import (
    SnapshotManager,
    SNAPSHOT_MANAGER_SCHEMA_VERSION,
)


logger = logging.getLogger(__name__)


PERSISTENCE_RUNTIME_SCHEMA_VERSION = "1.0"


def _safe_str(value: Any, max_len: int = 120) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


class PersistenceRuntime:
    """SelfModel 持久化协调器 (Phase 4.6 / v1.0)。

    典型用法:
        pr = PersistenceRuntime(
            self_model_store=SelfModelStore(),
            evolution_history_store=EvolutionHistoryStore(),
        )
        pr.initialize(identity_id)
        snapshot = pr.load_current_self_model()       # 启动时
        pr.persist_evolution(evolution_result)        # SELF_MODEL_PERSISTENCE 阶段
        # 启动时
        pr.restore_on_startup(identity_id)
    """

    name: str = "persistence_runtime"
    schema_version: str = PERSISTENCE_RUNTIME_SCHEMA_VERSION

    def __init__(
        self,
        self_model_store: Optional[SelfModelStore] = None,
        evolution_history_store: Optional[EvolutionHistoryStore] = None,
        snapshot_manager: Optional[SnapshotManager] = None,
        # 启动时是否自动 create checkpoint
        auto_startup_checkpoint: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        self._store = self_model_store or SelfModelStore()
        self._history = evolution_history_store or EvolutionHistoryStore()
        self._manager = snapshot_manager or SnapshotManager(
            self_model_store=self._store,
            evolution_history_store=self._history,
        )
        self._auto_startup_ckpt = bool(auto_startup_checkpoint)

        # 统计
        self._init_count: int = 0
        self._load_count: int = 0
        self._persist_count: int = 0
        self._persist_accepted: int = 0
        self._persist_rejected: int = 0
        self._persist_records: int = 0
        self._restore_count: int = 0
        self._last_error: Optional[str] = None
        self._current_identity_id: Optional[str] = None
        self._last_loaded_version: Optional[int] = None
        self._last_loaded_id: Optional[str] = None
        self._last_persisted_version: Optional[int] = None

    @property
    def store(self) -> SelfModelStore:
        return self._store

    @property
    def history(self) -> EvolutionHistoryStore:
        return self._history

    @property
    def manager(self) -> SnapshotManager:
        return self._manager

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def current_identity_id(self) -> Optional[str]:
        return self._current_identity_id

    # --------------------------------------------------------
    # initialize
    # --------------------------------------------------------
    def initialize(self, identity_id: str) -> bool:
        """把 identity_id 记为当前活跃 id。返回 True 成功。"""
        if not isinstance(identity_id, str) or not identity_id.strip():
            self._last_error = "identity_id_invalid"
            return False
        self._current_identity_id = identity_id
        self._init_count += 1
        self._last_error = None
        return True

    # --------------------------------------------------------
    # load_current_self_model
    # --------------------------------------------------------
    def load_current_self_model(
        self,
        identity_id: Optional[str] = None,
    ) -> Optional[SelfModelSnapshot]:
        """从 SelfModelStore 读取最新 SelfModelSnapshot。"""
        iid = identity_id or self._current_identity_id
        if not iid:
            self._last_error = "identity_id_missing"
            return None
        snap = self._manager.restore_snapshot(iid)
        if snap is None:
            self._last_error = self._manager.last_error or "load_failed"
            return None
        self._load_count += 1
        self._last_loaded_id = str(getattr(snap, "identity_id", iid))
        try:
            self._last_loaded_version = int(getattr(snap, "version", 0) or 0)
        except Exception:  # noqa: BLE001
            self._last_loaded_version = None
        if identity_id:
            self._current_identity_id = identity_id
        self._last_error = None
        return snap

    # --------------------------------------------------------
    # persist_evolution —— SELF_MODEL_PERSISTENCE 阶段调用
    # --------------------------------------------------------
    def persist_evolution(
        self,
        evolution_result: Any,
        identity_id: Optional[str] = None,
    ) -> bool:
        """把 SelfModelEvolutionResult 落盘。

        行为:
        1) 若 evolution_result.is_noop → return True(无副作用)
        2) 把 new_snapshot 写到 SelfModelStore
        3) 把所有 EvolutionRecord append 到 EvolutionHistoryStore
        4) 失败隔离:任一步骤失败不抛,return False + 记录 last_error
        """
        if evolution_result is None:
            # 视作 noop(允许调用方传 None)
            return True
        # 读取字段(用 getattr,安全)
        try:
            is_noop = bool(getattr(evolution_result, "is_noop", True))
        except Exception:  # noqa: BLE001
            is_noop = True
        if is_noop:
            return True

        # identity 解析
        iid = identity_id or self._current_identity_id
        if not iid:
            # 退化:从 result 拿
            try:
                snap = getattr(evolution_result, "new_snapshot", None)
            except Exception:  # noqa: BLE001
                snap = None
            if snap is not None:
                try:
                    iid = str(getattr(snap, "identity_id", "") or "")
                except Exception:  # noqa: BLE001
                    iid = ""
        if not iid:
            self._last_error = "identity_id_missing"
            return False

        self._current_identity_id = iid

        # 1) 写 snapshot
        try:
            new_snap = getattr(evolution_result, "new_snapshot", None)
        except Exception:  # noqa: BLE001
            new_snap = None
        saved = True
        if new_snap is not None:
            saved = self._manager.save_snapshot(new_snap)
            if saved:
                try:
                    self._last_persisted_version = int(
                        getattr(new_snap, "version", 0) or 0,
                    )
                except Exception:  # noqa: BLE001
                    self._last_persisted_version = None

        # 2) 写 history
        try:
            records: List[Any] = list(
                getattr(evolution_result, "evolution_records", []) or [],
            )
        except Exception:  # noqa: BLE001
            records = []
        appended = 0
        for rec in records:
            try:
                # 注入 identity_id 兜底
                if isinstance(rec, dict):
                    if not rec.get("identity_id"):
                        rec["identity_id"] = iid
                else:
                    if not getattr(rec, "identity_id", ""):
                        try:
                            rec.identity_id = iid
                        except Exception:  # noqa: BLE001
                            pass
            except Exception:  # noqa: BLE001
                pass
            if self._history.append(rec):
                appended += 1

        try:
            accepted = len(list(getattr(evolution_result, "accepted_changes", []) or []))
        except Exception:  # noqa: BLE001
            accepted = 0
        try:
            rejected = len(list(getattr(evolution_result, "rejected_changes", []) or []))
        except Exception:  # noqa: BLE001
            rejected = 0

        self._persist_count += 1
        self._persist_accepted += accepted
        self._persist_rejected += rejected
        self._persist_records += appended

        if not saved:
            self._last_error = "snapshot_save_failed"
            return False
        self._last_error = None
        return True

    # --------------------------------------------------------
    # restore_on_startup
    # --------------------------------------------------------
    def restore_on_startup(
        self,
        identity_id: Optional[str] = None,
        create_checkpoint: Optional[bool] = None,
    ) -> Optional[SelfModelSnapshot]:
        """Runtime 启动时调用:从 SelfModelStore 恢复最新 SelfModelSnapshot。

        参数:
        - identity_id: 要恢复的 id(None → 使用 self._current_identity_id)
        - create_checkpoint: 是否在恢复前 / 后做一次 checkpoint
          (None → 用构造时的 self._auto_startup_ckpt)

        行为:
        1) 加载 snapshot
        2) 若启用 checkpoint,把刚加载的 snapshot 写入 checkpoint 子目录
        3) 失败 → return None,主流程继续(向后兼容)
        """
        if identity_id:
            self._current_identity_id = identity_id
        snap = self.load_current_self_model()
        if snap is None:
            return None
        self._restore_count += 1
        # 可选 checkpoint
        do_ckpt = self._auto_startup_ckpt if create_checkpoint is None else bool(
            create_checkpoint,
        )
        if do_ckpt:
            try:
                self._manager.create_checkpoint(
                    snap,
                    label=f"startup_v{int(getattr(snap, 'version', 0) or 0)}",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("PersistenceRuntime 启动 checkpoint 失败: %s", exc)
        return snap

    # --------------------------------------------------------
    # 便捷:history 读取
    # --------------------------------------------------------
    def get_evolution_history(
        self,
        identity_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        iid = identity_id or self._current_identity_id
        if not iid:
            return []
        return self._history.list_history(iid, limit=limit)

    def get_evolution_count(
        self,
        identity_id: Optional[str] = None,
    ) -> int:
        iid = identity_id or self._current_identity_id
        if not iid:
            return 0
        return self._history.count(iid)

    # --------------------------------------------------------
    # checkpoint / rollback 转发
    # --------------------------------------------------------
    def checkpoint(
        self,
        snapshot: Any,
        label: str = "",
    ) -> Optional[str]:
        return self._manager.create_checkpoint(snapshot, label=label)

    def rollback(
        self,
        identity_id: str,
        target_version: int,
    ) -> Optional[SelfModelSnapshot]:
        return self._manager.rollback(identity_id, target_version)

    def list_checkpoints(
        self,
        identity_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        iid = identity_id or self._current_identity_id
        if not iid:
            return []
        return self._manager.list_checkpoints(iid)

    # --------------------------------------------------------
    # health
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "current_identity_id": self._current_identity_id,
            "init_count": self._init_count,
            "load_count": self._load_count,
            "persist_count": self._persist_count,
            "persist_accepted": self._persist_accepted,
            "persist_rejected": self._persist_rejected,
            "persist_records": self._persist_records,
            "restore_count": self._restore_count,
            "last_error": self._last_error,
            "last_loaded_id": self._last_loaded_id,
            "last_loaded_version": self._last_loaded_version,
            "last_persisted_version": self._last_persisted_version,
            "manager_health": (
                self._manager.health_check() if self._manager is not None else None
            ),
        }


__all__ = [
    "PersistenceRuntime",
    "PERSISTENCE_RUNTIME_SCHEMA_VERSION",
]
