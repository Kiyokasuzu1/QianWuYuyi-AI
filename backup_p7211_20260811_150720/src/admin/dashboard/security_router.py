# -*- coding: utf-8 -*-
"""
src/admin/dashboard/security_router.py

Phase 5.0 Dashboard Upgrade Step 8.4.7 —— Dashboard POST 安全路由。

包含:
1. POST /api/dashboard/v2/control —— 通用控制端点(走完整安全链路)
2. GET  /api/dashboard/v2/audit/logs —— 审计日志只读 API

链路:
    Frontend
        ↓
    Router (本模块)
        ↓
    Auth (_security.require_dashboard_post_auth)
        ↓
    Permission(role → action)
        ↓
    Governance (governance.check_action)
        ↓
    Adapter(占位 — 不修改业务对象)
        ↓
    Audit (dashboard.audit.record_dashboard_post)

约束:
- 禁止进入 runtime / personality / memory / growth / emotion / relationship
- 任何 POST 必须经过审计(成功 / 失败 / 拒绝)
- 任何异常隔离,绝不抛到外层
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from src.admin.dashboard import (
    DASHBOARD_API_PREFIX,
    DASHBOARD_SCHEMA_VERSION,
)
from src.admin.dashboard._security import (
    Principal,
    get_current_principal,
    get_current_role,
    require_dashboard_post_auth,
)
from src.admin.dashboard.audit import (
    list_dashboard_audit,
    record_dashboard_post,
)
from src.admin.dashboard.governance import (
    check_action_with_role,
)
from src.admin.dashboard.response import (
    PROVIDER_DASHBOARD,
    build_dashboard_response,
    build_fallback_response,
)
from src.admin.dashboard.router import _attach_schema_meta, _jsonify, _reject_non_local

logger = logging.getLogger(__name__)


# ============================================================
# Blueprint
# ============================================================
security_v2_bp = Blueprint(
    "dashboard_v2_security",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX,
)


# ============================================================
# 工具
# ============================================================
def _now_iso() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        try:
            return datetime.utcnow().isoformat() + "Z"  # noqa: F821
        except Exception:  # noqa: BLE001
            return ""


def _attach_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload["schema_version"] = DASHBOARD_SCHEMA_VERSION
    return payload


def _parse_json_payload() -> Dict[str, Any]:
    """从请求体解析 JSON payload,失败返回 {}。"""
    try:
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return {}
        return body
    except Exception:  # noqa: BLE001
        return {}


def _safe_get_flask_g(name: str, default: Any = None) -> Any:
    try:
        from flask import g
        return getattr(g, name, default)
    except Exception:  # noqa: BLE001
        return default


def _audit_event(
    *,
    action: str,
    payload: Dict[str, Any],
    result: str,
    reason: str,
    risk: str = "",
) -> bool:
    """记录一次 POST 事件(成功 / 失败 / 拒绝)。"""
    try:
        principal = get_current_principal() or {}
        role = str(principal.get("role") or get_current_role() or "unknown")
        who = str(principal.get("role") or role)
        token_hash = str(principal.get("token_hash") or "")
        remote_addr = str(principal.get("remote_addr") or "")
        permission = str(_safe_get_flask_g("dashboard_permission") or "")
        return record_dashboard_post(
            who=who,
            role=role,
            action=action,
            payload=payload,
            result=result,
            reason=reason,
            remote_addr=remote_addr,
            token_hash=token_hash,
            permission=permission,
            risk=risk,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard.audit event 写入失败: %s", exc)
        return False


# ============================================================
# 通用适配器(只读,不动业务)
# ============================================================
def _control_adapter(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    占位适配器 —— 严格不动任何业务对象。

    真实生产中,该函数会:
    1) 解析 action
    2) 路由到对应 Authority / Adapter
    3) 执行幂等操作
    4) 返回 before / after / result

    当前阶段(Step 8.4.7)只完成安全链路,
    不实际执行业务写操作,仅做 dry-run 校验并返回结构。
    """
    # 严格不调用 Authority / 不修改业务状态
    return {
        "executed": False,
        "action": action,
        "dry_run": True,
        "note": "Step 8.4.7: dry-run,实际业务由后续步骤接入",
    }


# ============================================================
# 路由:POST /api/dashboard/v2/control
# ============================================================
@security_v2_bp.route("/control", methods=["POST"])
@require_dashboard_post_auth(permission="dashboard.control")
def dashboard_control_post():
    """
    通用控制端点。

    Request:
        {
            "action": "runtime.disable" | "module.reload" | ...,
            "payload": { ... }            # 可选
        }

    Response(envelope):
        {
            "ok": bool,
            "data": {...},
            "trace": {...},
            "confidence": float,
            "fallback": bool,
            "fallback_reason": str|None,
            "timestamp": iso
        }

    失败:
        - 401: missing_token / invalid_token(由 _security 中间件返回)
        - 403: dashboard_local_only / insufficient_role
        - 200 with envelope: governance denied(denied=true)
    """
    # 1) 解析 payload
    body = _parse_json_payload()
    action = str(body.get("action") or "").strip()
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    # 2) action 必填
    if not action:
        _audit_event(
            action="(empty)",
            payload=body,
            result="denied",
            reason="missing_action",
        )
        payload_env = build_fallback_response(
            data={"action": action},
            reason="missing_action",
            host_provider=PROVIDER_DASHBOARD,
            host_method="dashboard_control_post",
        )
        return _jsonify(_attach_meta(payload_env))

    # 3) Governance
    role = get_current_role() or "unknown"
    gov = check_action_with_role(action=action, role=role, payload=payload)
    risk = str(gov.get("risk", ""))

    if not gov.get("allowed", False):
        # 拒绝:Audit + envelope
        reason = str(gov.get("reason") or "denied")
        _audit_event(
            action=action,
            payload=payload,
            result="denied",
            reason=reason,
            risk=risk,
        )
        env = build_fallback_response(
            data={
                "action": action,
                "allowed": False,
                "reason": reason,
                "risk": risk,
                "role": role,
            },
            reason=reason,
            host_provider=PROVIDER_DASHBOARD,
            host_method="dashboard_control_post",
        )
        return _jsonify(_attach_meta(env))

    # 4) 执行(dry-run)
    try:
        result = _control_adapter(action, payload)
    except Exception as exc:  # noqa: BLE001
        # 失败:Audit 记录 failure
        _audit_event(
            action=action,
            payload=payload,
            result="failure",
            reason=f"adapter_error:{type(exc).__name__}",
            risk=risk,
        )
        env = build_fallback_response(
            data={"action": action, "executed": False},
            reason=f"adapter_error:{type(exc).__name__}",
            host_provider=PROVIDER_DASHBOARD,
            host_method="dashboard_control_post",
        )
        return _jsonify(_attach_meta(env))

    # 5) 成功:Audit + envelope
    _audit_event(
        action=action,
        payload=payload,
        result="success",
        reason="ok",
        risk=risk,
    )
    data = {
        "action": action,
        "executed": bool(result.get("executed", False)),
        "dry_run": bool(result.get("dry_run", True)),
        "note": result.get("note", ""),
    }
    env = build_dashboard_response(
        data=data,
        evidence_count=1,
        host_provider=PROVIDER_DASHBOARD,
        host_method="dashboard_control_post",
    )
    return _jsonify(_attach_meta(env))


# ============================================================
# 路由:GET /api/dashboard/v2/audit/logs
# ============================================================
@security_v2_bp.route("/audit/logs", methods=["GET"])
def dashboard_audit_logs():
    """
    审计日志只读 API。

    限制:本地访问(与 GET 一致,保护敏感审计数据)。

    Query:
        limit=50
        offset=0
        who=<role>
        action=<action>
        result=success|failure|denied

    Response(envelope):
        {
            "ok": true,
            "data": {
                "items": [
                    {
                        "record_id": "...",
                        "timestamp": "...",
                        "who": "operator",
                        "action": "runtime.disable",
                        "payload_hash": "...",
                        "result": "denied",
                        "reason": "..."
                    }
                ]
            },
            "trace": {...},
            "confidence": float,
            "fallback": bool,
            "fallback_reason": str|None,
            "timestamp": iso
        }
    """
    # 本地访问限制(保护审计数据)
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = int(request.args.get("offset", 0) or 0)
        except (TypeError, ValueError):
            offset = 0
        limit = max(1, min(200, limit))
        offset = max(0, offset)

        who = request.args.get("who") or None
        action = request.args.get("action") or None
        result = request.args.get("result") or None

        try:
            items = list_dashboard_audit(
                limit=limit,
                offset=offset,
                who=who,
                action=action,
                result=result,
            )
            ok = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard.audit.read 异常: %s", exc)
            items = []
            ok = False

        if not ok:
            env = build_fallback_response(
                data={"items": []},
                reason="audit_read_failed",
                host_provider=PROVIDER_DASHBOARD,
                host_method="dashboard_audit_logs",
            )
            return _jsonify(_attach_meta(env))

        env = build_dashboard_response(
            data={"items": items, "count": len(items), "limit": limit, "offset": offset},
            evidence_count=len(items),
            host_provider=PROVIDER_DASHBOARD,
            host_method="dashboard_audit_logs",
        )
        return _jsonify(_attach_meta(env))
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard.audit.logs 整体异常: %s", exc)
        env = build_fallback_response(
            data={"items": []},
            reason=f"audit_endpoint_error:{type(exc).__name__}",
            host_provider=PROVIDER_DASHBOARD,
            host_method="dashboard_audit_logs",
        )
        return _jsonify(_attach_meta(env))


__all__ = ["security_v2_bp"]
