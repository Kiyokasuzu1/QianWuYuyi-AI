# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_delivery_policy.py

Phase C.9.2 Initiative Delivery Policy —— 主动消息投递策略

职责:
  - 校验 initiative_output 是否允许投递
  - 检查项:
      1. decision 必须是 "initiate"(否则 blocked)
      2. confidence >= 阈值(默认 0.85)
      3. priority >= 阈值(默认 0.5)
      4. cooldown:距离上次主动消息不足 N 秒 → blocked
      5. relationship:关系等级不足 → blocked(只读)
      6. user_preference:reserved / silent → blocked

**只读**:
  - 读取 initiative_output(不可修改)
  - 读取 relationship snapshot(不可修改)
  - 读取 user preference(不可修改)

**绝不**:
  - 修改 Personality / SelfModel / Growth / Relationship
  - 调用任何 QQ / Web / 聊天发送接口
  - 修改 initiative_output 自身(只读取并构造 policy_result)
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Schema + 常量
# ============================================================

INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION = "1.0"
INITIATIVE_DELIVERY_POLICY_NAME = "initiative_delivery_policy"
INITIATIVE_DELIVERY_POLICY_VERSION = "1.0.0"

# Decision labels
DECISION_ALLOW = "allow"
DECISION_BLOCK = "block"
ALL_DELIVERY_DECISIONS: List[str] = [DECISION_ALLOW, DECISION_BLOCK]

# Reason codes
REASON_OK = "ok"
REASON_NOT_INITIATE = "decision_not_initiate"
REASON_CONFIDENCE_LOW = "confidence_too_low"
REASON_PRIORITY_LOW = "priority_too_low"
REASON_COOLDOWN = "cooldown_active"
REASON_RELATIONSHIP_LOW = "relationship_too_low"
REASON_USER_PREFERENCE = "user_preference_blocks"
REASON_INVALID_OUTPUT = "invalid_output"
REASON_MISSING_CONTENT = "missing_message_content"
REASON_DISABLED = "policy_disabled"
REASON_ERROR = "policy_error"

ALL_DELIVERY_REASONS: List[str] = [
    REASON_OK,
    REASON_NOT_INITIATE,
    REASON_CONFIDENCE_LOW,
    REASON_PRIORITY_LOW,
    REASON_COOLDOWN,
    REASON_RELATIONSHIP_LOW,
    REASON_USER_PREFERENCE,
    REASON_INVALID_OUTPUT,
    REASON_MISSING_CONTENT,
    REASON_DISABLED,
    REASON_ERROR,
]

# Defaults
DEFAULT_CONFIDENCE_THRESHOLD = 0.85
DEFAULT_PRIORITY_THRESHOLD = 0.5
DEFAULT_COOLDOWN_SECONDS = 1800  # 30 分钟(主动消息默认冷却)
DEFAULT_MIN_RELATIONSHIP_LEVEL = 0.3
DEFAULT_USER_PREFERENCE = "open"
DEFAULT_ENABLED = True


# ============================================================
# 工具
# ============================================================


def _now_ts() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return "1970-01-01T00:00:00Z"


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
    except (TypeError, ValueError):
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if v is None:
            return default
        return bool(v)
    except Exception:
        return default


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        if isinstance(v, tuple):
            return list(v)
        return []
    except Exception:
        return []


def _extract_relationship_level(relationship_snapshot: Any) -> float:
    """从 relationship snapshot 提取 level(只读)。"""
    if not isinstance(relationship_snapshot, dict):
        return 0.5
    # 尝试多个常见位置
    # 1) 直接 level
    lvl = _safe_float(relationship_snapshot.get("level", None), -1.0)
    if lvl >= 0.0:
        return max(0.0, min(1.0, lvl))
    # 2) current_metrics.familiarity
    metrics = relationship_snapshot.get("current_metrics")
    if isinstance(metrics, dict):
        lvl = _safe_float(metrics.get("familiarity", None), -1.0)
        if lvl >= 0.0:
            return max(0.0, min(1.0, lvl))
    return 0.5


def _extract_user_preference(initiative_output: Any, metadata: Any = None) -> str:
    """从 initiative_output.metadata 或单独 metadata 提取用户偏好。"""
    if isinstance(initiative_output, dict):
        meta = initiative_output.get("metadata")
        if isinstance(meta, dict):
            pref = _safe_str(meta.get("user_preference", ""), "")
            if pref:
                return pref
    if isinstance(metadata, dict):
        pref = _safe_str(metadata.get("user_preference", ""), "")
        if pref:
            return pref
    return DEFAULT_USER_PREFERENCE


def _build_block(
    reasons: List[str],
    adjusted_priority: float = 0.0,
    cooldown_remaining: float = 0.0,
) -> Dict[str, Any]:
    return {
        "decision": DECISION_BLOCK,
        "reasons": [str(r) for r in reasons],
        "adjusted_priority": float(adjusted_priority),
        "cooldown_remaining_seconds": float(cooldown_remaining),
        "schema_version": INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION,
        "timestamp": _now_iso(),
    }


def _build_allow(
    reasons: List[str],
    adjusted_priority: float,
) -> Dict[str, Any]:
    return {
        "decision": DECISION_ALLOW,
        "reasons": [str(r) for r in reasons],
        "adjusted_priority": float(adjusted_priority),
        "cooldown_remaining_seconds": 0.0,
        "schema_version": INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION,
        "timestamp": _now_iso(),
    }


# ============================================================
# InitiativeDeliveryPolicy
# ============================================================


class InitiativeDeliveryPolicy:
    """
    Initiative Delivery Policy (Phase C.9.2 / v1.0)

    **Read-Only Policy** —— 只读取 initiative_output / relationship snapshot,
    返回 allow / block 决策。

    规则顺序(短路):
      1) policy disabled → block
      2) output invalid → block
      3) decision != "initiate" → block
      4) missing content → block
      5) confidence < threshold → block
      6) priority < threshold → block
      7) cooldown active → block
      8) relationship < min → block
      9) user_preference reserved / silent → block
      else → allow

    全部 fail-soft:异常时 block + reason="policy_error"。
    """

    SCHEMA_VERSION = INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION
    NAME = INITIATIVE_DELIVERY_POLICY_NAME
    VERSION = INITIATIVE_DELIVERY_POLICY_VERSION

    def __init__(
        self,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        priority_threshold: float = DEFAULT_PRIORITY_THRESHOLD,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        min_relationship_level: float = DEFAULT_MIN_RELATIONSHIP_LEVEL,
        enabled: bool = DEFAULT_ENABLED,
    ) -> None:
        self._confidence_threshold = float(confidence_threshold)
        self._priority_threshold = float(priority_threshold)
        self._cooldown_seconds = float(cooldown_seconds)
        self._min_relationship_level = float(min_relationship_level)
        self._enabled = bool(enabled)

        self._lock = threading.RLock()
        # last delivery timestamp(用于 cooldown 检查)
        self._last_delivery_ts: float = 0.0
        # 统计
        self._evaluate_count: int = 0
        self._allow_count: int = 0
        self._block_count: int = 0

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_confidence_threshold(self, threshold: float) -> None:
        with self._lock:
            self._confidence_threshold = float(threshold)

    def set_priority_threshold(self, threshold: float) -> None:
        with self._lock:
            self._priority_threshold = float(threshold)

    def set_cooldown_seconds(self, seconds: float) -> None:
        with self._lock:
            self._cooldown_seconds = float(max(0.0, seconds))

    def set_min_relationship_level(self, level: float) -> None:
        with self._lock:
            self._min_relationship_level = float(max(0.0, min(1.0, level)))

    def record_delivery(self, timestamp: Optional[float] = None) -> None:
        """记录一次成功投递(用于 cooldown 跟踪)。"""
        with self._lock:
            self._last_delivery_ts = float(timestamp) if timestamp is not None else _now_ts()

    def get_last_delivery_ts(self) -> float:
        with self._lock:
            return self._last_delivery_ts

    def reset_last_delivery(self) -> None:
        with self._lock:
            self._last_delivery_ts = 0.0

    # --------------------------------------------------------
    # 核心
    # --------------------------------------------------------

    def evaluate(
        self,
        initiative_output: Any,
        relationship_snapshot: Any = None,
        metadata: Any = None,
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """评估是否允许投递。

        Args:
            initiative_output:     Phase C.9.1 adapter 输出
            relationship_snapshot: 关系快照(只读)
            metadata:              补充元数据(只读)
            now_ts:                当前时间戳(可选,用于测试)

        Returns:
            {
                "decision": "allow" | "block",
                "reasons": [str, ...],
                "adjusted_priority": float,
                "cooldown_remaining_seconds": float,
                "schema_version": str,
                "timestamp": str,
            }
        """
        with self._lock:
            self._evaluate_count += 1

        try:
            # 0) policy disabled
            if not self._enabled:
                with self._lock:
                    self._block_count += 1
                return _build_block([REASON_DISABLED])

            # 1) output 必须为 dict
            output = _safe_dict(initiative_output)
            if not output:
                with self._lock:
                    self._block_count += 1
                return _build_block([REASON_INVALID_OUTPUT])

            # 2) decision 必须为 initiate
            decision = _safe_str(output.get("decision", ""), "").strip().lower()
            if decision != "initiate":
                with self._lock:
                    self._block_count += 1
                return _build_block([REASON_NOT_INITIATE])

            # 3) message content 必须存在
            message_request = output.get("message_request")
            content = ""
            if isinstance(message_request, dict):
                content = _safe_str(
                    message_request.get("content", "")
                    or message_request.get("text", "")
                    or message_request.get("template", ""),
                    "",
                )
            if not content:
                # 退化:直接读 output.content
                content = _safe_str(output.get("content", ""), "")
            if not content:
                with self._lock:
                    self._block_count += 1
                return _build_block([REASON_MISSING_CONTENT])

            # 4) confidence 阈值
            confidence = _safe_float(output.get("confidence", 0.0), 0.0)
            if confidence < self._confidence_threshold:
                with self._lock:
                    self._block_count += 1
                return _build_block([REASON_CONFIDENCE_LOW], adjusted_priority=confidence)

            # 5) priority 阈值
            priority = _safe_float(output.get("priority", 0.0), 0.0)
            if priority < self._priority_threshold:
                with self._lock:
                    self._block_count += 1
                return _build_block([REASON_PRIORITY_LOW], adjusted_priority=priority)

            # 6) cooldown
            now = float(now_ts) if now_ts is not None else _now_ts()
            with self._lock:
                last_ts = self._last_delivery_ts
            if last_ts > 0.0 and (now - last_ts) < self._cooldown_seconds:
                remaining = max(0.0, self._cooldown_seconds - (now - last_ts))
                with self._lock:
                    self._block_count += 1
                return _build_block(
                    [REASON_COOLDOWN],
                    adjusted_priority=priority,
                    cooldown_remaining=remaining,
                )

            # 7) relationship 检查(只读)
            rel_level = _extract_relationship_level(relationship_snapshot)
            if rel_level < self._min_relationship_level:
                with self._lock:
                    self._block_count += 1
                return _build_block(
                    [REASON_RELATIONSHIP_LOW],
                    adjusted_priority=priority,
                )

            # 8) user_preference 检查
            pref = _extract_user_preference(output, metadata)
            pref_norm = pref.strip().lower()
            if pref_norm in ("reserved", "silent", "closed", "off", "false", "no"):
                with self._lock:
                    self._block_count += 1
                return _build_block(
                    [REASON_USER_PREFERENCE],
                    adjusted_priority=priority,
                )

            # 全部通过
            with self._lock:
                self._allow_count += 1
            return _build_allow([REASON_OK], adjusted_priority=priority)

        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c92] policy.evaluate 异常(已隔离): {exc}")
            with self._lock:
                self._block_count += 1
            return _build_block([REASON_ERROR], adjusted_priority=0.0)

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "evaluate_count": int(self._evaluate_count),
                "allow_count": int(self._allow_count),
                "block_count": int(self._block_count),
                "last_delivery_ts": float(self._last_delivery_ts),
                "enabled": bool(self._enabled),
                "confidence_threshold": float(self._confidence_threshold),
                "priority_threshold": float(self._priority_threshold),
                "cooldown_seconds": float(self._cooldown_seconds),
                "min_relationship_level": float(self._min_relationship_level),
                "schema_version": self.schema_version,
            }

    @property
    def schema_version(self) -> str:
        return INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION


# ============================================================
# 工厂
# ============================================================


def create_initiative_delivery_policy(
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    priority_threshold: float = DEFAULT_PRIORITY_THRESHOLD,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    min_relationship_level: float = DEFAULT_MIN_RELATIONSHIP_LEVEL,
    enabled: bool = DEFAULT_ENABLED,
) -> InitiativeDeliveryPolicy:
    return InitiativeDeliveryPolicy(
        confidence_threshold=confidence_threshold,
        priority_threshold=priority_threshold,
        cooldown_seconds=cooldown_seconds,
        min_relationship_level=min_relationship_level,
        enabled=enabled,
    )


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION",
    "INITIATIVE_DELIVERY_POLICY_NAME",
    "INITIATIVE_DELIVERY_POLICY_VERSION",
    "DECISION_ALLOW",
    "DECISION_BLOCK",
    "ALL_DELIVERY_DECISIONS",
    "REASON_OK",
    "REASON_NOT_INITIATE",
    "REASON_CONFIDENCE_LOW",
    "REASON_PRIORITY_LOW",
    "REASON_COOLDOWN",
    "REASON_RELATIONSHIP_LOW",
    "REASON_USER_PREFERENCE",
    "REASON_INVALID_OUTPUT",
    "REASON_MISSING_CONTENT",
    "REASON_DISABLED",
    "REASON_ERROR",
    "ALL_DELIVERY_REASONS",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_PRIORITY_THRESHOLD",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MIN_RELATIONSHIP_LEVEL",
    "DEFAULT_USER_PREFERENCE",
    "DEFAULT_ENABLED",
    "InitiativeDeliveryPolicy",
    "create_initiative_delivery_policy",
]
