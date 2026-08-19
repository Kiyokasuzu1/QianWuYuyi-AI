# -*- coding: utf-8 -*-
"""
src/admin/dashboard/goal_router.py

Phase 5.0 Dashboard Upgrade Step 8.1 —— Goal 子路由。

职责:
- 注册 /api/dashboard/v2/goal/* 下的 GET 接口
- 调用 GoalDashboardProvider
- 严格只读

API:
- GET /api/dashboard/v2/goal/summary
- GET /api/dashboard/v2/goal/current
- GET /api/dashboard/v2/goal/list?limit=20&status=active
- GET /api/dashboard/v2/goal/history?limit=50
- GET /api/dashboard/v2/goal/<goal_id>
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.goal_dashboard_provider import (
    ALL_GOAL_STATUSES,
    get_goal_dashboard_provider,
)

logger = logging.getLogger(__name__)


goal_v2_bp = Blueprint(
    "dashboard_v2_goal",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/goal",
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


@goal_v2_bp.route("/summary", methods=["GET"])
def goal_summary():
    """GET /api/dashboard/v2/goal/summary"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_goal_dashboard_provider()
        data = provider.get_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.goal.summary 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@goal_v2_bp.route("/current", methods=["GET"])
def goal_current():
    """GET /api/dashboard/v2/goal/current"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_goal_dashboard_provider()
        data = provider.get_current()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.goal.current 异常: %s", exc)
        payload = fallback_response(
            data={"current": None}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@goal_v2_bp.route("/list", methods=["GET"])
def goal_list():
    """GET /api/dashboard/v2/goal/list?limit=20&status=active"""
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
        if isinstance(status_arg, str) and status_arg not in ALL_GOAL_STATUSES:
            status_arg = None
        provider = get_goal_dashboard_provider()
        data = provider.list_goals(limit=limit, status=status_arg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.goal.list 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@goal_v2_bp.route("/history", methods=["GET"])
def goal_history():
    """GET /api/dashboard/v2/goal/history?limit=50"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_goal_dashboard_provider()
        data = provider.get_history(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.goal.history 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@goal_v2_bp.route("/<goal_id>", methods=["GET"])
def goal_detail(goal_id: str):
    """GET /api/dashboard/v2/goal/<goal_id>"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_goal_dashboard_provider()
        data = provider.get_goal(goal_id=goal_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.goal.detail 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


__all__ = ["goal_v2_bp"]
