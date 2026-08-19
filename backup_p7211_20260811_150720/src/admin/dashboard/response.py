# -*- coding: utf-8 -*-
"""
src/admin/dashboard/response.py

Phase 5.0 Dashboard Upgrade Step 8.4.5 —— 统一 Response Builder。

职责:
- 提供 build_dashboard_response():所有 Dashboard Router 共用
- 输入:纯 data + 额外上下文(sources / evidence / fallback / ...)
- 输出:标准 envelope {ok, data, trace, confidence, fallback, fallback_reason, timestamp}

约束:
- data 永远是纯业务数据(禁止注入 _meta / confidence / source)
- 不修改业务 data
- 不调用 LLM
- 不持有任何运行时状态
- 不 import 业务模块
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from src.admin.dashboard.trace import (
    TraceContext,
    _now_iso,
    calculate_confidence,
    DEFAULT_TRACE_CAPACITY,
)

logger = logging.getLogger(__name__)


# ============================================================
# Provider 标识常量(用于标准化 source 名称)
# ============================================================

PROVIDER_LIFE_STATE = "LifeStateProvider"
PROVIDER_LIFE_GRAPH = "LifeGraphProvider"
PROVIDER_LIFE_TIMELINE = "LifeTimelineProvider"
PROVIDER_EVENT_STREAM = "EventStreamProvider"
PROVIDER_MEMORY = "MemoryDashboardProvider"
PROVIDER_REFLECTION = "ReflectionDashboardProvider"
PROVIDER_GOAL = "GoalDashboardProvider"
PROVIDER_INITIATIVE = "InitiativeDashboardProvider"
PROVIDER_RUNTIME = "RuntimeDashboardProvider"
PROVIDER_SELF_MODEL = "SelfModelDashboardProvider"
PROVIDER_DASHBOARD = "DashboardProvider"
PROVIDER_EVENT_HUB = "EventHub"


# ============================================================
# 主入口:build_dashboard_response
# ============================================================

def build_dashboard_response(
    data: Any,
    sources: Optional[List[Dict[str, Any]]] = None,
    evidence_count: int = 0,
    *,
    fallback: bool = False,
    fallback_reason: Optional[str] = None,
    exception: bool = False,
    host_provider: str = "",
    host_method: str = "",
) -> Dict[str, Any]:
    """
    所有 Dashboard Router 共用的统一 Response Builder。

    严格不修改 data 字段 —— 仅在 data 之外添加 trace / confidence / fallback。

    Args:
        data: 纯业务 data(任何 JSON 可序列化对象)
        sources: 数据来源列表,每个 source 形如:
            {
                "provider": "LifeStateProvider",
                "method": "get_state_summary",
                "duration_ms": 5,
                "ok": true,
                "ts": "2026-08-01T..."
            }
        evidence_count: 证据数量
        fallback: 是否 fallback
        fallback_reason: fallback 原因
        exception: Provider 是否抛过异常
        host_provider: 入口 provider 名(用于自动添加 host source)
        host_method: 入口方法名

    Returns:
        {
            "ok": bool,
            "data": ...,
            "trace": {sources, evidence_count, generated_at},
            "confidence": float (0~1),
            "fallback": bool,
            "fallback_reason": str|None,
            "timestamp": iso
        }
    """
    ctx = TraceContext()
    # 1. 添加其他 sources(去重:相同 provider+method 只保留一次)
    seen_keys: set = set()
    if sources:
        for s in sources:
            if not isinstance(s, dict):
                continue
            prov = str(s.get("provider", ""))
            meth = str(s.get("method", ""))
            key = (prov, meth)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ctx.add_source(
                provider=prov,
                method=meth,
                ok=bool(s.get("ok", True)),
                duration_ms=s.get("duration_ms"),
                error=s.get("error"),
            )
    # 2. 添加 host source(若指定,放在最前;若已存在则不重复)
    if host_provider:
        key = (str(host_provider), str(host_method or "unknown"))
        if key not in seen_keys:
            ctx.add_source(
                host_provider,
                host_method or "unknown",
                ok=not exception,
                error=(fallback_reason if exception else None),
            )
            # 移到列表最前
            try:
                if ctx._sources:
                    src = ctx._sources.pop()
                    ctx._sources.insert(0, src)
            except Exception:  # noqa: BLE001
                pass
    # 3. 设置 evidence / fallback / exception
    ctx.set_evidence(evidence_count)
    if fallback:
        ctx.mark_fallback(True, fallback_reason)
    if exception:
        ctx.mark_exception(fallback_reason or "exception")
    return ctx.build_envelope(data)


def build_fallback_response(
    data: Any = None,
    *,
    reason: str = "data_unavailable",
    sources: Optional[List[Dict[str, Any]]] = None,
    host_provider: str = "",
    host_method: str = "",
) -> Dict[str, Any]:
    """
    构造一个 fallback 响应。

    自动设置:
        fallback = True
        ok = False
        confidence ≈ 0
    """
    return build_dashboard_response(
        data if data is not None else {},
        sources=sources,
        evidence_count=0,
        fallback=True,
        fallback_reason=reason,
        exception=False,
        host_provider=host_provider,
        host_method=host_method,
    )


def build_exception_response(
    error_name: str = "Exception",
    *,
    host_provider: str = "",
    host_method: str = "",
) -> Dict[str, Any]:
    """构造一个 provider 异常响应。"""
    return build_dashboard_response(
        {},
        sources=[],
        evidence_count=0,
        fallback=True,
        fallback_reason=f"provider_error:{error_name}",
        exception=True,
        host_provider=host_provider,
        host_method=host_method,
    )


# ============================================================
# Router helper:把 envelope 直接交给 Flask
# ============================================================

def build_router_envelope(
    *,
    provider_name: str,
    method_name: str,
    call: Any,
    on_error: Any = None,
) -> Dict[str, Any]:
    """
    在 Router 层使用的统一调用封装。

    Args:
        provider_name: Provider 名
        method_name: 调用方法名
        call: 一个无参 callable,返回 envelope dict(已包含 trace / confidence ...)
              或者返回 (data, sources, evidence_count) 元组
        on_error: 异常处理函数(返回 envelope)或 None(用默认)

    Returns:
        envelope dict
    """
    start = time.time()
    try:
        result = call()
    except Exception as exc:  # noqa: BLE001
        duration_ms = int((time.time() - start) * 1000)
        if on_error is not None:
            try:
                return on_error(exc)
            except Exception:  # noqa: BLE001
                pass
        envelope = build_exception_response(
            error_name=type(exc).__name__,
            host_provider=provider_name,
            host_method=method_name,
        )
        # 修正 duration_ms
        try:
            if envelope.get("trace", {}).get("sources"):
                envelope["trace"]["sources"][-1]["duration_ms"] = duration_ms
        except Exception:  # noqa: BLE001
            pass
        return envelope

    # call() 返回值可能是 envelope 或元组
    duration_ms = int((time.time() - start) * 1000)
    if isinstance(result, dict) and "data" in result and "trace" in result:
        # 已经是 envelope
        # 修正 host source 的 duration_ms
        try:
            if result.get("trace", {}).get("sources"):
                first = result["trace"]["sources"][0]
                if isinstance(first, dict) and first.get("provider") == provider_name:
                    first["duration_ms"] = duration_ms
        except Exception:  # noqa: BLE001
            pass
        return result
    # 当作 (data, ...) 处理
    if isinstance(result, tuple) and len(result) >= 1:
        data = result[0]
        sources = result[1] if len(result) > 1 and isinstance(result[1], list) else None
        evidence = result[2] if len(result) > 2 and isinstance(result[2], int) else 0
        fallback = result[3] if len(result) > 3 and isinstance(result[3], bool) else False
        reason = result[4] if len(result) > 4 and isinstance(result[4], str) else None
        return build_dashboard_response(
            data,
            sources=sources,
            evidence_count=evidence,
            fallback=fallback,
            fallback_reason=reason,
            host_provider=provider_name,
            host_method=method_name,
        )
    # 兜底:把 result 当作 data
    return build_dashboard_response(
        result,
        host_provider=provider_name,
        host_method=method_name,
    )


__all__ = [
    "build_dashboard_response",
    "build_fallback_response",
    "build_exception_response",
    "build_router_envelope",
    "PROVIDER_LIFE_STATE",
    "PROVIDER_LIFE_GRAPH",
    "PROVIDER_LIFE_TIMELINE",
    "PROVIDER_EVENT_STREAM",
    "PROVIDER_MEMORY",
    "PROVIDER_REFLECTION",
    "PROVIDER_GOAL",
    "PROVIDER_INITIATIVE",
    "PROVIDER_RUNTIME",
    "PROVIDER_SELF_MODEL",
    "PROVIDER_DASHBOARD",
    "PROVIDER_EVENT_HUB",
]
