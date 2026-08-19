"""
情绪事件 (EmotionEvent)
表示一次可能引发情绪变化的事件。
"""
from dataclasses import dataclass


@dataclass
class EmotionEvent:
    event_type: str          # 事件类型："user_praise" / "user_conflict" / "achievement" 等
    intensity: float = 0.5   # 事件强度 0~1，自动 clamp
    description: str = ""    # 自然语言描述（可选，供未来 LLM 分析）
    source: str = "interaction"  # 来源："user" / "system" / "reflection" / "memory"

    def __post_init__(self):
        self.intensity = max(0.0, min(1.0, self.intensity))