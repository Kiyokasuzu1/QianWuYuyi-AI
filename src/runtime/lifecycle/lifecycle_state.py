# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_state.py

Phase 5.0-D1: LifecycleManager 自身状态机。

状态:
    CREATED → STARTING → RUNNING ⇄ PAUSED
                          ↓
                       DRAINING → STOPPED
                          ↓
                       FAILED → STOPPED
                          ↓
                       STOPPED (启动失败时)

不变量:
- 状态转移由 LifecycleStateMachine 守护
- 非法转移不破坏当前状态(返回 False,记录 warning)
- 不抛异常(避免污染主流程)
- 不依赖任何业务模块
- 线程安全(RLock)
"""
from __future__ import annotations

import enum
import logging
import threading
from typing import Any, Dict, List, Optional, Set


logger = logging.getLogger(__name__)


# ============================================================
# 状态枚举
# ============================================================
class LifecycleState(str, enum.Enum):
    """Runtime Lifecycle Manager 状态枚举。"""

    CREATED = "CREATED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    DRAINING = "DRAINING"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


# ============================================================
# 合法状态转移表
# ============================================================
_VALID_TRANSITIONS: Dict[LifecycleState, Set[LifecycleState]] = {
    LifecycleState.CREATED: {
        LifecycleState.STARTING,
        LifecycleState.STOPPED,  # 创建后未启动即销毁
    },
    LifecycleState.STARTING: {
        LifecycleState.RUNNING,
        LifecycleState.FAILED,  # 启动过程中失败
        LifecycleState.STOPPED,
    },
    LifecycleState.RUNNING: {
        LifecycleState.PAUSED,
        LifecycleState.DRAINING,
        LifecycleState.FAILED,  # 运行中异常
        LifecycleState.STOPPED,  # 强制停止
    },
    LifecycleState.PAUSED: {
        LifecycleState.RUNNING,
        LifecycleState.DRAINING,
        LifecycleState.STOPPED,
    },
    LifecycleState.DRAINING: {
        LifecycleState.STOPPED,
        LifecycleState.FAILED,  # 排错过程中异常
    },
    LifecycleState.FAILED: {
        LifecycleState.STOPPED,
    },
    LifecycleState.STOPPED: set(),  # 终态
}


# ============================================================
# 状态机
# ============================================================
class LifecycleStateMachine:
    """LifecycleManager 状态机。

    特性:
    - 状态转移: transition(new_state) -> bool
    - 非法转移: 返回 False,记录 warning,不抛异常
    - 状态查询: state, can_transition, is_terminal
    - 历史: transition_history(limit)
    - 线程安全: RLock 保护
    """

    __slots__ = (
        "_state",
        "_lock",
        "_history",
        "_max_history",
    )

    def __init__(
        self,
        initial: LifecycleState = LifecycleState.CREATED,
        max_history: int = 100,
    ) -> None:
        self._lock = threading.RLock()
        self._state = initial
        self._history: List[Dict[str, Any]] = []
        self._max_history = max(0, int(max_history))

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    @property
    def state(self) -> LifecycleState:
        with self._lock:
            return self._state

    @property
    def value(self) -> str:
        with self._lock:
            return self._state.value

    def can_transition(self, new_state: LifecycleState) -> bool:
        with self._lock:
            return new_state in _VALID_TRANSITIONS.get(self._state, set())

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return len(_VALID_TRANSITIONS.get(self._state, set())) == 0

    # --------------------------------------------------------
    # 转移
    # --------------------------------------------------------
    def transition(
        self,
        new_state: LifecycleState,
        *,
        reason: Optional[str] = None,
    ) -> bool:
        """尝试转移到新状态。

        返回:
        - True: 转移成功
        - False: 非法转移,状态保持不变(不抛异常)

        参数:
        - new_state: 目标状态
        - reason: 转移原因(用于日志和历史)
        """
        with self._lock:
            old = self._state
            if old == new_state:
                # 幂等:相同状态视为成功
                return True

            valid_targets = _VALID_TRANSITIONS.get(old, set())
            if new_state not in valid_targets:
                logger.warning(
                    "[LifecycleStateMachine] 非法状态转移: %s -> %s (忽略, reason=%s)",
                    old.value,
                    new_state.value,
                    reason,
                )
                return False

            self._state = new_state
            self._record_history(old, new_state, reason)
            logger.debug(
                "[LifecycleStateMachine] 状态转移: %s -> %s (reason=%s)",
                old.value,
                new_state.value,
                reason,
            )
            return True

    def force_set(
        self,
        new_state: LifecycleState,
        *,
        reason: Optional[str] = None,
    ) -> None:
        """强制设置状态(绕过合法性检查)。仅供内部使用。

        主要用于 stop() 在异常情况下的兜底。
        """
        with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state
            self._record_history(old, new_state, reason, forced=True)
            logger.warning(
                "[LifecycleStateMachine] 强制状态转移: %s -> %s (reason=%s)",
                old.value,
                new_state.value,
                reason,
            )

    # --------------------------------------------------------
    # 历史
    # --------------------------------------------------------
    def _record_history(
        self,
        old: LifecycleState,
        new: LifecycleState,
        reason: Optional[str],
        forced: bool = False,
    ) -> None:
        if self._max_history <= 0:
            return
        entry = {
            "from": old.value,
            "to": new.value,
            "reason": str(reason) if reason else "",
            "forced": bool(forced),
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            # 截断最早的
            del self._history[: len(self._history) - self._max_history]

    def transition_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """返回状态转移历史(从早到晚)。

        参数:
        - limit: 仅返回最近 N 条;None = 全部
        """
        with self._lock:
            data = list(self._history)
        if limit is None or limit < 0:
            return data
        if limit == 0:
            return []
        return data[-limit:]

    # --------------------------------------------------------
    # 健康度
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "is_terminal": self.is_terminal,
                "valid_targets": sorted(
                    s.value for s in _VALID_TRANSITIONS.get(self._state, set())
                ),
                "history_size": len(self._history),
            }

    # --------------------------------------------------------
    # 内部 (用于测试)
    # --------------------------------------------------------
    def reset(self) -> None:
        """重置回 CREATED。仅供测试用。"""
        with self._lock:
            self._state = LifecycleState.CREATED
            self._history.clear()

    def __repr__(self) -> str:
        with self._lock:
            return f"LifecycleStateMachine(state={self._state.value!r})"


# ============================================================
# 工具函数
# ============================================================
def is_valid_transition(
    old: LifecycleState,
    new: LifecycleState,
) -> bool:
    """检查 old -> new 是否为合法状态转移。"""
    return new in _VALID_TRANSITIONS.get(old, set())


def all_valid_targets(
    state: LifecycleState,
) -> List[LifecycleState]:
    """返回某状态所有合法目标状态(无序)。"""
    return list(_VALID_TRANSITIONS.get(state, set()))


__all__ = [
    "LifecycleState",
    "LifecycleStateMachine",
    "is_valid_transition",
    "all_valid_targets",
]
