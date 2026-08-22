"""
情绪上下文 (EmotionContext)
表示经过转换的情绪信息，用于注入行为层或 Prompt 生成。
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class EmotionContext:
    summary: str = ""                       # 当前情绪概览（自然语言）
    mood: str = "neutral"                   # 心境标签：calm / tense / joyful / flat 等
    expression_tendencies: List[str] = field(default_factory=list)  # 表达倾向，如 "外向表达倾向增强", "活跃度降低"
    # Emotion System 2.0（E-Emotion-5）：响应策略三要素（描述性倾向，非人格定义）
    expression_style: str = ""              # 表达方式，如 "偏开朗活跃"
    proactivity: str = ""                   # 主动程度：提高 / 降低 / 自然
    tone_style: str = ""                    # 语气倾向，如 "轻快温暖、更谨慎"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "mood": self.mood,
            "expression_tendencies": self.expression_tendencies,
            "expression_style": self.expression_style,
            "proactivity": self.proactivity,
            "tone_style": self.tone_style,
        }

    def to_prompt_text(self) -> str:
        """
        生成可用于 Prompt 的文本片段，采用描述性语气。
        """
        if not self.summary:
            return ""
        parts = [f"【当前情绪状态】{self.summary}"]
        if self.expression_tendencies:
            parts.append("表达倾向：" + "；".join(self.expression_tendencies) + "。")
        strategy_parts = []
        if self.expression_style:
            strategy_parts.append(f"表达方式：{self.expression_style}")
        if self.proactivity:
            strategy_parts.append(f"主动程度：{self.proactivity}")
        if self.tone_style:
            strategy_parts.append(f"语气倾向：{self.tone_style}")
        if strategy_parts:
            parts.append("；".join(strategy_parts) + "。")
        return "\n".join(parts)