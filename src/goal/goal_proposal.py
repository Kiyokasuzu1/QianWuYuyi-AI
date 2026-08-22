# -*- coding: utf-8 -*-
"""
src/goal/goal_proposal.py

v1.3 Agency Phase 1: Goal 关注方向提案封装(复用 GrowthProposal 信封)。

设计:
- 不新增 schema: GoalProposal = GrowthProposal(proposal_type=goal)
  + metadata["goal_proposal"] 载荷(与 self_model_proposal 同模式)。
- payload 字段: goal_id / description / source_refs / priority / confidence。
- 本模块只负责构造与提取, 不做任何审批/应用/状态修改。
- 来源真实性约束由 goal_approved_drain 在消费端强制
  (governance fail-closed), 构造端保持宽松以便 admin 先看到候选提案。

红线:
- 禁止调用 LLM / 事件总线 / 任何状态存储写入;
- 禁止 import src.personality / src.emotion / src.relationship。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from src.growth.proposal.constants import (
    PRIORITY_LEVEL,
    PROPOSAL_STATUS,
    PROPOSAL_TYPE,
)
from src.growth.proposal.proposal import GrowthProposal

# metadata 载荷键(与 self_model_proposal / emotion_proposal 命名惯例一致)
GOAL_PAYLOAD_KEY = "goal_proposal"


def _normalize_priority(priority: Optional[str]) -> str:
    _p = str(priority or PRIORITY_LEVEL["MEDIUM"])
    if _p in (PRIORITY_LEVEL["LOW"], PRIORITY_LEVEL["MEDIUM"], PRIORITY_LEVEL["HIGH"]):
        return _p
    return PRIORITY_LEVEL["MEDIUM"]


def build_goal_proposal(
    *,
    goal_id: str,
    description: str,
    source_refs: List[Dict[str, Any]],
    priority: Optional[str] = None,
    confidence: float = 0.0,
    proposal_id: Optional[str] = None,
    source: str = "admin",
    user_id: str = "",
) -> GrowthProposal:
    """构造一个 PENDING 的 Goal 治理提案(等待审批; 不落盘, 由调用方 save)。

    来源真实性在此不校验——提案先入治理链, 由审批环节 + GoalDrain 消费端
    双重把关(见 goal_source_validation.validate_goal_source_refs)。
    """
    _refs = list(source_refs or [])
    payload: Dict[str, Any] = {
        "goal_id": str(goal_id or "").strip(),
        "description": str(description or ""),
        "source_refs": _refs,
        "priority": _normalize_priority(priority),
        "confidence": float(confidence or 0.0),
    }
    evidence: List[str] = []
    for ref in _refs:
        if isinstance(ref, dict):
            _st = str(ref.get("source_type", "") or "")
            _sid = str(ref.get("source_id", "") or "")
            if _st and _sid:
                evidence.append(f"{_st}:{_sid}")
    return GrowthProposal(
        proposal_id=proposal_id or f"prop_{uuid.uuid4().hex[:8]}",
        proposal_type=PROPOSAL_TYPE["GOAL"],
        status=PROPOSAL_STATUS["PENDING"],
        source=source,
        user_id=user_id,
        confidence=float(confidence or 0.0),
        reason=f"goal: {str(description or '')[:128]}",
        evidence=evidence,
        metadata={
            GOAL_PAYLOAD_KEY: payload,
            "governance_decision": {"action": "approval_required"},
        },
    )


def extract_goal_payload(proposal: Any) -> Optional[Dict[str, Any]]:
    """从提案提取 goal 载荷; 缺失/损坏返回 None(fail-soft)。"""
    try:
        meta = getattr(proposal, "metadata", None)
        if not isinstance(meta, dict):
            return None
        payload = meta.get(GOAL_PAYLOAD_KEY)
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "GOAL_PAYLOAD_KEY",
    "build_goal_proposal",
    "extract_goal_payload",
]
