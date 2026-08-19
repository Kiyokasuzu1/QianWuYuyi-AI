# -*- coding: utf-8 -*-
"""
src/admin/dashboard/memory_router.py

Phase 5.0 Dashboard Upgrade Step 7.3 —— Memory 子路由。

职责:
- 注册 /api/dashboard/v2/memory/* 下的 GET 接口
- 调用 MemoryDashboardProvider
- 严格只读
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.memory_dashboard_provider import get_memory_dashboard_provider

logger = logging.getLogger(__name__)


memory_v2_bp = Blueprint(
    "dashboard_v2_memory",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/memory",
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


@memory_v2_bp.route("/summary", methods=["GET"])
def memory_summary():
    """GET /api/dashboard/v2/memory/summary"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_memory_dashboard_provider()
        data = provider.get_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.summary 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@memory_v2_bp.route("/recent", methods=["GET"])
def memory_recent():
    """GET /api/dashboard/v2/memory/recent?limit=20"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_memory_dashboard_provider()
        data = provider.list_recent(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.recent 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@memory_v2_bp.route("/important", methods=["GET"])
def memory_important():
    """GET /api/dashboard/v2/memory/important?limit=20"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_memory_dashboard_provider()
        data = provider.list_important(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.important 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@memory_v2_bp.route("/timeline", methods=["GET"])
def memory_timeline():
    """GET /api/dashboard/v2/memory/timeline?range=7d"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        range_str = request.args.get("range", "7d") or "7d"
        provider = get_memory_dashboard_provider()
        data = provider.get_timeline(range_str=range_str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.timeline 异常: %s", exc)
        payload = fallback_response(data={"buckets": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@memory_v2_bp.route("/<memory_id>", methods=["GET"])
def memory_detail(memory_id: str):
    """GET /api/dashboard/v2/memory/<memory_id>"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_memory_dashboard_provider()
        data = provider.get_memory(memory_id=memory_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.detail 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


# ============================================================
# Phase C.1 P1-1: 新增 memory 类型统计 / 质量状态 / 组合视图
# 严格只读,不修改 memory 数据
# ============================================================

@memory_v2_bp.route("/type-stats", methods=["GET"])
def memory_type_stats():
    """GET /api/dashboard/v2/memory/type-stats — memory 类型统计"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_memory_dashboard_provider()
        data = provider.get_type_stats()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.type_stats 异常: %s", exc)
        payload = fallback_response(
            data={
                "total": 0, "by_type": {}, "by_category": {
                    "normal_user": 0, "system_pollution": 0,
                    "ai_internal_pollution": 0, "invalid": 0,
                },
                "pollution_count": 0, "invalid_count": 0,
            },
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@memory_v2_bp.route("/quality", methods=["GET"])
def memory_quality():
    """GET /api/dashboard/v2/memory/quality — memory 质量状态"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_memory_dashboard_provider()
        data = provider.get_quality()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.quality 异常: %s", exc)
        payload = fallback_response(
            data={
                "status": "unknown", "total": 0,
                "normal_count": 0, "pollution_count": 0, "invalid_count": 0,
                "normal_ratio": 0.0, "pollution_ratio": 0.0, "invalid_ratio": 0.0,
            },
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@memory_v2_bp.route("/combined", methods=["GET"])
def memory_combined():
    """GET /api/dashboard/v2/memory/combined?recent_limit=20 — 一次拉取所有 memory 视图"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            recent_limit = int(request.args.get("recent_limit", 20) or 20)
        except (TypeError, ValueError):
            recent_limit = 20
        recent_limit = max(1, min(recent_limit, 100))
        provider = get_memory_dashboard_provider()
        data = provider.get_combined(recent_limit=recent_limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.memory.combined 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


__all__ = ["memory_v2_bp"]
