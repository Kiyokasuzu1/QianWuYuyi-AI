"""
人格特质状态 (TraitState) v1.0

职责：
保存单一人格维度的完整动态状态。
不仅包含当前值，还包含动量方向、稳定性和置信度。

设计原则：
- momentum 是趋势强度（0~1），不是速度（-1~1）
- direction 单独保存：increase / stable / decrease
- stability 随验证次数提高，不被单次事件扰动
- confidence（元属性）表示该维度已被多少次经历验证，
  与人格维度 self_confidence（自信程度）区分
"""

from typing import TypedDict, Literal, Optional, Dict, List


class TraitState(TypedDict, total=False):
    """单一人格维度的完整动态状态"""

    # ---- 核心 ----
    trait: str                          # 维度名称（如 "creativity"）
    current_value: float                # 当前值 0~1

    # ---- 动态 ----
    momentum: float                     # 趋势强度 0~1，值越高表示近期变化越活跃
    direction: Literal["increase", "stable", "decrease"]  # 趋势方向
    stability: float                    # 抗扰动能力 0~1，值越高越稳定
    confidence: float                   # 确信程度 0~1，值越高表示该维度已被多次验证（元属性）

    # ---- 追踪 ----
    last_growth_direction: str          # 最近一次变化方向
    last_updated: str                   # 最后更新时间（ISO格式）
    consecutive_same_direction: int     # 连续同方向变化次数


def create_trait_state(trait: str, value: float) -> TraitState:
    """创建初始 TraitState（无历史记录时的默认状态）"""
    return {
        "trait": trait,
        "current_value": value,
        "momentum": 0.1,
        "direction": "stable",
        "stability": 0.3,
        "confidence": 0.1,
        "last_growth_direction": "stable",
        "last_updated": "",
        "consecutive_same_direction": 0,
    }


# ============================================================
# 人格维度联动关系
# ============================================================
# 当一个维度变化时，相关维度会受到轻微连带影响。
# 注意：这只是趋势联动，不是直接修改数值。
# 实际影响由 EvolutionEngine 根据 momentum 和 stability 计算。
# confidence 已改名为 self_confidence，避免与 TraitState.confidence 混淆

TRAIT_RELATIONS: Dict[str, Dict[str, float]] = {
    "creativity": {
        "curiosity": 0.15,
        "self_confidence": 0.05,
    },
    "curiosity": {
        "creativity": 0.1,
        "self_expression": 0.1,
    },
    "self_confidence": {
        "self_expression": 0.15,
        "initiative": 0.1,
    },
    "shyness": {
        # 羞怯与其他维度多为反向关系，在 Tensions 中处理
    },
}


# ============================================================
# 人格矛盾定义
# ============================================================
# 允许矛盾的维度同时存在，形成真实的人格张力。
# 例如：shyness=0.8 且 desire_connection=0.9
# 形成“想靠近别人，但害怕主动”的真实感。

PERSONALITY_TENSIONS = {
    "social_approach_avoidance": {
        "dimensions": ["shyness", "desire_connection"],
        "description": "社交趋避冲突：渴望连接但害怕主动",
    },
    "independence_vs_attachment": {
        "dimensions": ["independence", "dependence"],
        "description": "独立与依恋的张力：既想独立又不愿分离",
    },
}