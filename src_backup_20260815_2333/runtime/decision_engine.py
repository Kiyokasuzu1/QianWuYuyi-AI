"""
DecisionEngine —— 决策引擎

基于当前世界状态和自身状态，
评估是否产生行动决策。

设计原则：
- 规则驱动（第一阶段），后续可接入 LLM
- 可解释（每个决策附带 reason）
- 可配置（阈值可从外部调整）
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from src.runtime.world_state import WorldState


@dataclass
class Decision:
    action_type: str
    priority: float
    payload: Dict[str, Any]
    reason: str
    confidence: float = 0.5


class DecisionEngine:
    """
    决策引擎

    评估世界状态，输出决策列表。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        # 可配置阈值
        self.thresholds = {
            "initiative_min": self.config.get("initiative_min", 0.5),
            "social_need_min": self.config.get("social_need_min", 0.6),
            "energy_low": self.config.get("energy_low", 0.2),
            "curiosity_high": self.config.get("curiosity_high", 0.7),
        }

    def evaluate(self, world_state: WorldState) -> Optional[Decision]:
        """
        评估是否产生单一最高优先级决策

        Args:
            world_state: 当前世界状态

        Returns:
            决策或 None
        """
        decisions = self.evaluate_all(world_state)
        if decisions:
            return decisions[0]
        return None

    def evaluate_all(self, world_state: WorldState) -> List[Decision]:
        """
        评估所有可能的决策

        Args:
            world_state: 当前世界状态

        Returns:
            按优先级排序的决策列表
        """
        decisions = []
        ss = world_state.self_state

        # 规则 1：主动倾向高 + 社交需求高 → 主动说话
        if ss.initiative > self.thresholds["initiative_min"] and ss.social_need > self.thresholds["social_need_min"]:
            decisions.append(
                Decision(
                    action_type="send_message",
                    priority=ss.initiative * ss.social_need,
                    payload={"type": "proactive", "context": "主动关心"},
                    reason=f"initiative={ss.initiative:.2f}, social_need={ss.social_need:.2f}",
                    confidence=0.6,
                )
            )

        # 规则 2：精力过低 → 休息
        if ss.energy < self.thresholds["energy_low"]:
            decisions.append(
                Decision(
                    action_type="rest",
                    priority=0.9,
                    payload={"duration_minutes": 30, "reason": "energy_low"},
                    reason=f"energy={ss.energy:.2f} < {self.thresholds['energy_low']}",
                    confidence=0.8,
                )
            )

        # 规则 3：好奇心高 + 有环境变化 → 探索/询问
        if ss.curiosity > self.thresholds["curiosity_high"] and world_state.environment:
            decisions.append(
                Decision(
                    action_type="explore",
                    priority=ss.curiosity * 0.7,
                    payload={"type": "ask", "context": "好奇用户在做什么"},
                    reason=f"curiosity={ss.curiosity:.2f}",
                    confidence=0.5,
                )
            )

        # 按优先级排序
        decisions.sort(key=lambda d: d.priority, reverse=True)
        return decisions