"""
情绪变化量 (EmotionDelta)
表示一次事件带来的情绪变化，不是状态本身。

Emotion System 2.0（E-Emotion-1）：新增 stability/happiness/sadness/trust/attachment
5 个维度（默认 0，旧构造调用不受影响）。
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
    # Emotion System 2.0 新增维度
    stability: float = 0.0     # 稳定性变化，-1 ~ 1
    happiness: float = 0.0     # 喜悦变化，-1 ~ 1
    sadness: float = 0.0       # 悲伤变化，-1 ~ 1
    trust: float = 0.0         # 信任变化，-1 ~ 1（长期）
    attachment: float = 0.0    # 依恋变化，-1 ~ 1（长期）