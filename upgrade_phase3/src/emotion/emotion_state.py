# -*- coding: utf-8 -*-
"""
情绪状态 (EmotionState) — Phase 9.0C 覆盖版

职责：管理羽依的短期情绪状态。
仅包含数据协议，不负责文件存储、衰减或 Prompt 生成。
衰减逻辑已迁移至独立的 EmotionDecay 类。
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional

from src.emotion.emotion_delta import EmotionDelta


@dataclass
class EmotionState:
    valence: float = 0.0       # 愉悦，-1 ~ 1
    arousal: float = 0.5       # 激活，0 ~ 1
    curiosity: float = 0.5     # 好奇，0 ~ 1
    anxiety: float = 0.0       # 不安，0 ~ 1
    confidence: float = 0.5    # 自信，0 ~ 1
    energy: float = 0.5        # 精力，0 ~ 1
    updated_at: Optional[str] = None  # 最后更新时间，None 时自动生成

    def __post_init__(self):
        # 边界保护
        self.valence = max(-1.0, min(1.0, self.valence))
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.curiosity = max(0.0, min(1.0, self.curiosity))
        self.anxiety = max(0.0, min(1.0, self.anxiety))
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.energy = max(0.0, min(1.0, self.energy))

        # 仅在未显式传入 updated_at 时自动生成
        if self.updated_at is None:
            self.updated_at = datetime.now().isoformat()

    def apply_delta(self, delta: EmotionDelta) -> "EmotionState":
        """
        安全应用情绪变化，返回新状态。
        原状态不变，返回新对象。
        """
        import copy
        new = copy.deepcopy(self)

        new.valence = max(-1.0, min(1.0, self.valence + delta.valence))
        new.arousal = max(0.0, min(1.0, self.arousal + delta.arousal))
        new.curiosity = max(0.0, min(1.0, self.curiosity + delta.curiosity))
        new.anxiety = max(0.0, min(1.0, self.anxiety + delta.anxiety))
        new.confidence = max(0.0, min(1.0, self.confidence + delta.confidence))
        new.energy = max(0.0, min(1.0, self.energy + delta.energy))

        new.updated_at = datetime.now().isoformat()
        return new

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "curiosity": self.curiosity,
            "anxiety": self.anxiety,
            "confidence": self.confidence,
            "energy": self.energy,
            "updated_at": self.updated_at if self.updated_at else "",
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EmotionState":
        return cls(
            valence=data.get("valence", 0.0),
            arousal=data.get("arousal", 0.5),
            curiosity=data.get("curiosity", 0.5),
            anxiety=data.get("anxiety", 0.0),
            confidence=data.get("confidence", 0.5),
            energy=data.get("energy", 0.5),
            updated_at=data.get("updated_at", ""),  # 空字符串会被保留，不会触发自动生成
        )