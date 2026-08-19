"""
Phase B.2.6 — Personality State Updater

职责：
应用 PersonalityEvolutionProposal 到 trait_states（dict[str, float]），
写入 personality_evolution_history。支持 rollback。

调用链：
    EvolutionProposal
        ↓
    PersonalityStateUpdater.apply()
        ↓
    new_trait_states + evolution_history
        ↓
    SelfModelStore.set_personality_evolution_history()

安全约束：
- 不破坏 TraitState 结构（仅修改 current_value）
- 核心人格保护（CoreIdentity.check_change_allowed）
- 单次变化限幅
- 异常隔离
- 支持 rollback（status -> rolled_back，旧值恢复）
"""
from __future__ import annotations

import copy
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.personality.evolution_engine import (
    PersonalityEvolutionProposal,
    EvolutionProposalItem,
    MAX_SINGLE_TRAIT_DELTA,
)

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class EvolutionHistoryEntry:
    """单次演化历史记录"""
    evolution_id: str = field(default_factory=lambda: f"eh_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)
    source_proposal_id: str = ""
    source_growth_record_ids: List[str] = field(default_factory=list)
    changed_traits: Dict[str, Dict[str, float]] = field(default_factory=dict)
    # changed_traits: {trait: {before, after, delta, evidence_count, confidence}}
    confidence: float = 0.0
    reason: str = ""
    status: str = "applied"  # applied / rolled_back


class PersonalityStateUpdater:
    """
    Phase B.2.6 应用 EvolutionProposal 到 trait_states。
    """

    MAX_HISTORY_LENGTH = 100
    MAX_SINGLE_DELTA = MAX_SINGLE_TRAIT_DELTA  # 0.05

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.max_single_delta: float = float(
            self.config.get("max_single_delta", self.MAX_SINGLE_DELTA)
        )

    # ============================================================
    # apply: 应用 EvolutionProposal
    # ============================================================
    def apply(
        self,
        proposal: PersonalityEvolutionProposal,
        trait_states: Dict[str, float],
        evolution_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        应用一个 EvolutionProposal 到 trait_states。

        Returns:
            {
                "applied": bool,
                "new_trait_states": Dict[str, float],
                "new_evolution_history": List[Dict],
                "entry": Dict | None,
                "rejection_reasons": List[str],
            }
        """
        result: Dict[str, Any] = {
            "applied": False,
            "new_trait_states": dict(trait_states),
            "new_evolution_history": list(evolution_history or []),
            "entry": None,
            "rejection_reasons": [],
        }

        try:
            if proposal.status not in ("pending", "accepted"):
                result["rejection_reasons"].append(
                    f"proposal_status_not_pending:{proposal.status}"
                )
                return result

            if not proposal.items:
                result["rejection_reasons"].append("no_items")
                return result

            # 核心人格保护
            try:
                from src.personality.core_identity import CoreIdentity
                forbidden = CoreIdentity.get_forbidden_changes()
            except Exception:
                forbidden = []

            new_states = dict(trait_states)
            changed: Dict[str, Dict[str, float]] = {}
            rejected_traits: List[str] = []

            for item in proposal.items:
                trait = item.trait
                delta = item.proposed_delta
                before = float(new_states.get(trait, 0.5))
                # 限幅
                safe_delta = max(-self.max_single_delta, min(self.max_single_delta, delta))
                # 核心保护：trait 名包含 forbidden 关键词 → 拒绝
                if any(kw in trait for kw in forbidden):
                    rejected_traits.append(trait)
                    continue
                # 应用
                after = max(0.0, min(1.0, before + safe_delta))
                new_states[trait] = round(after, 4)
                changed[trait] = {
                    "before": round(before, 4),
                    "after": round(after, 4),
                    "delta": round(safe_delta, 4),
                    "evidence_count": item.evidence_count,
                    "confidence": item.cumulative_confidence,
                }

            if not changed:
                result["rejection_reasons"].extend(
                    [f"core_protected:{t}" for t in rejected_traits]
                )
                if not rejected_traits:
                    result["rejection_reasons"].append("no_eligible_changes")
                return result

            # ============================================================
            # [personality_state_updated]
            # ============================================================
            entry = EvolutionHistoryEntry(
                source_proposal_id=proposal.proposal_id,
                source_growth_record_ids=list(proposal.source_growth_record_ids),
                changed_traits=changed,
                confidence=proposal.confidence,
                reason=proposal.reason,
                status="applied",
            )
            entry_dict = asdict(entry)
            new_history = list(evolution_history or [])
            new_history.append(entry_dict)
            # 限长
            if len(new_history) > self.MAX_HISTORY_LENGTH:
                new_history = new_history[-self.MAX_HISTORY_LENGTH:]

            result["applied"] = True
            result["new_trait_states"] = new_states
            result["new_evolution_history"] = new_history
            result["entry"] = entry_dict
            if rejected_traits:
                result["rejection_reasons"].extend(
                    [f"core_protected:{t}" for t in rejected_traits]
                )

            logger.info(
                "[personality_state_updated] proposal_id=%s traits=%s",
                proposal.proposal_id,
                list(changed.keys()),
            )
            return result

        except Exception as e:
            logger.exception("[personality_apply_failed] %s", e)
            result["rejection_reasons"].append(f"exception:{e}")
            return result

    # ============================================================
    # rollback: 回滚最近一次应用
    # ============================================================
    def rollback(
        self,
        evolution_history: List[Dict[str, Any]],
        trait_states: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        回滚最近一次 applied 演化（按 evolution_history 顺序）。
        返回更新后的状态。
        """
        result = {
            "rolled_back": False,
            "new_trait_states": dict(trait_states),
            "new_evolution_history": list(evolution_history or []),
            "entry": None,
        }
        try:
            history = list(evolution_history or [])
            # 找到最近一条 applied
            for i in range(len(history) - 1, -1, -1):
                e = history[i]
                if e.get("status") == "applied":
                    # 恢复 before 值
                    new_states = dict(trait_states)
                    for trait, change in (e.get("changed_traits") or {}).items():
                        new_states[trait] = change.get("before", new_states.get(trait, 0.5))
                    # 标记为 rolled_back
                    e2 = dict(e)
                    e2["status"] = "rolled_back"
                    e2["timestamp"] = now_iso()
                    new_history = list(history)
                    new_history[i] = e2
                    result["rolled_back"] = True
                    result["new_trait_states"] = new_states
                    result["new_evolution_history"] = new_history
                    result["entry"] = e2
                    logger.info(
                        "[personality_state_rolled_back] evolution_id=%s",
                        e.get("evolution_id", ""),
                    )
                    return result
            return result
        except Exception as e:
            logger.exception("[personality_rollback_failed] %s", e)
            return result
