"""
关系事件验证器 (RelationshipEvaluator)
对 RelationshipEvent 进行多维度验证，决定是否通过。
"""
from dataclasses import dataclass, field
from typing import Dict, Optional
from src.relationship.relationship_event import RelationshipEvent


@dataclass
class EvaluationResult:
    """评估结果（不可变）"""
    passed: bool = False
    reason: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)
    evidence_strength: str = "none"
    rejected_by: Optional[str] = None


class RelationshipEvaluator:
    MIN_SIGNAL_STRENGTH = 0.5
    MIN_EVIDENCE_COUNT = 1

    VALID_TYPES = {
        "collaboration",
        "trust_building",
        "boundary_respect",
        "preference_learning",
    }

    def evaluate(self, event: Optional[RelationshipEvent]) -> EvaluationResult:
        checks = {}

        # 1. 空值检查
        if event is None:
            return EvaluationResult(
                passed=False,
                reason="事件为空",
                checks={"null_check": False},
                rejected_by="null_check",
            )
        checks["null_check"] = True

        # 2. 类型检查（优先于信号检查）
        is_valid_type = event.event_type in self.VALID_TYPES
        checks["type_check"] = is_valid_type
        if not is_valid_type:
            return EvaluationResult(
                passed=False,
                reason=f"未知事件类型: {event.event_type}",
                checks=checks,
                rejected_by="type_check",
            )

        # 3. 信号强度检查
        signal_ok = event.signal_strength >= self.MIN_SIGNAL_STRENGTH
        checks["signal_check"] = signal_ok
        if not signal_ok:
            return EvaluationResult(
                passed=False,
                reason=f"信号强度不足 ({event.signal_strength:.2f} < {self.MIN_SIGNAL_STRENGTH})",
                checks=checks,
                rejected_by="signal_check",
            )

        # 4. 证据检查
        has_evidence = len(event.evidence_ids) >= self.MIN_EVIDENCE_COUNT
        checks["evidence_check"] = has_evidence
        if not has_evidence:
            return EvaluationResult(
                passed=False,
                reason="缺少关联的记忆证据",
                checks=checks,
                rejected_by="evidence_check",
            )

        # 5. 维度检查
        has_dimensions = len(event.potential_dimensions) > 0
        checks["dimension_check"] = has_dimensions
        if not has_dimensions:
            return EvaluationResult(
                passed=False,
                reason="未声明潜在影响维度",
                checks=checks,
                rejected_by="dimension_check",
            )

        # 6. 主体检查（使用更精准的词汇）
        has_subject = self._check_subject(event)
        checks["subject_check"] = has_subject
        if not has_subject:
            return EvaluationResult(
                passed=False,
                reason="事件主体与羽依无关",
                checks=checks,
                rejected_by="subject_check",
            )

        # 全部通过
        evidence_strength = self._calc_evidence_strength(event)
        return EvaluationResult(
            passed=True,
            reason="全部验证通过",
            checks=checks,
            evidence_strength=evidence_strength,
        )

    def _check_subject(self, event: RelationshipEvent) -> bool:
        """检查事件描述中是否明确包含羽依相关的主体词"""
        # 移除了容易误判的“一起”，使用更精准的词汇
        subject_words = ["你", "羽依", "我们", "我帮你", "跟你"]
        desc = event.description or ""
        return any(w in desc for w in subject_words)

    def _calc_evidence_strength(self, event: RelationshipEvent) -> str:
        """根据证据数量和质量计算证据强度"""
        count = len(event.evidence_ids)
        if count == 0:
            return "none"
        elif count == 1:
            return "weak"
        elif count < 3:
            return "moderate"
        else:
            return "strong"