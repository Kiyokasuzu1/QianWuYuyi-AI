# -*- coding: utf-8 -*-
"""
src/admin/dashboard/selfmodel_router.py

Phase 5.0 Dashboard Upgrade Step 7.2 —— SelfModel 子路由。

职责:
- 注册 /api/dashboard/v2/selfmodel/* 下的 GET 接口
- 调用 SelfModelDashboardProvider
- 严格只读
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.selfmodel_dashboard_provider import get_selfmodel_dashboard_provider

logger = logging.getLogger(__name__)


selfmodel_v2_bp = Blueprint(
    "dashboard_v2_selfmodel",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/selfmodel",
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


@selfmodel_v2_bp.route("/identity", methods=["GET"])
def selfmodel_identity():
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_identity()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.identity 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/traits", methods=["GET"])
def selfmodel_traits():
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_traits()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.traits 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/capabilities", methods=["GET"])
def selfmodel_capabilities():
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_capabilities()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.capabilities 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/beliefs", methods=["GET"])
def selfmodel_beliefs():
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_beliefs(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.beliefs 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/timeline", methods=["GET"])
def selfmodel_timeline():
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_evolution_timeline(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.timeline 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/health", methods=["GET"])
def selfmodel_health():
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_health()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.health 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


# ============================================================
# Phase C.1 P1-1: 新增 6 个 state 端点 + 1 个综合端点
# 严格只读,仅返回 state 字段,不允许修改
# ============================================================

@selfmodel_v2_bp.route("/identity-state", methods=["GET"])
def selfmodel_identity_state():
    """GET /api/dashboard/v2/selfmodel/identity-state — 完整身份状态"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_identity_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.identity_state 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/personality-state", methods=["GET"])
def selfmodel_personality_state():
    """GET /api/dashboard/v2/selfmodel/personality-state — 当前人格向量/状态"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_personality_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.personality_state 异常: %s", exc)
        payload = fallback_response(
            data={"traits": {}, "trait_count": 0, "tensions": []},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/growth-history", methods=["GET"])
def selfmodel_growth_history():
    """GET /api/dashboard/v2/selfmodel/growth-history?limit=50 — GrowthRecord 历史"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_growth_history(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.growth_history 异常: %s", exc)
        payload = fallback_response(data={"items": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/relationship-state", methods=["GET"])
def selfmodel_relationship_state():
    """GET /api/dashboard/v2/selfmodel/relationship-state — 与清清的关系状态"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_relationship_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.relationship_state 异常: %s", exc)
        payload = fallback_response(
            data={
                "trust": 0.0, "familiarity": 0.0, "bond_strength": 0.0,
                "shared_history": 0.0, "promise_level": 0.0, "activity_level": 0.0,
                "milestones": [],
            },
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/capability-boundary", methods=["GET"])
def selfmodel_capability_boundary():
    """GET /api/dashboard/v2/selfmodel/capability-boundary — 能力边界"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_capability_boundary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.capability_boundary 异常: %s", exc)
        payload = fallback_response(
            data={
                "static_limitations": [], "runtime_capabilities": [],
                "known_uncertainties": [],
            },
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/personality-evolution-history", methods=["GET"])
def selfmodel_personality_evolution_history():
    """GET /api/dashboard/v2/selfmodel/personality-evolution-history?limit=50 — 人格演化历史"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 50) or 50)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 200))
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_personality_evolution_history(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.personality_evolution_history 异常: %s", exc)
        payload = fallback_response(
            data={
                "total_count": 0, "applied_count": 0, "rolled_back_count": 0,
                "current_personality_state": {}, "items": [],
            },
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@selfmodel_v2_bp.route("/full-state", methods=["GET"])
def selfmodel_full_state():
    """GET /api/dashboard/v2/selfmodel/full-state — 一次返回所有 6 个 state 视图"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_selfmodel_dashboard_provider()
        data = provider.get_full_state()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.selfmodel.full_state 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


__all__ = ["selfmodel_v2_bp"]
