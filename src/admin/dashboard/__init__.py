# -*- coding: utf-8 -*-
"""
src/admin/dashboard/__init__.py

Phase 5.0 Dashboard Upgrade —— 基础包入口。

职责:
- 暴露统一响应格式构造器
- 暴露核心类型供外部 import
- 不持有任何运行时状态
- 不调用任何业务模块

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model
- 仅依赖 Python 标准库
- 严格只读
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

# 包级常量（与设计文档 Step 2 一致）
DASHBOARD_API_PREFIX = "/api/dashboard/v2"
DASHBOARD_WS_PATH = "/api/dashboard/v2/ws"
DASHBOARD_SCHEMA_VERSION = "1.0"


def now_iso() -> str:
    """返回当前 UTC ISO 8601 时间戳。"""
    return datetime.utcnow().isoformat() + "Z"


def ok_response(data: Any = None, **extra: Any) -> Dict[str, Any]:
    """
    构造成功响应。

    格式:
        {
            "ok": true,
            "data": ...,
            "timestamp": "..."
        }
    """
    payload: Dict[str, Any] = {
        "ok": True,
        "data": data if data is not None else {},
        "timestamp": now_iso(),
    }
    if extra:
        payload.update(extra)
    return payload


def error_response(
    code: str,
    message: str,
    *,
    http_status: int = 400,
    details: Optional[Dict[str, Any]] = None,
) -> tuple[Dict[str, Any], int]:
    """
    构造失败响应。

    Returns:
        (response_dict, http_status) 元组,Flask jsonify + status_code 用法
    """
    payload: Dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
        "timestamp": now_iso(),
    }
    if details:
        payload["error"]["details"] = details
    return payload, http_status


def fallback_response(
    data: Any = None,
    *,
    reason: str = "data_unavailable",
) -> Dict[str, Any]:
    """
    构造 fallback 响应。

    当 Provider 无法获取真实数据时使用,明确标识 fallback。
    严格禁止把 fallback 当作 ok=true 的真实数据。

    格式:
        {
            "ok": true,
            "data": ...,
            "fallback": true,
            "fallback_reason": "...",
            "timestamp": "..."
        }
    """
    payload: Dict[str, Any] = {
        "ok": True,
        "data": data if data is not None else {},
        "fallback": True,
        "fallback_reason": reason,
        "timestamp": now_iso(),
    }
    return payload


__all__ = [
    "DASHBOARD_API_PREFIX",
    "DASHBOARD_WS_PATH",
    "DASHBOARD_SCHEMA_VERSION",
    "now_iso",
    "ok_response",
    "error_response",
    "fallback_response",
    # 子模块(显式 import,避免隐式依赖)
    "snapshot",
    "provider",
    "event_hub",
    "router",
    "ws",
    "runtime_router",
    "selfmodel_router",
    "memory_router",
    "growth_router",                # Phase C.1 P1-1
    "reflection_router",
    "goal_router",
    "initiative_router",
    "life_graph_router",
    "life_state_router",
    "life_timeline_router",
    "event_stream_router",
    # Step 8.4.5 —— Trace & Response
    "trace",
    "response",
    # Step 8.4.7 —— Security / Governance / Audit
    "_security",
    "governance",
    "audit",
    "security_router",
]
