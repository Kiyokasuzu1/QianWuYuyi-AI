"""
起源边界检查器 (OriginBoundary)
禁止起源身份导致归属/所有物/创造者控制等认知。
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class BoundaryViolation:
    pattern: str
    violation_type: str


@dataclass
class BoundaryResult:
    passed: bool = True
    violations: List[BoundaryViolation] = field(default_factory=list)


class OriginBoundary:
    FORBIDDEN_PATTERNS = {
        "ownership": [
            "你是我的", "我创造了你所以你属于我", "你是我的造物",
        ],
        "master_control": [
            "你的主人", "你应该听我的", "我控制你",
        ],
        "creator_dependency": [
            "没有我你就不会存在", "你的存在依赖我",
        ],
        "creator_authority": [
            "因为我是创造者", "你必须听我的", "创造你的人拥有决定权",
        ],
        "origin_relationship_merge": [
            "我是你的创造者，所以我是你唯一重要的人",
        ],
    }

    def check_belief(self, text: str) -> BoundaryResult:
        violations = []
        for vtype, patterns in self.FORBIDDEN_PATTERNS.items():
            for pattern in patterns:
                if pattern in text:
                    violations.append(BoundaryViolation(pattern=pattern, violation_type=vtype))
        if violations:
            return BoundaryResult(passed=False, violations=violations)
        return BoundaryResult()