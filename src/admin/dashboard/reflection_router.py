# -*- coding: utf-8 -*-
"""
src/admin/dashboard/reflection_router.py

Phase 5.0 Dashboard Upgrade Step 7.4 —— Reflection 子路由。

职责:
- 注册 /api/dashboard/v2/reflection/* 下的 GET 接口
- 调用 ReflectionDashboardProvider
- 严格只读

API:
- GET /api/dashboard/v2/reflection/list?limit=20&type=daily
- GET /api/dashboard/v2/reflection/summary
- GET /api/dashboard/v2/reflection/<reflection_id>
- GET /api/dashboard/v2/reflection/<reflection_id>/insights
- GET /api/dashboard/v2/reflection/<reflection_id>/evidence
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.reflection_dashboard_provider import (
    ALL_REFLECTION_TYPES,
    get_reflection_dashboard_provider,
)

logger = logging.getLogger(__name__)


reflection_v2_bp = Blueprint(
    "dashboard_v2_reflection",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/reflection",
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


@reflection_v2_bp.route("/list", methods=["GET"])
def reflection_list():
    """GET /api/dashboard/v2/reflection/list?limit=20&type=daily"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        type_arg = request.args.get("type", None)
        # 校验 type 参数(无效值降级为 None)
        if isinstance(type_arg, str) and type_arg not in ALL_REFLECTION_TYPES:
            type_arg = None
        provider = get_reflection_dashboard_provider()
        data = provider.list_reflections(limit=limit, type=type_arg)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.reflection.list 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@reflection_v2_bp.route("/summary", methods=["GET"])
def reflection_summary():
    """GET /api/dashboard/v2/reflection/summary"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_reflection_dashboard_provider()
        data = provider.get_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.reflection.summary 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@reflection_v2_bp.route("/<reflection_id>", methods=["GET"])
def reflection_detail(reflection_id: str):
    """GET /api/dashboard/v2/reflection/<reflection_id>"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_reflection_dashboard_provider()
        data = provider.get_reflection(reflection_id=reflection_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.reflection.detail 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@reflection_v2_bp.route("/<reflection_id>/insights", methods=["GET"])
def reflection_insights(reflection_id: str):
    """GET /api/dashboard/v2/reflection/<reflection_id>/insights"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_reflection_dashboard_provider()
        data = provider.get_insights(reflection_id=reflection_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.reflection.insights 异常: %s", exc)
        payload = fallback_response(
            data={"items": []}, reason=f"provider_error:{type(exc).__name__}"
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@reflection_v2_bp.route("/<reflection_id>/evidence", methods=["GET"])
def reflection_evidence(reflection_id: str):
    """GET /api/dashboard/v2/reflection/<reflection_id>/evidence"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_reflection_dashboard_provider()
        data = provider.get_evidence_chain(reflection_id=reflection_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.reflection.evidence 异常: %s", exc)
        payload = fallback_response(
            data={"chain": {"events": [], "memories": [], "evidence": []}},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


__all__ = ["reflection_v2_bp"]
