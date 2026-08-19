# -*- coding: utf-8 -*-
"""
Phase 4.1.0: CommunicationStyle 契约

这是羽依「如何说话」的统一数据契约。它不决定「说什么」，
只决定「用什么语气、怎么称呼、主动程度」。

职责边界：
- Relationship: 负责回答「羽依和对方是什么关系」
- CommunicationStyle: 负责回答「基于这个关系，羽依应该怎么说话」
- PromptBuilder: 负责把 CommunicationStyle 渲染成 Prompt 文本

不负责：
- 不修改 RelationshipState
- 不修改 Personality
- 不生成回复内容
- 不直接调用 LLM

设计原则：
1. 所有字段都是可选的（Optional），缺失时 PromptBuilder 使用默认值
2. 所有字段都是可序列化的（to_dict / from_dict）
3. 不 import 业务模块（只依赖 dataclass + typing）
4. 单一来源推导：从 RelationshipState 通过 Resolver 计算，不硬编码
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================
# 顶层：CommunicationStyle
# ============================================================

@dataclass
class CommunicationStyle:
    """羽依对特定用户的沟通风格快照。

    由 CommunicationStyleResolver 从 RelationshipState 推导，
    不是手动创建的配置项。
    """

    addressing: Optional[AddressingPolicy] = None
    tone: Optional[TonePolicy] = None
    interaction: Optional[InteractionPolicy] = None
    source: Optional[StyleSource] = None

    # 版本追踪
    version: str = "1.0"
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "addressing": self.addressing.to_dict() if self.addressing else None,
            "tone": self.tone.to_dict() if self.tone else None,
            "interaction": self.interaction.to_dict() if self.interaction else None,
            "source": self.source.to_dict() if self.source else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CommunicationStyle":
        return cls(
            version=str(data.get("version", "1.0")),
            generated_at=str(data.get("generated_at", "")),
            addressing=AddressingPolicy.from_dict(data["addressing"])
            if data.get("addressing") else None,
            tone=TonePolicy.from_dict(data["tone"])
            if data.get("tone") else None,
            interaction=InteractionPolicy.from_dict(data["interaction"])
            if data.get("interaction") else None,
            source=StyleSource.from_dict(data["source"])
            if data.get("source") else None,
        )


# ============================================================
# 子策略：AddressingPolicy
# ============================================================

@dataclass
class AddressingPolicy:
    """羽依如何称呼对方。

    由 Relationship 中的 familiarity + trust + bond 推导，
    不是硬编码的 calling_rule。
    """

    # 称呼方式
    preferred_name: str = ""                # 对方希望被称呼的名字（如 "清清"）
    fallback_name: str = ""                 # 无法使用 preferred_name 时的备选
    forbidden_names: List[str] = field(default_factory=list)  # 绝对不要用的称呼

    # 称呼强度（0~1）
    # 0.0 = 可以不称呼（如陌生人）
    # 0.5 = 建议使用昵称
    # 1.0 = 必须使用昵称，不能回避
    addressing_strength: float = 0.5

    # 称呼来源（用于可解释性）
    # "derived": 从 RelationshipState 推导
    # "explicit": 用户明确设置
    # "default": 系统默认
    source: str = "default"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preferred_name": self.preferred_name,
            "fallback_name": self.fallback_name,
            "forbidden_names": list(self.forbidden_names),
            "addressing_strength": self.addressing_strength,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AddressingPolicy":
        return cls(
            preferred_name=str(data.get("preferred_name", "")),
            fallback_name=str(data.get("fallback_name", "")),
            forbidden_names=list(data.get("forbidden_names", [])),
            addressing_strength=float(data.get("addressing_strength", 0.5)),
            source=str(data.get("source", "default")),
        )


# ============================================================
# 子策略：TonePolicy
# ============================================================

@dataclass
class TonePolicy:
    """羽依的语气参数。

    由 Relationship 中的 trust + familiarity + bond + relationship_stage 推导。
    所有值 0~1，0 = 最低，1 = 最高。
    """

    warmth: float = 0.5          # 温度：冷淡（0）→ 温暖（1）
    intimacy: float = 0.3         # 亲密：疏远（0）→ 高度亲密（1）
    formality: float = 0.5        # 正式：随意（0）→ 正式（1）
    playfulness: float = 0.3      # 活泼：严肃（0）→ 活泼/爱开玩笑（1）

    # 语气标签（用于 Prompt 渲染）
    # 例如：["温柔", "贴近", "有温度"]
    tone_tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "warmth": self.warmth,
            "intimacy": self.intimacy,
            "formality": self.formality,
            "playfulness": self.playfulness,
            "tone_tags": list(self.tone_tags),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TonePolicy":
        return cls(
            warmth=float(data.get("warmth", 0.5)),
            intimacy=float(data.get("intimacy", 0.3)),
            formality=float(data.get("formality", 0.5)),
            playfulness=float(data.get("playfulness", 0.3)),
            tone_tags=list(data.get("tone_tags", [])),
        )


# ============================================================
# 子策略：InteractionPolicy
# ============================================================

@dataclass
class InteractionPolicy:
    """羽依的互动行为参数。

    由 Relationship 中的 familiarity + trust + activity_level 推导。
    """

    # 回应距离（0~1）
    # 0.0 = 极简回答（陌生人）
    # 0.5 = 正常对话
    # 1.0 = 主动展开、反问、分享
    response_distance: float = 0.5

    # 情绪协调（0~1）
    # 0.0 = 不感知对方情绪
    # 1.0 = 高度感知并回应对情绪
    emotional_attunement: float = 0.3

    # 主动程度（0~1）
    # 0.0 = 完全被动（只回答）
    # 1.0 = 高度主动（主动提问、分享、关心）
    initiative_level: float = 0.2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "response_distance": self.response_distance,
            "emotional_attunement": self.emotional_attunement,
            "initiative_level": self.initiative_level,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InteractionPolicy":
        return cls(
            response_distance=float(data.get("response_distance", 0.5)),
            emotional_attunement=float(data.get("emotional_attunement", 0.3)),
            initiative_level=float(data.get("initiative_level", 0.2)),
        )


# ============================================================
# 来源追踪：StyleSource
# ============================================================

@dataclass
class StyleSource:
    """记录 CommunicationStyle 是从哪些关系数据推导出来的。

    用于可解释性：如果羽依语气变了，可以追溯到是哪个关系维度变了。
    """

    relationship_stage: str = ""      # "initial" / "developing" / "stable" / "deep_collaboration"
    familiarity: float = 0.0          # 原始 familiarity 值
    trust: float = 0.0                # 原始 trust 值
    bond: float = 0.0                 # 原始 bond_strength 值（v0.6 用）
    collaboration: float = 0.0        # 原始 collaboration 值（v3.5.27 用）
    shared_history: float = 0.0       # 原始 shared_history 值（v0.6 用）
    activity_level: float = 0.0       # 原始 activity_level 值

    # 数据来源版本
    source_version: str = ""          # 数据来源的版本号（如 "0.6" / "3.5.27"）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_stage": self.relationship_stage,
            "familiarity": self.familiarity,
            "trust": self.trust,
            "bond": self.bond,
            "collaboration": self.collaboration,
            "shared_history": self.shared_history,
            "activity_level": self.activity_level,
            "source_version": self.source_version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StyleSource":
        return cls(
            relationship_stage=str(data.get("relationship_stage", "")),
            familiarity=float(data.get("familiarity", 0.0)),
            trust=float(data.get("trust", 0.0)),
            bond=float(data.get("bond", 0.0)),
            collaboration=float(data.get("collaboration", 0.0)),
            shared_history=float(data.get("shared_history", 0.0)),
            activity_level=float(data.get("activity_level", 0.0)),
            source_version=str(data.get("source_version", "")),
        )


# ============================================================
# 契约定义：CommunicationStyleResolver 接口
# ============================================================

class CommunicationStyleResolver:
    """Phase 4.1.1 实现。

    从 RelationshipState 推导 CommunicationStyle。
    当前是接口定义（契约），具体实现放在 src/communication/style_resolver.py。

    推导规则（待实现）：
    - addressing.preferred_name: 从 Identity + user_meta 获取
    - addressing.addressing_strength: familiarity × trust × bond
    - tone.warmth: familiarity × trust × bond
    - tone.intimacy: familiarity × bond × (1 - formality_from_relationship_stage)
    - tone.formality: 1 - familiarity (越熟越不正式)
    - tone.playfulness: trust × interaction_frequency
    - interaction.response_distance: familiarity × trust
    - interaction.emotional_attunement: trust × familiarity
    - interaction.initiative_level: trust × activity_level × bond
    """

    def resolve(
        self,
        relationship_state: Any,
        user_identity: Optional[Dict[str, Any]] = None,
    ) -> CommunicationStyle:
        """从关系状态推导沟通风格。

        Args:
            relationship_state: 关系状态（兼容两套 RelationshipState）
            user_identity: 用户身份信息（含 preferred_name 等）

        Returns:
            CommunicationStyle
        """
        raise NotImplementedError(
            "Phase 4.1.1: CommunicationStyleResolver.resolve() 待实现"
        )