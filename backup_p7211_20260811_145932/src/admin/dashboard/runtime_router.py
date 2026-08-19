# -*- coding: utf-8 -*-
"""
src/admin/dashboard/runtime_router.py

Phase 5.0 Dashboard Upgrade —— Runtime 子路由。

职责:
- 注册 /api/dashboard/v2/runtime/* 下的 GET 接口
- 调用 RuntimeDashboardProvider 聚合数据
- 严格只读,统一响应格式

约束:
- 禁止 import 任何 src/runtime/** 业务模块
- 仅依赖: src.admin.dashboard.* + src.admin.runtime_dashboard_provider
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import (
    DASHBOARD_API_PREFIX,
    error_response,
    fallback_response,
    ok_response,
)
from src.admin.dashboard.response import (
    PROVIDER_RUNTIME,
    build_dashboard_response,
    build_fallback_response,
)
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify
from src.admin.runtime_dashboard_provider import get_runtime_dashboard_provider

logger = logging.getLogger(__name__)


runtime_v2_bp = Blueprint(
    "dashboard_v2_runtime",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/runtime",
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


def _build_response(data: Dict[str, Any]) -> Any:
    """统一构造 ok / fallback 响应。"""
    if not data or not isinstance(data, dict):
        payload = fallback_response(data={}, reason="empty_data")
        return _jsonify(_attach_schema_meta(payload))
    if data.get("fallback"):
        payload = fallback_response(
            data={k: v for k, v in data.items() if k not in ("fallback", "fallback_reason")},
            reason=data.get("fallback_reason") or "data_unavailable",
        )
        return _jsonify(_attach_schema_meta(payload))
    payload = ok_response(data=data)
    return _jsonify(_attach_schema_meta(payload))


@runtime_v2_bp.route("/status", methods=["GET"])
def runtime_status():
    """
    GET /api/dashboard/v2/runtime/status
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_runtime_dashboard_provider()
        data = provider.get_runtime_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.status 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


@runtime_v2_bp.route("/tasks", methods=["GET"])
def runtime_tasks():
    """
    GET /api/dashboard/v2/runtime/tasks
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_runtime_dashboard_provider()
        data = provider.get_lifecycle_tasks()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.tasks 异常: %s", exc)
        payload = fallback_response(data={"tasks": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


@runtime_v2_bp.route("/ticks", methods=["GET"])
def runtime_ticks():
    """
    GET /api/dashboard/v2/runtime/ticks?limit=20
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_runtime_dashboard_provider()
        data = provider.get_tick_history(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.ticks 异常: %s", exc)
        payload = fallback_response(data={"ticks": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


@runtime_v2_bp.route("/events", methods=["GET"])
def runtime_events():
    """
    GET /api/dashboard/v2/runtime/events?limit=20
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_runtime_dashboard_provider()
        data = provider.get_integration_events(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.events 异常: %s", exc)
        payload = fallback_response(data={"ticks": []}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


# ============================================================
# Step 8.4.6: Live2D Signal —— 只读联动 Runtime Snapshot
# ============================================================
@runtime_v2_bp.route("/live2d", methods=["GET"])
def runtime_live2d():
    """
    GET /api/dashboard/v2/runtime/live2d

    Step 8.4.6 —— Live2D Runtime Snapshot 联动。

    数据流:
        RuntimeSnapshot JSON → RuntimeDashboardProvider.get_live2d_signal() → 本接口

    严格只读:
    - Dashboard 不控制 Live2D
    - Dashboard 不修改 Runtime
    - Dashboard 不创建事件
    - 仅做 read-only 渲染

    响应:统一 envelope(Step 8.4.5)
        {
            "ok": bool,
            "data": { "live2d_signal": {...} },
            "trace": { "sources": [...], "evidence_count": ..., "generated_at": ... },
            "confidence": float,
            "fallback": bool,
            "fallback_reason": str|None,
            "timestamp": iso
        }
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    try:
        provider = get_runtime_dashboard_provider()
        signal = provider.get_live2d_signal()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.live2d 异常: %s", exc)
        payload = build_fallback_response(
            data={},
            reason=f"provider_error:{type(exc).__name__}",
            host_provider=PROVIDER_RUNTIME,
            host_method="get_live2d_signal",
        )
        return _jsonify(_attach_schema_meta(payload))

    # signal 内不允许出现 _meta / source / confidence / trace 等元数据
    # 这里已经是纯业务数据(由 get_live2d_signal 返回的 dict)
    # 但为了避免 signal 内的 fallback/fallback_reason 干扰 data 纯净性,
    # 我们只把核心字段放进 data
    data = {
        "available": bool(signal.get("available", False)),
        "expression": signal.get("expression"),
        "motion": signal.get("motion"),
        "reason": signal.get("reason"),
        "timestamp": signal.get("timestamp"),
        "readonly": bool(signal.get("readonly", True)),
    }

    is_fallback = bool(signal.get("fallback", False))
    fallback_reason = signal.get("fallback_reason")

    if is_fallback:
        payload = build_fallback_response(
            data=data,
            reason=fallback_reason or "data_unavailable",
            host_provider=PROVIDER_RUNTIME,
            host_method="get_live2d_signal",
        )
    else:
        payload = build_dashboard_response(
            data=data,
            evidence_count=1,  # 1 个 live2d 字段
            host_provider=PROVIDER_RUNTIME,
            host_method="get_live2d_signal",
        )

    return _jsonify(_attach_schema_meta(payload))


# ============================================================
# Phase 7.0 Dashboard Runtime Center —— 新增端点
# ============================================================

@runtime_v2_bp.route("/lifecycle", methods=["GET"])
def runtime_lifecycle():
    """
    GET /api/dashboard/v2/runtime/lifecycle

    Phase 7.0 —— Runtime 生命周期状态(uptime / current_task / last_event / last_response)。

    数据源: RuntimeStatusTracker(同进程内存,由 RuntimePipeline 钩子更新)。

    响应:
        {
            "ok": bool,
            "data": {
                "status": "running",
                "started_at": "2026-08-10T...",
                "uptime_seconds": 45200.5,
                "uptime_human": "12h 33m 20s",
                "current_task": "idle",
                "last_event": "user_message",
                "last_event_at": "...",
                "last_response_at": "...",
                "last_response_age_seconds": 20.5,
                "last_response_preview": "你好呀...",
                "last_trace_id": "pipeline_abc123",
                "last_error": null,
                "request_count": 42,
                "success_count": 40,
                "failure_count": 2
            },
            "schema_version": "1.0",
            "timestamp": "..."
        }
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_runtime_dashboard_provider()
        data = provider.get_runtime_lifecycle_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.lifecycle 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


@runtime_v2_bp.route("/traces", methods=["GET"])
def runtime_traces():
    """
    GET /api/dashboard/v2/runtime/traces?limit=20

    Phase 7.0 —— 请求链路追踪(最近 N 条 trace)。

    数据源: RuntimeTraceRecorder(内存缓存,由 RuntimePipeline 钩子写入)。

    响应:
        {
            "ok": bool,
            "data": {
                "traces": [
                    {
                        "trace_id": "pipeline_abc123",
                        "session_id": "pipe_abc123",
                        "user_message_preview": "你好羽依",
                        "stages": [
                            {"name": "token_optimization", "start": "...", "end": "...", "duration_ms": 5, "error": null},
                            {"name": "runtime_path", "start": "...", "end": "...", "duration_ms": 3000, "error": null},
                            ...
                        ],
                        "reply_source": "runtime",
                        "reply_preview": "你好呀...",
                        "total_duration_ms": 5000,
                        "success": true,
                        "error": null,
                        "started_at": "...",
                        "ended_at": "..."
                    }
                ],
                "count": 20
            },
            "schema_version": "1.0",
            "timestamp": "..."
        }
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))
        provider = get_runtime_dashboard_provider()
        data = provider.get_recent_traces(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.traces 异常: %s", exc)
        payload = fallback_response(data={"traces": [], "count": 0}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


@runtime_v2_bp.route("/services", methods=["GET"])
def runtime_services():
    """
    GET /api/dashboard/v2/runtime/services

    Phase 7.0 —— 4 个服务状态总览(api_server / runtime / initiative_sender / agent_server)。

    数据源:
    - api_server: RuntimeStatusTracker(同进程内存)
    - runtime: RuntimeProvider(同进程)
    - initiative_sender + agent_server: data/agent_server_status.json(跨进程文件)

    响应:
        {
            "ok": bool,
            "data": {
                "api_server": {"status": "running", "uptime_seconds": 45200.5, ...},
                "runtime": {"status": "running", "initialized": true, ...},
                "initiative_sender": {"status": "running", "last_heartbeat_age_seconds": 3.2, ...},
                "agent_server": {"status": "running", "host": "0.0.0.0", "port": 8765, ...}
            },
            "schema_version": "1.0",
            "timestamp": "..."
        }
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_runtime_dashboard_provider()
        data = provider.get_services_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.runtime.services 异常: %s", exc)
        payload = fallback_response(data={}, reason=f"provider_error:{type(exc).__name__}")
        return _jsonify(_attach_schema_meta(payload))
    return _build_response(data)


__all__ = ["runtime_v2_bp"]
