# -*- coding: utf-8 -*-
"""
src/initiative/initiative_observability.py

v1.3 Phase 5.5: Initiative Action 可观察性(InitiativeActionLedger)。

职责:
- append-only JSONL 记录 Action 全生命周期审计
  (proposal_created / action_created / safety_passed / safety_blocked /
   budget_blocked / dispatch_disabled / dispatched);
- 只读查询(admin 观察入口), 不执行任何动作;
- 字段(action_id / proposal_id / goal_reference / stage / status /
  safety_result / rejection_reason / created_at / executed_at)全可追溯。

红线:
- 禁止执行 Action / 绕过 Proposal / 修改 Dispatcher 状态机;
- 不修改人格 / self_model / emotion / relationship;
- 不新建 scheduler/thread;
- 默认关闭(initiative_observability_enabled=false = 零写入)。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = "data/initiative/initiative_actions.jsonl"
SCHEMA_VERSION = "initiative_action.1.0"

# 生命周期 stage(append-only, 顺序可追溯)
STAGE_PROPOSAL_CREATED = "proposal_created"
STAGE_ACTION_CREATED = "action_created"
STAGE_SAFETY_PASSED = "safety_passed"
STAGE_SAFETY_BLOCKED = "safety_blocked"
STAGE_BUDGET_BLOCKED = "budget_blocked"
STAGE_DISPATCH_DISABLED = "dispatch_disabled"
STAGE_DISPATCHED = "dispatched"

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Union[str, Path]) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InitiativeActionLedger:
    """Initiative Action 审计账本(append-only JSONL, fail-soft)。"""

    def __init__(self, path: str = DEFAULT_LEDGER_PATH) -> None:
        self.path = Path(path).resolve()

    def record(self, entry: Dict[str, Any]) -> bool:
        """追加一条审计记录。失败返回 False(不抛)。"""
        if not isinstance(entry, dict):
            return False
        _record: Dict[str, Any] = {
            "action_id": str(entry.get("action_id", "") or ""),
            "proposal_id": str(entry.get("proposal_id", "") or ""),
            "goal_reference": str(entry.get("goal_reference", "") or ""),
            "stage": str(entry.get("stage", "") or ""),
            "status": str(entry.get("status", "") or ""),
            "safety_result": str(entry.get("safety_result", "") or ""),
            "rejection_reason": str(entry.get("rejection_reason", "") or ""),
            "created_at": str(entry.get("created_at", "") or _utc_now_iso()),
            "executed_at": str(entry.get("executed_at", "") or ""),
            "schema_version": SCHEMA_VERSION,
        }
        try:
            line = json.dumps(_record, ensure_ascii=False, default=str)
            with _path_lock(self.path):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except OSError:
                        pass
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativeActionLedger] 记录失败(已隔离): %s", exc)
            return False

    def query(self, limit: int = 100) -> List[Dict[str, Any]]:
        """只读查询(新→旧); 损坏行跳过; 异常返回空。"""
        try:
            _cap = max(1, int(limit or 100))
        except (TypeError, ValueError):
            _cap = 100
        _out: List[Dict[str, Any]] = []
        try:
            with _path_lock(self.path):
                if not self.path.exists():
                    return _out
                with open(self.path, "r", encoding="utf-8") as f:
                    _lines = f.readlines()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativeActionLedger] 查询失败(已隔离): %s", exc)
            return _out
        for _line in reversed(_lines):
            _line = _line.strip()
            if not _line:
                continue
            try:
                _item = json.loads(_line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(_item, dict):
                _out.append(_item)
                if len(_out) >= _cap:
                    break
        return _out

    def count(self) -> int:
        return len(self.query(limit=100000))


def list_initiative_actions(
    path: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Admin 只读入口: 读取 Action 审计记录(不执行任何动作)。"""
    try:
        _ledger = InitiativeActionLedger(path or DEFAULT_LEDGER_PATH)
        return _ledger.query(limit=limit)
    except Exception:  # noqa: BLE001
        return []


__all__ = [
    "DEFAULT_LEDGER_PATH",
    "SCHEMA_VERSION",
    "STAGE_PROPOSAL_CREATED",
    "STAGE_ACTION_CREATED",
    "STAGE_SAFETY_PASSED",
    "STAGE_SAFETY_BLOCKED",
    "STAGE_BUDGET_BLOCKED",
    "STAGE_DISPATCH_DISABLED",
    "STAGE_DISPATCHED",
    "InitiativeActionLedger",
    "list_initiative_actions",
]
