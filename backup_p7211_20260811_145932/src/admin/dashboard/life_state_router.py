# -*- coding: utf-8 -*-
"""
src/admin/dashboard/life_state_router.py

Phase 5.0 Dashboard Upgrade Step 8.4.1 —— LifeState 子路由。

职责:
- 注册 /api/dashboard/v2/life-state/* 下的 GET 接口
- 调用 LifeStateProvider,返回统一 trace envelope
- 严格只读

API:
- GET /api/dashboard/v2/life-state/summary
    返回 {ok, data, trace, confidence, fallback, fallback_reason, timestamp}
    其中 data 是纯结构化 state_summary,不包含任何 _meta
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response
from src.admin.dashboard.response import (
    PROVIDER_LIFE_STATE,
    build_dashboard_response,
    build_exception_response,
)
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.life_state_provider import get_life_state_provider

logger = logging.getLogger(__name__)


life_state_v2_bp = Blueprint(
    "dashboard_v2_life_state",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/life-state",
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


@life_state_v2_bp.route("/summary", methods=["GET"])
def life_state_summary():
    """GET /api/dashboard/v2/life-state/summary

    返回结构化 LifeStateSummary envelope:
        {
            "ok": bool,
            "data": {...},          # 纯业务字段
            "trace": {...},         # trace 元数据
            "confidence": float,    # 0~1
            "fallback": bool,
            "fallback_reason": str|None,
            "timestamp": iso
        }
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_life_state_provider()
        envelope = provider.get_state_summary()
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_state.summary 异常: %s", exc)
        envelope = build_exception_response(
            error_name=type(exc).__name__,
            host_provider=PROVIDER_LIFE_STATE,
            host_method="get_state_summary",
        )
    return _jsonify(_attach_schema_meta(envelope))


__all__ = ["life_state_v2_bp"]
