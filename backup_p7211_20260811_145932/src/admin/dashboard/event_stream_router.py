# -*- coding: utf-8 -*-
"""
src/admin/dashboard/event_stream_router.py

Phase 5.0 Dashboard Upgrade Step 8.4.4 —— Event Stream 子路由。

API:
- GET /api/dashboard/v2/event-stream?types=&keyword=&since=&limit=
- GET /api/dashboard/v2/event-stream/types
- GET /api/dashboard/v2/event-stream/health
- GET /api/dashboard/v2/event-stream/ws-info  (WebSocket 频道元信息)

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

约束:
- 只读 GET,严格不写
- 不调用 LLM
- 不创建业务事件
- 仅依赖: src.admin.event_stream_provider + src.admin.dashboard.*
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, DASHBOARD_WS_PATH
from src.admin.dashboard._meta import build_envelope, calc_confidence
from src.admin.dashboard.router import (
    _attach_schema_meta,
    _is_local_request,
    _jsonify,
    error_response,
)
from src.admin.event_stream_provider import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    EventStreamProvider,
    get_event_stream_provider,
    reset_event_stream_provider_for_testing,
)

logger = logging.getLogger(__name__)


event_stream_v2_bp = Blueprint(
    "dashboard_v2_event_stream",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/event-stream",
)


# ============================================================
# 工具
# ============================================================

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


# ============================================================
# 路由 1:list events
# ============================================================
@event_stream_v2_bp.route("", methods=["GET"])
@event_stream_v2_bp.route("/", methods=["GET"])
def event_stream_list():
    """
    GET /api/dashboard/v2/event-stream?types=&keyword=&since=&limit=

    参数:
      types   - 事件类型过滤,逗号分隔,支持通配符如 "memory.*"
      keyword - 关键词,搜索 event_type / summary / payload.topic / payload.title
      since   - 时间过滤(epoch 浮点 或 ISO 字符串)
      limit   - 最大返回数(1~200,默认 50)
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    types_param = request.args.get("types", "") or None
    keyword = request.args.get("keyword", "") or None
    since_param = request.args.get("since") or request.args.get("since_ts") or None
    limit_param = request.args.get("limit", str(DEFAULT_LIMIT)) or str(DEFAULT_LIMIT)

    try:
        provider = get_event_stream_provider()
        data = provider.list_events(
            types=types_param,
            keyword=keyword,
            since_ts=since_param,
            limit=limit_param,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.event_stream 异常: %s", exc)
        envelope = build_envelope(
            data={
                "items": [],
                "available_types": [],
                "total": 0,
            },
            sources=[{
                "provider": "EventStreamProvider",
                "method": "list_events",
                "ok": False,
                "error": type(exc).__name__,
            }],
            fallback=True,
            fallback_reason=f"provider_error:{type(exc).__name__}",
            ok=False,
        )
        return _jsonify(_attach_schema_meta(envelope))

    # data 内禁止出现 _meta / confidence / source
    # 注入额外展示字段
    safe_data = {
        "items": data.get("items", []),
        "available_types": data.get("available_types", []),
        "total": data.get("total", 0),
        "filter": {
            "types": types_param,
            "keyword": keyword,
            "since": since_param,
            "limit": limit_param,
        },
    }
    is_fallback = bool(data.get("fallback"))
    safe_data["fallback"] = is_fallback
    safe_data["fallback_reason"] = data.get("fallback_reason")
    envelope = _envelope_from(
        data=safe_data,
        sources=[],
        provider_name="EventStreamProvider",
        method_name="list_events",
    )
    return _jsonify(_attach_schema_meta(envelope))


# ============================================================
# 路由 2:available types
# ============================================================
@event_stream_v2_bp.route("/types", methods=["GET"])
def event_stream_types():
    """GET /api/dashboard/v2/event-stream/types"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    try:
        provider = get_event_stream_provider()
        types = provider.list_available_types()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.event_stream.types 异常: %s", exc)
        envelope = build_envelope(
            data={"types": [], "count": 0},
            sources=[{
                "provider": "EventStreamProvider",
                "method": "list_available_types",
                "ok": False,
                "error": type(exc).__name__,
            }],
            fallback=True,
            fallback_reason=f"provider_error:{type(exc).__name__}",
            ok=False,
        )
        return _jsonify(_attach_schema_meta(envelope))

    data = {
        "types": types,
        "count": len(types),
    }
    envelope = _envelope_from(
        data=data,
        sources=[],
        provider_name="EventStreamProvider",
        method_name="list_available_types",
    )
    return _jsonify(_attach_schema_meta(envelope))


# ============================================================
# 路由 3:health
# ============================================================
@event_stream_v2_bp.route("/health", methods=["GET"])
def event_stream_health():
    """
    GET /api/dashboard/v2/event-stream/health

    不需要本地访问限制(用于监控/探活)。
    """
    try:
        provider = get_event_stream_provider()
        info = provider.health()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.event_stream.health 异常: %s", exc)
        payload, status = error_response(
            code="event_stream_health_error",
            message=str(exc),
            http_status=500,
        )
        return jsonify(payload), status

    data = {
        "status": "ok" if info.get("ok") else "degraded",
        "ok": bool(info.get("ok")),
        "event_hub_available": bool(info.get("event_hub_available")),
        "collector_available": bool(info.get("collector_available")),
        "cache_size": int(info.get("cache_size", 0)),
        "last_refresh_iso": info.get("last_refresh_iso"),
        "subscribers": int(info.get("subscribers", 0)),
        "supported_prefixes": list(info.get("supported_prefixes", [])),
    }
    envelope = build_envelope(
        data=data,
        sources=[{
            "provider": "EventStreamProvider",
            "method": "health",
            "ok": bool(data["ok"]),
        }],
        confidence=calc_confidence(
            available=bool(data["ok"]),
            sources_count=1,
            fallback=not data["ok"],
        ),
        fallback=not data["ok"],
        fallback_reason=None if data["ok"] else "degraded",
        ok=bool(data["ok"]),
    )
    return _jsonify(_attach_schema_meta(envelope))


# ============================================================
# 路由 4:WS info(为前端提供频道元信息)
# ============================================================
@event_stream_v2_bp.route("/ws-info", methods=["GET"])
def event_stream_ws_info():
    """
    GET /api/dashboard/v2/event-stream/ws-info

    返回 WebSocket 频道元信息(供前端探测)。
    """
    data = {
        "ws_path": DASHBOARD_WS_PATH,
        "channel": "dashboard.event_stream",
        "protocol": "json",
        "message_format": {
            "type": "event",
            "data": {
                "event_id": "<string>",
                "event_type": "<string>",
                "timestamp": "<iso8601>",
            },
        },
        "fallback_polling_seconds": 5,
        "status": "placeholder",
        "note": "本阶段为 Step 8.4.4 占位;真实 WS 在 Phase 5.0-E 接入",
    }
    envelope = build_envelope(
        data=data,
        sources=[{
            "provider": "EventStreamProvider",
            "method": "ws_info",
            "ok": True,
        }],
        confidence=0.9,
    )
    return _jsonify(_attach_schema_meta(envelope))


__all__ = ["event_stream_v2_bp"]
