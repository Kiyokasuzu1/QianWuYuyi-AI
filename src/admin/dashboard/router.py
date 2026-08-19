# -*- coding: utf-8 -*-
"""
src/admin/dashboard/router.py

Phase 5.0 Dashboard Upgrade —— Dashboard V2 API Router。

职责:
- 定义 dashboard_v2_bp(子 Blueprint)
- 注册 /api/dashboard/v2/ 下的 GET 接口
- 严格只读 GET,所有写操作后续必须经过 Auth → Permission → Governance → Audit
- 调用 DashboardProvider 聚合数据
- 统一返回 {ok, data/error, timestamp} 格式

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 仅依赖: src.admin.dashboard.* + Flask
- 数据不可用时必须返回 {fallback: true},禁止 mock 伪装
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from flask import Blueprint, jsonify

from src.admin.dashboard import (
    DASHBOARD_API_PREFIX,
    DASHBOARD_SCHEMA_VERSION,
    error_response,
    fallback_response,
    ok_response,
)
from src.admin.dashboard.event_hub import get_dashboard_event_hub
from src.admin.dashboard.provider import get_dashboard_provider

logger = logging.getLogger(__name__)


# ============================================================
# Blueprint 定义
# ============================================================
dashboard_v2_bp = Blueprint(
    "dashboard_v2",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX,
)


# ============================================================
# 安全:本地访问限制(Step 5)
# ============================================================
_ALLOWED_REMOTE_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_local_request() -> bool:
    """判断请求是否来自本地。"""
    try:
        from flask import request

        remote = (request.remote_addr or "").strip()
        if not remote:
            return False
        return remote in _ALLOWED_REMOTE_HOSTS
    except Exception:
        return False


def _reject_non_local():
    """
    拒绝非本地访问。

    Returns:
        (response, status_code) 或 None(表示通过)
    """
    if _is_local_request():
        return None
    payload, status = error_response(
        code="dashboard_local_only",
        message="Dashboard V2 仅允许本地访问",
        http_status=403,
        details={"remote_addr": "non-local"},
    )
    return jsonify(payload), status


# ============================================================
# 通用处理工具
# ============================================================
def _attach_schema_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    """附加 schema 元信息。"""
    payload["schema_version"] = DASHBOARD_SCHEMA_VERSION
    return payload


def _jsonify(payload: Dict[str, Any]) -> Tuple[Any, int]:
    """统一 jsonify 包装。"""
    return jsonify(payload), 200


# ============================================================
# 路由:Overview
# ============================================================
@dashboard_v2_bp.route("/overview", methods=["GET"])
def dashboard_overview():
    """
    GET /api/dashboard/v2/overview

    Returns:
        {
            "ok": true,
            "data": {
                "online": bool,
                "runtime_status": {...},
                "emotion": {...},
                "current_goal": ...,
                "current_interest": ...,
                "recent_events": [...],
                "health": {...}
            },
            "timestamp": "..."
        }

    失败/降级时返回 {ok: true, fallback: true, fallback_reason: ...}。
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    try:
        provider = get_dashboard_provider()
        data = provider.get_overview()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.overview provider 异常: %s", exc)
        payload = fallback_response(
            data={},
            reason=f"provider_error:{type(exc).__name__}",
        )
        payload, _ = _jsonify(_attach_schema_meta(payload))
        return payload, 200

    # 检查是否需要 fallback(数据为空表示 Provider 未拿到任何真实数据)
    if not data or not isinstance(data, dict):
        payload = fallback_response(data={}, reason="empty_data")
        return _jsonify(_attach_schema_meta(payload))

    payload = ok_response(data=data)
    return _jsonify(_attach_schema_meta(payload))


# ============================================================
# 路由:Snapshot(完整)
# ============================================================
@dashboard_v2_bp.route("/snapshot", methods=["GET"])
def dashboard_snapshot():
    """
    GET /api/dashboard/v2/snapshot

    Returns:
        完整 YuyiDashboardSnapshot
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    try:
        provider = get_dashboard_provider()
        snapshot = provider.get_snapshot()
        data = snapshot.to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.snapshot provider 异常: %s", exc)
        payload = fallback_response(
            data={},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))

    payload = ok_response(data=data)
    return _jsonify(_attach_schema_meta(payload))


# ============================================================
# 路由:Events(供前端轮询,WebSocket 之外的 fallback)
# ============================================================
@dashboard_v2_bp.route("/events", methods=["GET"])
def dashboard_events():
    """
    GET /api/dashboard/v2/events?limit=20

    Returns:
        {
            "ok": true,
            "data": {
                "events": [...]
            }
        }
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected

    try:
        from flask import request

        try:
            limit = int(request.args.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        limit = max(1, min(limit, 200))

        hub = get_dashboard_event_hub()
        events = hub.poll_recent(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.events hub 异常: %s", exc)
        payload = fallback_response(
            data={"events": []},
            reason=f"hub_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))

    payload = ok_response(data={"events": events})
    return _jsonify(_attach_schema_meta(payload))


# ============================================================
# 路由:Health(轻量)
# ============================================================
@dashboard_v2_bp.route("/health", methods=["GET"])
def dashboard_health():
    """
    GET /api/dashboard/v2/health

    不需要本地访问限制(用于监控/探活)。
    """
    try:
        provider = get_dashboard_provider()
        snapshot = provider.get_snapshot()
        data = {
            "status": snapshot.health.status,
            "score": snapshot.health.score,
            "issues": snapshot.health.issues,
        }
    except Exception as exc:  # noqa: BLE001
        payload, status = error_response(
            code="dashboard_health_error",
            message=str(exc),
            http_status=500,
        )
        return jsonify(payload), status

    payload = ok_response(data=data)
    return _jsonify(_attach_schema_meta(payload))


# ============================================================
# 路由:WS info(描述 WebSocket 端点)
# ============================================================
@dashboard_v2_bp.route("/ws/info", methods=["GET"])
def dashboard_ws_info():
    """
    GET /api/dashboard/v2/ws/info

    返回 WebSocket 端点信息(本阶段 WS 端点为占位)。
    """
    from src.admin.dashboard import DASHBOARD_WS_PATH

    payload = ok_response(
        data={
            "ws_path": DASHBOARD_WS_PATH,
            "status": "placeholder",
            "note": "WebSocket 端点为 Phase 5.0-E 预留,本阶段仅占位",
        }
    )
    return _jsonify(_attach_schema_meta(payload))


__all__ = ["dashboard_v2_bp"]
