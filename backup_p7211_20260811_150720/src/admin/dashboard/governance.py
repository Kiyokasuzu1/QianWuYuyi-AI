# -*- coding: utf-8 -*-
"""
src/admin/dashboard/governance.py

Phase 5.0 Dashboard Upgrade Step 8.4.7 —— Governance Hook。

职责:
1. 高风险操作二次检查
2. 不在白名单 / 黑名单中的 action 默认拒绝
3. 任何异常隔离,返回 deny(默认安全)

设计原则:
- 默认拒绝(unknown action → deny)
- 不调用 LLM
- 不修改业务状态
- 严格不依赖 memory / growth / emotion / personality / relationship / runtime
- 不创建业务事件(只读 / 决策,不写)

与业务 Authority 的边界:
- Dashboard Governance 仅做"是否允许 Dashboard 执行"检查
- 真正"业务 Authority"由 src/admin/governance_provider 提供(不在本模块)
- 本模块不调用 Authority(避免双重治理导致循环依赖)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 风险分级
# ============================================================
# 高风险操作 —— 需特殊审查(默认 deny,除非 allowlist 显式授权)
HIGH_RISK_ACTIONS = frozenset({
    "runtime.disable",
    "runtime.force_stop",
    "authority.switch",
    "authority.grant",
    "module.reload",
    "module.stop",
    "config.write",
    "config.rollback",
    "memory.delete",
    "personality.propose",
    "growth.apply",
    "emotion.override",
    "audit.purge",
    "audit.export",
})

# 中风险操作 —— 需要 role permission(由 _security 层处理)
MEDIUM_RISK_ACTIONS = frozenset({
    "runtime.control",
    "runtime.pause",
    "runtime.resume",
    "runtime.snapshot",
    "module.start",
    "dashboard.refresh",
})

# 低风险操作 —— 默认允许
LOW_RISK_ACTIONS = frozenset({
    "dashboard.read",
    "runtime.read",
    "snapshot.read",
    "audit.read",
    "live2d.read",
})

# 高风险 allowlist(显式授权)—— 默认仅 admin 可在白名单中
DEFAULT_HIGH_RISK_ALLOWLIST: Dict[str, List[str]] = {
    # action → required roles
    "runtime.disable": ["admin"],
    "runtime.force_stop": ["admin"],
    "authority.switch": ["admin"],
    "authority.grant": ["admin"],
    "module.reload": ["admin", "operator"],
    "module.stop": ["admin", "operator"],
    "config.write": ["admin"],
    "config.rollback": ["admin"],
    "memory.delete": ["admin"],
    "personality.propose": ["admin"],
    "growth.apply": ["admin"],
    "emotion.override": ["admin"],
    "audit.purge": ["admin"],
    "audit.export": ["admin", "operator"],
}


# ============================================================
# 可注入覆盖
# ============================================================
_governance_singleton: Dict[str, Any] = {}
_governance_lock = threading.RLock()


def _get_allowlist() -> Dict[str, List[str]]:
    with _governance_lock:
        overridden = _governance_singleton.get("high_risk_allowlist")
        if isinstance(overridden, dict) and overridden:
            return overridden
    return DEFAULT_HIGH_RISK_ALLOWLIST


def _get_high_risk_actions() -> frozenset:
    with _governance_lock:
        overridden = _governance_singleton.get("high_risk_actions")
        if isinstance(overridden, (set, list, frozenset)):
            return frozenset(overridden)
    return HIGH_RISK_ACTIONS


def _get_disallowed_actions() -> frozenset:
    """显式 deny 列表(deny > allow)。"""
    with _governance_lock:
        overridden = _governance_singleton.get("disallowed_actions")
        if isinstance(overridden, (set, list, frozenset)):
            return frozenset(overridden)
    return frozenset()


def set_governance_overrides(
    *,
    high_risk_actions: Optional[List[str]] = None,
    high_risk_allowlist: Optional[Dict[str, List[str]]] = None,
    disallowed_actions: Optional[List[str]] = None,
) -> None:
    """测试 / 高级配置用:覆盖 governance 规则。"""
    with _governance_lock:
        if high_risk_actions is not None:
            _governance_singleton["high_risk_actions"] = list(high_risk_actions)
        if high_risk_allowlist is not None:
            _governance_singleton["high_risk_allowlist"] = dict(high_risk_allowlist)
        if disallowed_actions is not None:
            _governance_singleton["disallowed_actions"] = list(disallowed_actions)


def reset_governance_overrides() -> None:
    """测试用:重置所有 governance 注入。"""
    with _governance_lock:
        _governance_singleton.clear()


# ============================================================
# 核心:check_action
# ============================================================
def classify_action(action: str) -> str:
    """把 action 分级:high / medium / low / unknown。"""
    if not action or not isinstance(action, str):
        return "unknown"
    a = action.strip()
    if not a:
        return "unknown"
    if a in _get_disallowed_actions():
        return "disallowed"
    if a in _get_high_risk_actions():
        return "high"
    if a in MEDIUM_RISK_ACTIONS:
        return "medium"
    if a in LOW_RISK_ACTIONS:
        return "low"
    return "unknown"


def check_action(action: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Governance 主入口:检查 action 是否被允许。

    Args:
        action: 动作标识(如 "runtime.disable" / "module.reload" / "dashboard.read")。
        payload: 动作 payload(只用于上下文记录,不参与判断)。

    Returns:
        {
            "allowed": bool,
            "reason": str,
            "risk": "high"|"medium"|"low"|"unknown"|"disallowed",
            "needs_review": bool,  # 提示是否需要人工审查
            "action": str,
            "payload_hash": str,   # payload 哈希(便于审计)
        }

    规则:
        - disallowed: deny,reason="action_disallowed"
        - high: 需 allowlist 显式授权,否则 deny,reason="high_risk_requires_review"
        - medium: 允许(由 _security.permission 二次把关)
        - low: 允许
        - unknown: deny(默认安全),reason="unknown_action"
    """
    try:
        a = (action or "").strip() if isinstance(action, str) else ""
        risk = classify_action(a)
        payload_dict = payload if isinstance(payload, dict) else {}
        payload_hash = _hash_payload(payload_dict)

        # 1) disallowed 显式黑名单
        if risk == "disallowed":
            return {
                "allowed": False,
                "reason": "action_disallowed",
                "risk": risk,
                "needs_review": True,
                "action": a,
                "payload_hash": payload_hash,
            }

        # 2) unknown action 默认拒绝
        if risk == "unknown":
            return {
                "allowed": False,
                "reason": "unknown_action",
                "risk": risk,
                "needs_review": True,
                "action": a,
                "payload_hash": payload_hash,
            }

        # 3) high risk:需 allowlist 授权
        if risk == "high":
            allowlist = _get_allowlist()
            allowed_roles = allowlist.get(a) or []
            return {
                "allowed": False,  # 默认 deny(由调用方传入 role 再做二次校验)
                "reason": "high_risk_requires_review",
                "risk": risk,
                "needs_review": True,
                "action": a,
                "payload_hash": payload_hash,
                "allowed_roles": allowed_roles,
            }

        # 4) medium / low:允许
        return {
            "allowed": True,
            "reason": "ok",
            "risk": risk,
            "needs_review": (risk == "medium"),
            "action": a,
            "payload_hash": payload_hash,
        }
    except Exception as exc:  # noqa: BLE001
        # 默认 deny(任何异常都视作不安全)
        logger.warning("governance.check_action 异常: %s", exc)
        return {
            "allowed": False,
            "reason": f"governance_error:{type(exc).__name__}",
            "risk": "unknown",
            "needs_review": True,
            "action": str(action) if action else "",
            "payload_hash": "",
        }


def check_action_with_role(
    action: str,
    role: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    check_action 的扩展:对 high risk action 同时校验 role 是否在 allowlist。

    Returns:
        check_action 的结果 + 额外字段:
            - role: 传入的 role
            - role_authorized: bool(high risk 才有效,其他 = True)
    """
    base = check_action(action, payload)
    base["role"] = role or ""
    if base.get("risk") != "high":
        base["role_authorized"] = True
        return base
    allowlist = _get_allowlist()
    allowed_roles = allowlist.get(action) or []
    authorized = isinstance(role, str) and role in allowed_roles
    base["role_authorized"] = authorized
    if not authorized:
        base["allowed"] = False
        # reason 优先保留"high_risk_requires_review"
        # 如果 role 不在 allowlist → 提示更具体的 forbidden
        if role and allowed_roles and role not in allowed_roles:
            base["reason"] = "role_not_in_allowlist"
    else:
        # role 在 allowlist 中 → 放行(high risk 需 allowlist 授权后即可执行)
        base["allowed"] = True
        base["reason"] = "ok"
    return base


# ============================================================
# 工具
# ============================================================
def _hash_payload(payload: Dict[str, Any]) -> str:
    """payload 哈希(用于审计,稳定 + 短)。"""
    try:
        s = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        try:
            s = repr(payload)
        except Exception:  # noqa: BLE001
            s = ""
    try:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "0" * 16


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        try:
            return datetime.utcnow().isoformat() + "Z"  # noqa: F821
        except Exception:  # noqa: BLE001
            return ""


__all__ = [
    "HIGH_RISK_ACTIONS",
    "MEDIUM_RISK_ACTIONS",
    "LOW_RISK_ACTIONS",
    "DEFAULT_HIGH_RISK_ALLOWLIST",
    "classify_action",
    "check_action",
    "check_action_with_role",
    "set_governance_overrides",
    "reset_governance_overrides",
]
