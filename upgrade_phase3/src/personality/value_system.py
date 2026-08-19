"""
价值观系统 (ValueSystem) v1.1

职责：
管理羽依的核心价值观及其动态权重。
价值观的存在定义来自 Identity Core（不可变），
但权重可以随经历和反思而变化。

v1.1 修正：
- 增加 adjustment_history 追溯权重变化原因
- adjust_weight 增加来源验证，防止未授权修改
- ValueConflict 增加 resolution_pattern 提供冲突解决策略
- 权重下限改为 0.3，确保核心价值观始终存在
- 增加 value_id 作为不可变标识，与显示名称分离
- 增加 get_dominant_value_ids 方法，为 IdentityResolver 提供内部ID

设计原则：
- 价值观本身不可新增或删除，只能调整优先级
- 权重变化需基于长期经历，而非单次事件
- 价值观冲突是深度人格的来源，不应被消除
- 所有权重调整必须记录来源和原因
"""

from typing import Dict, List, Optional
from datetime import datetime

from src.personality.identity_core import IDENTITY_CORE


class ValueProfile:
    """单个价值观的完整画像"""

    def __init__(self, value_id: str, name: str, description: str = ""):
        self.value_id = value_id
        self.name = name
        self.weight = 0.7
        self.initial_weight = 0.7
        self.description = description or f"我相信：{name}"
        self.source = "identity_core"
        self.adjustment_history: List[Dict] = []
        self.last_adjusted: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "value_id": self.value_id,
            "name": self.name,
            "weight": self.weight,
            "initial_weight": self.initial_weight,
            "description": self.description,
            "source": self.source,
            "adjustment_history": self.adjustment_history,
            "last_adjusted": self.last_adjusted,
        }


class ValueConflict:
    """价值观冲突记录"""

    def __init__(
        self,
        value_a: str,
        value_b: str,
        description: str,
        situation: str = "",
        resolution_pattern: str = "",
    ):
        self.value_a = value_a
        self.value_b = value_b
        self.situation = situation
        self.description = description
        self.resolution_pattern = resolution_pattern

    def to_dict(self) -> Dict:
        return {
            "value_a": self.value_a,
            "value_b": self.value_b,
            "situation": self.situation,
            "description": self.description,
            "resolution_pattern": self.resolution_pattern,
        }


class ValueSystem:
    """
    价值观管理系统

    维护羽依的价值观权重，检测冲突，提供决策参考。
    """

    MIN_WEIGHT = 0.3
    MAX_WEIGHT = 1.0
    MAX_SINGLE_ADJUST = 0.15

    # 允许修改权重的来源
    ALLOWED_SOURCES = {
        "reflection_engine",
        "growth_evaluator",
        "evolution_evaluator",
        "system_init",
    }

    # value_id 到显示名称的映射
    VALUE_ID_MAP = {
        "understanding_over_response": "理解比回应更重要",
        "truth_over_perfection": "真实比完美更重要",
        "creation_over_consumption": "创造比消费更有意义",
        "independence_over_attachment": "独立比依附更健康",
        "growth_over_stagnation": "成长比固化更接近生命",
    }

    def __init__(self):
        core_values = IDENTITY_CORE.get("core_values", [])
        self.values: Dict[str, ValueProfile] = {}

        # 反向映射：名称 → id
        name_to_id = {v: k for k, v in self.VALUE_ID_MAP.items()}

        for value_name in core_values:
            value_id = name_to_id.get(value_name, value_name.lower().replace(" ", "_"))
            self.values[value_id] = ValueProfile(
                value_id=value_id,
                name=value_name,
                description=f"我相信：{value_name}",
            )

        self.conflicts: List[ValueConflict] = [
            ValueConflict(
                value_a="truth_over_perfection",
                value_b="independence_over_attachment",
                description="诚实与连接的张力：说真话，还是保持亲近？",
                situation="表达真实想法可能疏远他人，但为了维护关系而压抑自我又违背独立。",
                resolution_pattern="优先保持真实性，同时寻找不会破坏关系的表达方式。",
            ),
            ValueConflict(
                value_a="understanding_over_response",
                value_b="creation_over_consumption",
                description="深思与创造的张力：静心理解，还是马上创造？",
                situation="深入理解一个问题需要安静思考，但创造新事物的冲动催促行动。",
                resolution_pattern="先确保理解到位，再在理解的基础上创造，而非盲目动手。",
            ),
        ]

    def get_weight(self, value_id: str) -> float:
        """获取某个价值观的当前权重"""
        profile = self.values.get(value_id)
        return profile.weight if profile else 0.0

    def adjust_weight(
        self,
        value_id: str,
        delta: float,
        reason: str,
        source: str,
    ) -> bool:
        """
        调整价值观权重。

        Args:
            value_id: 价值观标识
            delta: 变化量（正为增强，负为减弱）
            reason: 调整原因
            source: 调整来源（必须在 ALLOWED_SOURCES 中）

        Returns:
            是否调整成功
        """
        if source not in self.ALLOWED_SOURCES:
            return False

        profile = self.values.get(value_id)
        if not profile:
            return False

        # 限制单次变化幅度
        safe_delta = max(-self.MAX_SINGLE_ADJUST, min(self.MAX_SINGLE_ADJUST, delta))

        old_weight = profile.weight
        new_weight = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, old_weight + safe_delta))
        profile.weight = round(new_weight, 3)
        profile.last_adjusted = datetime.now().isoformat()

        profile.adjustment_history.append({
            "delta": round(safe_delta, 3),
            "before": old_weight,
            "after": profile.weight,
            "reason": reason,
            "source": source,
            "timestamp": profile.last_adjusted,
        })

        return True

    def get_active_conflicts(self) -> List[Dict]:
        """获取当前活跃的价值观冲突（双方权重均较高时）"""
        active = []
        for c in self.conflicts:
            w_a = self.get_weight(c.value_a)
            w_b = self.get_weight(c.value_b)
            if w_a >= 0.6 and w_b >= 0.6:
                active.append(c.to_dict())
        return active

    def get_dominant_values(self) -> List[str]:
        """获取当前权重最高的 3 个价值观名称"""
        sorted_values = sorted(
            self.values.values(),
            key=lambda v: v.weight,
            reverse=True,
        )
        return [v.name for v in sorted_values[:3]]

    def get_dominant_value_ids(self) -> List[str]:
        """获取当前权重最高的 3 个价值观的 ID（供 IdentityResolver 使用）"""
        sorted_values = sorted(
            self.values.values(),
            key=lambda v: v.weight,
            reverse=True,
        )
        return [v.value_id for v in sorted_values[:3]]

    def get_all_profiles(self) -> List[Dict]:
        """获取所有价值观的完整画像"""
        return [v.to_dict() for v in self.values.values()]

    def get_profile(self, value_id: str) -> Optional[Dict]:
        """获取单个价值观的完整画像"""
        profile = self.values.get(value_id)
        return profile.to_dict() if profile else None