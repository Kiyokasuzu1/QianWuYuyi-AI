# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_policy.py

Phase C.9.0 Initiative Policy.

只负责规则判断:
  - cooldown: 距离上一次主动的最小时间间隔
  - frequency_limit: 单位窗口内主动次数上限
  - priority_threshold: 优先级低于阈值不主动
  - relationship_level: 关系不足时降低概率
  - user_preference: 用户不喜欢主动时降低

不修改 Personality / SelfModel / Relationship / Memory。
"""
from __future__ import annotations

import copy
import logging
import time
from typing import Any, Dict, FrozenSet, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema + constants
# ============================================================

INITIATIVE_POLICY_SCHEMA_VERSION = "1.0"
INITIATIVE_POLICY_NAME = "initiative_policy"
INITIATIVE_POLICY_VERSION = "1.0.0"

# Cooldown (秒):默认 4 小时
DEFAULT_COOLDOWN_SECONDS = 4 * 3600

# Frequency limit:每 24 小时最多 3 次
DEFAULT_FREQUENCY_WINDOW_SECONDS = 24 * 3600
DEFAULT_FREQUENCY_LIMIT = 3

# Priority threshold
DEFAULT_PRIORITY_THRESHOLD = 0.6
MIN_PRIORITY = 0.0
MAX_PRIORITY = 1.0

# Relationship level
DEFAULT_MIN_RELATIONSHIP_LEVEL = 0.3  # 关系等级 < 0.3 降低主动概率

# User preference
DEFAULT_USER_PREFERENCE = "neutral"  # open / neutral / reserved

# Decisions
DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_DEFER = "defer"

ALL_DECISIONS: FrozenSet[str] = frozenset({
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_DEFER,
})

# Reasons
REASON_OK = "ok"
REASON_COOLDOWN = "in_cooldown"
REASON_FREQUENCY_LIMIT = "frequency_limit_exceeded"
REASON_PRIORITY_TOO_LOW = "priority_too_low"
REASON_RELATIONSHIP_LOW = "relationship_level_too_low"
REASON_USER_RESERVED = "user_preference_reserved"
REASON_DISABLED = "policy_disabled"
REASON_INVALID = "invalid_input"
REASON_ERROR = "policy_error"


# ============================================================
# Utilities
# ============================================================


def _now_ts() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
    except Exception:
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if v is None:
            return default
        return bool(v)
    except Exception:
        return default


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        return []
    except Exception:
        return []


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


def _safe_deepcopy(v: Any) -> Any:
    try:
        return copy.deepcopy(v)
    except Exception:
        if isinstance(v, dict):
            return {}
        if isinstance(v, list):
            return []
        return v


# ============================================================
# InitiativePolicy
# ============================================================


class InitiativePolicy:
    """
    Initiative Policy (Phase C.9.0 / v1.0).

    输入:
      - proposed: 提议的优先级 / trigger / 上下文
      - history_stats: 来自 InitiativeMemoryStore 的统计
        {
          "last_initiated_ts": float,        # 上次 initiated 的 ts
          "decision_count_in_window": int,   # 窗口内决策总数
          "initiated_count_in_window": int,  # 窗口内 initiated 数
        }
      - relationship_level: float (0.0 - 1.0)
      - user_preference: "open" / "neutral" / "reserved"

    输出:
      {
        "decision": "allow" | "deny" | "defer",
        "reasons": [str],
        "adjusted_priority": float,
        "policy_version": str,
      }
    """

    SCHEMA_VERSION = INITIATIVE_POLICY_SCHEMA_VERSION
    NAME = INITIATIVE_POLICY_NAME
    VERSION = INITIATIVE_POLICY_VERSION

    def __init__(
        self,
        enabled: bool = True,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        frequency_window_seconds: float = DEFAULT_FREQUENCY_WINDOW_SECONDS,
        frequency_limit: int = DEFAULT_FREQUENCY_LIMIT,
        priority_threshold: float = DEFAULT_PRIORITY_THRESHOLD,
        min_relationship_level: float = DEFAULT_MIN_RELATIONSHIP_LEVEL,
        reserved_preference_penalty: float = 0.5,
    ) -> None:
        self._enabled = bool(enabled)
        self._cooldown_seconds = float(cooldown_seconds)
        self._frequency_window_seconds = float(frequency_window_seconds)
        self._frequency_limit = int(frequency_limit)
        self._priority_threshold = float(priority_threshold)
        self._min_relationship_level = float(min_relationship_level)
        self._reserved_preference_penalty = float(reserved_preference_penalty)

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def evaluate(
        self,
        proposed: Dict[str, Any],
        history_stats: Optional[Dict[str, Any]] = None,
        relationship_level: float = 0.5,
        user_preference: str = DEFAULT_USER_PREFERENCE,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        评估是否允许本次主动。

        返回:
          {
            "decision": "allow" / "deny" / "defer",
            "reasons": [str, ...],
            "adjusted_priority": float,
            "schema_version": str,
          }
        """
        try:
            if not self._enabled:
                return self._build(
                    DECISION_DENY,
                    [REASON_DISABLED],
                    0.0,
                )
            proposed = _safe_dict(proposed)
            ts = float(now_ts) if now_ts is not None else _now_ts()
            history = _safe_dict(history_stats)

            priority = _safe_float(proposed.get("priority"), 0.0)
            priority = max(MIN_PRIORITY, min(MAX_PRIORITY, priority))

            reasons: List[str] = []

            # 1. priority threshold
            if priority < self._priority_threshold:
                reasons.append(REASON_PRIORITY_TOO_LOW)

            # 2. cooldown
            last_ts = _safe_float(history.get("last_initiated_ts"), 0.0)
            if last_ts > 0:
                elapsed = ts - last_ts
                if elapsed < self._cooldown_seconds:
                    reasons.append(REASON_COOLDOWN)

            # 3. frequency limit
            init_count = int(_safe_float(history.get("initiated_count_in_window"), 0))
            if init_count >= self._frequency_limit:
                reasons.append(REASON_FREQUENCY_LIMIT)

            # 4. relationship level
            rel_level = _safe_float(relationship_level, 0.5)
            if rel_level < self._min_relationship_level:
                reasons.append(REASON_RELATIONSHIP_LOW)

            # 5. user preference
            adjusted_priority = priority
            pref = _safe_str(user_preference, DEFAULT_USER_PREFERENCE)
            if pref == "reserved":
                reasons.append(REASON_USER_RESERVED)
                adjusted_priority = priority * self._reserved_preference_penalty

            # decision
            if not reasons:
                return self._build(DECISION_ALLOW, [REASON_OK], adjusted_priority)
            # relationship_low / priority_too_low / user_reserved -> defer
            # cooldown / frequency_limit -> deny
            if REASON_COOLDOWN in reasons or REASON_FREQUENCY_LIMIT in reasons:
                return self._build(DECISION_DENY, reasons, adjusted_priority)
            return self._build(DECISION_DEFER, reasons, adjusted_priority)
        except Exception as exc:
            logger.warning("[InitiativePolicy] evaluate failed: %s", exc)
            return self._build(
                DECISION_DEFER,
                [REASON_ERROR],
                0.0,
                error=str(exc),
            )

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "enabled": self._enabled,
            "cooldown_seconds": self._cooldown_seconds,
            "frequency_window_seconds": self._frequency_window_seconds,
            "frequency_limit": self._frequency_limit,
            "priority_threshold": self._priority_threshold,
            "min_relationship_level": self._min_relationship_level,
            "reserved_preference_penalty": self._reserved_preference_penalty,
        }

    def _build(
        self,
        decision: str,
        reasons: List[str],
        adjusted_priority: float,
        error: str = "",
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "decision": decision,
            "reasons": _safe_list(reasons),
            "adjusted_priority": float(adjusted_priority),
            "schema_version": self.SCHEMA_VERSION,
        }
        if error:
            out["error"] = error
        return out


def create_initiative_policy(
    enabled: bool = True,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    frequency_window_seconds: float = DEFAULT_FREQUENCY_WINDOW_SECONDS,
    frequency_limit: int = DEFAULT_FREQUENCY_LIMIT,
    priority_threshold: float = DEFAULT_PRIORITY_THRESHOLD,
    min_relationship_level: float = DEFAULT_MIN_RELATIONSHIP_LEVEL,
    reserved_preference_penalty: float = 0.5,
) -> InitiativePolicy:
    return InitiativePolicy(
        enabled=enabled,
        cooldown_seconds=cooldown_seconds,
        frequency_window_seconds=frequency_window_seconds,
        frequency_limit=frequency_limit,
        priority_threshold=priority_threshold,
        min_relationship_level=min_relationship_level,
        reserved_preference_penalty=reserved_preference_penalty,
    )
