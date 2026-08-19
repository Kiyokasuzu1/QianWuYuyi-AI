"""
信号衰减器 (SignalDecay) v1.1

v1.1 修正：修复 timezone 兼容问题
"""

from typing import List
from datetime import datetime
import math


class SignalDecay:

    DECAY_RATES = {
        "trait_state": 0.001,
        "value_system": 0.0005,
        "personality_tension": 0.002,
        "default": 0.001,
    }

    def apply(self, signals: List[dict]) -> List[dict]:
        decayed = []
        now = datetime.now()

        for sig in signals:
            strength = sig.get("strength", 0.5)
            source = sig.get("source", "default")
            timestamp = sig.get("timestamp", "")

            decay_rate = self.DECAY_RATES.get(source, 0.001)
            days = 0
            if timestamp:
                try:
                    created = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    # 统一为 naive datetime 以便计算
                    if created.tzinfo is not None:
                        created = created.replace(tzinfo=None)
                    days = (now - created).days
                except (ValueError, TypeError):
                    pass

            decayed_strength = strength * math.exp(-decay_rate * max(days, 0))
            new_strength = round(max(0.1, decayed_strength), 3)

            decayed.append({
                **sig,
                "strength": new_strength,
                "original_strength": strength,
                "days_since_created": days,
            })

        return decayed