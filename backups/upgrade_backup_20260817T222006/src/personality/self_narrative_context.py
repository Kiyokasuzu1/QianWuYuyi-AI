"""
自我叙事上下文 (SelfNarrativeContext) — Phase 12.2 最终版
三大区块：【核心自我】/【当前关系】/【当前状态】
内部 Builder：OriginBuilder / TraitBuilder / BeliefBuilder / RelationshipBuilder
"""
from typing import Optional, List
from src.personality.self_model_v3 import SelfModelV3, NarrativeType
from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_cognitive_profile import RelationshipCognitiveProfile
from src.emotion.emotion_context import EmotionContext
from src.agreement.agreement import Agreement
from src.agreement.agreement_context import AgreementContext
from src.personality.belief_verifier import BeliefVerifier, BeliefType


class OriginBuilder:
    MAX_ITEMS = 3
    MIN_CONFIDENCE = 0.6

    @classmethod
    def build(cls, model: SelfModelV3) -> str:
        if not model or not model.narrative_items:
            return ""
        origin_items = [
            item for item in model.narrative_items
            if item.narrative_type in (NarrativeType.ORIGIN, NarrativeType.FOUNDATION)
            and item.confidence >= cls.MIN_CONFIDENCE
        ]
        if not origin_items:
            return ""
        # 按 importance × confidence 降序排列
        origin_items.sort(
            key=lambda x: x.importance * x.confidence,
            reverse=True
        )
        items = origin_items[:cls.MAX_ITEMS]
        narratives = [item.text for item in items]
        if narratives:
            return "我的形成经历：\n" + "\n".join(f"- {n}" for n in narratives)
        return ""


class TraitBuilder:
    @classmethod
    def build(cls, model: SelfModelV3) -> str:
        if not model or not model.traits:
            return ""
        trait_parts = []
        for name, value in model.traits.items():
            if value >= 0.7:
                trait_parts.append(f"明显地{name}")
            elif value >= 0.5:
                trait_parts.append(f"比较{name}")
        if trait_parts:
            return "我的性格特点：" + "、".join(trait_parts) + "。"
        return ""


class BeliefBuilder:
    MAX_ITEMS = 5
    ALLOWED_TYPES = {BeliefType.CORE_VALUE, BeliefType.SELF_UNDERSTANDING}

    @classmethod
    def build(cls, model: SelfModelV3) -> str:
        if not model or not model.beliefs:
            return ""
        safe_beliefs = BeliefVerifier.verify(model.beliefs)
        if not safe_beliefs:
            return ""
        # 只保留核心价值和自我理解
        core_beliefs = [
            b for b in safe_beliefs
            if BeliefVerifier.classify(b) in cls.ALLOWED_TYPES
        ]
        if not core_beliefs:
            return ""
        sorted_beliefs = sorted(
            core_beliefs,
            key=lambda b: BeliefVerifier.classify(b).priority
        )
        belief_text = "；".join(sorted_beliefs[:cls.MAX_ITEMS])
        return f"我相信：{belief_text}。"


class RelationshipBuilder:
    MAX_PATTERNS = 3

    @classmethod
    def build(
        cls,
        state: Optional[RelationshipState],
        profile: Optional[RelationshipCognitiveProfile],
    ) -> str:
        if not state:
            return ""
        parts = []
        if state.familiarity >= 0.6:
            parts.append("与当前用户较为熟悉")
        if state.trust >= 0.6:
            parts.append("有一定的信任基础")
        if state.collaboration >= 0.5:
            parts.append("有过协作经历")
        if profile and profile.confirmed_patterns:
            patterns = profile.confirmed_patterns[:cls.MAX_PATTERNS]
            parts.append("已观察到的互动模式：" + "；".join(patterns))
        if parts:
            return "【当前关系】" + "；".join(parts) + "。"
        return ""


class SelfNarrativeContext:
    @classmethod
    def build(
        cls,
        self_model: Optional[SelfModelV3] = None,
        relationship_state: Optional[RelationshipState] = None,
        cognitive_profile: Optional[RelationshipCognitiveProfile] = None,
        emotion_ctx: Optional[EmotionContext] = None,
        agreements: Optional[List[Agreement]] = None,
    ) -> str:
        core_parts = []
        relationship_text = ""
        emotion_text = ""

        # === 核心自我区块 ===

        # 1. 不可改变核心约定
        if agreements:
            agreement_text = AgreementContext.build(agreements)
            if agreement_text:
                core_parts.append(agreement_text)

        # 2. 核心身份
        if self_model and self_model.identity:
            core_parts.append(f"我是{self_model.identity}。")

        # 3. 起源身份
        if self_model:
            origin_text = OriginBuilder.build(self_model)
            if origin_text:
                core_parts.append(origin_text)

        # 4. 性格特征
        if self_model:
            trait_text = TraitBuilder.build(self_model)
            if trait_text:
                core_parts.append(trait_text)

        # 5. 安全信念
        if self_model:
            belief_text = BeliefBuilder.build(self_model)
            if belief_text:
                core_parts.append(belief_text)

        # === 当前关系区块 ===
        if relationship_state:
            rel_text = RelationshipBuilder.build(relationship_state, cognitive_profile)
            if rel_text:
                relationship_text = rel_text

        # === 当前状态区块 ===
        if emotion_ctx and emotion_ctx.summary:
            emotion_text = f"【当前状态】{emotion_ctx.summary}"

        # 组装
        result_parts = []

        if core_parts:
            result_parts.append("【核心自我】\n" + "\n".join(core_parts))

        if relationship_text:
            result_parts.append(relationship_text)

        if emotion_text:
            result_parts.append(emotion_text)

        return "\n\n".join(result_parts) if result_parts else ""