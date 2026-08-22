# -*- coding: utf-8 -*-
"""
情绪更新引擎 (EmotionUpdater) — Emotion System 2.0（E-Emotion-3）

职责：
- 应用合法的 EmotionChangeProposal → EmotionState（唯一应用方）
- 每次应用记录 before/after + audit（AuditEntry，component="emotion"）
- 最大变化限制：单次事件/单维度的变化量封顶，防止一次事件巨大跳变
- 衰减：委托 EmotionDecay，短期维度（兴奋/焦虑）快衰减，
  长期维度（信任/依恋）慢衰减

边界（红线）：
- 只允许修改 EmotionState；禁止触碰 identity_core / personality_state /
  relationship_state / growth_state / self_model
- 提案携带非法维度 / 超限变化 → 拒绝并审计，不静默
- 本模块不持久化情绪状态（保存由 EmotionRepository/调用方负责），
  审计写入委托可注入的 audit sink
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.contracts.audit_schema import AuditEntry
from src.emotion.emotion_change_proposal import (
    EmotionChangeProposal,
    VALID_DIMENSIONS,
)
from src.emotion.emotion_decay import EmotionDecay
from src.emotion.emotion_state import (
    EmotionState,
    LONG_TERM_DIMENSIONS,
    SHORT_TERM_DIMENSIONS,
)

ACTOR = "emotion_updater"

# 最大变化限制（E-Emotion-3）：单维度单次应用的变化量上限
# 短期维度（易波动）上限宽松；长期维度（trust/attachment）上限严格——
# 信任与依恋是长期积累，不能被单次事件大幅改变。
DEFAULT_MAX_DELTA = {
    "valence": 0.35,
    "arousal": 0.35,
    "happiness": 0.35,
    "sadness": 0.35,
    "anxiety": 0.35,
    "energy": 0.35,
    "curiosity": 0.30,
    "confidence": 0.30,
    "stability": 0.25,
    "trust": 0.10,
    "attachment": 0.10,
}

# 置信度门槛：低于该值的提案拒绝应用（低质量信号不扰动长期状态）
DEFAULT_MIN_CONFIDENCE = 0.3


@dataclass
class EmotionApplyResult:
    """一次批量应用的结果（可审计）。"""

    state_before: Dict[str, Any] = field(default_factory=dict)
    state_after: Dict[str, Any] = field(default_factory=dict)
    applied: List[Dict[str, Any]] = field(default_factory=list)
    rejected: List[Dict[str, Any]] = field(default_factory=list)
    audit_entries: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state_before": self.state_before,
            "state_after": self.state_after,
            "applied": self.applied,
            "rejected": self.rejected,
            "audit_entries": self.audit_entries,
        }


class EmotionUpdater:
    """情绪提案应用器：校验 → 限幅 → 应用 → 审计。"""

    def __init__(
        self,
        max_delta: Optional[Dict[str, float]] = None,
        min_confidence: Optional[float] = None,
        audit_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
        decay: Optional[EmotionDecay] = None,
    ) -> None:
        self.max_delta = dict(max_delta or DEFAULT_MAX_DELTA)
        self.min_confidence = (
            float(min_confidence)
            if min_confidence is not None
            else DEFAULT_MIN_CONFIDENCE
        )
        self.audit_sink = audit_sink
        # 衰减引擎（实例属性命名为 _decay_engine，避免遮蔽 decay() 方法）
        self._decay_engine = decay or EmotionDecay()

    # --------------------------------------------------------
    # 提案应用
    # --------------------------------------------------------
    def apply(
        self,
        state: EmotionState,
        proposals: List[EmotionChangeProposal],
        *,
        actor: str = ACTOR,
        source_event_id: Optional[str] = None,
    ) -> EmotionApplyResult:
        """应用提案列表（逐个校验），返回新状态与完整审计。

        - 非法维度 / 低置信度 / 超限 delta → 拒绝并审计（不静默）
        - 状态变化走 EmotionState.apply_delta（不可变，原状态不改变）
        - 不持久化状态；审计条目经 audit_sink 输出（若注入）
        """
        before = state.to_dict()
        rejected: List[Dict[str, Any]] = []
        applied: List[Dict[str, Any]] = []
        audit_entries: List[Dict[str, Any]] = []

        current = state
        for proposal in proposals:
            entry, ok = self._apply_one(
                current, proposal, actor=actor, source_event_id=source_event_id
            )
            audit_entries.append(entry)
            record = {
                "proposal_id": proposal.proposal_id,
                "emotion_dimension": proposal.emotion_dimension,
                "delta": proposal.delta,
                "confidence": proposal.confidence,
                "reason": proposal.reason,
            }
            if ok:
                applied.append(record)
                current = current.apply_delta(
                    self._delta_from_proposal(proposal)
                )
            else:
                rejected.append(record)

        result = EmotionApplyResult(
            state_before=before,
            state_after=current.to_dict(),
            applied=applied,
            rejected=rejected,
            audit_entries=audit_entries,
        )

        if self.audit_sink is not None:
            for entry in audit_entries:
                self.audit_sink(entry)
        return result

    def _apply_one(
        self,
        state: EmotionState,
        proposal: EmotionChangeProposal,
        *,
        actor: str,
        source_event_id: Optional[str],
    ) -> tuple:
        """校验单条提案并生成审计条目。返回 (audit_dict, accepted)。"""
        dim = proposal.emotion_dimension
        reason_accept = f"applied {dim} delta={proposal.delta:+.4f}"

        if dim not in VALID_DIMENSIONS:
            reason = f"rejected: invalid_dimension={dim}"
            return self._entry(
                state, proposal, actor, source_event_id, reason, accepted=False
            ), False
        if proposal.confidence < self.min_confidence:
            reason = (
                f"rejected: low_confidence={proposal.confidence:.2f}"
                f"<{self.min_confidence:.2f}"
            )
            return self._entry(
                state, proposal, actor, source_event_id, reason, accepted=False
            ), False

        limit = self.max_delta.get(dim, 0.35)
        if abs(proposal.delta) > limit:
            reason = (
                f"rejected: delta_exceeds_limit "
                f"|delta|={abs(proposal.delta):.4f}>{limit:.4f}"
            )
            return self._entry(
                state, proposal, actor, source_event_id, reason, accepted=False
            ), False

        return self._entry(
            state, proposal, actor, source_event_id, reason_accept, accepted=True
        ), True

    def _entry(
        self,
        state: EmotionState,
        proposal: EmotionChangeProposal,
        actor: str,
        source_event_id: Optional[str],
        reason: str,
        *,
        accepted: bool,
    ) -> Dict[str, Any]:
        entry = AuditEntry(
            component="emotion",
            actor=actor,
            reason=reason,
            source_event_id=source_event_id or proposal.source_event_id,
            evidence_memory_ids=list(proposal.evidence_ids or []),
            before={"dimension": proposal.emotion_dimension, "value": _dim_value(state, proposal.emotion_dimension)},
            after={
                "proposal_id": proposal.proposal_id,
                "delta": proposal.delta,
                "confidence": proposal.confidence,
                "accepted": accepted,
            },
            version="emotion.2.0",
        )
        return entry.to_dict()

    @staticmethod
    def _delta_from_proposal(proposal: EmotionChangeProposal):
        from src.emotion.emotion_delta import EmotionDelta

        return EmotionDelta(**{proposal.emotion_dimension: proposal.delta})

    # --------------------------------------------------------
    # 衰减（短期快 / 长期慢）
    # --------------------------------------------------------
    def decay(self, state: EmotionState, seconds: float) -> EmotionState:
        """时间衰减：短期维度快回落，长期维度（信任/依恋）慢回落。

        委托 EmotionDecay（配置内含 11 维度不同半衰期）。
        返回新状态，不修改原对象。
        """
        return self._decay_engine.apply(state, seconds)

    def decay_dimension_categories(self) -> Dict[str, List[str]]:
        """暴露维度分类（审计/测试用）。"""
        return {
            "short_term": list(SHORT_TERM_DIMENSIONS),
            "long_term": list(LONG_TERM_DIMENSIONS),
        }


def _dim_value(state: EmotionState, dim: str) -> float:
    try:
        return float(getattr(state, dim))
    except Exception:
        return 0.0
