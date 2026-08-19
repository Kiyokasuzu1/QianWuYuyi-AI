"""
Phase B.2.4 — Personality Conflict Detection & Resolution

职责：
检测 EvolutionProposalItem 与当前 trait 状态的冲突，
生成 PersonalityConflict 并标记 needs_review。

设计：
- 不直接覆盖现有 state
- 冲突不破坏人格，仅标记
- 支持 accepted / merged / needs_review 三种状态
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now().isoformat()


class ConflictStatus(str, Enum):
    """冲突解决状态"""
    ACCEPTED = "accepted"  # 接受新提议
    MERGED = "merged"  # 合并（取折中值）
    NEEDS_REVIEW = "needs_review"  # 需要人工审核


# 单次提议变化超过 0.3 视为潜在冲突
DEFAULT_CONFLICT_THRESHOLD = 0.3
# 当前状态与提议方向相反且差值大，视为冲突
DIRECTION_REVERSAL_THRESHOLD = 0.15


@dataclass
class PersonalityConflict:
    """
    单个 trait 的冲突信息
    """
    conflict_id: str = field(default_factory=lambda: f"conf_{uuid.uuid4().hex[:8]}")
    dimension: str = ""
    old_state: float = 0.0
    new_proposal: float = 0.0
    delta: float = 0.0
    evidence: List[str] = field(default_factory=list)
    conflict_type: str = "oversized_change"  # oversized_change / direction_reversal / over_max
    resolution_status: str = ConflictStatus.NEEDS_REVIEW.value
    resolution_reason: str = ""
    timestamp: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ConflictResolver:
    """
    Phase B.2.4 ConflictResolver

    检测 EvolutionProposalItem 是否与当前 state 冲突。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.oversized_threshold: float = float(
            self.config.get("oversized_threshold", DEFAULT_CONFLICT_THRESHOLD)
        )
        self.direction_reversal_threshold: float = float(
            self.config.get("direction_reversal_threshold", DIRECTION_REVERSAL_THRESHOLD)
        )

    def detect(
        self,
        proposal_items: List[Any],
        current_states: Dict[str, float],
    ) -> List[PersonalityConflict]:
        """
        检测冲突；返回冲突列表（可能为空）。
        """
        conflicts: List[PersonalityConflict] = []
        try:
            for item in proposal_items:
                # item 可能是 EvolutionProposalItem dataclass 或 dict
                if hasattr(item, "trait"):
                    trait = item.trait
                    new_proposal = getattr(item, "after_value", 0.0)
                    delta = getattr(item, "proposed_delta", 0.0)
                    evidence = list(getattr(item, "source_record_ids", []) or [])
                elif isinstance(item, dict):
                    trait = item.get("trait", "")
                    new_proposal = float(item.get("after_value", 0.0))
                    delta = float(item.get("proposed_delta", 0.0))
                    evidence = list(item.get("source_record_ids", []) or [])
                else:
                    continue

                old_state = float(current_states.get(trait, 0.5))

                # 检测 1：单次提议变化过大（虽然被 max_single_delta 限过，
                # 但 cumulative delta 仍可能 > 0.3）
                abs_delta = abs(new_proposal - old_state)
                if abs_delta > self.oversized_threshold:
                    conflicts.append(PersonalityConflict(
                        dimension=trait,
                        old_state=old_state,
                        new_proposal=new_proposal,
                        delta=delta,
                        evidence=evidence,
                        conflict_type="oversized_change",
                        resolution_status=ConflictStatus.NEEDS_REVIEW.value,
                        resolution_reason=(
                            f"cumulative_change {abs_delta:.3f} > "
                            f"threshold {self.oversized_threshold:.3f}"
                        ),
                    ))
                    continue

                # 检测 2：方向反转（当前已被推向一端，提议反向）
                if abs_delta > 0:
                    sign_old = 1 if (old_state - 0.5) > 0 else -1
                    sign_new = 1 if (new_proposal - 0.5) > 0 else -1
                    if sign_old != 0 and sign_new != 0 and sign_old != sign_new:
                        if abs(old_state - 0.5) > self.direction_reversal_threshold:
                            conflicts.append(PersonalityConflict(
                                dimension=trait,
                                old_state=old_state,
                                new_proposal=new_proposal,
                                delta=delta,
                                evidence=evidence,
                                conflict_type="direction_reversal",
                                resolution_status=ConflictStatus.NEEDS_REVIEW.value,
                                resolution_reason=(
                                    f"reverse direction: old={old_state:.2f} "
                                    f"-> new={new_proposal:.2f}"
                                ),
                            ))

            if conflicts:
                logger.info(
                    "[conflict_detected] count=%d types=%s",
                    len(conflicts),
                    [c.conflict_type for c in conflicts],
                )
            return conflicts
        except Exception as e:
            logger.exception("[conflict_detect_failed] %s", e)
            return []

    def resolve(
        self,
        conflict: PersonalityConflict,
        strategy: str = "auto",
    ) -> ConflictStatus:
        """
        解决冲突（标记状态）。
        strategy: auto / accepted / merged / needs_review
        """
        if strategy == "accepted":
            conflict.resolution_status = ConflictStatus.ACCEPTED.value
        elif strategy == "merged":
            # 折中：取 old + (new - old) * 0.5
            mid = conflict.old_state + (conflict.new_proposal - conflict.old_state) * 0.5
            conflict.new_proposal = round(mid, 4)
            conflict.resolution_status = ConflictStatus.MERGED.value
            conflict.resolution_reason = (
                f"{conflict.resolution_reason}; merged to {conflict.new_proposal:.3f}"
            )
        else:  # auto or needs_review
            conflict.resolution_status = ConflictStatus.NEEDS_REVIEW.value
        return ConflictStatus(conflict.resolution_status)
