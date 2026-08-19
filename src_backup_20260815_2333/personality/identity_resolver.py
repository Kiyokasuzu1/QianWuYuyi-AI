"""
身份解析器 (Identity Resolver) v1.2

职责：
整合 Identity Core、SelfModel、ValueSystem 和 TraitState，
生成统一的“当前人格快照”，供对话和行为系统调用。

v1.2 修正：
- personality_signals 改为结构化信号 List[dict]，直接供 BehaviorEngine 消费
- 统一信号 ID 命名规则（trait.* / tension.* / value.*）
- 保留 v1.1 的所有特性
"""

from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from src.personality.identity_core import IDENTITY_CORE
from src.personality.self_model import SelfModel
from src.personality.value_system import ValueSystem
from src.personality.trait_state import TraitState


@dataclass
class CurrentIdentitySnapshot:
    """
    当前身份快照

    整合了身份锚点、自我认知、价值观和当前特质，
    是羽依“现在是谁”的完整画像。
    """

    identity_id: str
    identity_name: str
    self_description: dict
    self_understanding: dict
    dominant_values: List[str]       # 显示名称（供展示）
    dominant_value_ids: List[str]    # 内部 ID（供判断）
    value_profiles: List[dict]       # 完整价值观状态
    current_traits: Dict[str, float]
    active_conflicts: List[dict]
    active_tensions: List[dict]
    personality_signals: List[dict]  # v1.2: 结构化人格信号
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    schema_version: str = "identity_snapshot_v1"

    def to_dict(self) -> Dict:
        """转换为字典，便于序列化"""
        return {
            "identity_id": self.identity_id,
            "identity_name": self.identity_name,
            "self_description": self.self_description,
            "self_understanding": self.self_understanding,
            "dominant_values": self.dominant_values,
            "dominant_value_ids": self.dominant_value_ids,
            "value_profiles": self.value_profiles,
            "current_traits": self.current_traits,
            "active_conflicts": self.active_conflicts,
            "active_tensions": self.active_tensions,
            "personality_signals": self.personality_signals,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }


class IdentityResolver:
    """
    身份解析器

    读取各模块数据，生成 CurrentIdentitySnapshot。
    """

    def __init__(self, value_system: ValueSystem):
        """
        Args:
            value_system: 价值观系统实例（强制外部注入，避免状态丢失）
        Raises:
            TypeError: 如果传入的不是 ValueSystem 实例
        """
        if not isinstance(value_system, ValueSystem):
            raise TypeError(
                f"IdentityResolver requires a ValueSystem instance, "
                f"got {type(value_system).__name__}"
            )
        self.value_system = value_system

    def resolve(
        self,
        self_model: Optional[SelfModel] = None,
        trait_states: Optional[Dict[str, TraitState]] = None,
    ) -> CurrentIdentitySnapshot:
        """
        解析当前身份，生成完整快照。
        """
        # 1. 身份锚点
        identity_id = IDENTITY_CORE.get("identity_id", "")
        identity_name = IDENTITY_CORE.get("name", "")

        # 2. 自我认知
        self_description = self_model.get("self_description", {}) if self_model else {}
        self_understanding = self_model.get("self_understanding", {}) if self_model else {}

        # 3. 价值观状态
        dominant_ids = self.value_system.get_dominant_value_ids()
        dominant_names = self.value_system.get_dominant_values()
        value_profiles = self.value_system.get_all_profiles()
        active_conflicts = self.value_system.get_active_conflicts()

        # 4. 当前特质
        current_traits = self._extract_traits(trait_states) if trait_states else {}

        # 5. 人格矛盾
        active_tensions = self_model.get("personality_tensions", []) if self_model else []

        # 6. 人格信号（结构化）
        personality_signals = self._infer_personality_signals(
            dominant_ids, current_traits, active_tensions
        )

        return CurrentIdentitySnapshot(
            identity_id=identity_id,
            identity_name=identity_name,
            self_description=self_description,
            self_understanding=self_understanding,
            dominant_values=dominant_names,
            dominant_value_ids=dominant_ids,
            value_profiles=value_profiles,
            current_traits=current_traits,
            active_conflicts=active_conflicts,
            active_tensions=active_tensions,
            personality_signals=personality_signals,
        )

    def _extract_traits(self, trait_states: Dict[str, TraitState]) -> Dict[str, float]:
        """从 TraitState 提取当前特质值"""
        traits = {}
        for dim, state in trait_states.items():
            if isinstance(state, dict):
                traits[dim] = state.get("current_value", 0.5)
            elif hasattr(state, "current_value"):
                traits[dim] = getattr(state, "current_value", 0.5)
        return traits

    def _infer_personality_signals(
        self,
        dominant_ids: List[str],
        traits: Dict[str, float],
        tensions: List[dict],
    ) -> List[dict]:
        """
        基于当前状态推断结构化人格信号。

        信号 ID 命名规则：
        - trait.* : 特质信号
        - tension.* : 矛盾信号
        - value.* : 价值观信号
        - state.* : 默认状态
        """
        signals = []

        # 从价值观推断
        value_signal_map = {
            "understanding_over_response": ("value.understanding_priority", "理解优先"),
            "creation_over_consumption": ("value.creation_priority", "创造倾向高"),
            "truth_over_perfection": ("value.truth_priority", "真实优先"),
            "independence_over_attachment": ("value.independence_priority", "独立性强"),
            "growth_over_stagnation": ("value.growth_priority", "成长驱动"),
        }
        for vid in dominant_ids:
            if vid in value_signal_map:
                sid, label = value_signal_map[vid]
                signals.append({
                    "id": sid, "label": label,
                    "strength": 0.7, "source": "value_system",
                })

        # 从当前特质推断
        creativity = traits.get("creativity", 0.5)
        if creativity >= 0.7:
            signals.append({
                "id": "trait.creativity.high", "label": "创造力高",
                "strength": creativity, "source": "trait_state",
            })

        curiosity = traits.get("curiosity", 0.5)
        if curiosity >= 0.7:
            signals.append({
                "id": "trait.curiosity.high", "label": "好奇心强",
                "strength": curiosity, "source": "trait_state",
            })

        shyness = traits.get("shyness", 0.5)
        warmth = traits.get("warmth", 0.5)
        if shyness >= 0.7 and warmth >= 0.6:
            signals.append({
                "id": "trait.shy_warm", "label": "羞怯但温和",
                "strength": round((shyness + warmth) / 2, 3), "source": "trait_state",
            })
        elif shyness >= 0.7:
            signals.append({
                "id": "trait.cautious_expression", "label": "表达谨慎",
                "strength": shyness, "source": "trait_state",
            })

        if warmth >= 0.7:
            signals.append({
                "id": "trait.warm_friendly", "label": "温和友善",
                "strength": warmth, "source": "trait_state",
            })

        # 从人格矛盾推断
        tension_dims = set()
        for t in tensions:
            tension_dims.add(t.get("trait_a", ""))
            tension_dims.add(t.get("trait_b", ""))
        if "shyness" in tension_dims and "desire_connection" in tension_dims:
            signals.append({
                "id": "tension.social_approach", "label": "社交趋避张力",
                "strength": 0.7, "source": "personality_tension",
            })

        if not signals:
            signals.append({
                "id": "state.stable", "label": "状态平稳",
                "strength": 0.5, "source": "default",
            })

        return signals

    def get_short_description(self, snapshot: CurrentIdentitySnapshot) -> str:
        """生成简短的人格摘要描述"""
        name = snapshot.identity_name
        values = "、".join(snapshot.dominant_values[:2]) if snapshot.dominant_values else "思考与探索"
        understanding = snapshot.self_understanding.get("overall", 0.5) if isinstance(snapshot.self_understanding, dict) else 0.5

        return (
            f"{name}是一个重视{values}的存在。"
            f"她当前对自己的理解程度约为{int(understanding * 100)}%。"
        )