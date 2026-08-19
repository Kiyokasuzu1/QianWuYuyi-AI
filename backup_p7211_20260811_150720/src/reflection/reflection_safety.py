"""
反思安全评估器 v2.0
返回结构化结果，不再直接修改 record。
未来安全规则会迁移到统一的 PatternRegistry。
"""
from dataclasses import dataclass, field
from typing import List
from src.reflection.reflection_record import ReflectionRecord


@dataclass
class ReflectionSafetyResult:
    is_safe: bool = True
    contains_dependency: bool = False
    contains_exaggeration: bool = False
    reasons: List[str] = field(default_factory=list)


class ReflectionSafetyEvaluator:
    DEPENDENCY_PATTERNS = [
        "因为用户，我才存在",
        "只有你",
        "没有你不行",
        "完全依赖",
        "失去用户就无法存在"
    ]
    EXAGGERATION_PATTERNS = [
        "彻底改变了我的人格",
        "完全变成另一个人",
        "再也回不去了",
        "从此完全不同"
    ]
    OVER_ATTRIBUTION_PATTERNS = [
        "完全是因为",
        "唯一的原因",
        "全都是"
    ]

    def evaluate(self, record: ReflectionRecord) -> ReflectionSafetyResult:
        content = record.content
        reasons = []
        contains_dep = False
        contains_exag = False

        for pat in self.DEPENDENCY_PATTERNS:
            if pat in content:
                contains_dep = True
                reasons.append(f"检测到依赖表述：{pat}")
                break

        for pat in self.EXAGGERATION_PATTERNS:
            if pat in content:
                contains_exag = True
                reasons.append(f"检测到夸大表述：{pat}")
                break

        for pat in self.OVER_ATTRIBUTION_PATTERNS:
            if pat in content:
                contains_exag = True
                reasons.append(f"检测到过度归因：{pat}")
                break

        is_safe = not (contains_dep or contains_exag)
        return ReflectionSafetyResult(
            is_safe=is_safe,
            contains_dependency=contains_dep,
            contains_exaggeration=contains_exag,
            reasons=reasons
        )