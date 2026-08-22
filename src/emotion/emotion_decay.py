"""
情绪衰减 (EmotionDecay)
负责根据经过的时间让情绪值自然回归基线。

Emotion System 2.0（E-Emotion-3 前置）：
- 新增 stability/happiness/sadness/trust/attachment 维度衰减配置
- 短期维度（valence/arousal/happiness/sadness/anxiety/energy/curiosity）快衰减
  （半衰期 22 分钟 ~ 60 分钟）
- 长期维度（trust/attachment）慢衰减（半衰期 7 天 / 30 天）——
  信任与依恋是长期积累，不随单次情绪波动快速回落
"""
import math
from datetime import datetime
from src.emotion.emotion_state import EmotionState


class EmotionDecay:
    # 衰减配置: (基线, lambda)
    # lambda = ln(2) / 半衰期(秒)
    CONFIG = {
        "valence":   (0.0, 0.000385),   # 半衰期 ~30 分钟
        "arousal":   (0.5, 0.000385),   # 半衰期 ~30 分钟
        "anxiety":   (0.0, 0.000513),   # 半衰期 ~22 分钟 (更快)
        "curiosity": (0.5, 0.000193),   # 半衰期 ~60 分钟 (更慢)
        "confidence":(0.5, 0.000257),   # 半衰期 ~45 分钟
        "energy":    (0.5, 0.000385),   # 半衰期 ~30 分钟
        # ---- Emotion System 2.0 新增维度 ----
        "happiness": (0.5, 0.000385),   # 半衰期 ~30 分钟（短期）
        "sadness":   (0.0, 0.000385),   # 半衰期 ~30 分钟（短期）
        "stability": (0.7, 0.0000963),  # 半衰期 ~2 小时（中速回归）
        "trust":     (0.5, 0.000001146),  # 半衰期 ~7 天（长期）
        "attachment":(0.5, 0.000000267),  # 半衰期 ~30 天（长期）
    }

    def apply(self, state: EmotionState, seconds: float) -> EmotionState:
        """
        根据时间间隔衰减情绪，返回新的 EmotionState。
        不影响原对象。
        """
        import copy
        new = copy.deepcopy(state)

        for dim, (baseline, lam) in self.CONFIG.items():
            current = getattr(new, dim)
            decayed = baseline + (current - baseline) * math.exp(-lam * seconds)
            # 防止越过基线
            if baseline > current:
                decayed = min(baseline, decayed)
            else:
                decayed = max(baseline, decayed)
            setattr(new, dim, round(decayed, 4))

        new.updated_at = datetime.now().isoformat()
        return new