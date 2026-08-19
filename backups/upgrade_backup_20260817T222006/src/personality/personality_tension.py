"""
人格矛盾检测 (PersonalityTension)

职责：
检测人格中是否存在相互冲突但共存的维度组合，
形成真实的人格张力，而非消除矛盾。

设计原则：
- 矛盾不是需要修复的错误，而是人格复杂性的体现
- 检测基于阈值，当两个冲突维度同时高于阈值时激活
- Tension 信息传递给 Resolver，影响表达倾向
"""

from typing import Dict, List, TypedDict, Optional


class PersonalityTension(TypedDict, total=False):
    """人格矛盾定义"""
    name: str                       # 矛盾名称
    dimensions: List[str]           # 涉及的维度（通常2个）
    intensity: float                # 矛盾强度 0~1
    description: str                # 自然语言描述
    active: bool                    # 当前是否激活
    dimension_values: Dict[str, float]  # 各维度的当前值（用于表达生成）


# ============================================================
# 人格矛盾配置
# ============================================================
# 每个矛盾定义包含：涉及的维度、激活阈值、描述

TENSION_CONFIGS = [
    {
        "name": "social_approach_avoidance",
        "dimensions": ["shyness", "desire_connection"],
        "threshold": 0.6,           # 两个维度都超过此阈值时激活
        "description": "社交趋避冲突：渴望连接但害怕主动",
    },
    {
        "name": "independence_vs_attachment",
        "dimensions": ["independence", "dependence"],
        "threshold": 0.6,
        "description": "独立与依恋的张力：既想独立又不愿分离",
    },
    {
        "name": "expression_vs_reticence",
        "dimensions": ["self_expression", "shyness"],
        "threshold": 0.6,
        "description": "表达与克制的矛盾：有想法但害怕说出来",
    },
]


def detect_tensions(traits: Dict[str, float]) -> List[PersonalityTension]:
    """
    检测当前人格中活跃的矛盾。

    Args:
        traits: 当前人格维度值字典

    Returns:
        活跃的矛盾列表
    """
    active_tensions = []

    for config in TENSION_CONFIGS:
        dims = config["dimensions"]
        threshold = config["threshold"]

        # 获取各维度值
        values = {d: traits.get(d, 0.0) for d in dims}
        if all(v >= threshold for v in values.values()):
            tension: PersonalityTension = {
                "name": config["name"],
                "dimensions": dims,
                "intensity": round(sum(values.values()) / len(values), 3),  # 均值表示整体强度
                "description": config["description"],
                "active": True,
                "dimension_values": values,  # 保存原始维度值供表达层使用
            }
            active_tensions.append(tension)

    return active_tensions


def get_tension_summary(tensions: List[PersonalityTension]) -> str:
    """生成人格矛盾的自然语言摘要"""
    if not tensions:
        return ""
    parts = []
    for t in tensions:
        parts.append(t["description"])
    return "；".join(parts)