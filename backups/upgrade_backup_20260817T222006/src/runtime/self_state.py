"""
SelfState —— 羽依自身状态

7 个核心参数：
- energy: 精力
- mood: 当前情绪
- curiosity: 好奇程度
- social_need: 社交需求
- focus: 专注度
- trust: 对用户的信任
- initiative: 主动倾向

设计原则：
- 轻量，不复杂
- 可序列化/反序列化
- 支持自然衰减
- 受事件影响更新
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class SelfState:
    energy: float = 0.7
    mood: str = "平静"
    curiosity: float = 0.5
    social_need: float = 0.4
    focus: float = 0.6
    trust: float = 0.5
    initiative: float = 0.3
    last_updated: float = field(default_factory=time.time)

    # 自然衰减率（每秒）
    DECAY_RATES: Dict[str, float] = field(
        default_factory=lambda: {
            "energy": 0.05 / 3600,
            "curiosity": 0.03 / 3600,
            "social_need": 0.08 / 3600,
            "focus": 0.1 / 3600,
            "trust": 0.01 / 3600,
            "initiative": 0.04 / 3600,
        }
    )

    def decay(self, delta_seconds: float) -> Dict[str, float]:
        """
        状态自然衰减

        Args:
            delta_seconds: 经过的秒数

        Returns:
            各参数变化量的字典
        """
        changes = {}
        for key, rate in self.DECAY_RATES.items():
            old_value = getattr(self, key)
            if isinstance(old_value, (int, float)):
                new_value = max(0.0, min(1.0, old_value - rate * delta_seconds))
                setattr(self, key, new_value)
                changes[key] = round(new_value - old_value, 6)
        self.last_updated = time.time()
        return changes

    def update_from_event(self, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据事件更新状态

        Args:
            event_type: 事件类型
            event_data: 事件数据

        Returns:
            变化量的字典
        """
        changes = {}

        if event_type == "user.input":
            # 用户输入：社交需求下降，精力微升
            old = self.social_need
            self.social_need = max(0.0, self.social_need - 0.1)
            changes["social_need"] = round(self.social_need - old, 3)

            old = self.energy
            self.energy = min(1.0, self.energy + 0.05)
            changes["energy"] = round(self.energy - old, 3)

        elif event_type == "emotion.state_changed":
            # 情绪变化事件
            new_mood = event_data.get("current_emotion", self.mood)
            if new_mood != self.mood:
                changes["mood"] = {"from": self.mood, "to": new_mood}
                self.mood = new_mood

        elif event_type == "action.proactive_executed":
            # 主动行动后：主动倾向微降（释放压力）
            old = self.initiative
            self.initiative = max(0.0, self.initiative - 0.05)
            changes["initiative"] = round(self.initiative - old, 3)

        elif event_type == "system.tick":
            # 长时间无交互：社交需求缓慢上升
            tick_type = event_data.get("tick_type", "minute")
            if tick_type == "hour":
                old = self.social_need
                self.social_need = min(1.0, self.social_need + 0.05)
                changes["social_need"] = round(self.social_need - old, 3)

        self.last_updated = time.time()
        return changes

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "energy": self.energy,
            "mood": self.mood,
            "curiosity": self.curiosity,
            "social_need": self.social_need,
            "focus": self.focus,
            "trust": self.trust,
            "initiative": self.initiative,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfState":
        """从字典反序列化"""
        return cls(
            energy=data.get("energy", 0.7),
            mood=data.get("mood", "平静"),
            curiosity=data.get("curiosity", 0.5),
            social_need=data.get("social_need", 0.4),
            focus=data.get("focus", 0.6),
            trust=data.get("trust", 0.5),
            initiative=data.get("initiative", 0.3),
            last_updated=data.get("last_updated", time.time()),
        )

    @classmethod
    def default(cls) -> "SelfState":
        """创建默认状态"""
        return cls()