# -*- coding: utf-8 -*-
"""
src/initiative/action_safety.py

v1.3 Phase 5.2: Initiative Action 执行前安全检查层(ActionSafetyFilter)。

职责(只做这一件事):
    Action(Phase 5.1 InitiativeDrain 产出)
        ↓
    ActionSafetyFilter.check(action)
        ↓
    SafetyDecision {allowed, reasons, checks{confidence/permission/duplicate/cooldown/schema}}

规则(默认 fail-closed, 任一不满足即拒绝):
    1. schema:    action 类型必须在白名单内; payload 未知字段 → 拒绝;
    2. confidence: payload.confidence < min_confidence → 拒绝;
    3. permission: payload.user_preference ∈ {deny, silent, reserved} 或缺失 → 拒绝;
    4. duplicate: 同 proposal_id(或同 payload 指纹)重复检查 → 拒绝;
    5. cooldown:  同 target_user 冷却窗口内再次通过 → 拒绝;
    enabled=False → 不执行任何检查(等价于无安全层, 回滚语义)。

红线:
- 不修改 Action(纯读取);
- 不发送消息(不 import sender / bridge);
- 不调用 LLM;
- 不接 InitiativeEngine / 不开启主动行为;
- 内存态记录(fingerprint / 冷却)不落盘, 不触碰任何状态域。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_COOLDOWN_SECONDS = 60.0

# 白名单(未知即拒绝 —— 未来扩展必须显式登记)
ALLOWED_ACTION_TYPES: frozenset = frozenset({"send_message"})
ALLOWED_PAYLOAD_KEYS: frozenset = frozenset({
    "proposal_id",
    "goal_reference",
    "initiative_id",
    "message",
    "confidence",
    "user_preference",
    "target_user",
    "source_refs",
})
# 用户许可语义对齐 InitiativeDeliveryPolicy(deny-list):
# open 默认通过; deny/silent/reserved/closed/off/false/no 阻断
ALLOWED_USER_PREFERENCES: frozenset = frozenset({
    "allow", "open", "permitted", "yes", "true", "on",
})
DENIED_USER_PREFERENCES: frozenset = frozenset({
    "deny", "silent", "reserved", "closed", "off", "false", "no",
})


@dataclass
class SafetyDecision:
    """安全检查决策(纯数据, 不修改 Action)。"""

    allowed: bool
    reasons: List[str] = field(default_factory=list)
    checks: Dict[str, str] = field(default_factory=dict)
    evaluated: bool = True


class ActionSafetyFilter:
    """Initiative Action 执行前安全检查(默认 fail-closed)。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        time_source: Optional[Callable[[], float]] = None,
        permission_provider: Optional[Callable[[Any], str]] = None,
    ) -> None:
        self._enabled = bool(enabled)
        try:
            self._min_confidence = min(1.0, max(0.0, float(min_confidence)))
        except (TypeError, ValueError):
            self._min_confidence = DEFAULT_MIN_CONFIDENCE
        try:
            self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        except (TypeError, ValueError):
            self._cooldown_seconds = DEFAULT_COOLDOWN_SECONDS
        self._time_source = time_source or time.monotonic
        # v1.3 Phase 5.5: 真实只读许可来源优先于 payload 注入(升级点)
        self._permission_provider = permission_provider
        self._lock = threading.RLock()
        # 内存态(进程内, 不落盘): 已通过指纹 + 目标用户最近放行时间
        self._seen_fingerprints: Set[str] = set()
        self._last_allowed_at: Dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def check(self, action: Any) -> SafetyDecision:
        """检查一个 Action。不修改 Action; 任何异常 → fail-closed 拒绝。"""
        if not self._enabled:
            # 关闭模式: 不执行检查(等价于无安全层, 不记录任何状态)
            return SafetyDecision(
                allowed=True, reasons=[], checks={}, evaluated=False,
            )

        _reasons: List[str] = []
        _checks: Dict[str, str] = {}

        def _reject(name: str, reason: str) -> SafetyDecision:
            _checks[name] = "fail"
            _reasons.append(reason)
            return SafetyDecision(allowed=False, reasons=_reasons, checks=_checks)

        try:
            # ---- 1) schema: action 结构 + 类型白名单 + 字段白名单 ----
            if not hasattr(action, "action_type"):
                return _reject("schema", "invalid_action")
            _action_type = str(getattr(action, "action_type", "") or "").strip()
            _payload = getattr(action, "payload", None)
            if not isinstance(_payload, dict):
                return _reject("schema", "invalid_payload")
            if _action_type not in ALLOWED_ACTION_TYPES:
                return _reject("schema", f"unknown_action_type:{_action_type}")
            _unknown = set(_payload.keys()) - ALLOWED_PAYLOAD_KEYS
            if _unknown:
                return _reject(
                    "schema", f"unknown_field:{','.join(sorted(_unknown))}"
                )
            _checks["schema"] = "pass"

            # ---- 2) confidence ----
            try:
                _confidence = float(_payload.get("confidence", None))
            except (TypeError, ValueError):
                _confidence = None
            if _confidence is None:
                return _reject("confidence", "missing_confidence")
            if _confidence < self._min_confidence:
                return _reject(
                    "confidence",
                    f"low_confidence:{_confidence:.3f}<{self._min_confidence:.3f}",
                )
            _checks["confidence"] = "pass"

            # ---- 3) permission(用户许可, 真实只读来源优先) ----
            _pref = self._resolve_preference(action)
            if _pref in DENIED_USER_PREFERENCES:
                return _reject("permission", f"permission_user_denied:{_pref}")
            if _pref not in ALLOWED_USER_PREFERENCES:
                return _reject("permission", "permission_missing_user_preference")
            _checks["permission"] = "pass"

            # ---- 4) duplicate(proposal_id / payload 指纹) ----
            _fingerprint = self._fingerprint_of(action)
            with self._lock:
                _duplicate = _fingerprint in self._seen_fingerprints
            if _duplicate:
                return _reject("duplicate", f"duplicate:{_fingerprint}")
            _checks["duplicate"] = "pass"

            # ---- 5) cooldown(同目标用户冷却窗口) ----
            _target = str(_payload.get("target_user", "") or "").strip()
            if not _target:
                return _reject("cooldown", "missing_target_user")
            _now = self._now()
            with self._lock:
                _last = self._last_allowed_at.get(_target)
            if _last is not None and (_now - _last) < self._cooldown_seconds:
                return _reject("cooldown", f"cooldown_active:{_target}")
            _checks["cooldown"] = "pass"

            # ---- 全部通过: 记录放行状态(仅内存) ----
            with self._lock:
                self._seen_fingerprints.add(_fingerprint)
                self._last_allowed_at[_target] = _now
            return SafetyDecision(allowed=True, reasons=[], checks=_checks)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ActionSafetyFilter] 检查异常(已隔离, fail-closed): %s", exc)
            return SafetyDecision(
                allowed=False,
                reasons=[f"safety_error:{type(exc).__name__}"],
                checks=_checks,
            )

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _now(self) -> float:
        try:
            return float(self._time_source())
        except Exception:  # noqa: BLE001
            return time.monotonic()

    def _resolve_preference(self, action: Any) -> str:
        """解析真实用户许可: Provider 优先, payload 兜底(fail-closed)。"""
        if self._permission_provider is not None:
            try:
                return str(self._permission_provider(action) or "").strip().lower()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ActionSafetyFilter] 许可来源读取失败(已隔离, fail-closed): %s", exc,
                )
                return ""
        _payload = getattr(action, "payload", None)
        if isinstance(_payload, dict):
            return str(_payload.get("user_preference", "") or "").strip().lower()
        return ""

    @staticmethod
    def _fingerprint_of(action: Any) -> str:
        _payload = getattr(action, "payload", None)
        if isinstance(_payload, dict):
            _pid = str(_payload.get("proposal_id", "") or "").strip()
            if _pid:
                return f"proposal:{_pid}"
            try:
                _raw = json.dumps(
                    _payload, sort_keys=True, ensure_ascii=False, default=str,
                )
                return "payload:" + hashlib.sha1(_raw.encode("utf-8")).hexdigest()[:16]
            except Exception:  # noqa: BLE001
                pass
        return f"action:{str(getattr(action, 'action_id', '') or '')}"


__all__ = [
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_COOLDOWN_SECONDS",
    "ALLOWED_ACTION_TYPES",
    "ALLOWED_PAYLOAD_KEYS",
    "ALLOWED_USER_PREFERENCES",
    "DENIED_USER_PREFERENCES",
    "SafetyDecision",
    "ActionSafetyFilter",
]
