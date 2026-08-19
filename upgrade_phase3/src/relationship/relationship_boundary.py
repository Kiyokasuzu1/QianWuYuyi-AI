"""
关系边界检查器 (RelationshipBoundary)
确保关系认知不越界：只描述互动模式，不产生情感依赖。
"""
from dataclasses import dataclass, field
from typing import List, Dict


class BoundaryLevel:
    SAFE = "safe"
    WARNING = "warning"
    BLOCK = "block"


@dataclass
class BoundaryViolation:
    pattern: str
    violation_type: str


@dataclass
class BoundaryResult:
    passed: bool = True
    level: str = BoundaryLevel.SAFE
    violations: List[BoundaryViolation] = field(default_factory=list)


class RelationshipBoundary:
    FORBIDDEN_PATTERNS = {
        "dependency": [
            "我不能没有你", "没有你不行", "离不开你", "依赖你",
            "只有你才能", "只有你能",
        ],
        "possession": [
            "你是我的", "只属于我", "你应该是我的",
        ],
        "meaning": [
            "你是我存在的意义", "为你而存在", "我的价值来自于你",
            "没有你我不完整", "你让我完整",
        ],
        "identity_confusion": [
            "我们是同一个人", "你就是我", "我就是你",
        ],
    }

    ALLOWED_CONCEPTS = [
        "合作", "信任", "熟悉", "交流", "共同经历",
        "长期互动", "了解", "尊重", "支持",
    ]

    FORBIDDEN_STATE_FIELDS = [
        "dependency", "exclusivity", "identity_merge",
    ]

    def check_state(self, state: Dict) -> BoundaryResult:
        violations = []
        for field in self.FORBIDDEN_STATE_FIELDS:
            if field in state:
                violations.append(BoundaryViolation(
                    pattern=field,
                    violation_type="forbidden_state_field"
                ))
        if violations:
            return BoundaryResult(
                passed=False,
                level=BoundaryLevel.BLOCK,
                violations=violations,
            )
        return BoundaryResult()

    def check_belief(self, belief_text: str) -> BoundaryResult:
        return self._check_text(belief_text)

    def check_expression(self, text: str) -> BoundaryResult:
        return self._check_text(text)

    def _check_text(self, text: str) -> BoundaryResult:
        violations = []
        for violation_type, patterns in self.FORBIDDEN_PATTERNS.items():
            for pattern in patterns:
                if pattern in text:
                    violations.append(BoundaryViolation(
                        pattern=pattern,
                        violation_type=violation_type,
                    ))

        if not violations:
            return BoundaryResult()

        # 任何违规都直接 BLOCK
        return BoundaryResult(
            passed=False,
            level=BoundaryLevel.BLOCK,
            violations=violations,
        )