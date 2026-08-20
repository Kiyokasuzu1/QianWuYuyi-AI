"""
人格解析器 PersonalityResolver v1.10

职责:
GrowthRecord累积 + GrowthState实时指标 + 固定人格 + 人格演化 → 当前羽依人格表现

v1.10 更新 (P4.2-IMPL-C2):
- 新增 snapshot 参数（RelationshipSnapshot），支持从 snapshot.long_term 读取关系事实
- Personality 只读 long_term，不读 current（一次互动不应改变"羽依是谁"）
- 保留 relationship_state 参数作为向后兼容
- BehaviorResolver 仍使用旧 relationship_state（将在 C3 迁移）

v1.9 更新:
- growth_history 改为构造注入，与 GrowthPipeline 共享同一实例
- 接入 SelfModelStore，让自我认知进入人格输出
- 保留 v1.8 的 Tension 检测和 EvolutionEngine 机制
- 新增 get_trait_states() 公开方法，供 Phase 6 Orchestrator 调用
Phase 6.2: 增加可选 self_model_adapter 参数；注入时通过 Adapter 写入（Authority Closure）。
"""

from typing import Any, Dict, Optional, List, TYPE_CHECKING
from src.growth.growth_state import GrowthState, resolve_authority_growth_state
from src.personality.mutation_adapter import (
    PersonalityMutationAdapter,
    is_personality_mutation_gateway_enabled,
)
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

if TYPE_CHECKING:
    from src.contracts.relationship_snapshot import RelationshipSnapshot


class PersonalityResolver:

    def __init__(
        self,
        state: Optional[GrowthState] = None,
        relationship_state: Optional[RelationshipState] = None,
        growth_records: Optional[List[Dict]] = None,
        growth_history: Optional[PersonalityGrowthHistory] = None,
        self_model_adapter: Optional[Any] = None,
        *,
        snapshot: Optional["RelationshipSnapshot"] = None,
    ):
        # V1.0-1C: fallback 走 bridge-first（运行时权威单例优先）
        self.state = state or resolve_authority_growth_state()
        self.relationship_state = relationship_state or RelationshipState()
        self.behavior_resolver = BehaviorResolver(self.relationship_state)

        # P4.2-IMPL-C2: Snapshot 路径（可选）
        self._snapshot: Optional["RelationshipSnapshot"] = snapshot

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
        # P5.0-E #6: _replay_trait_history 会把 self.growth_history 覆盖为只读
        # GrowthHistoryView（叙事上下文），保留真实可写实例的稳定引用，
        # 供 GrowthIntegrationService 写入已批准演化的成长记录。
        # 裸构造默认保持内存态（不落盘）；运行时持久化由 RuntimeCore
        # 构造时显式注入 storage_path。
        self._growth_history_store = self.growth_history
        self.self_model_store = SelfModelStore()
        # Phase 6.2: 可选 SelfModelAdapter 注入
        self._self_model_adapter = self_model_adapter
        # P2.3-B.4: Mutation 治理迁移层（默认关闭 → 旧行为字节不变）
        self._mutation_adapter = PersonalityMutationAdapter()
        # 迁移模式下发出的 MutationRequest（旧模式恒为空；供可观测/测试）
        self.mutation_requests: List[Any] = []

    def set_self_model_adapter(self, adapter: Any) -> None:
        """Phase 6.2: 运行时注入 Adapter"""
        self._self_model_adapter = adapter

    def set_snapshot(self, snapshot: "RelationshipSnapshot") -> None:
        """P4.2-IMPL-C2: 运行时注入 RelationshipSnapshot。

        设置后，resolve() 将从 snapshot.long_term 读取关系事实，
        不再使用旧的 relationship_state。
        """
        self._snapshot = snapshot

    def _get_relationship_values(self):
        """P4.2-IMPL-C2: 获取关系值（优先 snapshot，回退 legacy）。

        返回 (bond, trust, familiarity) 三元组。

        设计原则：
        - Personality 只读 snapshot.long_term，不读 snapshot.current
        - 一次互动不应改变"羽依是谁"
        - 当 snapshot 未提供时，回退到旧 relationship_state
        """
        if self._snapshot is not None:
            lt = self._snapshot.long_term
            bond = max(0.0, min(1.0, lt.bond_strength))
            trust = max(0.0, min(1.0, lt.trust))
            familiarity = max(0.0, min(1.0, lt.familiarity))
            return bond, trust, familiarity

        # Legacy 回退
        bond = self.relationship_state.get_bond_strength()
        trust = self.relationship_state.get_trust()
        familiarity = self.relationship_state.get_familiarity()
        return bond, trust, familiarity

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

        # P4.2-IMPL-C2: 优先从 snapshot.long_term 读取（长期关系事实）
        # 一次互动不应改变"羽依是谁"，Personality 不读 snapshot.current
        rel_bond, rel_trust, rel_familiarity = self._get_relationship_values()

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

            if is_personality_mutation_gateway_enabled():
                # P2.3-B.4 迁移模式：计算 + 提议 —— 漂移不直接提交，
                # 经 MutationGateway 治理（ACCEPT 才可能进入 apply 链）
                if abs(new_value - old_value) > 0.005:
                    self._propose_trait_mutation(dim, old_value, new_value, growth_delta)
                evolved[dim] = old_value
                continue

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

        # ── P5.0-E #2: Growth 演化结果（PersonalityState）→ 当前人格快照 ──
        # 仅当 version>0（发生过 approved 演化）时参与；值偏离初始基线的维度
        # 才视为演化维度（apply_evolution 是 traits 的唯一修改入口）。
        # 失败静默 → 无演化时行为与改动前完全一致。
        evolved_overlay: Dict[str, float] = {}
        ps_version = 0
        try:
            from src.personality.personality_state import (
                get_personality_state,
                DEFAULT_TRAIT_BASELINE,
            )
            _ps = get_personality_state()
            ps_version = int(getattr(_ps, "version", 0) or 0)
            if ps_version > 0:
                for _k, _v in (getattr(_ps, "traits", None) or {}).items():
                    try:
                        _vf = float(_v)
                    except (TypeError, ValueError):
                        continue
                    if abs(_vf - float(DEFAULT_TRAIT_BASELINE.get(_k, 0.5))) <= 1e-9:
                        continue
                    evolved_overlay[_k] = self._clamp(_vf)
        except Exception:  # noqa: BLE001
            evolved_overlay = {}
        evolved_extra: List[Dict[str, Any]] = [
            {"trait": k, "value": v} for k, v in evolved_overlay.items()
        ]

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
        # Phase 6.2: Authority Closure — 若 adapter 注入则路由
        if self.self_model_store.should_update(self.growth_history):
            if is_personality_mutation_gateway_enabled():
                # P2.3-B.4 迁移模式：SelfModel 更新意图经 Gateway 治理，
                # 不再静默直写 Store / Adapter
                self._propose_self_model_mutation()
            else:
                try:
                    # P5.0-E #2: SelfModel 消费演化维度（personality_state → trait_states）
                    _merged_ts = dict(self._trait_states or {})
                    for _k, _v in evolved_overlay.items():
                        _merged_ts[_k] = {"current_value": _v}
                    self.self_model_store.update(self.growth_history, _merged_ts)
                except Exception:
                    pass
                if self._self_model_adapter is not None:
                    try:
                        # 收集被影响的 traits，写入 SelfModelAdapter
                        affected: Dict[str, float] = {}
                        for tname, tstate in (self._trait_states or {}).items():
                            try:
                                last_delta = float(getattr(tstate, "last_delta", 0.0) or 0.0)
                            except Exception:
                                last_delta = 0.0
                            if abs(last_delta) > 1e-9:
                                affected[tname] = round(last_delta, 5)
                        self._self_model_adapter.apply_external_change(
                            change_type="personality",
                            reason="personality_resolver_update",
                            source="personality_resolver",
                            confidence=0.5,
                            affected_traits=affected,
                        )
                    except Exception:
                        pass

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
            # P5.0-E #2: 演化维度 + 状态版本（Stage 6 Prompt 消费）
            "evolved_traits": evolved_extra,
            "personality_state_version": ps_version,
        }

        # P5.0-E #2: 演化维度覆盖扁平值（Stage 6 的 _CORE_FOR_PROMPT 直接读这些键）
        for _k, _v in evolved_overlay.items():
            if _k in data_dict:
                data_dict[_k] = _v

        # ── Phase 7.2: Cognitive Trace hook（只读, 不改 data_dict）──
        try:
            from src.runtime.observer.cognitive_hooks import emit_personality_resolved

            # 提取高激活 traits_used（值 > 0.6 的维度名, 最多 10 个）
            traits_used = []
            for dim in core_dimensions:
                val = data_dict.get(dim, 0.5)
                try:
                    val = float(val)
                except (TypeError, ValueError):
                    val = 0.5
                if val >= 0.6:
                    traits_used.append(dim)

            emit_personality_resolved(
                persona_version="v1.9",
                traits_used=traits_used,
                traits_count=len(data_dict),
                growth_metrics_used=len(metrics),
                growth_records_count=len(self.growth_records) if self.growth_records else 0,
                self_model_involved=bool(self_model),
                tension_count=len(active_tensions) if isinstance(active_tensions, list) else 0,
            )
        except Exception:  # noqa: BLE001
            pass

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

    # ============================================================
    # P2.3-B.4: 迁移模式提议方法（开关默认关闭时永不调用）
    # ============================================================
    def _propose_trait_mutation(
        self,
        dim: str,
        old_value: float,
        new_value: float,
        growth_delta: float,
    ) -> None:
        """trait 漂移 → MutationRequest → Gateway（不直接提交状态）。

        迁移模式下 resolve() 从「计算 + 修改」变为「计算 + 提议」：
        漂移不写入 _trait_states / history，只经治理链裁决。
        """
        request = self._mutation_adapter.build_request(
            source_event={
                "type": "growth_accumulation_drift",
                "dimension": dim,
                "growth_delta": round(float(growth_delta), 6),
                "occurrence_count": 1,
            },
            actor_identity="system",
            target_path=f"personality.traits.{dim}",
            proposed_change={
                "path": f"personality.traits.{dim}",
                "before": round(float(old_value), 6),
                "after": round(float(new_value), 6),
                "delta": round(float(new_value) - float(old_value), 6),
                "confidence": 0.5,
            },
            evidence=[{"ref": f"accumulation:{dim}", "type": "growth_accumulation"}],
            context_snapshot={"source": "personality_resolver"},
            risk_level="low",
        )
        self.mutation_requests.append(request)
        self._mutation_adapter.route(request)

    def _propose_self_model_mutation(self) -> None:
        """SelfModel 重建意图 → MutationRequest → Gateway（不直接写 Store）。"""
        request = self._mutation_adapter.build_request(
            source_event={
                "type": "self_model_refresh",
                "growth_count": int(self.growth_history.count()),
                "occurrence_count": 1,
            },
            actor_identity="system",
            target_domain="self_model",
            target_path="self_model.update",
            proposed_change={
                "path": "self_model.update",
                "reason": "growth_history_changed",
                "confidence": 0.5,
            },
            evidence=[{"ref": "growth_history_refresh", "type": "growth_accumulation"}],
            context_snapshot={"source": "personality_resolver"},
            risk_level="low",
        )
        self.mutation_requests.append(request)
        self._mutation_adapter.route(request)