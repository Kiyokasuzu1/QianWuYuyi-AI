# -*- coding: utf-8 -*-
"""
情绪状态 (EmotionState) — Emotion System 2.0（E-Emotion-1 升级版）

职责：管理羽依的情绪状态。
仅包含数据协议，不负责文件存储、衰减或 Prompt 生成。

E-Emotion-1 变更（向后兼容增量）：
- 新增 5 个维度：stability(稳定性)、happiness(喜悦)、sadness(悲伤)、
  trust(信任,长期)、attachment(依恋,长期)
- to_dict 携带 schema_version 版本标记；旧数据 from_dict 自动补默认值
- 新增 snapshot()/restore() 快照与恢复
- 原有 6 维度、派生属性 dominant/intensity 逻辑不变
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional

from src.emotion.emotion_delta import EmotionDelta

# Emotion System 2.0 状态协议版本
SCHEMA_VERSION = 2

# 维度分类：短期维度（快衰减）与长期维度（慢衰减）
SHORT_TERM_DIMENSIONS = (
    "valence", "arousal", "happiness", "sadness", "anxiety", "energy", "curiosity",
)
LONG_TERM_DIMENSIONS = ("trust", "attachment", "stability", "confidence")


@dataclass
class EmotionState:
    valence: float = 0.0       # 愉悦，-1 ~ 1
    arousal: float = 0.5       # 激活，0 ~ 1
    curiosity: float = 0.5     # 好奇，0 ~ 1
    anxiety: float = 0.0       # 不安，0 ~ 1
    confidence: float = 0.5    # 自信，0 ~ 1
    energy: float = 0.5        # 精力，0 ~ 1
    # ---- Emotion System 2.0 新增维度 ----
    stability: float = 0.7     # 稳定性，0 ~ 1（高=不易被单次事件扰动）
    happiness: float = 0.5     # 喜悦，0 ~ 1
    sadness: float = 0.0       # 悲伤，0 ~ 1
    trust: float = 0.5         # 信任，0 ~ 1（长期）
    attachment: float = 0.5    # 依恋，0 ~ 1（长期）
    updated_at: Optional[str] = None  # 最后更新时间，None 时自动生成

    def __post_init__(self):
        # 边界保护
        self.valence = max(-1.0, min(1.0, self.valence))
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.curiosity = max(0.0, min(1.0, self.curiosity))
        self.anxiety = max(0.0, min(1.0, self.anxiety))
        self.confidence = max(0.0, min(1.0, self.confidence))
        self.energy = max(0.0, min(1.0, self.energy))
        # Emotion 2.0 新维度边界保护
        self.stability = max(0.0, min(1.0, self.stability))
        self.happiness = max(0.0, min(1.0, self.happiness))
        self.sadness = max(0.0, min(1.0, self.sadness))
        self.trust = max(0.0, min(1.0, self.trust))
        self.attachment = max(0.0, min(1.0, self.attachment))

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
        # Emotion 2.0 新维度
        new.stability = max(0.0, min(1.0, self.stability + delta.stability))
        new.happiness = max(0.0, min(1.0, self.happiness + delta.happiness))
        new.sadness = max(0.0, min(1.0, self.sadness + delta.sadness))
        new.trust = max(0.0, min(1.0, self.trust + delta.trust))
        new.attachment = max(0.0, min(1.0, self.attachment + delta.attachment))

        new.updated_at = datetime.now().isoformat()
        return new

    @property
    def dominant(self) -> str:
        """主导情绪标签（派生属性，从 valence/arousal/anxiety/curiosity 计算）。

        与 EmotionContextProvider 的 mood 逻辑保持一致，供 prompt 注入使用。
        """
        if self.anxiety > 0.6:
            return "anxious"
        if self.valence > 0.3:
            if self.arousal > 0.7:
                return "joyful"
            if self.arousal < 0.3:
                return "serene"
            return "positive"
        if self.valence < -0.3:
            if self.arousal > 0.7:
                return "tense"
            if self.arousal < 0.3:
                return "flat"
            return "uneasy"
        return "neutral"

    @property
    def intensity(self) -> float:
        """综合情绪强度（派生属性，valence 与 arousal 加权）。"""
        return abs(self.valence) * 0.6 + self.arousal * 0.4

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "curiosity": self.curiosity,
            "anxiety": self.anxiety,
            "confidence": self.confidence,
            "energy": self.energy,
            # Emotion 2.0 新维度
            "stability": self.stability,
            "happiness": self.happiness,
            "sadness": self.sadness,
            "trust": self.trust,
            "attachment": self.attachment,
            "updated_at": self.updated_at if self.updated_at else "",
            # 派生属性（便于序列化消费）
            "dominant": self.dominant,
            "intensity": self.intensity,
            # 协议版本标记（版本兼容：旧读取方忽略未知键）
            "schema_version": SCHEMA_VERSION,
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
            # Emotion 2.0：旧数据缺字段时自动补默认值（版本兼容）
            stability=data.get("stability", 0.7),
            happiness=data.get("happiness", 0.5),
            sadness=data.get("sadness", 0.0),
            trust=data.get("trust", 0.5),
            attachment=data.get("attachment", 0.5),
            updated_at=data.get("updated_at", ""),  # 空字符串会被保留，不会触发自动生成
        )

    # --------------------------------------------------------
    # Emotion System 2.0：快照与恢复
    # --------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """生成带版本与时间戳的完整快照（用于审计/回滚）。"""
        return {
            "schema_version": SCHEMA_VERSION,
            "snapshot_at": datetime.now().isoformat(),
            "state": self.to_dict(),
        }

    @classmethod
    def restore(cls, snapshot: Dict[str, Any]) -> "EmotionState":
        """从快照恢复（兼容直接传入的旧式扁平状态 dict）。"""
        if isinstance(snapshot, dict) and isinstance(snapshot.get("state"), dict):
            return cls.from_dict(snapshot["state"])
        return cls.from_dict(snapshot if isinstance(snapshot, dict) else {})
