"""
情绪衰减 (EmotionDecay)
负责根据经过的时间让情绪值自然回归基线。
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