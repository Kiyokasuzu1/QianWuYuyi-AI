# -*- coding: utf-8 -*-
"""
Phase 4.1.2-B: CommunicationRenderer

职责：把 CommunicationStyle（数值倾向）转为低约束的自然语言描述。

设计原则：
- 只描述倾向，不规定说法
- 不输出祈使句（"你必须"、"你应该"）
- 不输出否定约束（"不要"、"禁止"）
- 不输出固定句式模板
- 不把数值机械映射到标签

正确输出：
  "当前表达倾向：较为温暖、亲近。"
  "你和对方的互动较为自然亲近。"

错误输出（禁止）：
  "你必须温柔"
  "请用温柔的语气"
  "绝对不要正式"
  "说话要像..."

约束：
- 不 import 业务模块
- 不访问文件系统
- 不调用 LLM
- 纯数据 → 文本转换
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from src.contracts.communication_style import CommunicationStyle


# ============================================================
# 阈值定义
# ============================================================

# 阈值用于判断"当前处于什么区间"，不是映射到指令
_WARMTH_HIGH = 0.70
_WARMTH_MEDIUM = 0.45
_INTIMACY_HIGH = 0.70
_INTIMACY_MEDIUM = 0.45
_FORMALITY_LOW = 0.35
_FORMALITY_MEDIUM = 0.60
_PLAYFULNESS_HIGH = 0.65
_PLAYFULNESS_MEDIUM = 0.40
_CLOSENESS_HIGH = 0.70
_CLOSENESS_MEDIUM = 0.45
_ATTUNEMENT_HIGH = 0.65
_ATTUNEMENT_MEDIUM = 0.40
_INITIATIVE_HIGH = 0.60
_INITIATIVE_MEDIUM = 0.35


# ============================================================
# Renderer
# ============================================================


class CommunicationRenderer:
    """Phase 4.1.2-B: 把 CommunicationStyle 渲染为 Prompt 可用的描述性上下文。

    输出是"当前状态描述"，不是"行为指令"。
    """

    def render(self, style: CommunicationStyle) -> str:
        """渲染 CommunicationStyle 为单段自然语言描述。

        Args:
            style: CommunicationStyle 实例

        Returns:
            描述性文本（可能为空字符串），单段，不包含指令性语言
        """
        if style is None:
            return ""

        parts: list[str] = []

        # 1. 称呼倾向（来自 AddressingPolicy）
        if style.addressing:
            addr_text = self._render_addressing(style.addressing)
            if addr_text:
                parts.append(addr_text)

        # 2. 语气倾向（来自 TonePolicy）
        if style.tone:
            tone_text = self._render_tone(style.tone)
            if tone_text:
                parts.append(tone_text)

        # 3. 互动倾向（来自 InteractionPolicy）
        if style.interaction:
            inter_text = self._render_interaction(style.interaction)
            if inter_text:
                parts.append(inter_text)

        return " ".join(parts) if parts else ""

    # ============================================================
    # 子渲染
    # ============================================================

    @staticmethod
    def _render_addressing(addressing) -> str:
        """渲染称呼倾向。

        称呼是身份事实，不是表达风格。这里只描述"你们之间的称呼习惯"。
        """
        preferred = getattr(addressing, "preferred_name", "")
        if not preferred:
            return ""

        strength = getattr(addressing, "addressing_strength", 0.5)

        # 称呼是事实，用陈述句
        if strength >= 0.6:
            return f"你习惯称呼对方为「{preferred}」。"
        elif strength >= 0.3:
            return f"你可以称呼对方为「{preferred}」。"
        else:
            return f"对方希望你称呼她为「{preferred}」。"

    @staticmethod
    def _render_tone(tone) -> str:
        """渲染语气倾向。

        只描述当前状态，不定指令。
        """
        warmth = getattr(tone, "warmth", 0.5)
        intimacy = getattr(tone, "intimacy", 0.3)
        formality = getattr(tone, "formality", 0.5)
        playfulness = getattr(tone, "playfulness", 0.3)

        descriptors: list[str] = []

        # 温暖度 → 描述
        if warmth >= _WARMTH_HIGH:
            descriptors.append("温暖")
        elif warmth >= _WARMTH_MEDIUM:
            descriptors.append("温和")

        # 亲密感 → 描述
        if intimacy >= _INTIMACY_HIGH:
            descriptors.append("亲近")
        elif intimacy >= _INTIMACY_MEDIUM:
            descriptors.append("自然")

        # 正式度 → 描述（低正式 = 随意）
        if formality <= _FORMALITY_LOW:
            descriptors.append("较为随意")
        elif formality <= _FORMALITY_MEDIUM:
            descriptors.append("自然")

        # 活泼度 → 描述
        if playfulness >= _PLAYFULNESS_HIGH:
            descriptors.append("轻松活泼")
        elif playfulness >= _PLAYFULNESS_MEDIUM:
            descriptors.append("轻松")

        if not descriptors:
            return ""

        joined = "、".join(descriptors)
        return f"当前表达倾向：{joined}。"

    @staticmethod
    def _render_interaction(interaction) -> str:
        """渲染互动倾向。

        只描述互动的自然状态，不定行为规则。
        """
        closeness = getattr(interaction, "response_distance", 0.5)
        attunement = getattr(interaction, "emotional_attunement", 0.3)
        initiative = getattr(interaction, "initiative_level", 0.2)

        descriptors: list[str] = []

        if closeness >= _CLOSENESS_HIGH:
            descriptors.append("互动较为亲近")
        elif closeness >= _CLOSENESS_MEDIUM:
            descriptors.append("互动自然")

        if attunement >= _ATTUNEMENT_HIGH:
            descriptors.append("能较好地感知对方的情绪")
        elif attunement >= _ATTUNEMENT_MEDIUM:
            descriptors.append("能一定程度地感知对方的情绪")

        if initiative >= _INITIATIVE_HIGH:
            descriptors.append("倾向于主动关心")
        elif initiative >= _INITIATIVE_MEDIUM:
            descriptors.append("偶尔会主动表达关心")

        if not descriptors:
            return ""

        return "。".join(d for d in descriptors if d) + "。"

    # ============================================================
    # 便捷方法：从原始数据直接渲染（跳过 CommunicationStyle）
    # ============================================================

    def render_from_state(
        self,
        relationship_state: Any,
        user_identity: Optional[Dict[str, Any]] = None,
    ) -> str:
        """从 RelationshipState 直接渲染，一站式方法。

        内部调用 CommunicationStyleResolver + render()。

        Args:
            relationship_state: 关系状态（dict 或 RelationshipState 对象）
            user_identity: 用户身份信息

        Returns:
            描述性文本
        """
        from src.communication.style_resolver import CommunicationStyleResolver

        resolver = CommunicationStyleResolver()
        style = resolver.resolve(relationship_state, user_identity)
        return self.render(style)


# ============================================================
# 降级渲染：兼容旧的 calling_rule / intimacy_rule
# ============================================================

def merge_with_legacy_meta(
    communication_text: str,
    user_meta: Optional[Dict[str, Any]],
) -> str:
    """Phase 4.1.2-B: 将 CommunicationRenderer 的输出与旧的 user_meta 合并。

    策略：
    - communication_text 优先（新链）
    - user_meta 中的 preferred_name 保留（身份事实）
    - user_meta 中的 calling_rule / intimacy_rule 作为 fallback（旧链保障）

    新链运行时：communication_text 非空，user_meta 的 calling_rule/intimacy_rule
    降级为 fallback（不注入 Prompt）。

    新链失败时：communication_text 为空，回退到 user_meta 的 calling_rule/intimacy_rule。
    """
    if communication_text:
        # 新链可用：只保留 preferred_name 和 display_name 作为身份事实
        if isinstance(user_meta, dict) and user_meta:
            lines = [communication_text]
            preferred = user_meta.get("preferred_name")
            if isinstance(preferred, str) and preferred.strip():
                lines.append(f"对方希望你称呼她为「{preferred}」。")
            return "\n".join(lines)
        return communication_text

    # ══════════════════════════════════════════════════════════════
    # FALLBACK ONLY（Phase 4.1.3）
    # 新链失败时，将 legacy user_meta 的 calling_rule/intimacy_rule
    # 注入到 communication_text 中作为兜底。
    # 正常路径下此段代码不应被触发。
    # ══════════════════════════════════════════════════════════════
    if isinstance(user_meta, dict) and user_meta:
        parts = []
        calling = user_meta.get("calling_rule")
        if calling:
            parts.append(calling)
        intimacy = user_meta.get("intimacy_rule")
        if intimacy:
            parts.append(intimacy)
        return "\n".join(parts) if parts else ""

    return ""