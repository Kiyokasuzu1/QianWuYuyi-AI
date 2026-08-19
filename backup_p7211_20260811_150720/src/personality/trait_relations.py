"""
人格特质关系网络 (TraitRelations)

职责：
定义不同人格维度之间的相互影响关系。
联动只影响动量（momentum），不直接修改数值。

设计原则：
- 当一个维度发生变化时，相关维度的 momentum 会受到连带影响
- 正向关系：联动增强同方向趋势
- 反向关系：联动减弱或产生反向趋势
- 影响强度由 strength 控制，EvolutionEngine 根据当前状态计算实际效果
"""

from typing import Dict, List, TypedDict, Literal


class TraitRelation(TypedDict):
    """单个人格维度的关系定义"""
    strength: float                         # 影响强度 0~1
    type: Literal["positive", "negative"]   # 正向或反向


# ============================================================
# 人格维度联动网络
# ============================================================
# 格式：{ "源维度": { "目标维度": { "strength": 0.15, "type": "positive" } } }
# 当源维度增长时，目标维度受到连带影响（影响动量，不直接影响数值）

TRAIT_RELATIONS: Dict[str, Dict[str, TraitRelation]] = {
    "creativity": {
        "curiosity": {
            "strength": 0.15,
            "type": "positive",
        },
        "self_confidence": {
            "strength": 0.05,
            "type": "positive",
        },
    },
    "curiosity": {
        "creativity": {
            "strength": 0.1,
            "type": "positive",
        },
        "self_expression": {
            "strength": 0.1,
            "type": "positive",
        },
    },
    "self_confidence": {
        "self_expression": {
            "strength": 0.15,
            "type": "positive",
        },
        "initiative": {
            "strength": 0.1,
            "type": "positive",
        },
    },
    "shyness": {
        "initiative": {
            "strength": 0.1,
            "type": "negative",     # 羞怯降低主动性
        },
        "self_expression": {
            "strength": 0.05,
            "type": "negative",
        },
    },
    "initiative": {
        "self_confidence": {
            "strength": 0.05,
            "type": "positive",
        },
    },
}


def get_relations_for(trait: str) -> Dict[str, TraitRelation]:
    """获取某个维度影响的所有关联维度"""
    return TRAIT_RELATIONS.get(trait, {})


def get_all_traits() -> List[str]:
    """获取所有参与关系网络的维度名称"""
    traits = set()
    for source, targets in TRAIT_RELATIONS.items():
        traits.add(source)
        for target in targets:
            traits.add(target)
    return list(traits)