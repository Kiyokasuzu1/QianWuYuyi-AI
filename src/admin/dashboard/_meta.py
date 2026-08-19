# -*- coding: utf-8 -*-
"""
src/admin/dashboard/_meta.py

Phase 5.0 Dashboard Upgrade Step 8.4 —— trace / envelope 工具。

职责:
- 提供 @traceable 装饰器,在响应层自动注入 trace 元数据
- 提供 build_envelope() 统一响应外壳构造器
- 提供 calc_confidence() 置信度计算工具
- 不污染业务 data 字段,所有元数据放在 data 之外的 trace / confidence / fallback

约束:
- 仅依赖 Python 标准库
- 不 import 任何业务模块
- 不调用 LLM
- 不持有运行时状态
"""
from __future__ import annotations

import functools
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 默认 trace 容量(防止内存膨胀)
DEFAULT_TRACE_CAPACITY = 64


# ============================================================
# 工具函数
# ============================================================

def _now_iso() -> str:
    """返回当前 UTC ISO 8601 时间戳。"""
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clip_sources(sources: List[Dict[str, Any]], limit: int = DEFAULT_TRACE_CAPACITY) -> List[Dict[str, Any]]:
    """裁剪 sources 列表,防止 trace 过大。"""
    if not isinstance(sources, list):
        return []
    if len(sources) <= limit:
        return list(sources)
    return list(sources[-limit:])


# ============================================================
# 置信度计算
# ============================================================

def calc_confidence(
    *,
    available: bool,
    sources_count: int,
    fallback: bool,
    partial: bool = False,
) -> float:
    """
    计算响应数据可信度(0~1)。

    规则:
    - 数据完全不可用 → 0.0
    - fallback 响应 → 0.3
    - 部分字段缺失 → 0.7
    - 数据健康 → 0.92
    - 多源交叉验证(>=3)→ 0.98

    Args:
        available: 数据是否真正获取到
        sources_count: trace.sources 数量
        fallback: 是否 fallback
        partial: 是否部分字段缺失

    Returns:
        0.0 ~ 1.0 之间的浮点
    """
    if not available:
        return 0.0
    if fallback:
        return 0.3
    if partial:
        return 0.7
    if sources_count >= 3:
        return 0.98
    if sources_count >= 1:
        return 0.92
    return 0.85


# ============================================================
# Envelope 构造器
# ============================================================

def build_envelope(
    *,
    data: Any,
    sources: Optional[List[Dict[str, Any]]] = None,
    confidence: Optional[float] = None,
    fallback: bool = False,
    fallback_reason: Optional[str] = None,
    ok: bool = True,
) -> Dict[str, Any]:
    """
    构造统一响应外壳。

    响应格式:
        {
            "ok": bool,
            "data": ... (纯业务字段,无 _meta),
            "trace": {
                "sources": [{"provider", "method", "duration_ms", "ok", "ts"}],
                "evidence_count": int,
                "generated_at": iso
            },
            "confidence": float,
            "fallback": bool,
            "fallback_reason": str | None,
            "timestamp": iso
        }

    关键约束:
    - data 永远是纯业务字段,禁止包含 _meta / confidence / source
    - trace 与 data 同级
    - confidence 是聚合后总分(0~1)
    """
    srcs = _clip_sources(sources or [])
    evidence_count = len(srcs)
    if confidence is None:
        confidence = calc_confidence(
            available=ok and not fallback,
            sources_count=evidence_count,
            fallback=fallback,
        )
    return {
        "ok": bool(ok),
        "data": data if data is not None else {},
        "trace": {
            "sources": srcs,
            "evidence_count": evidence_count,
            "generated_at": _now_iso(),
        },
        "confidence": float(max(0.0, min(1.0, confidence))),
        "fallback": bool(fallback),
        "fallback_reason": fallback_reason,
        "timestamp": _now_iso(),
    }


def make_fallback_envelope(
    *,
    data: Any = None,
    reason: str = "data_unavailable",
) -> Dict[str, Any]:
    """构造一个 fallback 响应外壳。"""
    return build_envelope(
        data=data if data is not None else {},
        sources=[],
        confidence=0.0,
        fallback=True,
        fallback_reason=reason,
        ok=False,
    )


# ============================================================
# @traceable 装饰器
# ============================================================

def traceable(provider_name: str, method_name: Optional[str] = None) -> Callable:
    """
    装饰器:在响应层自动注入 trace 元数据。

    约定:被装饰函数返回值必须形如:
        {
            "data": ...,        # 纯业务字段
            "fallback": bool,
            "fallback_reason": str | None,
            # 其它可选字段
        }
    装饰器会在 result["data"] 之外加:
        result["trace"] = {...}
    不污染 data。

    Args:
        provider_name: Provider 名称,如 "InitiativeDashboardProvider"
        method_name: 方法名(可选,默认取函数名)

    Usage:
        @traceable("MyProvider", "get_summary")
        def get_summary(self) -> Dict[str, Any]:
            return {"data": {...}, "fallback": False}
    """
    def decorator(fn: Callable) -> Callable:
        mname = method_name or getattr(fn, "__name__", "unknown")

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            start = time.time()
            ok = True
            err_name = ""
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                ok = False
                err_name = type(exc).__name__
                logger.debug("traceable %s.%s 异常: %s", provider_name, mname, exc)
                # 抛回给上层 router,不在装饰器内吞掉
                raise
            finally:
                duration_ms = int((time.time() - start) * 1000)

            if not isinstance(result, dict):
                # 非 dict 返回值,直接返回,不加 trace
                return result

            # 注入 trace 源
            sources_in = []
            if isinstance(result.get("trace"), dict):
                existing = result["trace"].get("sources")
                if isinstance(existing, list):
                    sources_in = list(existing)
            sources_in.append({
                "provider": provider_name,
                "method": mname,
                "duration_ms": duration_ms,
                "ok": ok,
                "error": err_name or None,
                "ts": _now_iso(),
            })
            result.setdefault("trace", {})
            result["trace"]["sources"] = _clip_sources(sources_in)
            result["trace"]["evidence_count"] = len(sources_in)
            result["trace"]["generated_at"] = _now_iso()
            return result

        return wrapper

    return decorator


# ============================================================
# 辅助:从 envelope 拆出 data
# ============================================================

def unwrap_data(envelope: Any) -> Any:
    """
    从 envelope 中拆出 data 字段(仅供 Provider 内部使用,不污染业务)。

    约定 envelope 是 dict,返回 envelope["data"];否则返回 envelope。
    """
    if isinstance(envelope, dict) and "data" in envelope:
        return envelope.get("data")
    return envelope


__all__ = [
    "traceable",
    "build_envelope",
    "make_fallback_envelope",
    "calc_confidence",
    "unwrap_data",
    "DEFAULT_TRACE_CAPACITY",
]
