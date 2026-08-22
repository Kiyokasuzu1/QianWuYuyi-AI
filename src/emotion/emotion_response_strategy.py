# -*- coding: utf-8 -*-
"""
情绪响应策略 (EmotionResponseStrategy) — Emotion System 2.0（E-Emotion-5）

职责：
    将 EmotionState 映射为响应策略描述（表达方式 / 主动程度 / 语气倾向）。

边界（不可违反）：
    - 纯派生映射：只读 EmotionState，绝不修改任何状态。
    - 不 import personality / identity 模块：情绪只描述「我现在感觉如何」，
      绝不覆盖「我是谁」。Personality = who I am，Emotion = how I feel now。
    - 输出是描述性倾向文本，不是对人格的行为指令，也不修改 identity_core。

输入兼容：
    build() 接受 EmotionState 实例或 dict（EmotionState.to_dict() 快照），
    便于 Runtime 链（ctx.emotion_snapshot 为 dict）与 Legacy 链共用同一映射。
"""
from dataclasses import dataclass, asdict
from typing import Any, Dict, Mapping, Optional

from src.emotion.emotion_state import EmotionState

# 阈值（与 EmotionContextProvider 的心境阈值保持同一量级，避免两套口径打架）
HIGH_AROUSAL = 0.7
LOW_AROUSAL = 0.3
HIGH_ANXIETY = 0.6
POSITIVE_VALENCE = 0.3
NEGATIVE_VALENCE = -0.3
HIGH_ENERGY = 0.7
LOW_ENERGY = 0.3
HIGH_CURIOSITY = 0.7
HIGH_SADNESS = 0.5
HIGH_TRUST = 0.7


@dataclass
class ResponseStrategy:
    """情绪衍生的响应策略（描述性倾向，非人格定义）。"""
    expression_style: str = "自然表达"          # 表达方式
    proactivity: str = "自然"                    # 主动程度：提高 / 降低 / 自然
    tone_style: str = "平稳自然"                # 语气倾向

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "ResponseStrategy":
        payload = dict(data or {})
        return cls(
            expression_style=str(payload.get("expression_style") or "自然表达"),
            proactivity=str(payload.get("proactivity") or "自然"),
            tone_style=str(payload.get("tone_style") or "平稳自然"),
        )

    def to_prompt_text(self) -> str:
        """一行描述性文本，供 Prompt 追加（不带数值、不带指令语气）。"""
        return (
            f"表达方式：{self.expression_style}；"
            f"主动程度：{self.proactivity}；"
            f"语气倾向：{self.tone_style}。"
        )


class EmotionResponseStrategyBuilder:
    """从 EmotionState 派生响应策略（只读，无副作用）。"""

    def build(self, state: Any) -> ResponseStrategy:
        """state 可为 EmotionState 或 dict 快照；None 返回中性默认。"""
        if state is None:
            return ResponseStrategy()

        if isinstance(state, Mapping):
            s = EmotionState.from_dict(dict(state))
        elif isinstance(state, EmotionState):
            s = state
        else:
            # 兼容带同名字段的快照对象（跳过 None 字段，避免污染默认值）
            s = EmotionState.from_dict({
                k: getattr(state, k)
                for k in ("valence", "arousal", "anxiety", "energy", "curiosity",
                          "sadness", "trust")
                if getattr(state, k, None) is not None
            })

        # 表达方式：激活 × 情绪方向
        if s.arousal >= HIGH_AROUSAL and s.valence > 0:
            expression_style = "偏开朗活跃"
        elif s.anxiety >= HIGH_ANXIETY:
            expression_style = "偏内敛谨慎"
        elif s.arousal <= LOW_AROUSAL or s.sadness >= HIGH_SADNESS:
            expression_style = "偏沉静收敛"
        else:
            expression_style = "自然表达"

        # 主动程度：精力 / 好奇心 / 悲伤
        if s.energy >= HIGH_ENERGY or s.curiosity >= HIGH_CURIOSITY:
            proactivity = "提高"
        elif s.energy <= LOW_ENERGY or s.sadness >= HIGH_SADNESS:
            proactivity = "降低"
        else:
            proactivity = "自然"

        # 语气倾向：愉悦方向 + 焦虑 / 信任修饰
        tone_parts = []
        if s.valence > POSITIVE_VALENCE:
            tone_parts.append("轻快温暖")
        elif s.valence < NEGATIVE_VALENCE:
            tone_parts.append("低沉克制")
        else:
            tone_parts.append("平稳自然")
        if s.anxiety >= HIGH_ANXIETY:
            tone_parts.append("更谨慎")
        if s.trust >= HIGH_TRUST:
            tone_parts.append("更放松信任")
        tone_style = "、".join(tone_parts)

        return ResponseStrategy(
            expression_style=expression_style,
            proactivity=proactivity,
            tone_style=tone_style,
        )


def response_strategy_prompt_line(state: Any) -> str:
    """一行策略文本；state 无效时返回空串（调用方可安全拼接）。"""
    if state is None:
        return ""
    try:
        return EmotionResponseStrategyBuilder().build(state).to_prompt_text()
    except Exception:
        return ""
