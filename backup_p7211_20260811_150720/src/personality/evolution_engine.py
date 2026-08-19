"""
Phase B.2.1 — Personality Evolution Engine

职责：
读取 PersonalityGrowthHistory.records，分析多个 GrowthRecord，
生成 PersonalityEvolutionProposal（不是直接修改人格）。

调用链：
    GrowthRecord
        ↓
    EvolutionEngine.evaluate()
        ↓
    EvolutionProposal
        ↓
    PersonalityStateUpdater
        ↓
    SelfModel.personality_state

安全约束：
- 不直接修改 TraitState（仅生成 Proposal）
- 只读 GrowthHistory，不读取原始聊天
- 所有变化必须 evidence_count >= MIN_EVIDENCE_COUNT
- 异常完全隔离 try/except
- 支持 rollback（proposal 标记 status）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.evidence_tracker import EvidenceTracker
from src.personality.personality_conflict import (
    PersonalityConflict,
    ConflictResolver,
    ConflictStatus,
)

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


# Phase B.2.2: 单次变化最大 delta
MAX_SINGLE_TRAIT_DELTA = 0.05

# Phase B.2.2: 累计变化最少独立 evidence 数
MIN_EVIDENCE_COUNT = 2

# Phase B.2.2: 最低累计 confidence（平均 confidence * evidence_count 的下限）
MIN_CUMULATIVE_CONFIDENCE = 0.7

# 核心人格保护：这些 trait 不允许通过 EvolutionEngine 修改
CORE_PROTECTED_TRAITS = frozenset({
    "core_identity",  # 通用核心身份占位
    # 实际保护在 apply 阶段通过 CoreIdentity.check_change_allowed 校验
})


@dataclass
class EvolutionProposalItem:
    """单个 trait 的演化提议"""
    trait: str
    proposed_delta: float = 0.0
    evidence_count: int = 0
    cumulative_confidence: float = 0.0
    before_value: float = 0.5
    after_value: float = 0.5
    reason: str = ""
    source_record_ids: List[str] = field(default_factory=list)


@dataclass
class PersonalityEvolutionProposal:
    """
    Phase B.2.1 EvolutionProposal
    由 EvolutionEngine 生成，PersonalityStateUpdater 消费。
    不直接修改人格。
    """
    proposal_id: str = field(default_factory=lambda: f"evo_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=now_iso)
    source_growth_record_ids: List[str] = field(default_factory=list)
    items: List[EvolutionProposalItem] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    status: str = "pending"  # pending / applied / rejected / rolled_back
    rejection_reasons: List[str] = field(default_factory=list)
    conflicts: List[PersonalityConflict] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # PersonalityConflict 不一定可 asdict，转 dict
        d["conflicts"] = [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in self.conflicts]
        return d


class EvolutionEngine:
    """
    Phase B.2.1 Personality Evolution Engine

    读取 PersonalityGrowthHistory.records → 累积 evidence →
    生成 PersonalityEvolutionProposal。
    """

    def __init__(
        self,
        evidence_tracker: Optional[EvidenceTracker] = None,
        conflict_resolver: Optional[ConflictResolver] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.evidence_tracker = evidence_tracker or EvidenceTracker()
        self.conflict_resolver = conflict_resolver or ConflictResolver()
        self.config = config or {}
        self.max_single_delta: float = float(
            self.config.get("max_single_delta", MAX_SINGLE_TRAIT_DELTA)
        )
        self.min_evidence_count: int = int(
            self.config.get("min_evidence_count", MIN_EVIDENCE_COUNT)
        )
        self.min_cumulative_confidence: float = float(
            self.config.get("min_cumulative_confidence", MIN_CUMULATIVE_CONFIDENCE)
        )

    # ============================================================
    # evaluate: GrowthHistory → EvolutionProposal
    # ============================================================
    def evaluate(
        self,
        history: PersonalityGrowthHistory,
        current_states: Optional[Dict[str, float]] = None,
    ) -> PersonalityEvolutionProposal:
        """
        评估 GrowthHistory 累积成长证据，生成 EvolutionProposal。

        Args:
            history: PersonalityGrowthHistory 实例（只读）
            current_states: 当前 trait 状态（可选），用于决定 before/after

        Returns:
            PersonalityEvolutionProposal (pending, 待 PersonalityStateUpdater 应用)
        """
        current_states = current_states or {}
        proposal = PersonalityEvolutionProposal()

        try:
            records = history.all() if hasattr(history, "all") else []
            if not records:
                proposal.status = "rejected"
                proposal.rejection_reasons.append("empty_history")
                return proposal

            # ============================================================
            # [evolution_evaluate] 累计 evidence
            # ============================================================
            logger.info(
                "[evolution_evaluate] records=%d",
                len(records),
            )

            for record in records:
                self.evidence_tracker.absorb(record)

            # ============================================================
            # 为每个被影响的 trait 生成 EvolutionProposalItem
            # ============================================================
            affected_traits = set()
            for record in records:
                for dim in record.get("affected_dimensions", []) or []:
                    affected_traits.add(dim)

            for trait in sorted(affected_traits):
                evidence = self.evidence_tracker.summary(trait)
                item = self._build_item(
                    trait=trait,
                    evidence=evidence,
                    current_value=current_states.get(trait, 0.5),
                )
                if item is not None:
                    proposal.items.append(item)
                    proposal.source_growth_record_ids.extend(evidence.get("source_record_ids", []))

            # 去重 source_growth_record_ids
            proposal.source_growth_record_ids = list(dict.fromkeys(proposal.source_growth_record_ids))

            # ============================================================
            # 冲突检测
            # ============================================================
            conflicts = self.conflict_resolver.detect(
                proposal_items=proposal.items,
                current_states=current_states,
            )
            proposal.conflicts = conflicts
            for c in conflicts:
                if c.status == ConflictStatus.NEEDS_REVIEW.value:
                    proposal.rejection_reasons.append(f"conflict_needs_review:{c.dimension}")

            # ============================================================
            # 整体 confidence
            # ============================================================
            if proposal.items:
                avg_conf = sum(it.cumulative_confidence for it in proposal.items) / len(proposal.items)
                proposal.confidence = round(avg_conf, 4)
            else:
                proposal.confidence = 0.0

            # ============================================================
            # 安全门槛校验
            # ============================================================
            if not proposal.items:
                proposal.status = "rejected"
                proposal.rejection_reasons.append("no_eligible_items")
            elif any(r.startswith("conflict_needs_review") for r in proposal.rejection_reasons):
                proposal.status = "needs_review"
            elif proposal.confidence < self.min_cumulative_confidence:
                proposal.status = "rejected"
                proposal.rejection_reasons.append(
                    f"low_confidence:{proposal.confidence:.3f}<{self.min_cumulative_confidence:.3f}"
                )
            else:
                proposal.status = "pending"
                proposal.reason = self._build_overall_reason(proposal)

            logger.info(
                "[evolution_proposal_generated] id=%s items=%d confidence=%.3f status=%s",
                proposal.proposal_id,
                len(proposal.items),
                proposal.confidence,
                proposal.status,
            )
            return proposal

        except Exception as e:
            logger.exception("[evolution_engine_failed] %s", e)
            proposal.status = "rejected"
            proposal.rejection_reasons.append(f"exception:{e}")
            return proposal

    # ============================================================
    # _build_item: 单 trait 提议
    # ============================================================
    def _build_item(
        self,
        trait: str,
        evidence: Dict[str, Any],
        current_value: float,
    ) -> Optional[EvolutionProposalItem]:
        """根据累计 evidence 构建提议项；不满足条件返回 None。"""
        # 核心人格保护（trait 名在 CoreIdentity 关键字中 → 拒绝）
        try:
            from src.personality.core_identity import CoreIdentity
            forbidden = CoreIdentity.get_forbidden_changes()
            for kw in forbidden:
                if kw in trait:
                    return None
        except Exception:
            pass

        evidence_count = int(evidence.get("count", 0))
        cumulative_confidence = float(evidence.get("avg_confidence", 0.0))
        cumulative_delta = float(evidence.get("cumulative_delta", 0.0))
        source_record_ids = list(evidence.get("source_record_ids", []))

        # 安全规则: evidence 不够
        if evidence_count < self.min_evidence_count:
            return None
        if cumulative_confidence < self.min_cumulative_confidence:
            return None

        # 单次 delta 限幅
        safe_delta = max(
            -self.max_single_delta,
            min(self.max_single_delta, cumulative_delta),
        )
        before_value = current_value
        after_value = max(0.0, min(1.0, before_value + safe_delta))

        return EvolutionProposalItem(
            trait=trait,
            proposed_delta=round(safe_delta, 4),
            evidence_count=evidence_count,
            cumulative_confidence=round(cumulative_confidence, 4),
            before_value=round(before_value, 4),
            after_value=round(after_value, 4),
            reason=evidence.get("reason", ""),
            source_record_ids=source_record_ids,
        )

    def _build_overall_reason(self, proposal: PersonalityEvolutionProposal) -> str:
        parts = []
        for it in proposal.items:
            parts.append(
                f"{it.trait}{it.proposed_delta:+.3f}(ev={it.evidence_count},conf={it.cumulative_confidence:.2f})"
            )
        return "evolution:" + ";".join(parts)
