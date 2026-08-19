# -*- coding: utf-8 -*-
"""
src/admin/dashboard/ws.py

Phase 5.0 Dashboard Upgrade —— WebSocket 端点(占位)。

职责:
- 暴露 register_dashboard_ws(app) 接口
- 当前阶段为占位实现,仅做端点存在性声明与协议契约定义
- 真正的 WebSocket 传输在 Phase 5.0-E 接入
- Step 8.4.4 扩展:在 supported_events 中追加 dashboard.event_stream 频道

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 不引入 flask-sock / websockets 等重量级依赖
- 本阶段不连接任何业务模块,只保留接口骨架
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_WS_PATH, error_response, ok_response

logger = logging.getLogger(__name__)


# ============================================================
# Blueprint 骨架
# ============================================================
dashboard_ws_bp = Blueprint(
    "dashboard_v2_ws",
    __name__,
    url_prefix=DASHBOARD_WS_PATH,
)


@dashboard_ws_bp.route("", methods=["GET", "POST"])
def ws_placeholder():
    """
    WebSocket 端点占位。

    当前阶段:
    - GET: 返回端点状态(JSON),便于前端探测
    - POST: 返回 426 Upgrade Required(提示这是 WebSocket 端点)
    """
    if request.method == "GET":
        payload = ok_response(
            data={
                "endpoint": DASHBOARD_WS_PATH,
                "transport": "websocket",
                "status": "placeholder",
                "note": "Phase 5.0-E 接入真正的 WebSocket 传输",
                "supported_events": [
                    "runtime_state_changed",
                    "emotion_changed",
                    "snapshot_updated",
                    "fallback_changed",
                    # Step 8.4.4 —— Event Stream 频道
                    "dashboard.event_stream",
                ],
                "channels": {
                    "dashboard.event_stream": {
                        "description": "统一生命事件流 — 实时推送 IntegrationEvent",
                        "message_format": {
                            "type": "event",
                            "data": {
                                "event_id": "<string>",
                                "event_type": "<string>",
                                "timestamp": "<iso8601>",
                            },
                        },
                        "fallback_polling_seconds": 5,
                        "provider": "EventStreamProvider",
                    },
                },
            }
        )
        return jsonify(payload), 200
    # POST/PUT 等
    payload, status = error_response(
        code="ws_upgrade_required",
        message="该端点需要 WebSocket Upgrade",
        http_status=426,
    )
    return jsonify(payload), status


def register_dashboard_ws(app: Any) -> Optional[Blueprint]:
    """
    注册 Dashboard V2 WebSocket 端点(占位)。

    Args:
        app: Flask app 或兼容对象
    """
    try:
        app.register_blueprint(dashboard_ws_bp)
        logger.info("Dashboard V2 WS placeholder registered at %s", DASHBOARD_WS_PATH)
        return dashboard_ws_bp
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dashboard V2 WS 注册失败: %s", exc)
        return None


__all__ = ["dashboard_ws_bp", "register_dashboard_ws"]
