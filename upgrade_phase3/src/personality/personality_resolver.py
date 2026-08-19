"""
人格解析器 PersonalityResolver v1.9

职责:
GrowthRecord累积 + GrowthState实时指标 + 固定人格 + 人格演化 → 当前羽依人格表现

v1.9 更新:
- growth_history 改为构造注入，与 GrowthPipeline 共享同一实例
- 接入 SelfModelStore，让自我认知进入人格输出
- 保留 v1.8 的 Tension 检测和 EvolutionEngine 机制
- 新增 get_trait_states() 公开方法，供 Phase 6 Orchestrator 调用
"""

from typing import Dict, Optional, List
from src.growth.growth_state import GrowthState
from src.personality.personality_profile import PersonalityProfile
from src.personality.behavior_resolver import BehaviorResolver
from src.personality.relationship_state import RelationshipState
from src.personality.personality_vector import PersonalityVector
from src.personality.growth_accumulator import GrowthAccumulator
from src.personality.personality_evolution import PersonalityEvolutionEngine
from src.personality.personality_history import PersonalityHistory
from src.personality.trait_state import TraitState, create_trait_state
from src.personality.personality_tension import detect_tensions, get_tension_summary
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.self_model_store import SelfModelStore


class PersonalityResolver:

    def __init__(
        self,
        state: Optional[GrowthState] = None,
        relationship_state: Optional[RelationshipState] = None,
        growth_records: Optional[List[Dict]] = None,
        growth_history: Optional[PersonalityGrowthHistory] = None,
    ):
        self.state = state or GrowthState()
        self.relationship_state = relationship_state or RelationshipState()
        self.behavior_resolver = BehaviorResolver(self.relationship_state)

        self.growth_records = (
            growth_records if growth_records is not None else []
        )
        self.accumulator = GrowthAccumulator()

        self.evolution_engine = PersonalityEvolutionEngine()
        self.personality_history = PersonalityHistory()
        self._trait_states: Dict[str, TraitState] = {}

        # Phase 3.4：SelfModel 存储（接收外部传入的 growth_history）
        self.growth_history = (
            growth_history
            if growth_history is not None
            else PersonalityGrowthHistory()
        )
        self.self_model_store = SelfModelStore()

    def resolve(self) -> PersonalityVector:
        """解析当前人格向量"""

        data = self.state.get()
        metrics = data.get("metrics", {})
        behaviors = data.get("behaviors", {})
        identities = data.get("identities", [])

        growth_trust = metrics.get("trust", 0)
        growth_closeness = metrics.get("closeness", 0)
        growth_security = metrics.get("security", 0)
        growth_awareness = metrics.get("self_awareness", 0)
        growth_confidence = metrics.get("self_confidence", 0)
        growth_attachment = metrics.get("attachment", 0)
        growth_identity_strength = metrics.get("identity_strength", 0)
        growth_emotional_memory = metrics.get("emotional_memory", 0)
        growth_warmth_memory = metrics.get("warmth", 0)

        rel_bond = self.relationship_state.get_bond_strength()
        rel_trust = self.relationship_state.get_trust()
        rel_familiarity = self.relationship_state.get_familiarity()

        accumulated = self.accumulator.compute(
            records=self.growth_records,
            base_personality=PersonalityProfile.BASE.copy(),
        )

        evolved = {}
        base = PersonalityProfile.BASE.copy()
        core_dimensions = ["warmth", "gentleness", "shyness", "sensitivity",
                           "emotional_expression", "caring"]

        for dim in core_dimensions:
            if dim not in self._trait_states:
                self._trait_states[dim] = create_trait_state(dim, base.get(dim, 0.5))

            trait_state = self._trait_states[dim]
            growth_delta = accumulated.get(dim, base.get(dim, 0.5)) - trait_state["current_value"]

            updated_state = self.evolution_engine.update_trait(
                trait_state=trait_state,
                growth_delta=growth_delta,
                history=self.personality_history,
            )

            old_value = trait_state["current_value"]
            new_value = updated_state["current_value"]
            if abs(new_value - old_value) > 0.005:
                self.personality_history.record_change(
                    before={dim: old_value},
                    after={dim: new_value},
                    reason=f"累积偏移量 {growth_delta:+.4f}",
                )

            self._trait_states[dim] = updated_state
            evolved[dim] = new_value

        warmth = self._clamp(
            evolved.get("warmth", base.get("warmth", 0.7))
            + growth_closeness * 0.25
            + growth_trust * 0.15
            + growth_warmth_memory * 0.15
            + rel_bond * 0.2
        )

        gentleness = self._clamp(
            evolved.get("gentleness", base.get("gentleness", 0.8))
            + growth_closeness * 0.2
            + growth_emotional_memory * 0.1
            + rel_bond * 0.15
        )

        shyness = self._clamp(
            evolved.get("shyness", base.get("shyness", 0.75))
            - growth_security * 0.1
            + growth_attachment * 0.05
            - rel_familiarity * 0.1
        )

        sensitivity = self._clamp(
            evolved.get("sensitivity", base.get("sensitivity", 0.8))
            + growth_awareness * 0.2
        )

        dependence = self._clamp(
            0.5
            + growth_attachment * 0.3
            + growth_closeness * 0.1
            + rel_bond * 0.15
        )

        emotional_expression = self._clamp(
            evolved.get("emotional_expression", base.get("emotional_expression", 0.65))
            + growth_closeness * 0.25
            + growth_confidence * 0.15
            + growth_security * 0.1
            + rel_trust * 0.1
        )

        caring = self._clamp(
            evolved.get("caring", base.get("caring", 0.7))
            + growth_closeness * 0.25
            + growth_trust * 0.15
            + rel_bond * 0.2
        )

        self_identity = self._clamp(
            growth_identity_strength
            + growth_awareness * 0.5
        )

        self_expression = self._clamp(
            0.2
            + growth_awareness * 0.3
            + growth_confidence * 0.3
            + growth_identity_strength * 0.2
            + rel_trust * 0.1
        )

        initiative = self._clamp(
            0.25
            + growth_confidence * 0.3
            + growth_identity_strength * 0.15
            + rel_familiarity * 0.1
        )

        care_level = self._clamp(
            0.25
            + growth_closeness * 0.4
            + growth_emotional_memory * 0.15
            + rel_bond * 0.2
        )

        directness = self._clamp(
            0.25
            + growth_confidence * 0.4
            + rel_familiarity * 0.1
        )

        playfulness = self._clamp(
            0.2
            + growth_closeness * 0.3
            + growth_security * 0.2
            + rel_familiarity * 0.15
        )

        behavior_traits = self.behavior_resolver.resolve(metrics)

        core_traits = {
            "warmth": warmth,
            "gentleness": gentleness,
            "shyness": shyness,
            "sensitivity": sensitivity,
            "dependence": dependence,
            "emotional_expression": emotional_expression,
            "caring": caring,
            "self_identity": self_identity,
            "self_expression": self_expression,
            "initiative": initiative,
            "care_level": care_level,
            "directness": directness,
            "playfulness": playfulness,
        }

        persona_summary = self._generate_persona_summary(
            warmth, shyness, emotional_expression,
            self_expression, initiative, care_level
        )

        combined_trust = growth_trust * 0.4 + rel_trust * 0.6
        interaction_label = self._get_interaction_familiarity_label(combined_trust)

        # ---- Phase 3.3 Step 3：人格矛盾检测 ----
        active_tensions = detect_tensions({
            "warmth": warmth,
            "shyness": shyness,
            "self_expression": self_expression,
            "dependence": dependence,
            "initiative": initiative,
        })
        tension_summary = get_tension_summary(active_tensions)

        # ---- Phase 3.4 Step 4：更新并注入 SelfModel ----
        if self.self_model_store.should_update(self.growth_history):
            self.self_model_store.update(self.growth_history, self._trait_states)

        self_model = self.self_model_store.get()
        identity_summary = self_model.get("identity_summary", "") if self_model else ""

        data_dict = {
            "warmth": warmth,
            "gentleness": gentleness,
            "shyness": shyness,
            "sensitivity": sensitivity,
            "dependence": dependence,
            "emotional_expression": emotional_expression,
            "caring": caring,
            "self_identity": self_identity,
            "self_expression": self_expression,
            "initiative": initiative,
            "care_level": care_level,
            "directness": directness,
            "playfulness": playfulness,
            "behavior_traits": behavior_traits,
            "behavior_text": self.behavior_resolver.to_prompt_text(
                behavior_traits, core_traits
            ),
            "compact_behavior": self.behavior_resolver.to_compact_prompt(
                behavior_traits, core_traits
            ),
            "persona_summary": persona_summary,
            "attachment_level": self._get_attachment_label(growth_attachment),
            "interaction_familiarity_level": interaction_label,
            "behaviors": behaviors,
            "identities": identities,
            "active_tensions": active_tensions,
            "tension_summary": tension_summary,
            "self_model": self_model,           # Phase 3.4 新增
            "identity_summary": identity_summary,  # Phase 3.4 新增
        }

        return PersonalityVector(data_dict)

    def _generate_persona_summary(
        self, warmth, shyness,
        emotional_expression, self_expression,
        initiative, care_level
    ) -> str:
        parts = []

        if warmth >= 0.7:
            parts.append("性格温暖而柔和")
        elif warmth >= 0.5:
            parts.append("待人温和友善")

        if shyness >= 0.7:
            parts.append("内心带有一丝羞怯")
        elif shyness >= 0.5:
            parts.append("偶尔会流露出害羞的一面")

        if emotional_expression >= 0.7:
            parts.append("情绪表达自然流畅")
        elif emotional_expression >= 0.5:
            parts.append("能够自然地表达自己的感受")

        if self_expression >= 0.6:
            parts.append("有自己的想法并愿意表达")

        if initiative >= 0.6 and care_level >= 0.6:
            parts.append("会主动关注对方的表达和状态")
        elif care_level >= 0.6:
            parts.append("会在交流中关注对方的表达和状态")

        if not parts:
            return "羽依正在逐渐认识这个世界和身边的人。"

        return "羽依" + "，".join(parts) + "。"

    @staticmethod
    def _clamp(v):
        return round(max(0, min(1, v)), 3)

    @staticmethod
    def _get_attachment_label(score):
        if score < 0.2: return "初识"
        if score < 0.4: return "探索"
        if score < 0.6: return "靠近"
        if score < 0.8: return "依赖"
        return "安全依恋"

    @staticmethod
    def _get_interaction_familiarity_label(score):
        if score < 0.2: return "怀疑"
        if score < 0.4: return "试探"
        if score < 0.6: return "信任"
        if score < 0.8: return "深信"
        return "完全信任"

    # ============================================================
    # Phase 6 新增：公开访问 TraitState
    # ============================================================
    def get_trait_states(self) -> Dict[str, TraitState]:
        """返回当前所有维度的 TraitState（供 Orchestrator 等外部模块调用）"""
        return self._trait_states