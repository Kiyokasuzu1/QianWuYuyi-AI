# -*- coding: utf-8 -*-
"""
src/goal/goal_state.py

v1.3 Agency Phase 1 Task 2: GoalState 独立存储模块(append-only)。

定位:
- Goal = 羽依的"关注方向/行为倾向"(behavioral tendency), 不是人格,
  不是情绪, 不是自我模型。本模块只存 Goal 状态, 不修改任何其他域。
- 治理链唯一入口: 经 GoalProposal(proposal_type=goal) → 审批 → GoalDrain
  → 本模块 append 状态迁移记录 → state_mutations 审计。

设计:
- append-only JSONL: 每次状态迁移追加一行, 不覆盖不删除。
  同一 goal_id 的最后一条记录 = 当前状态(last-record-wins)。
- 状态机(本阶段只消费到 active; completed/archived 由后续阶段写入):
  candidate → approved → active → completed → archived
- fail-soft: 任何读写失败不抛出, append 返回 False; 损坏行读取时隔离。
- 并发: per-path RLock + flush + fsync, 与 state_mutation_audit 同惯例。
- 无单例: 调用方显式持有 GoalStateStore(path)(依赖注入, 便于测试隔离)。

红线:
- 禁止 import src.personality / src.emotion / src.relationship /
  src.memory / src.growth(本模块是纯存储层);
- 禁止调用 LLM / 网络 / 事件总线;
- 禁止删除历史记录(append-only)。
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

DEFAULT_GOAL_STATE_PATH = "data/goal/goal_state.jsonl"
SCHEMA_VERSION = "goal_state.1.0"

# 状态机(v1.3 任务书): candidate/approved/active/completed/archived
GOAL_STATUS = {
    "CANDIDATE": "candidate",
    "APPROVED": "approved",
    "ACTIVE": "active",
    "COMPLETED": "completed",
    "ARCHIVED": "archived",
}
GOAL_STATUS_SET = frozenset(GOAL_STATUS.values())

# 按路径写锁(进程内); 跨进程追加不做强保证, 审计属尽力而为日志
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


class GoalStateStore:
    """Goal 状态 append-only JSONL 存储(行为倾向域, 与人格完全隔离)。"""

    def __init__(self, path: str = DEFAULT_GOAL_STATE_PATH) -> None:
        # 构造时 resolve 为绝对路径——实例跨 cwd 切换后读写仍锚定首次构造位置
        self.path = Path(path).resolve()

    # --------------------------------------------------------
    # 写入(append-only, fail-soft)
    # --------------------------------------------------------
    def append_state(
        self,
        *,
        goal_id: str,
        status: str,
        description: str = "",
        source_refs: Optional[List[Dict[str, Any]]] = None,
        priority: str = "medium",
        confidence: float = 0.0,
        proposal_id: str = "",
        reason: str = "",  # v1.3 Phase 2: 缘由(来自已审批提案), 供 GoalResolver 展示
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """追加一条状态迁移记录。成功 True; 任何失败/非法输入返回 False(不抛)。

        - goal_id 为空或 status 不在状态机内 → False(不做写盘);
        - created_at: 首次出现用当前时间, 后续迁移继承首条记录的 created_at;
        - updated_at: 每次迁移取当前时间;
        - extra 提供时合并进条目(核心字段不可被覆盖)。
        """
        _goal_id = str(goal_id or "").strip()
        _status = str(status or "")
        if not _goal_id or _status not in GOAL_STATUS_SET:
            logger.warning(
                "[GoalState] append 拒绝: goal_id=%r status=%r", _goal_id, _status,
            )
            return False
        try:
            _created_at = _utc_now_iso()
            with _path_lock(self.path):
                _prev = self._last_record_unsafe(_goal_id)
                if isinstance(_prev, dict) and _prev.get("created_at"):
                    _created_at = str(_prev["created_at"])
                entry: Dict[str, Any] = {
                    "goal_id": _goal_id,
                    "status": _status,
                    "description": str(description or ""),
                    "reason": str(reason or ""),
                    "source_refs": list(source_refs or []),
                    "priority": str(priority or "medium"),
                    "confidence": float(confidence or 0.0),
                    "proposal_id": str(proposal_id or ""),
                    "created_at": _created_at,
                    "updated_at": _utc_now_iso(),
                    "schema_version": SCHEMA_VERSION,
                }
                if isinstance(extra, dict):
                    for _key, _value in extra.items():
                        if _key not in entry:
                            entry[_key] = _value
                line = json.dumps(entry, ensure_ascii=False, default=str)
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
            logger.warning("[GoalState] append 失败(已隔离): %s", exc)
            return False

    # --------------------------------------------------------
    # 读取(损坏行隔离, fail-soft)
    # --------------------------------------------------------
    def _read_lines_unsafe(self) -> List[Dict[str, Any]]:
        """读取全部有效行(旧→新); 损坏行跳过; 异常返回空列表。"""
        out: List[Dict[str, Any]] = []
        try:
            if not self.path.exists():
                return out
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[GoalState] 读取失败(已隔离): %s", exc)
            return out
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(item, dict):
                out.append(item)
        return out

    def _last_record_unsafe(self, goal_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(self._read_lines_unsafe()):
            if str(item.get("goal_id", "") or "") == goal_id:
                return item
        return None

    def get(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """当前状态(该 goal_id 最后一条记录)。"""
        if not goal_id:
            return None
        try:
            with _path_lock(self.path):
                return self._last_record_unsafe(str(goal_id))
        except Exception:  # noqa: BLE001
            return None

    def records(self, goal_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """全部记录(append-only 审计视图, 新→旧); 可按 goal_id 过滤。"""
        try:
            with _path_lock(self.path):
                items = self._read_lines_unsafe()
        except Exception:  # noqa: BLE001
            return []
        if goal_id is not None:
            _gid = str(goal_id)
            items = [i for i in items if str(i.get("goal_id", "") or "") == _gid]
        items.reverse()
        return items

    def count_records(self, goal_id: Optional[str] = None) -> int:
        return len(self.records(goal_id=goal_id))

    def list_all(self) -> List[Dict[str, Any]]:
        """每个 goal_id 取最新一条(当前状态), 新→旧。"""
        latest: Dict[str, Dict[str, Any]] = {}
        order: List[str] = []
        for item in self.records():
            _gid = str(item.get("goal_id", "") or "")
            if not _gid:
                continue
            if _gid not in latest:
                latest[_gid] = item
                order.append(_gid)
        return [latest[_gid] for _gid in order]

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        return [g for g in self.list_all() if g.get("status") == status]


__all__ = [
    "DEFAULT_GOAL_STATE_PATH",
    "SCHEMA_VERSION",
    "GOAL_STATUS",
    "GOAL_STATUS_SET",
    "GoalStateStore",
]
