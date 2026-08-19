"""
表达约束 (ExpressionConstraint) v1.0

职责：
定义羽依在当前证据下可以表达什么、不可以表达什么。
这是审核结果与生成系统之间的桥梁。

设计原则：
- 不是“禁止清单”，而是“表达边界”
- 提供可解释的风险等级和来源
- 表达指引而非固定模板
"""

from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ExpressionLevel(Enum):
    """声明强度等级"""
    GENERAL = "general"
    STRONG = "strong"
    ABSOLUTE = "absolute"


@dataclass
class ExpressionConstraint:
    """表达约束"""

    # 是否允许表达该声明
    allowed: bool = True

    # 是否需要进行改写
    rewrite_required: bool = False

    # 允许的最高声明强度
    max_claim_strength: str = ExpressionLevel.GENERAL.value

    # 禁止使用的词汇/模式
    forbidden_patterns: List[str] = field(default_factory=list)

    # 表达指引（非固定模板，供 LLM 参考）
    expression_guidelines: List[str] = field(default_factory=list)

    # 表达风格偏好
    preferred_style: str = "humble"

    # 是否允许提及成长影响
    allow_growth_claim: bool = True

    # 风险等级
    risk_level: str = "low"

    # 审核来源（便于调试）
    source_strength: Optional[str] = None