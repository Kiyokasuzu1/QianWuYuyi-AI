# -*- coding: utf-8 -*-
"""
src/admin/dashboard/growth_router.py

Phase C.1 P1-1 —— Growth 子路由。

职责:
- 注册 /api/dashboard/v2/growth/* 下的 GET 接口
- 调用 GrowthDashboardProvider 聚合数据
- 严格只读,不允许触发成长

约束:
- 不允许修改 GrowthProposal 状态
- 不允许 apply / reject / approve
- 仅 GET,无副作用
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.growth_dashboard_provider import get_growth_dashboard_provider

logger = logging.getLogger(__name__)


growth_v2_bp = Blueprint(
    "dashboard_v2_growth",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/growth",
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


@growth_v2_bp.route("/summary", methods=["GET"])
def growth_summary():
    """GET /api/dashboard/v2/growth/summary — Proposal 数量 / 状态统计"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_growth_dashboard_provider()
        data = provider.get_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.growth.summary 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@growth_v2_bp.route("/recent", methods=["GET"])
def growth_recent():
    """GET /api/dashboard/v2/growth/recent?limit=20 — 最近 growth 记录"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_growth_dashboard_provider()
        data = provider.get_recent(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.growth.recent 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@growth_v2_bp.route("/evolution-history", methods=["GET"])
def growth_evolution_history():
    """GET /api/dashboard/v2/growth/evolution-history?limit=50 — evolution history"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_growth_dashboard_provider()
        data = provider.get_evolution_history(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.growth.evolution_history 异常: %s", exc)
        payload = fallback_response(
            data={"items": []},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@growth_v2_bp.route("/combined", methods=["GET"])
def growth_combined():
    """GET /api/dashboard/v2/growth/combined — 一次拉取所有 growth 视图"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            recent_limit = int(request.args.get("recent_limit", 20) or 20)
        except (TypeError, ValueError):
            recent_limit = 20
        try:
            evolution_limit = int(request.args.get("evolution_limit", 50) or 50)
        except (TypeError, ValueError):
            evolution_limit = 50
        recent_limit = max(1, min(recent_limit, 200))
        evolution_limit = max(1, min(evolution_limit, 200))
        provider = get_growth_dashboard_provider()
        data = provider.get_combined(
            recent_limit=recent_limit,
            evolution_limit=evolution_limit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.growth.combined 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


__all__ = ["growth_v2_bp"]
