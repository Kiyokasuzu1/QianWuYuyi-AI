# -*- coding: utf-8 -*-
"""
src/admin/dashboard/initiative_router.py

Phase 5.0 Dashboard Upgrade Step 8.2 —— Initiative 子路由。

职责:
- 注册 /api/dashboard/v2/initiative/* 下的 GET 接口
- 调用 InitiativeDashboardProvider
- 严格只读

API:
- GET /api/dashboard/v2/initiative/summary
- GET /api/dashboard/v2/initiative/interests?limit=20&trend=new
- GET /api/dashboard/v2/initiative/actions?limit=20&status=pending
- GET /api/dashboard/v2/initiative/filtered?limit=20
- GET /api/dashboard/v2/initiative/history?limit=50
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.initiative_dashboard_provider import (
    ALL_ACTION_STATUSES,
    ALL_INTEREST_TRENDS,
    get_initiative_dashboard_provider,
)

logger = logging.getLogger(__name__)


initiative_v2_bp = Blueprint(
    "dashboard_v2_initiative",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/initiative",
)


def _reject_non_local():
    if _is_local_request():
        return None
    payload, status = error_response(
        code="dashboard_local_only",
        message="Dashboard V2 仅允许本地访问",
        http_status=403,
    )
    return jsonify(payload), status


def _build(data: Dict[str, Any]):
    if not data or not isinstance(data, dict):
        payload = fallback_response(data={}, reason="empty_data")
        return _jsonify(_attach_schema_meta(payload))
    if data.get("fallback"):
        payload = fallback_response(
            data={k: v for k, v in data.items() if k not in ("fallback", "fallback_reason")},
            reason=data.get("fallback_reason") or "data_unavailable",
        )
        return _jsonify(_attach_schema_meta(payload))
    from src.admin.dashboard import ok_response
    return _jsonify(_attach_schema_meta(ok_response(data=data)))


@initiative_v2_bp.route("/summary", methods=["GET"])
def initiative_summary():
    """GET /api/dashboard/v2/initiative/summary"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_initiative_dashboard_provider()
        data = provider.get_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.initiative.summary 异常: %s", exc)
        payload = fallback_response(
            data={}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@initiative_v2_bp.route("/interests", methods=["GET"])
def initiative_interests():
    """GET /api/dashboard/v2/initiative/interests?limit=20&trend=new"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        trend_arg = request.args.get("trend", None)
        if isinstance(trend_arg, str) and trend_arg not in ALL_INTEREST_TRENDS:
            trend_arg = None
        provider = get_initiative_dashboard_provider()
        data = provider.list_interests(limit=limit, trend=trend_arg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.initiative.interests 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@initiative_v2_bp.route("/actions", methods=["GET"])
def initiative_actions():
    """GET /api/dashboard/v2/initiative/actions?limit=20&status=pending"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        status_arg = request.args.get("status", None)
        if isinstance(status_arg, str) and status_arg not in ALL_ACTION_STATUSES:
            status_arg = None
        provider = get_initiative_dashboard_provider()
        data = provider.list_actions(limit=limit, status=status_arg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.initiative.actions 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@initiative_v2_bp.route("/filtered", methods=["GET"])
def initiative_filtered():
    """GET /api/dashboard/v2/initiative/filtered?limit=20"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_initiative_dashboard_provider()
        data = provider.list_filtered(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.initiative.filtered 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@initiative_v2_bp.route("/history", methods=["GET"])
def initiative_history():
    """GET /api/dashboard/v2/initiative/history?limit=50"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_initiative_dashboard_provider()
        data = provider.get_history(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.initiative.history 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


__all__ = ["initiative_v2_bp"]
