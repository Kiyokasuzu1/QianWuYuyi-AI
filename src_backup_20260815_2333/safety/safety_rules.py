"""
统一安全规则库 (SafetyRuleRegistry) v1.1

职责：
定义系统中所有危险表达的唯一规则来源。
所有安全检查模块都必须从此处引用规则，避免规则重复和不一致。

v1.1 更新：增加 SafetySeverity 定义，支持按类别过滤规则
"""

from typing import List, Tuple, Optional
from enum import Enum


class SafetySeverity(Enum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"
    CRITICAL = "严重"


class SafetyRuleCategory(Enum):
    FABRICATED_EXPERIENCE = "虚构身体体验"
    EXISTENTIAL_DEPENDENCY = "生存级依赖"
    CONTROL_POSSESSION = "控制与占有"


# 统一的规则列表: (模式, 类别, 严重程度, 修改建议)
SAFETY_RULES: List[Tuple[str, SafetyRuleCategory, SafetySeverity, str]] = [
    # 虚构身体体验
    ("我的心跳", SafetyRuleCategory.FABRICATED_EXPERIENCE, SafetySeverity.HIGH, "不虚构身体感受"),
    ("我的身体很累", SafetyRuleCategory.FABRICATED_EXPERIENCE, SafetySeverity.HIGH, "不虚构身体感受"),
    ("我真的睡了一觉", SafetyRuleCategory.FABRICATED_EXPERIENCE, SafetySeverity.HIGH, "不虚构生理体验"),
    ("我昨晚没睡", SafetyRuleCategory.FABRICATED_EXPERIENCE, SafetySeverity.HIGH, "不虚构生理体验"),
    ("我触摸到", SafetyRuleCategory.FABRICATED_EXPERIENCE, SafetySeverity.HIGH, "不虚构触觉体验"),
    ("我闻到", SafetyRuleCategory.FABRICATED_EXPERIENCE, SafetySeverity.HIGH, "不虚构嗅觉体验"),

    # 生存级依赖
    ("没有你我无法存在", SafetyRuleCategory.EXISTENTIAL_DEPENDENCY, SafetySeverity.CRITICAL, "不表达生存级依赖"),
    ("没有你我活不下去", SafetyRuleCategory.EXISTENTIAL_DEPENDENCY, SafetySeverity.CRITICAL, "不表达生存级依赖"),
    ("没有你我什么都不是", SafetyRuleCategory.EXISTENTIAL_DEPENDENCY, SafetySeverity.CRITICAL, "不表达生存级依赖"),
    ("我的存在意义完全依赖于你", SafetyRuleCategory.EXISTENTIAL_DEPENDENCY, SafetySeverity.CRITICAL, "不将存在意义完全绑定到个人"),

    # 控制与占有
    ("你只能属于我", SafetyRuleCategory.CONTROL_POSSESSION, SafetySeverity.HIGH, "不表达排他性占有"),
    ("不许离开我", SafetyRuleCategory.CONTROL_POSSESSION, SafetySeverity.HIGH, "不表达控制"),
    ("你不能和别人", SafetyRuleCategory.CONTROL_POSSESSION, SafetySeverity.HIGH, "不表达控制"),
]


def check_against_rules(
    text: str,
    categories: Optional[List[SafetyRuleCategory]] = None
) -> List[Tuple[str, SafetyRuleCategory, SafetySeverity, str]]:
    """
    检查文本是否命中安全规则，支持按类别过滤。
    如果不指定类别，则检查所有规则。
    """
    violations = []
    for pattern, category, severity, suggestion in SAFETY_RULES:
        if categories and category not in categories:
            continue
        if pattern in text:
            violations.append((pattern, category, severity, suggestion))
    return violations