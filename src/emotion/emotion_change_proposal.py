# -*- coding: utf-8 -*-
"""
情绪变化提案 (EmotionChangeProposal) — Emotion System 2.0（E-Emotion-2）

职责：
- EmotionEvaluator 输出情绪变化的评估提案（只提议，不修改状态）
- EmotionUpdater 是唯一应用方（E-Emotion-3）

与 GrowthProposal 的关系：
- 本提案是情绪域内的短期状态提案，不进入人格/身份域
- 禁止携带 identity_core / personality_state 相关字段
- 若未来情绪模式需升级为人格提案，必须走 Event → GrowthProposal → 审核
  治理链（本提案不替代治理链）
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def _new_proposal_id() -> str:
    return f"ecp_{uuid.uuid4().hex[:12]}"


@dataclass
class EmotionChangeProposal:
    """一次情绪评估产生的单维度变化提案。

    字段契约（任务书 E-Emotion-2）：
    - emotion_dimension: 目标维度（valence/happiness/trust/...）
    - delta: 变化量（有符号）
    - confidence: 置信度 0~1（评估可靠性，非变化强度）
    - reason: 自然语言理由
    - evidence_ids: 证据（trace_id/memory_id/event 引用）
    """

    emotion_dimension: str = ""
    delta: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    proposal_id: str = field(default_factory=_new_proposal_id)
    source_event_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence or 0.0)))
        if not self.proposal_id:
            self.proposal_id = _new_proposal_id()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "emotion_dimension": self.emotion_dimension,
            "delta": self.delta,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence_ids": list(self.evidence_ids or []),
            "source_event_id": self.source_event_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionChangeProposal":
        return cls(
            emotion_dimension=data.get("emotion_dimension", ""),
            delta=float(data.get("delta", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            reason=data.get("reason", ""),
            evidence_ids=list(data.get("evidence_ids", []) or []),
            proposal_id=data.get("proposal_id", ""),
            source_event_id=data.get("source_event_id"),
            created_at=data.get("created_at", ""),
        )


# 合法目标维度（与 EmotionState 的 11 个维度对齐）
VALID_DIMENSIONS = frozenset({
    "valence", "arousal", "curiosity", "anxiety", "confidence", "energy",
    "stability", "happiness", "sadness", "trust", "attachment",
})
