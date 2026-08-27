# -*- coding: utf-8 -*-
"""人格版本化时间线（Self History Minimal Layer，2026-08-27）。

与 src/personality/self_history.py（SelfModel 生命周期事件层，Phase 6.1）区分：
    - self_history.py  = SelfModel 事件（beliefs/reflection/pcr，内存容器 + 整体序列化）
    - 本模块           = 人格版本时间线（PersonalityState.version 驱动的
                          append-only 快照序列，data/self_history.jsonl）

职责：
    PersonalityState.apply_evolution 成功
        → 追加一条 SelfHistoryRecord（完整 before/after trait 快照）

与 personality_growth_history.json（observation log，delta 形式）严格区分：
    - growth_history = 系统观察到什么成长信号
    - self_history   = 羽依真正发生过的人格变化的不可变时间线

Schema（冻结）：
    record_id / version / applied_at / before(完整 traits) / after(完整 traits)
    / proposal_id / approval_id / evidence_ids / reason

约束：
    - append-only：追加新行，绝不覆盖/删除/修改历史；
    - 幂等：同一 proposal_id 只产生一条记录（重复 drain / retry 不重复）；
    - fail-soft：任何异常只记录 warning，绝不阻断人格 apply；
    - 无真实变化（apply 失败）→ 无记录。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()

DEFAULT_STORAGE_PATH = os.path.join("data", "self_history.jsonl")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _backup_corrupt(path: str) -> None:
    try:
        if os.path.exists(path):
            backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            import shutil
            shutil.copy2(path, backup)
            logger.warning("SelfHistoryTimelineStore: 损坏文件已备份为 %s", backup)
    except Exception:  # noqa: BLE001
        pass


class SelfHistoryTimelineStore:
    """人格版本时间线 append-only JSONL（路径 data/self_history.jsonl）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or DEFAULT_STORAGE_PATH
        self._lock = _path_lock(self.path)

    # ============ 读 ============
    def load(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.path):
                return []
            records: List[Dict[str, Any]] = []
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line)
                        except Exception:  # noqa: BLE001
                            continue
                        if isinstance(parsed, dict):
                            records.append(parsed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SelfHistoryTimelineStore.load 失败（已隔离）: %s", exc)
                _backup_corrupt(self.path)
                return []
            return records

    def get_by_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        if not proposal_id:
            return None
        for r in self.load():
            if str(r.get("proposal_id") or "") == str(proposal_id):
                return r
        return None

    def timeline(self) -> List[Dict[str, Any]]:
        """按 version 升序的人格版本时间线（不可变历史）。"""
        recs = self.load()
        recs.sort(key=lambda r: (int(r.get("version") or 0), str(r.get("applied_at") or "")))
        return recs

    # ============ 写 ============
    def _append(self, record: Dict[str, Any]) -> bool:
        with self._lock:
            try:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("SelfHistoryTimelineStore._append 失败: %s", exc)
                return False

    def record_evolution(
        self,
        *,
        version: int,
        before: Dict[str, float],
        after: Dict[str, float],
        proposal_id: str,
        approval_id: str = "",
        evidence_ids: Optional[List[str]] = None,
        reason: str = "",
        applied_at: Optional[str] = None,
        before_snapshot: Optional[List[dict]] = None,
        experience_trace_ids: Optional[List[str]] = None,
    ) -> Optional[str]:
        """追加一条真实人格变化记录（幂等：proposal_id 已存在则跳过）。

        返回 record_id；幂等命中 / 写入失败返回 None（fail-soft）。
        T1-B：追加 before_snapshot / experience_trace_ids / schema_version
        （只追加，不改历史；旧记录 .get 兼容）。
        """
        if not proposal_id:
            return None
        if self.get_by_proposal(proposal_id) is not None:
            logger.warning(
                "[SelfHistory] proposal %s 已存在历史记录，跳过（幂等）",
                proposal_id,
            )
            return None
        record_id = f"sh_{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"
        rec: Dict[str, Any] = {
            "record_id": record_id,
            "version": int(version),
            "applied_at": applied_at or _now(),
            "before": {k: round(float(v), 6) for k, v in (before or {}).items()},
            "after": {k: round(float(v), 6) for k, v in (after or {}).items()},
            "proposal_id": str(proposal_id),
            "approval_id": str(approval_id or ""),
            "evidence_ids": [str(x) for x in (evidence_ids or [])],
            "reason": str(reason or "")[:500],
            "schema_version": 1,
            "before_snapshot": list(before_snapshot or []),
            "experience_trace_ids": [str(x) for x in (experience_trace_ids or [])],
        }
        return record_id if self._append(rec) else None


# 模块级默认 store（与项目 store 单例模式一致；测试可显式传 path）
_default_store: Optional[SelfHistoryTimelineStore] = None
_default_store_lock = threading.Lock()


def get_self_history_timeline_store() -> SelfHistoryTimelineStore:
    global _default_store
    if _default_store is None:
        with _default_store_lock:
            if _default_store is None:
                _default_store = SelfHistoryTimelineStore()
    return _default_store
