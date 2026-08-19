"""
ProposalEvaluator —— GrowthProposal 审批前置评估器（Phase 3.5.7）

职责：
在 GrowthProposal 转换为 PersonalityChangeRequest 之前，进行多维质量评估。
不通过的提案不会进入人格系统，但不会删除提案（仍然保留在 GrowthAdapter 中供人工审查。

评估维度：
1. confidence       - 提案自身置信度
2. evidence   - 支撑证据（经验 ID）数量充足性
3. contradiction - 与近期已批准提案不冲突检测
4. stability     - 同一维度近期变化方向稳定性

设计原则：
- 不修改现有 Growth 核心，不直接修改人格
- 不通过提案不会被删除，只是不会生成 rejection reason，仍然在提案标记 requires_validation 标记
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.contracts.growth_schema import GrowthProposal

logger = logging.getLogger(__name__)


# ============================================================
# 评估阈值（供外部配置
# ============================================================

@dataclass
class EvaluationThresholds:
    """ProposalEvaluator 阈值配置"""

    min_confidence: float = 0.45                    # 最低置信度
    min_evidence_count: int = 2                     # 最少证据数量
    contradiction_max_delta: float = 0.08           # 单维度冲突变化量阈值（|delta| 超过此值的 50% 视为冲突）
    stability_window: int = 5                       # 稳定性窗口
    stability_min_ratio: float = 0.6                # 稳定性：窗口内同一方向比例阈值


# ============================================================
# EvaluationResult
# ============================================================

@dataclass
class EvaluationResult:
    """ProposalEvaluator 结果"""

    passed: bool
    score: float                                   # 综合评分 [0, 1]
    confidence_check: Dict[str, Any] = field(default_factory=dict)
    evidence_check: Dict[str, Any] = field(default_factory=dict)
    contradiction_check: Dict[str, Any] = field(default_factory=dict)
    stability_check: Dict[str, Any] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)          # 不通过/通过的原因
    meta: Dict[str, Any] = field(default_factory=dict)         # 自定义元数据（传递 downstream

    def to_evaluator_meta(self) -> Dict[str, Any]:
        """转换为 evaluator_meta 字典（写入 proposal.evaluator_meta）"""
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "reasons": self.reasons,
            "confidence": self.confidence_check,
            "evidence": self.evidence_check,
            "contradiction": self.contradiction_check,
            "stability": self.stability_check,
            **self.meta,
        }


# ============================================================
# 历史提案：方向 & 稳定性追踪（in-memory，外部持久化交给外部调用方
# ============================================================

@dataclass
class _HistoricalProposal:
    proposal_id: str
    trait: str
    delta: float  # (+)
    timestamp: str
    status: str    # proposed / accepted / rejected


class ProposalEvaluator:
    """
    GrowthProposal 前置评估器。

    用法：
        evaluator = ProposalEvaluator()
        result = evaluator.evaluate(proposal, recent_proposals=...)
        if result.passed:
            change_request = personality_adapter.build_change_request(
                proposal, evaluator_meta=result.to_evaluator_meta())
    """

    def __init__(self, thresholds: Optional[EvaluationThresholds] = None):
        self.thresholds = thresholds or EvaluationThresholds()

    # ============================================================
    # 主接口
    # ============================================================

    def evaluate(
        self,
        proposal: GrowthProposal,
        recent_accepted_proposals: Optional[List[GrowthProposal]] = None,
        recent_change_requests: Optional[List[Dict[str, Any]]] = None,
    ) -> EvaluationResult:
        """
        对 GrowthProposal 进行四维评估。

        Args:
            proposal: 待评估的提案
            recent_accepted_proposals: 近期已接受的提案列表（用于稳定性/冲突检测）
            recent_change_requests: 近期已生成的 PersonalityChangeRequest 列表（可选附加上下文）

        Returns:
            EvaluationResult：包含四维检查结果及综合评分
        """
        recent_accepted_proposals = recent_accepted_proposals or []
        recent_change_requests = recent_change_requests or []

        reasons: List[str] = []

        # ---- 1. Confidence 检查 ----
        confidence_check = self._check_confidence(proposal)
        if not confidence_check["passed"]:
            reasons.extend(confidence_check.get("reasons", []))

        # ---- 2. Evidence 数量检查 ----
        evidence_check = self._check_evidence(proposal)
        if not evidence_check["passed"]:
            reasons.extend(evidence_check.get("reasons", []))

        # ---- 3. Contradiction 冲突检查 ----
        contradiction_check = self._check_contradiction(proposal, recent_accepted_proposals)
        if not contradiction_check["passed"]:
            reasons.extend(contradiction_check.get("reasons", []))

        # ---- 4. Stability 稳定性检查 ----
        stability_check = self._check_stability(proposal, recent_accepted_proposals)
        if not stability_check["passed"]:
            reasons.extend(stability_check.get("reasons", []))

        # ---- 综合评分 ----
        score = (
            confidence_check["score"] * 0.35
            + evidence_check["score"] * 0.25
            + contradiction_check["score"] * 0.20
            + stability_check["score"] * 0.20
        )
        passed = all([
            confidence_check["passed"],
            evidence_check["passed"],
            contradiction_check["passed"],
            stability_check["passed"],
        ])

        if passed and not reasons:
            reasons.append("all_checks_passed")

        # 将 pattern_detected 透传到 meta（如 proposal 里有）
        extra_meta: Dict[str, Any] = {}
        if hasattr(proposal, "evaluator_meta") and isinstance(proposal.evaluator_meta, dict):
            extra_meta["pattern_detected"] = proposal.evaluator_meta.get("pattern_detected")

        return EvaluationResult(
            passed=passed,
            score=score,
            confidence_check=confidence_check,
            evidence_check=evidence_check,
            contradiction_check=contradiction_check,
            stability_check=stability_check,
            reasons=reasons,
            meta=extra_meta,
        )

    # ============================================================
    # 1. Confidence 检查
    # ============================================================

    def _check_confidence(self, proposal: GrowthProposal) -> Dict[str, Any]:
        conf = float(getattr(proposal, "confidence", 0.0) or 0.0)
        min_conf = self.thresholds.min_confidence
        passed = conf >= min_conf
        score = min(1.0, max(0.0, (conf - 0.2) / 0.8) if conf >= 0.2 else 0.0)
        reasons = []
        if not passed:
            reasons.append(
                f"confidence_too_low: {conf:.2f} < {min_conf:.2f}"
            )
        return {
            "passed": passed,
            "score": round(score, 4),
            "confidence": conf,
            "min_required": min_conf,
            "reasons": reasons,
        }

    # ============================================================
    # 2. Evidence 数量检查
    # ============================================================

    def _check_evidence(self, proposal: GrowthProposal) -> Dict[str, Any]:
        evidence_ids = getattr(proposal, "evidence_ids", []) or []
        count = len(evidence_ids)
        min_count = self.thresholds.min_evidence_count
        # 评估 proposed_changes 也算作内部证据（宽松一点
        proposed_count = len(getattr(proposal, "proposed_changes", []) or [])
        effective_count = count + max(0, proposed_count - 1)

        passed = effective_count >= min_count
        # 评分：达到 min_count → 0.6；超过线性上升到 1.0
        if effective_count >= min_count + 3:
            score = 1.0
        else:
            score = min(1.0, effective_count / max(1, min_count + 3))

        reasons = []
        if not passed:
            reasons.append(
                f"insufficient_evidence: {count}(ids)+{max(0, proposed_count-1)}(changes) = {effective_count} < {min_count}"
            )
        return {
            "passed": passed,
            "score": round(score, 4),
            "evidence_count": count,
            "proposed_changes_count": proposed_count,
            "effective_count": effective_count,
            "min_required": min_count,
            "reasons": reasons,
        }

    # ============================================================
    # 3. Contradiction 冲突检查
    # ============================================================

    def _check_contradiction(
        self,
        proposal: GrowthProposal,
        recent_accepted: List[GrowthProposal],
    ) -> Dict[str, Any]:
        """
        检查当前提案与近期已接受提案是否存在「方向冲突 + 幅度过大。
        """
        # 提取当前提案中每个 trait 的 delta
        current_deltas: Dict[str, float] = {}
        for ci in proposal.proposed_changes or []:
            trait = _extract_trait_simple(ci.path)
            if trait == "unknown":
                continue
            b = _safe_float(ci.before)
            a = _safe_float(ci.after)
            if a is None or b is None:
                continue
            current_deltas[trait] = a - b

        if not current_deltas:
            # 没有明确的 trait delta，跳过冲突检查通过（基于 pattern 的 GrowthRecord 路径
            return {
                "passed": True,
                "score": 0.8,
                "checked_traits": [],
                "contradictions": [],
                "reasons": ["no_explicit_trait_changes_skipping_contradiction"],
            }

        contradictions: List[Dict[str, Any]] = []
        checked_traits = list(current_deltas.keys())
        max_delta_allowed = self.thresholds.contradiction_max_delta

        # 与近期已接受提案比较
        for recent in recent_accepted:
            recent_deltas = _extract_deltas_from_proposal(recent)
            for trait, cur_delta in current_deltas.items():
                if trait not in recent_deltas:
                    continue
                rec_delta = recent_deltas[trait]
                # 方向不同 → 冲突
                if (cur_delta > 0 and rec_delta < 0) or (cur_delta < 0 and rec_delta > 0):
                    # 计算冲突强度
                    conflict_strength = min(abs(cur_delta), abs(rec_delta))
                    if conflict_strength > max_delta_allowed * 0.5:
                        contradictions.append({
                            "trait": trait,
                            "current_delta": round(cur_delta, 4),
                            "recent_delta": round(rec_delta, 4),
                            "recent_proposal_id": recent.id,
                            "strength": round(conflict_strength, 4),
                        })

        passed = len(contradictions) == 0
        # 冲突越多分越低
        if contradictions:
            score = max(0.0, 1.0 - 0.3 * len(contradictions))
            reasons = [
                f"contradiction_trait={c['trait']}:current={c['current_delta']:+.4f} vs recent={c['recent_delta']:+.4f}"
                for c in contradictions
            ]
        else:
            score = 1.0
            reasons = ["no_contradiction_found"]

        return {
            "passed": passed,
            "score": round(score, 4),
            "checked_traits": checked_traits,
            "contradictions": contradictions,
            "reasons": reasons,
        }

    # ============================================================
    # 4. Stability 稳定性检查
    # ============================================================

    def _check_stability(
        self,
        proposal: GrowthProposal,
        recent_accepted: List[GrowthProposal],
    ) -> Dict[str, Any]:
        """
        检查：近期接受的提案中，同一 trait 的变化方向是否稳定。
        如果某 trait 的方向比例超过阈值，则认为不稳定（频繁来回变 = 不稳定）。
        """
        current_deltas: Dict[str, float] = {}
        for ci in proposal.proposed_changes or []:
            trait = _extract_trait_simple(ci.path)
            if trait == "unknown":
                continue
            b = _safe_float(ci.before)
            a = _safe_float(ci.after)
            if a is None or b is None:
                continue
            current_deltas[trait] = a - b

        if not current_deltas:
            # 无明确 trait 变化，用 pattern 的稳定性检查直接通过（走 GrowthRecord）
            return {
                "passed": True,
                "score": 0.8,
                "per_trait": {},
                "reasons": ["no_explicit_trait_changes_skipping_stability"],
            }

        window = max(1, self.thresholds.stability_window)
        min_ratio = self.thresholds.stability_min_ratio

        per_trait: Dict[str, Any] = {}
        all_passed = True
        overall_score_parts: List[float] = []

        for trait, cur_delta in current_deltas.items():
            # 收集近期窗口内的该 trait 的 delta 方向
            directions: List[int] = []
            for recent in recent_accepted[-window:]:
                rd = _extract_deltas_from_proposal(recent).get(trait)
                if rd is None:
                    continue
                directions.append(1 if rd > 0 else (-1 if rd < 0 else 0))
            # 加上当前提案方向
            cur_dir = 1 if cur_delta > 0 else (-1 if cur_delta < 0 else 0)
            if cur_dir == 0:
                per_trait[trait] = {"passed": True, "score": 1.0, "reason": "zero_delta"}
                overall_score_parts.append(1.0)
                continue

            directions.append(cur_dir)

            if len(directions) <= 1:
                # 历史不足，宽松通过
                per_trait[trait] = {"passed": True, "score": 0.9, "reason": "insufficient_history"}
                overall_score_parts.append(0.9)
                continue

            same_as_current = sum(1 for d in directions if d == cur_dir or d == 0)
            ratio = same_as_current / len(directions)

            # 方向一致性 >= min_ratio → 稳定通过
            passed_trait = ratio >= min_ratio
            if not passed_trait:
                all_passed = False

            score_trait = min(1.0, ratio)
            overall_score_parts.append(score_trait)
            per_trait[trait] = {
                "passed": passed_trait,
                "score": round(score_trait, 4),
                "ratio": round(ratio, 4),
                "same_dir_count": same_as_current,
                "total": len(directions),
                "min_required_ratio": min_ratio,
            }

        overall_score = sum(overall_score_parts) / len(overall_score_parts) if overall_score_parts else 1.0
        reasons = []
        if all_passed:
            reasons.append("stability_ok")
        else:
            for trait, info in per_trait.items():
                if not info["passed"]:
                    reasons.append(
                        f"unstable_trait={trait}:ratio={info.get('ratio',0):.2f} < {min_ratio:.2f}"
                    )

        return {
            "passed": all_passed,
            "score": round(overall_score, 4),
            "per_trait": per_trait,
            "window_size": window,
            "reasons": reasons,
        }


# ============================================================
# 辅助函数
# ============================================================

def _extract_trait_simple(path: Any) -> str:
    """简化版 trait 提取。"""
    if not isinstance(path, str):
        return "unknown"
    parts = path.split(".")
    return parts[-1] if parts else "unknown"


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_deltas_from_proposal(proposal: GrowthProposal) -> Dict[str, float]:
    """从一个提案提取 trait → delta"""
    result: Dict[str, float] = {}
    for ci in getattr(proposal, "proposed_changes", []) or []:
        trait = _extract_trait_simple(ci.path)
        if trait == "unknown":
            continue
        b = _safe_float(ci.before)
        a = _safe_float(ci.after)
        if a is None or b is None:
            continue
        result[trait] = a - b
    return result
