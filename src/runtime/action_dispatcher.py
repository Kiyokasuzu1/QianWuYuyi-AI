"""
ActionDispatcher —— 行动分发器

接收决策，转换为行动，
分发给已注册的处理函数。

设计原则：
- 解耦：决策引擎不关心具体如何执行
- 可扩展：通过 register 添加新行动类型
- 可追踪：记录行动历史
"""

import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from src.runtime.decision_engine import Decision


@dataclass
class Action:
    action_id: str
    action_type: str
    payload: Dict[str, Any]
    reason: str = ""
    status: str = "pending"
    result: Optional[Any] = None

    @classmethod
    def from_decision(cls, decision: Decision) -> "Action":
        """从决策创建行动"""
        return cls(
            action_id=f"act_{uuid.uuid4().hex[:8]}",
            action_type=decision.action_type,
            payload=decision.payload,
            reason=decision.reason,
        )


class ActionDispatcher:
    """
    行动分发器

    注册处理器并分发行动。
    """

    def __init__(self):
        self._handlers: Dict[str, Callable[[Action], Any]] = {}
        self._history: List[Action] = []
        self._max_history: int = 100

    def register(self, action_type: str, handler: Callable[[Action], Any]) -> None:
        """
        注册行动处理器

        Args:
            action_type: 行动类型
            handler: 处理函数，接收 Action，返回结果
        """
        self._handlers[action_type] = handler

    def unregister(self, action_type: str) -> None:
        """注销处理器"""
        self._handlers.pop(action_type, None)

    def dispatch(self, action: Action) -> Any:
        """
        分发行动

        Args:
            action: 行动对象

        Returns:
            处理结果，如果没有处理器则返回默认结果
        """
        if action.action_type in self._handlers:
            try:
                action.status = "executing"
                result = self._handlers[action.action_type](action)
                action.status = "completed"
                action.result = result
            except Exception as e:
                action.status = "failed"
                action.result = {"error": str(e)}
        else:
            action.status = "no_handler"
            action.result = {"status": "no_handler", "action_type": action.action_type}

        self._history.append(action)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return action.result

    def get_history(self, action_type: Optional[str] = None) -> List[Action]:
        """
        获取行动历史

        Args:
            action_type: 筛选特定类型，None 则返回全部

        Returns:
            行动列表
        """
        if action_type is None:
            return self._history.copy()
        return [a for a in self._history if a.action_type == action_type]

    def has_handler(self, action_type: str) -> bool:
        """检查是否有处理器"""
        return action_type in self._handlers