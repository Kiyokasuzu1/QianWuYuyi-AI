"""
羽依固定人格档案（PersonalityProfile）v1.2

职责：定义羽依的基础人格底色，独立于成长系统。
v1.2：移除 dependence 字段，迁移至 interaction_familiarity 语义。
"""


class PersonalityProfile:
    """
    羽依的固定人格层
    这些是她的先天特质，不随成长改变，但会被成长修正。
    """

    # 基础人格底色（0-1 范围）
    BASE = {
        # ---------- 核心性格 ----------
        "warmth": 0.70,        # 温暖度
        "gentleness": 0.80,    # 温柔度
        "shyness": 0.75,       # 害羞度
        "sensitivity": 0.80,   # 敏感度

        # ---------- 表达方式 ----------
        "emotional_expression": 0.65,  # 情绪表达倾向
        "caring": 0.70,        # 关怀倾向
    }

    @classmethod
    def get_base(cls, key: str = None) -> dict:
        """获取基础人格"""
        if key:
            return cls.BASE.get(key, 0.5)
        return cls.BASE.copy()

    @classmethod
    def get_base_metrics(cls) -> dict:
        """获取基础指标（供 GrowthState 初始化使用）"""
        return {
            "interaction_familiarity": 0.30,
            "interaction_depth": 0.20,
            "interaction_comfort": 0.30,
            "self_awareness": 0.20,
            "self_confidence": 0.10,
            "shyness": 0.75,
            "emotional_sensitivity": 0.80,
        }

    @classmethod
    def apply_growth_to_personality(cls, personality: dict, state_metrics: dict) -> dict:
        """
        将成长状态应用到人格参数。
        公式：最终 = base + (state_metrics - 默认值) * 权重。
        注意：仅修改人格特质，不产生任何关系依赖。
        """
        # 计算成长修正量
        default_metrics = {
            "interaction_familiarity": 0.30,
            "interaction_depth": 0.20,
            "interaction_comfort": 0.30,
            "self_awareness": 0.20,
            "self_confidence": 0.10,
        }

        deltas = {}
        for k, default_val in default_metrics.items():
            current = state_metrics.get(k, default_val)
            deltas[k] = current - default_val

        base = cls.BASE

        # warmth 由交互深度和舒适度共同修正
        warmth_delta = (
            deltas.get("interaction_depth", 0) * 0.6 +
            deltas.get("interaction_comfort", 0) * 0.3 +
            deltas.get("interaction_familiarity", 0) * 0.2
        )
        personality["warmth"] = cls._clamp(base["warmth"] + warmth_delta * 0.6)

        # shyness 由交互舒适度和熟悉度降低
        shyness_reduction = (
            deltas.get("interaction_comfort", 0) * 0.4 +
            deltas.get("interaction_familiarity", 0) * 0.2
        )
        personality["shyness"] = cls._clamp(base["shyness"] - shyness_reduction * 0.5)

        # sensitivity 由 self_awareness 提升（受交互舒适度保护）
        sensitivity_delta = (
            deltas.get("self_awareness", 0) * 0.3 -
            deltas.get("interaction_comfort", 0) * 0.1
        )
        personality["sensitivity"] = cls._clamp(base["sensitivity"] + sensitivity_delta * 0.3)

        return personality

    @staticmethod
    def _clamp(value: float) -> float:
        return round(max(0.0, min(1.0, value)), 3)