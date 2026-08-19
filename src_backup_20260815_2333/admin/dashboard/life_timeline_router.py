# -*- coding: utf-8 -*-
"""
src/admin/dashboard/life_timeline_router.py

Phase 5.0 Dashboard Upgrade Step 8.4.3 —— Life Timeline 子路由。

API:
- GET /api/dashboard/v2/life-timeline?range=24h|7d|30d|90d|all
- GET /api/dashboard/v2/life-timeline/milestones
- GET /api/dashboard/v2/life-timeline/causality?start_time=...&end_time=...

返回统一 envelope (Step 8.4.1):
    {
        "ok": true,
        "data": ...,
        "trace": {sources, evidence_count, generated_at},
        "confidence": 0~1,
        "fallback": bool,
        "fallback_reason": str|None,
        "timestamp": iso
    }
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard._meta import build_envelope, calc_confidence
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.life_timeline_provider import (
    ALL_RANGES,
    DEFAULT_RANGE,
    LifeTimelineProvider,
    get_life_timeline_provider,
    reset_life_timeline_provider_for_testing,
)

logger = logging.getLogger(__name__)


life_timeline_v2_bp = Blueprint(
    "dashboard_v2_life_timeline",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/life-timeline",
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


def _envelope_from(
    *,
    data: Dict[str, Any],
    sources: List[Dict[str, Any]],
    provider_name: str,
    method_name: str,
) -> Dict[str, Any]:
    """把 provider 返回的 dict 包成统一 envelope。"""
    is_fallback = bool(data.get("fallback"))
    fallback_reason = data.get("fallback_reason")
    full_sources = list(sources) + [{
        "provider": provider_name,
        "method": method_name,
        "ok": not is_fallback,
    }]
    confidence = calc_confidence(
        available=not is_fallback and bool(data),
        sources_count=len(full_sources),
        fallback=is_fallback,
    )
    return build_envelope(
        data=data,
        sources=full_sources,
        confidence=confidence,
        fallback=is_fallback,
        fallback_reason=fallback_reason,
        ok=not is_fallback,
    )


@life_timeline_v2_bp.route("", methods=["GET"])
@life_timeline_v2_bp.route("/", methods=["GET"])
def life_timeline():
    """GET /api/dashboard/v2/life-timeline?range=24h|7d|30d|90d|all"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    range_key = request.args.get("range", DEFAULT_RANGE) or DEFAULT_RANGE
    try:
        provider = get_life_timeline_provider()
        data = provider.get_timeline(range_key=range_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_timeline 异常: %s", exc)
        envelope = build_envelope(
            data={
                "range": {"start": None, "end": None, "key": range_key, "seconds": 0},
                "lanes": [],
                "edges": [],
                "milestones": [],
                "counts": {},
            },
            sources=[{
                "provider": "LifeTimelineProvider",
                "method": "get_timeline",
                "ok": False,
                "error": type(exc).__name__,
            }],
            fallback=True,
            fallback_reason=f"provider_error:{type(exc).__name__}",
            ok=False,
        )
        return _jsonify(_attach_schema_meta(envelope))
    envelope = _envelope_from(
        data=data,
        sources=[],
        provider_name="LifeTimelineProvider",
        method_name="get_timeline",
    )
    return _jsonify(_attach_schema_meta(envelope))


@life_timeline_v2_bp.route("/milestones", methods=["GET"])
def life_timeline_milestones():
    """GET /api/dashboard/v2/life-timeline/milestones"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        limit_str = request.args.get("limit", "50") or "50"
        try:
            limit = int(limit_str)
        except (TypeError, ValueError):
            limit = 50
        provider = get_life_timeline_provider()
        data = provider.get_milestones(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_timeline.milestones 异常: %s", exc)
        envelope = build_envelope(
            data={"milestones": [], "counts": {}},
            sources=[{
                "provider": "LifeTimelineProvider",
                "method": "get_milestones",
                "ok": False,
                "error": type(exc).__name__,
            }],
            fallback=True,
            fallback_reason=f"provider_error:{type(exc).__name__}",
            ok=False,
        )
        return _jsonify(_attach_schema_meta(envelope))
    envelope = _envelope_from(
        data=data,
        sources=[],
        provider_name="LifeTimelineProvider",
        method_name="get_milestones",
    )
    return _jsonify(_attach_schema_meta(envelope))


@life_timeline_v2_bp.route("/causality", methods=["GET"])
def life_timeline_causality():
    """GET /api/dashboard/v2/life-timeline/causality?start_time=...&end_time=..."""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    start_time = request.args.get("start_time") or request.args.get("start") or None
    end_time = request.args.get("end_time") or request.args.get("end") or None
    try:
        provider = get_life_timeline_provider()
        data = provider.get_causality(start_time=start_time, end_time=end_time)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_timeline.causality 异常: %s", exc)
        envelope = build_envelope(
            data={"edges": [], "range": {}, "count": 0},
            sources=[{
                "provider": "LifeTimelineProvider",
                "method": "get_causality",
                "ok": False,
                "error": type(exc).__name__,
            }],
            fallback=True,
            fallback_reason=f"provider_error:{type(exc).__name__}",
            ok=False,
        )
        return _jsonify(_attach_schema_meta(envelope))
    envelope = _envelope_from(
        data=data,
        sources=[],
        provider_name="LifeTimelineProvider",
        method_name="get_causality",
    )
    return _jsonify(_attach_schema_meta(envelope))


@life_timeline_v2_bp.route("/lanes", methods=["GET"])
def life_timeline_lanes():
    """GET /api/dashboard/v2/life-timeline/lanes

    返回七条泳道定义(包含 count)。
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_life_timeline_provider()
        lanes = provider.get_lanes()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_timeline.lanes 异常: %s", exc)
        envelope = build_envelope(
            data={"lanes": []},
            sources=[{
                "provider": "LifeTimelineProvider",
                "method": "get_lanes",
                "ok": False,
                "error": type(exc).__name__,
            }],
            fallback=True,
            fallback_reason=f"provider_error:{type(exc).__name__}",
            ok=False,
        )
        return _jsonify(_attach_schema_meta(envelope))
    data = {"lanes": lanes, "count": len(lanes)}
    envelope = _envelope_from(
        data=data,
        sources=[],
        provider_name="LifeTimelineProvider",
        method_name="get_lanes",
    )
    return _jsonify(_attach_schema_meta(envelope))


__all__ = ["life_timeline_v2_bp"]
