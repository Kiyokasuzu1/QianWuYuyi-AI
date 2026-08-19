"""
情绪变化量 (EmotionDelta)
表示一次事件带来的情绪变化，不是状态本身。
"""
from dataclasses import dataclass


@dataclass
class EmotionDelta:
    valence: float = 0.0       # 愉悦变化，-1 ~ 1
    arousal: float = 0.0       # 激活变化，-1 ~ 1
    curiosity: float = 0.0     # 好奇变化，-1 ~ 1
    anxiety: float = 0.0       # 不安变化，-1 ~ 1
    confidence: float = 0.0    # 自信变化，-1 ~ 1
    energy: float = 0.0        # 精力变化，-1 ~ 1