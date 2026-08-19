# -*- coding: utf-8 -*-
"""
src/admin/dashboard/life_graph_router.py

Phase 5.0 Dashboard Upgrade Step 8.3 / 8.4.2 —— LifeGraph 子路由。

职责:
- 注册 /api/dashboard/v2/life-graph/* 下的 GET 接口
- 调用 LifeGraphProvider
- Step 8.4.2 扩展:node 详情、neighbors、why 解释
- 严格只读

API:
- GET /api/dashboard/v2/life-graph/overview
- GET /api/dashboard/v2/life-graph/timeline
- GET /api/dashboard/v2/life-graph/path?from_id=...&to_id=...
- GET /api/dashboard/v2/life-graph/node/<node_id>
- GET /api/dashboard/v2/life-graph/node/<node_id>/neighbors?depth=1
- GET /api/dashboard/v2/life-graph/why/<node_id>
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from src.admin.dashboard import DASHBOARD_API_PREFIX, fallback_response, ok_response
from src.admin.dashboard._meta import build_envelope, calc_confidence
from src.admin.dashboard.router import _attach_schema_meta, _is_local_request, _jsonify, error_response
from src.admin.life_graph_explanation import (
    get_life_graph_explanation,
    reset_life_graph_explanation_for_testing,
)
from src.admin.life_graph_provider import (
    ALL_NODE_TYPES,
    MAX_NEIGHBOR_DEPTH,
    get_life_graph_provider,
    reset_life_graph_provider_for_testing,
)

logger = logging.getLogger(__name__)


life_graph_v2_bp = Blueprint(
    "dashboard_v2_life_graph",
    __name__,
    url_prefix=DASHBOARD_API_PREFIX + "/life-graph",
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
    # 禁止空数据当 ok=true:Dashboard 层必须显式标记 fallback
    nodes = data.get("nodes")
    items = data.get("items")
    path = data.get("path")
    is_empty_overview = isinstance(nodes, list) and len(nodes) == 0
    is_empty_timeline = isinstance(items, list) and len(items) == 0
    is_empty_path = (
        isinstance(path, list)
        and len(path) == 0
        and data.get("found") is False
    )
    if is_empty_overview or is_empty_timeline or is_empty_path:
        payload = fallback_response(
            data={k: v for k, v in data.items() if k not in ("fallback", "fallback_reason")},
            reason="no_data",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _jsonify(_attach_schema_meta(ok_response(data=data)))


def _envelope_from(
    *,
    data: Dict[str, Any],
    sources: List[Dict[str, Any]],
    provider_name: str = "LifeGraphProvider",
    method_name: str = "unknown",
) -> Dict[str, Any]:
    """
    把 provider 返回 dict 包成统一 envelope。
    - data 字段保持纯净(无 _meta)
    - trace 在 data 外
    """
    is_fallback = bool(data.get("fallback"))
    fallback_reason = data.get("fallback_reason")
    sources_full = list(sources) + [{
        "provider": provider_name,
        "method": method_name,
        "ok": not is_fallback,
    }]
    confidence = calc_confidence(
        available=not is_fallback and bool(data),
        sources_count=len(sources_full),
        fallback=is_fallback,
    )
    return build_envelope(
        data=data,
        sources=sources_full,
        confidence=confidence,
        fallback=is_fallback,
        fallback_reason=fallback_reason,
        ok=not is_fallback,
    )


@life_graph_v2_bp.route("/overview", methods=["GET"])
def life_graph_overview():
    """GET /api/dashboard/v2/life-graph/overview"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            node_limit = int(request.args.get("node_limit", 200) or 200)
        except (TypeError, ValueError):
            node_limit = 200
        try:
            edge_limit = int(request.args.get("edge_limit", 400) or 400)
        except (TypeError, ValueError):
            edge_limit = 400
        types_arg = request.args.get("types", None)
        include_types: Any = None
        if isinstance(types_arg, str) and types_arg.strip():
            include_types = [
                t.strip() for t in types_arg.split(",") if t.strip() in ALL_NODE_TYPES
            ]
            if not include_types:
                include_types = None
        provider = get_life_graph_provider()
        data = provider.build_graph(
            node_limit=max(1, min(node_limit, 500)),
            edge_limit=max(1, min(edge_limit, 500)),
            include_node_types=include_types,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_graph.overview 异常: %s", exc)
        payload = fallback_response(
            data={"nodes": [], "edges": []},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@life_graph_v2_bp.route("/timeline", methods=["GET"])
def life_graph_timeline():
    """GET /api/dashboard/v2/life-graph/timeline?limit=100"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            limit = int(request.args.get("limit", 100) or 100)
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 500))
        provider = get_life_graph_provider()
        data = provider.get_timeline(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_graph.timeline 异常: %s", exc)
        payload = fallback_response(
            data={"items": [], "buckets": {}},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


@life_graph_v2_bp.route("/path", methods=["GET"])
def life_graph_path():
    """GET /api/dashboard/v2/life-graph/path?from_id=...&to_id=...&max_depth=8"""
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        from_id = request.args.get("from_id", "") or ""
        to_id = request.args.get("to_id", "") or ""
        try:
            max_depth = int(request.args.get("max_depth", 8) or 8)
        except (TypeError, ValueError):
            max_depth = 8
        if not from_id or not to_id:
            payload, status = error_response(
                code="invalid_input",
                message="from_id 与 to_id 必填",
                http_status=400,
            )
            return jsonify(payload), status
        provider = get_life_graph_provider()
        data = provider.find_path(
            from_id=from_id,
            to_id=to_id,
            max_depth=max(1, min(max_depth, 16)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_graph.path 异常: %s", exc)
        payload = fallback_response(
            data={"path": [], "edges_used": []},
            reason=f"provider_error:{type(exc).__name__}",
        )
        return _jsonify(_attach_schema_meta(payload))
    return _build(data)


# ============================================================
# Step 8.4.2 —— 节点详情 / 邻居 / Why 解释
# ============================================================

@life_graph_v2_bp.route("/node/<node_id>", methods=["GET"])
def life_graph_node(node_id: str):
    """GET /api/dashboard/v2/life-graph/node/<node_id>

    返回统一 envelope:
        {ok, data:{node, evidence, neighbors, fallback, fallback_reason},
         trace, confidence, fallback, fallback_reason, timestamp}
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        provider = get_life_graph_provider()
        data = provider.get_node_detail(node_id or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_graph.node 异常: %s", exc)
        envelope = build_envelope(
            data={},
            sources=[{
                "provider": "LifeGraphProvider",
                "method": "get_node_detail",
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
        provider_name="LifeGraphProvider",
        method_name="get_node_detail",
    )
    return _jsonify(_attach_schema_meta(envelope))


@life_graph_v2_bp.route("/node/<node_id>/neighbors", methods=["GET"])
def life_graph_node_neighbors(node_id: str):
    """GET /api/dashboard/v2/life-graph/node/<node_id>/neighbors?depth=1

    depth 默认 1,最大 MAX_NEIGHBOR_DEPTH=3。超过则 fallback=true, fallback_reason=depth_limit。
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        try:
            depth = int(request.args.get("depth", 1) or 1)
        except (TypeError, ValueError):
            depth = 1
        if depth > MAX_NEIGHBOR_DEPTH:
            envelope = build_envelope(
                data={
                    "center": node_id or "",
                    "nodes": [],
                    "edges": [],
                    "depth": depth,
                    "fallback": True,
                    "fallback_reason": "depth_limit",
                },
                sources=[{
                    "provider": "LifeGraphProvider",
                    "method": "get_neighbors",
                    "ok": False,
                }],
                fallback=True,
                fallback_reason="depth_limit",
                ok=False,
            )
            return _jsonify(_attach_schema_meta(envelope))
        provider = get_life_graph_provider()
        data = provider.get_neighbors(node_id or "", depth=depth)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_graph.node.neighbors 异常: %s", exc)
        envelope = build_envelope(
            data={},
            sources=[{
                "provider": "LifeGraphProvider",
                "method": "get_neighbors",
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
        provider_name="LifeGraphProvider",
        method_name="get_neighbors",
    )
    return _jsonify(_attach_schema_meta(envelope))


@life_graph_v2_bp.route("/why/<node_id>", methods=["GET"])
def life_graph_why(node_id: str):
    """GET /api/dashboard/v2/life-graph/why/<node_id>

    返回 LifeGraphExplanation.explain_why(node_id) 的结果。
    """
    rejected = _reject_non_local()
    if rejected is not None:
        return rejected
    try:
        explanation = get_life_graph_explanation()
        data = explanation.explain_why(node_id or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard_v2.life_graph.why 异常: %s", exc)
        envelope = build_envelope(
            data={},
            sources=[{
                "provider": "LifeGraphExplanation",
                "method": "explain_why",
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
        provider_name="LifeGraphExplanation",
        method_name="explain_why",
    )
    return _jsonify(_attach_schema_meta(envelope))


__all__ = ["life_graph_v2_bp"]
