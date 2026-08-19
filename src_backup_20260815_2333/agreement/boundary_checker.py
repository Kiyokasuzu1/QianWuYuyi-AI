"""
边界检查器 (BoundaryChecker) — Phase 11.8.1 最终版
检查文本是否违反已建立的不可变约定。
只检查 IMMUTABLE 约定，HIGH 仅进入 Prompt，不参与硬检测。
使用 AgreementRule 进行多模式大小写不敏感匹配。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Callable
from src.agreement.agreement_manager import AgreementManager
from src.agreement.agreement import AgreementPriority
from src.agreement.agreement_rule import get_rule_for_agreement


@dataclass
class BoundaryResult:
    passed: bool = True
    violations: List[str] = field(default_factory=list)
    severity: str = "none"


class BoundaryChecker:
    def __init__(
        self,
        manager: AgreementManager,
        evaluator: Optional[Callable[[str, List[str]], BoundaryResult]] = None,
    ):
        self.manager = manager
        self.evaluator = evaluator

    def check(self, text: str) -> BoundaryResult:
        # 只检查 IMMUTABLE 约定，HIGH 不在此处拦截
        agreements = [
            a for a in self.manager.get_active_agreements()
            if a.priority == AgreementPriority.IMMUTABLE
        ]
        if not agreements:
            return BoundaryResult()

        if self.evaluator:
            return self.evaluator(text, [a.content for a in agreements])

        violations = []
        for agreement in agreements:
            rule = get_rule_for_agreement(agreement.content)
            if rule.matches(text):
                violations.append(agreement.content)

        if not violations:
            return BoundaryResult()

        return BoundaryResult(
            passed=False,
            violations=violations,
            severity="block",
        )