"""
情绪上下文提供器 (EmotionContextProvider)
将 EmotionState 转换为结构化的 EmotionContext。
只负责数据转换，不修改状态、不直接决定行为。
Phase 9.7 更新：build 方法支持 influence 参数（当前透传，后续可控制表达强度）。
"""
from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_context import EmotionContext


class EmotionContextProvider:
    POSITIVE_VALENCE_THRESHOLD = 0.3
    NEGATIVE_VALENCE_THRESHOLD = -0.3
    HIGH_AROUSAL_THRESHOLD = 0.7
    LOW_AROUSAL_THRESHOLD = 0.3
    HIGH_ANXIETY_THRESHOLD = 0.6
    HIGH_CURIOSITY_THRESHOLD = 0.6
    LOW_CONFIDENCE_THRESHOLD = 0.3

    def build(self, state: EmotionState, influence: float = 0.3) -> EmotionContext:
        """
        构建情绪上下文。
        influence 参数预留，当前影响 summary 措辞，后续可用于控制表达强度。
        """
        if state is None:
            return EmotionContext()

        valence = state.valence
        arousal = state.arousal
        anxiety = state.anxiety
        curiosity = state.curiosity
        confidence = state.confidence

        summary_parts = []
        tendencies = []

        # 根据 influence 调整语气前缀
        if influence < 0.5:
            tone_prefix = "轻微感受到"
        else:
            tone_prefix = ""

        # 总体心境
        if valence > self.POSITIVE_VALENCE_THRESHOLD:
            if arousal > self.HIGH_AROUSAL_THRESHOLD:
                mood = "joyful"
                part = "心情愉悦，精力充沛"
                summary_parts.append(f"{tone_prefix}{part}" if tone_prefix else part)
                tendencies.append("外向表达倾向增强")
            elif arousal < self.LOW_AROUSAL_THRESHOLD:
                mood = "serene"
                part = "内心平静满足"
                summary_parts.append(f"{tone_prefix}{part}" if tone_prefix else part)
                tendencies.append("表达倾向温和细腻")
            else:
                mood = "positive"
                part = "情绪积极"
                summary_parts.append(f"{tone_prefix}{part}" if tone_prefix else part)
                tendencies.append("表达活跃度可能提高")
        elif valence < self.NEGATIVE_VALENCE_THRESHOLD:
            if arousal > self.HIGH_AROUSAL_THRESHOLD:
                mood = "tense"
                part = "感到有些紧张或低落"
                summary_parts.append(f"{tone_prefix}{part}" if tone_prefix else part)
                tendencies.append("表达可能更内敛谨慎")
            elif arousal < self.LOW_AROUSAL_THRESHOLD:
                mood = "flat"
                part = "有些疲惫或消沉"
                summary_parts.append(f"{tone_prefix}{part}" if tone_prefix else part)
                tendencies.append("表达活跃度可能降低")
            else:
                mood = "uneasy"
                part = "情绪偏低"
                summary_parts.append(f"{tone_prefix}{part}" if tone_prefix else part)
                tendencies.append("表达主动性可能减弱")
        else:
            mood = "neutral"
            summary_parts.append("情绪平稳")
            tendencies.append("保持自然表达倾向")

        # 细节维度
        if anxiety > self.HIGH_ANXIETY_THRESHOLD:
            summary_parts.append("略感不安")
            if "更谨慎" not in tendencies and "内敛" not in "".join(tendencies):
                tendencies.append("可能存在谨慎表达倾向")

        if curiosity > self.HIGH_CURIOSITY_THRESHOLD:
            summary_parts.append("好奇心较浓")
            tendencies.append("探索话题意愿增强")

        if confidence < self.LOW_CONFIDENCE_THRESHOLD:
            summary_parts.append("自信心稍低")
            tendencies.append("表达确定感可能降低")

        summary = "，".join(summary_parts) + "。"
        unique_tendencies = list(dict.fromkeys(tendencies))

        return EmotionContext(
            summary=summary,
            mood=mood,
            expression_tendencies=unique_tendencies
        )