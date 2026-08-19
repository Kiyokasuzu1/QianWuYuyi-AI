# -*- coding: utf-8 -*-
"""
src/growth/lifecycle_manager.py

Phase 5.5.2 Stability Layer: ProposalLifecycleManager

统一 Proposal 生命周期状态机：
    CREATED → PENDING → APPROVED → APPLYING → APPLIED
                  ↓        ↓           ↓
                REJECTED  FAILED   ROLLED_BACK → ARCHIVED
                                  ↑
                              APPROVED(回退)

职责：
- 定义合法生命周期状态与转换
- 校验转换合法性（统一 Authority 入口）
- 持久化生命周期记录
- 提供稳定接口供 ApprovalManager / GovernanceProvider 使用

设计原则：
- 不修改 RuntimeCore
- 不引入 EventBus
- 保留 Phase 5.5.1 状态机规则
- 兼容旧 Status（pending/approved/rejected/applied/cancelled）→ 映射到新生命周期
- Authority 修改仍只通过原入口（ApprovalManager / PersonalityAdapter）
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 生命周期状态
# ============================================================

class LifecycleState(str, Enum):
    """Proposal 统一生命周期状态"""
    CREATED = "created"             # 已创建（数据已生成，但还未进入审批）
    PENDING = "pending"             # 待审批
    APPROVED = "approved"           # 已批准
    APPLYING = "applying"           # 应用中（写入运行时）
    APPLIED = "applied"             # 已应用
    FAILED = "failed"               # 应用失败
    ROLLED_BACK = "rolled_back"     # 已回滚
    ARCHIVED = "archived"           # 已归档


# 终态：不再允许转换
TERMINAL_STATES: FrozenSet[str] = frozenset({
    LifecycleState.ARCHIVED.value,
    # ROLLED_BACK 在某些场景下也是终态（在 rollback 后不可再操作）
    # 这里保守地不设为终态，允许 ARCHIVED 后再迁移
})

# 合法状态转换表
LIFECYCLE_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    LifecycleState.CREATED.value: frozenset({LifecycleState.PENDING.value, LifecycleState.ARCHIVED.value}),
    LifecycleState.PENDING.value: frozenset({"rejected", LifecycleState.APPROVED.value, LifecycleState.ARCHIVED.value}),
    LifecycleState.APPROVED.value: frozenset({LifecycleState.APPLYING.value, LifecycleState.PENDING.value, LifecycleState.ARCHIVED.value}),
    LifecycleState.APPLYING.value: frozenset({LifecycleState.APPLIED.value, LifecycleState.FAILED.value}),
    LifecycleState.APPLIED.value: frozenset({LifecycleState.ROLLED_BACK.value}),
    LifecycleState.FAILED.value: frozenset({LifecycleState.PENDING.value, LifecycleState.ROLLED_BACK.value, LifecycleState.ARCHIVED.value}),
    LifecycleState.ROLLED_BACK.value: frozenset({LifecycleState.ARCHIVED.value, LifecycleState.PENDING.value}),
    LifecycleState.ARCHIVED.value: frozenset(),
    # rejected 是从 PENDING 派生的特殊状态，可归档
    "rejected": frozenset({LifecycleState.ARCHIVED.value, LifecycleState.PENDING.value}),
}


# 旧 status → 新 lifecycle 状态映射（向后兼容）
LEGACY_STATUS_TO_LIFECYCLE: Dict[str, str] = {
    "pending": LifecycleState.PENDING.value,
    "approved": LifecycleState.APPROVED.value,
    "rejected": "rejected",  # 在 LifecycleState 中无显式 REJECTED，作为 PENDING 的目标
    "applied": LifecycleState.APPLIED.value,
    "cancelled": LifecycleState.ARCHIVED.value,  # 取消视为归档
    "proposed": LifecycleState.PENDING.value,
}


# ============================================================
# 异常
# ============================================================

class IllegalLifecycleTransition(Exception):
    """非法生命周期转换"""
    pass


# ============================================================
# 生命周期记录
# ============================================================

@dataclass
class LifecycleEvent:
    """单次生命周期事件"""
    event_id: str = field(default_factory=lambda: f"lc_{uuid.uuid4().hex[:10]}")
    proposal_id: str = ""
    from_state: str = ""
    to_state: str = ""
    actor: str = "system"
    reason: str = ""
    timestamp: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# Lifecycle Manager
# ============================================================

class ProposalLifecycleManager:
    """
    Proposal 统一生命周期管理器。

    职责：
    - 校验状态转换合法性
    - 记录生命周期事件
    - 提供查询接口（get_state / get_history）
    - 持久化到 JSON

    不持有 Authority 引用。
    不直接修改 Authority 数据（仅记录 lifecycle 事件）。
    """

    DEFAULT_PATH = "data/growth/lifecycle/events.json"

    def __init__(self, storage_path: Optional[str] = None):
        self._path = Path(storage_path or self.DEFAULT_PATH)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._init_file()

    def _init_file(self) -> None:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump({"version": "1.0", "events": []}, f, ensure_ascii=False, indent=2)

    def _load(self) -> List[Dict[str, Any]]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                return json.load(f).get("events", [])
        except Exception as e:
            logger.error(f"LifecycleManager 加载失败: {e}")
            return []

    def _save(self, events: List[Dict[str, Any]]) -> None:
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": "1.0", "events": events},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception as e:
            logger.error(f"LifecycleManager 保存失败: {e}")

    # ============================================================
    # 校验
    # ============================================================

    def validate_transition(
        self,
        from_state: str,
        to_state: str,
    ) -> bool:
        """
        校验生命周期转换是否合法。

        Returns:
            True=合法
        Raises:
            IllegalLifecycleTransition
        """
        # 规范化
        from_state = self._normalize_state(from_state)
        to_state = self._normalize_state(to_state)

        if from_state == to_state:
            return True  # 幂等

        if to_state == "rejected" and from_state in (LifecycleState.PENDING.value,):
            return True  # rejected 是 PENDING 的合法目标

        if to_state not in [s.value for s in LifecycleState]:
            raise IllegalLifecycleTransition(f"未知目标状态: {to_state}")

        if from_state not in LIFECYCLE_TRANSITIONS:
            raise IllegalLifecycleTransition(f"未知起始状态: {from_state}")

        allowed = LIFECYCLE_TRANSITIONS[from_state]
        if to_state not in allowed:
            raise IllegalLifecycleTransition(
                f"非法生命周期转换: {from_state} → {to_state}（合法目标: {sorted(allowed)}）"
            )
        return True

    def _normalize_state(self, state: str) -> str:
        """规范化旧状态到新生命周期状态"""
        if not state:
            return LifecycleState.CREATED.value
        if state in [s.value for s in LifecycleState]:
            return state
        return LEGACY_STATUS_TO_LIFECYCLE.get(state, state)

    # ============================================================
    # 记录
    # ============================================================

    def record_transition(
        self,
        proposal_id: str,
        from_state: str,
        to_state: str,
        actor: str = "system",
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LifecycleEvent:
        """
        记录一次生命周期转换（先校验合法性）。

        Raises:
            IllegalLifecycleTransition: 非法转换
        """
        self.validate_transition(from_state, to_state)
        event = LifecycleEvent(
            proposal_id=proposal_id,
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            reason=reason,
            metadata=metadata or {},
        )
        events = self._load()
        events.append(event.to_dict())
        self._save(events)
        logger.info(
            f"LifecycleManager: {proposal_id} {from_state} → {to_state} by {actor}"
        )
        return event

    def get_state(self, proposal_id: str) -> str:
        """获取 Proposal 当前生命周期状态（无记录则返回 'unknown'）"""
        events = [e for e in self._load() if e.get("proposal_id") == proposal_id]
        if not events:
            return "unknown"
        # 返回最新事件的目标状态
        events.sort(key=lambda e: e.get("timestamp", ""))
        return events[-1].get("to_state", "unknown")

    def get_history(
        self,
        proposal_id: str,
        limit: int = 50,
    ) -> List[LifecycleEvent]:
        """获取 Proposal 生命周期历史"""
        events = [
            LifecycleEvent(**{k: v for k, v in e.items() if k in LifecycleEvent.__dataclass_fields__})
            for e in self._load()
            if e.get("proposal_id") == proposal_id
        ]
        events.sort(key=lambda e: e.timestamp)
        return events[-limit:]

    # ============================================================
    # 工具
    # ============================================================

    def is_terminal(self, state: str) -> bool:
        """判断是否终态"""
        normalized = self._normalize_state(state)
        return normalized in TERMINAL_STATES

    def get_legal_next_states(self, current_state: str) -> List[str]:
        """获取当前状态的所有合法下一状态"""
        normalized = self._normalize_state(current_state)
        return sorted(LIFECYCLE_TRANSITIONS.get(normalized, frozenset()))

    def get_state_info(self) -> Dict[str, Any]:
        """获取状态机完整信息（诊断用）"""
        return {
            "states": [s.value for s in LifecycleState],
            "transitions": {
                k: sorted(v) for k, v in LIFECYCLE_TRANSITIONS.items()
            },
            "terminal_states": sorted(TERMINAL_STATES),
            "legacy_mapping": LEGACY_STATUS_TO_LIFECYCLE,
        }


# ============================================================
# 模块级单例
# ============================================================

_lifecycle_instance: Optional[ProposalLifecycleManager] = None


def get_lifecycle_manager() -> Optional[ProposalLifecycleManager]:
    return _lifecycle_instance


def set_lifecycle_manager(mgr: Optional[ProposalLifecycleManager]) -> None:
    global _lifecycle_instance
    _lifecycle_instance = mgr
