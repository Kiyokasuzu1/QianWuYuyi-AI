# -*- coding: utf-8 -*-
"""
src/initiative/initiative_budget.py

v1.3 Phase 5.5: Initiative 统一预算策略(InitiativeBudgetPolicy)。

职责(只读判断 + 放行后内存计数):
    Action
      ↓ check(action)
    (per_goal_daily_limit / per_user_cooldown_seconds / global_daily_limit)
      ↓
    (allowed, reason)

红线:
- 只读判断, 不修改 Action;
- 不发送 / 不 dispatch / 不调用 LLM;
- 内存态计数(进程生命周期, 不落盘), 不触碰任何状态域;
- 不读取 personality / self_model / emotion / relationship;
- 默认由调用方决定是否启用(不启用 = 无预算限制 = 既有行为)。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_PER_GOAL_DAILY_LIMIT = 2
DEFAULT_PER_USER_COOLDOWN_SECONDS = 1800.0  # 30 分钟
DEFAULT_GLOBAL_DAILY_LIMIT = 10

REASON_PER_GOAL = "budget_per_goal_daily_limit"
REASON_USER_COOLDOWN = "budget_user_cooldown"
REASON_GLOBAL = "budget_global_daily_limit"


class InitiativeBudgetPolicy:
    """Initiative Action 预算策略(统一 per-goal / per-user / global 限制)。"""

    def __init__(
        self,
        *,
        per_goal_daily_limit: int = DEFAULT_PER_GOAL_DAILY_LIMIT,
        per_user_cooldown_seconds: float = DEFAULT_PER_USER_COOLDOWN_SECONDS,
        global_daily_limit: int = DEFAULT_GLOBAL_DAILY_LIMIT,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        try:
            self._per_goal_limit = max(0, int(per_goal_daily_limit))
        except (TypeError, ValueError):
            self._per_goal_limit = DEFAULT_PER_GOAL_DAILY_LIMIT
        try:
            self._per_user_cooldown = max(0.0, float(per_user_cooldown_seconds))
        except (TypeError, ValueError):
            self._per_user_cooldown = DEFAULT_PER_USER_COOLDOWN_SECONDS
        try:
            self._global_limit = max(0, int(global_daily_limit))
        except (TypeError, ValueError):
            self._global_limit = DEFAULT_GLOBAL_DAILY_LIMIT
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._goal_counts: Dict[Tuple[str, str], int] = {}
        self._global_counts: Dict[str, int] = {}
        self._last_allowed_at: Dict[str, datetime] = {}

    # --------------------------------------------------------
    # 接口
    # --------------------------------------------------------
    def check(self, action: Any) -> Tuple[bool, str]:
        """只读判断是否超预算。返回 (allowed, reason)。"""
        try:
            _now = self._now()
            _day = str(_now.date().isoformat())
            _payload = getattr(action, "payload", None) or {}
            if not isinstance(_payload, dict):
                return False, "budget_invalid_action"
            _goal_ref = str(_payload.get("goal_reference", "") or "").strip()
            _target = str(_payload.get("target_user", "") or "").strip()

            with self._lock:
                # 1) global daily
                if (
                    self._global_limit > 0
                    and self._global_counts.get(_day, 0) >= self._global_limit
                ):
                    return False, REASON_GLOBAL
                # 2) per-goal daily
                if _goal_ref and self._per_goal_limit > 0:
                    _key = (_day, _goal_ref)
                    if self._goal_counts.get(_key, 0) >= self._per_goal_limit:
                        return False, REASON_PER_GOAL
                # 3) per-user cooldown
                if _target and self._per_user_cooldown > 0:
                    _last = self._last_allowed_at.get(_target)
                    if _last is not None:
                        _elapsed = (_now - _last).total_seconds()
                        if _elapsed < self._per_user_cooldown:
                            return False, REASON_USER_COOLDOWN
            return True, "ok"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativeBudget] 检查异常(已隔离, fail-closed): %s", exc)
            return False, "budget_error"

    def record(self, action: Any) -> None:
        """放行后记录计数(仅内存)。"""
        try:
            _now = self._now()
            _day = str(_now.date().isoformat())
            _payload = getattr(action, "payload", None) or {}
            if not isinstance(_payload, dict):
                return
            _goal_ref = str(_payload.get("goal_reference", "") or "").strip()
            _target = str(_payload.get("target_user", "") or "").strip()
            with self._lock:
                if _goal_ref:
                    _key = (_day, _goal_ref)
                    self._goal_counts[_key] = self._goal_counts.get(_key, 0) + 1
                self._global_counts[_day] = self._global_counts.get(_day, 0) + 1
                if _target:
                    self._last_allowed_at[_target] = _now
        except Exception as exc:  # noqa: BLE001
            logger.warning("[InitiativeBudget] 记录失败(已隔离): %s", exc)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "per_goal_daily_limit": self._per_goal_limit,
                "per_user_cooldown_seconds": self._per_user_cooldown,
                "global_daily_limit": self._global_limit,
                "today_global_count": self._global_counts.get(
                    self._now().date().isoformat(), 0
                ),
            }

    def _now(self) -> datetime:
        try:
            _value = self._clock()
            if not isinstance(_value, datetime):
                raise TypeError("clock 必须返回 datetime")
            return _value
        except Exception:  # noqa: BLE001
            return datetime.now(timezone.utc)


__all__ = [
    "DEFAULT_PER_GOAL_DAILY_LIMIT",
    "DEFAULT_PER_USER_COOLDOWN_SECONDS",
    "DEFAULT_GLOBAL_DAILY_LIMIT",
    "REASON_PER_GOAL",
    "REASON_USER_COOLDOWN",
    "REASON_GLOBAL",
    "InitiativeBudgetPolicy",
]
