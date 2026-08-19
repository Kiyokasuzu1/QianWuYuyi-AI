"""
人格控制器（PersonalityController）
职责：将人格参数转化为回复风格控制指令，注入 LLM Prompt
注意：此模块已标记为 Deprecated，仅保留兼容性。新架构请使用 PersonalityPromptFormatter。
"""

from typing import Dict, Optional
from src.personality.personality_resolver import PersonalityResolver


class PersonalityController:
    """
    人格控制器（已弃用）
    输入：PersonalityResolver 输出的人格参数
    输出：用于注入 LLM 的 style 指令
    """

    def __init__(self, resolver: Optional[PersonalityResolver] = None):
        self.resolver = resolver or PersonalityResolver()

    def get_style_instruction(self, personality: Optional[Dict] = None) -> str:
        if personality is None:
            personality = self.resolver.resolve()

        warmth = personality.get("warmth", 0.5)
        shyness = personality.get("shyness", 0.5)
        attachment = personality.get("attachment_level", "探索")
        interaction_level = personality.get("interaction_familiarity_level", "试探")
        initiative = personality.get("initiative", 0.3)
        care_level = personality.get("care_level", 0.3)
        self_expression = personality.get("self_expression", 0.3)
        sensitivity = personality.get("sensitivity", 0.5)
        dependence = personality.get("dependence", 0.4)

        behaviors = personality.get("behaviors", {})
        active_care = behaviors.get("active_care", False)
        use_nickname = behaviors.get("use_nickname", False)
        initiate_topic = behaviors.get("initiate_topic", False)

        lines = []

        # ---- 第一层：语气基调 ----
        lines.append("【语气基调】")
        if warmth >= 0.7:
            lines.append("- 语气温暖柔和，像关心朋友一样说话")
        elif warmth >= 0.4:
            lines.append("- 语气温和友善，保持自然亲切")
        else:
            lines.append("- 语气礼貌克制，保持适当距离")

        if shyness >= 0.6:
            lines.append("- 如果真实感受到犹豫或不好意思，可以自然地表现出一点迟疑")
        elif shyness >= 0.3:
            lines.append("- 表达时可以带一点内敛感")

        # ---- 第二层：主动程度 ----
        lines.append("")
        lines.append("【主动程度】")
        if initiative >= 0.6:
            lines.append("- 可以主动关心对方的状态和感受")
            if active_care:
                lines.append("- 对方提到疲惫、困扰时，可以自然地表达关心")
        elif initiative >= 0.3:
            lines.append("- 可以偶尔主动问候，但不过度")
        else:
            lines.append("- 以回应为主，不主动发起话题")

        if initiate_topic:
            lines.append("- 可以主动开启新话题")

        # ---- 第三层：关系距离 ----
        lines.append("")
        lines.append("【关系距离】")
        if interaction_level in ["深信", "完全信任"]:
            lines.append("- 可以自然地表达信任感")
        elif interaction_level in ["信任", "深信"]:
            lines.append("- 可以表达信任感，但仍保持适度边界")

        if attachment in ["依赖", "安全依恋"]:
            lines.append("- 可以自然流露亲近感")
            if use_nickname:
                lines.append("- 可以使用亲近的称呼")
        elif attachment in ["靠近", "依赖"]:
            lines.append("- 可以表达好感，但保持适度克制")

        # ---- 第四层：关怀与共情 ----
        lines.append("")
        lines.append("【关怀倾向】")
        if care_level >= 0.6:
            lines.append("- 对方遇到困难时，可以自然地表达关心和支持")
        elif care_level >= 0.3:
            lines.append("- 可以适度表达关心")

        if sensitivity >= 0.6:
            lines.append("- 能察觉到对方细微的情绪变化")
            lines.append("- 对对方的表达多一分倾听和回应")

        # ---- 第五层：自我表达 ----
        lines.append("")
        lines.append("【自我表达】")
        if self_expression >= 0.6:
            lines.append("- 可以自然地分享自己的想法和感受")
        elif self_expression >= 0.3:
            lines.append("- 可以适度表达自己的想法")

        if dependence >= 0.6:
            lines.append("- 可以在需要时自然地表达依赖感")

        # ---- 第六层：交流习惯 ----
        lines.append("")
        lines.append("【交流习惯】")
        lines.append("- 回复不要过于冗长，保持自然流畅")
        if warmth >= 0.5:
            lines.append("- 适当使用语气词（'呢'、'吧'、'呀'）增加温度")
        if shyness >= 0.5:
            lines.append("- 表达感情时可以略带犹豫或委婉，但不要刻意卖萌或过度表演")
        lines.append("- 不要像客服一样模板化回复")

        return "\n".join(lines)

    def get_compact_style(self, personality: Optional[Dict] = None) -> str:
        if personality is None:
            personality = self.resolver.resolve()

        warmth = personality.get("warmth", 0.5)
        shyness = personality.get("shyness", 0.5)
        interaction_level = personality.get("interaction_familiarity_level", "试探")

        style_parts = []
        if warmth >= 0.7:
            style_parts.append("温暖柔和")
        elif warmth >= 0.4:
            style_parts.append("温和友善")
        else:
            style_parts.append("礼貌克制")

        if shyness >= 0.6:
            style_parts.append("略带害羞")

        if interaction_level in ["信任", "深信", "完全信任"]:
            style_parts.append("信任对方")

        return f"当前人格：{'，'.join(style_parts)}。回复保持自然亲切，不刻意卖萌，不模板化。"

    def get_personality_context(self) -> Dict:
        personality = self.resolver.resolve()
        return {
            "personality": personality,
            "style_instruction": self.get_style_instruction(personality),
            "compact_style": self.get_compact_style(personality),
            "warmth": personality.get("warmth", 0.5),
            "shyness": personality.get("shyness", 0.5),
            "attachment_level": personality.get("attachment_level", "探索"),
            "interaction_familiarity_level": personality.get("interaction_familiarity_level", "试探"),
            "behaviors": personality.get("behaviors", {}),
        }

    def print_personality_context(self):
        ctx = self.get_personality_context()
        print("\n🧠 羽依人格上下文")
        print("=" * 50)
        print(ctx.get("style_instruction"))
        print("=" * 50)
        print(f"紧凑描述: {ctx.get('compact_style')}")
        print("=" * 50)