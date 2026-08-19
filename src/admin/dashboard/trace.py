# -*- coding: utf-8 -*-
"""
src/admin/dashboard/trace.py

Phase 5.0 Dashboard Upgrade Step 8.4.5 —— 统一 Trace / Confidence / Fallback 工具。

职责:
- 提供 TraceContext:在响应层累积 sources / evidence / fallback 状态
- 提供统一 confidence 计算规则(Step 8.4.5 标准)
- 提供 trace envelope 构造器
- 严格不污染 data 字段
- 不 import 业务模块
- 不调用 LLM

设计:
    TraceContext 是无副作用的累加器。Provider / Router 在生成响应时
    持续 add_source / add_evidence / mark_fallback,最后调用
    build_envelope() 得到标准 trace 响应外壳。

约束:
- data 永远是纯业务数据
- trace / confidence / fallback / timestamp 全部位于 data 同级
- 不持有任何运行时状态(除本对象生命周期内)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 默认 trace 容量(防止内存膨胀)
DEFAULT_TRACE_CAPACITY = 64


# ============================================================
# 工具
# ============================================================

def _now_iso() -> str:
    """返回当前 UTC ISO 8601 时间戳。"""
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        except Exception:
            return ""


def _clip_sources(sources: List[Dict[str, Any]], limit: int = DEFAULT_TRACE_CAPACITY) -> List[Dict[str, Any]]:
    """裁剪 sources 列表,防止 trace 过大。"""
    if not isinstance(sources, list):
        return []
    if len(sources) <= limit:
        return list(sources)
    return list(sources[-limit:])


# ============================================================
# 置信度计算(Step 8.4.5 标准)
# ============================================================

# 扣减常量
_PENALTY_NO_SOURCE = 0.3
_PENALTY_NO_EVIDENCE = 0.2
_PENALTY_FALLBACK = 0.5
_PENALTY_EXCEPTION = 0.6

# 基础分
_BASE_CONFIDENCE = 1.0

# 边界
_CONFIDENCE_MIN = 0.0
_CONFIDENCE_MAX = 1.0


def calculate_confidence(
    *,
    has_sources: bool = True,
    evidence_count: int = 0,
    is_fallback: bool = False,
    is_exception: bool = False,
) -> float:
    """
    统一计算 confidence(0~1)。

    规则:
      基础分:1.0
      来源缺失:-0.3
      证据数量=0:-0.2
      fallback:-0.5
      Provider 异常:-0.6
      最低 0,最高 1

    Args:
        has_sources: 是否有任何 source 记录
        evidence_count: 证据数量
        is_fallback: 是否 fallback 响应
        is_exception: Provider 是否抛过异常

    Returns:
        0.0 ~ 1.0 之间的浮点(已 clip)
    """
    score = _BASE_CONFIDENCE
    if not has_sources:
        score -= _PENALTY_NO_SOURCE
    if evidence_count <= 0:
        score -= _PENALTY_NO_EVIDENCE
    if is_fallback:
        score -= _PENALTY_FALLBACK
    if is_exception:
        score -= _PENALTY_EXCEPTION
    return max(_CONFIDENCE_MIN, min(_CONFIDENCE_MAX, float(score)))


# ============================================================
# TraceContext —— 累加器
# ============================================================

class TraceContext:
    """
    Trace / Confidence / Fallback 累加器。

    Usage:
        ctx = TraceContext(provider_name="LifeStateProvider")
        ctx.add_source("InitiativeDashboardProvider", "get_summary", ok=True, duration_ms=5)
        ctx.add_evidence(3)
        ctx.mark_fallback(False)
        envelope = ctx.build_envelope(data={"foo": "bar"})

    字段:
        sources: List[Dict]  -- 每个 source 形如 {provider, method, duration_ms, ok, ts}
        evidence_count: int  -- 证据数量
        fallback: bool
        fallback_reason: str|None
        exception_seen: bool -- 是否出现过异常
    """

    def __init__(
        self,
        *,
        provider_name: str = "",
        method_name: str = "",
    ) -> None:
        self._provider_name = str(provider_name or "")
        self._method_name = str(method_name or "")
        self._sources: List[Dict[str, Any]] = []
        self._evidence_count: int = 0
        self._fallback: bool = False
        self._fallback_reason: Optional[str] = None
        self._exception_seen: bool = False
        # 不在构造函数中自动添加 host source —— 由调用方显式 add_source
        # 避免和 build_dashboard_response 中的显式 add_source 重复

    # --------------------------------------------------------
    # 累加 API
    # --------------------------------------------------------
    def add_source(
        self,
        provider: str,
        method: str,
        *,
        ok: bool = True,
        duration_ms: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        """添加一个数据来源。"""
        if not provider:
            return
        src: Dict[str, Any] = {
            "provider": str(provider),
            "method": str(method or ""),
            "ok": bool(ok),
            "ts": _now_iso(),
        }
        if duration_ms is not None:
            try:
                src["duration_ms"] = int(duration_ms)
            except (TypeError, ValueError):
                src["duration_ms"] = None
        if error:
            src["error"] = str(error)
        self._sources.append(src)
        if not ok:
            self._exception_seen = True

    def add_evidence(self, count: int) -> None:
        """
        增加 evidence 数量。

        Args:
            count: 增量(允许 0 / 负数将被忽略)
        """
        try:
            n = int(count)
        except (TypeError, ValueError):
            return
        if n <= 0:
            return
        self._evidence_count += n

    def set_evidence(self, count: int) -> None:
        """直接设置 evidence_count(覆盖)。"""
        try:
            n = int(count)
        except (TypeError, ValueError):
            return
        if n < 0:
            n = 0
        self._evidence_count = n

    def mark_fallback(self, fallback: bool = True, reason: Optional[str] = None) -> None:
        """标记 fallback。"""
        self._fallback = bool(fallback)
        if reason is not None:
            self._fallback_reason = str(reason)

    def mark_exception(self, error: Optional[str] = None) -> None:
        """标记 Provider 出现过异常。"""
        self._exception_seen = True
        if error:
            self._fallback_reason = str(error)

    # --------------------------------------------------------
    # 查询 API
    # --------------------------------------------------------
    @property
    def sources(self) -> List[Dict[str, Any]]:
        return list(self._sources)

    @property
    def evidence_count(self) -> int:
        return int(self._evidence_count)

    @property
    def fallback(self) -> bool:
        return bool(self._fallback)

    @property
    def fallback_reason(self) -> Optional[str]:
        return self._fallback_reason

    @property
    def exception_seen(self) -> bool:
        return bool(self._exception_seen)

    def has_sources(self) -> bool:
        return len(self._sources) > 0

    # --------------------------------------------------------
    # 计算
    # --------------------------------------------------------
    def calculate_confidence(self) -> float:
        """根据当前累加状态计算 confidence。"""
        return calculate_confidence(
            has_sources=self.has_sources(),
            evidence_count=self._evidence_count,
            is_fallback=self._fallback,
            is_exception=self._exception_seen,
        )

    # --------------------------------------------------------
    # 构造
    # --------------------------------------------------------
    def build_trace(self) -> Dict[str, Any]:
        """构造 trace 字段(不含 envelope 外壳)。"""
        return {
            "sources": _clip_sources(self._sources),
            "evidence_count": int(self._evidence_count),
            "generated_at": _now_iso(),
        }

    def build_envelope(
        self,
        data: Any = None,
        *,
        ok: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        构造统一响应外壳。

        Args:
            data: 纯业务 data(可以是任何 JSON 可序列化对象)
            ok: 显式指定 ok 状态;默认 = (not fallback and not exception_seen)

        Returns:
            {
                "ok": bool,
                "data": ...,
                "trace": {sources, evidence_count, generated_at},
                "confidence": float,
                "fallback": bool,
                "fallback_reason": str|None,
                "timestamp": iso
            }
        """
        if ok is None:
            ok = bool(not self._fallback and not self._exception_seen)
        return {
            "ok": bool(ok),
            "data": data if data is not None else {},
            "trace": self.build_trace(),
            "confidence": round(self.calculate_confidence(), 4),
            "fallback": bool(self._fallback),
            "fallback_reason": self._fallback_reason,
            "timestamp": _now_iso(),
        }


# ============================================================
# 模块级便捷函数
# ============================================================

def quick_envelope(
    data: Any,
    *,
    sources: Optional[List[Dict[str, Any]]] = None,
    evidence_count: int = 0,
    fallback: bool = False,
    fallback_reason: Optional[str] = None,
    exception: bool = False,
) -> Dict[str, Any]:
    """
    一步构造 envelope(无 TraceContext 对象)。

    Args:
        data: 业务数据
        sources: 来源列表
        evidence_count: 证据数
        fallback: 是否 fallback
        fallback_reason: fallback 原因
        exception: Provider 异常
    """
    ctx = TraceContext()
    if sources:
        for s in sources:
            if isinstance(s, dict):
                ctx.add_source(
                    provider=str(s.get("provider", "")),
                    method=str(s.get("method", "")),
                    ok=bool(s.get("ok", True)),
                    duration_ms=s.get("duration_ms"),
                    error=s.get("error"),
                )
    ctx.set_evidence(evidence_count)
    ctx.mark_fallback(fallback, fallback_reason)
    if exception:
        ctx.mark_exception(fallback_reason or "exception")
    return ctx.build_envelope(data)


__all__ = [
    "TraceContext",
    "calculate_confidence",
    "quick_envelope",
    "DEFAULT_TRACE_CAPACITY",
    "_now_iso",
]
