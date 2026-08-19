# -*- coding: utf-8 -*-
"""
src/admin/reflection_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 7.4 —— Reflection Dashboard Provider。

职责:
- 只读提供 Reflection 数据给 Dashboard
- 数据源:已有 Reflection 数据通路(通过 Admin 侧 EventHub 间接访问 IntegrationEvent)
  严禁直接 import 任何业务实现类
- 严格容错,任何子组件不可用时返回 fallback
- 不调用 LLM 生成 Reflection
- 不重新计算 Insight
- 不修改 Reflection 状态
- 严格只读

API:
- list_reflections(limit=20, type=None) -> {items, total, available, fallback}
- get_reflection(reflection_id) -> {reflection, available, fallback}
- get_insights(reflection_id) -> {items, total, available, fallback}
- get_evidence_chain(reflection_id) -> {chain, available, fallback}

数据流:
    Dashboard UI
        ↓
    ReflectionRouter
        ↓
    ReflectionDashboardProvider
        ↓
    EventHub (Admin 侧只读访问层) → IntegrationEvent
        ↓
    Reflection Authority(由 reflection/growth/personality/memory 业务模块内部维护,
    Dashboard 仅通过 EventHub 间接观察,不做任何穿透访问)

约束:
- 禁止 import 任何 src.runtime.reflection / src.growth / src.personality / src.memory 业务模块
- 禁止 import 任何业务实现类
- 仅依赖: src.admin.dashboard.event_hub
- 所有失败必须返回 fallback=true,绝不能"空数据但标记真实"
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# Reflection 类型
REFLECTION_TYPE_DAILY = "daily"
REFLECTION_TYPE_EVENT = "event"
REFLECTION_TYPE_GROWTH = "growth"

ALL_REFLECTION_TYPES = (
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
)

# Integration Event 关联 Reflection 的事件类型
# 这些事件由 ReflectionLifecycleTask 发出,数据流是 EventLog 的"读窗口"
INTEGRATION_REFLECTION_EVENT_TYPES = (
    "integration.reflection.completed",
    "integration.reflection.daily_completed",
    "integration.reflection.event_completed",
    "integration.reflection.growth_completed",
)

# 默认缓存容量(防止 history 无限增长)
DEFAULT_PROVIDER_CACHE_CAPACITY = 512
# 默认 limit
DEFAULT_LIMIT = 20
# 最大 limit
MAX_LIMIT = 200


# ============================================================
# 工具函数
# ============================================================

def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    try:
        return getattr(obj, key, default)
    except Exception:
        return default


def _safe_call(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        if fn is None:
            return None
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("reflection_dashboard_provider safe_call 失败: %s", exc)
        return None


def _is_reflection_event(event_type: Any) -> bool:
    """判断事件类型是否与 reflection 相关。"""
    if not event_type or not isinstance(event_type, str):
        return False
    return event_type in INTEGRATION_REFLECTION_EVENT_TYPES


def _resolve_reflection_type(event: Dict[str, Any]) -> str:
    """
    从 reflection event payload 中提取 reflection 类型。
    优先从 payload.reflection_type 取,否则从 event_type 推断。
    """
    payload = event.get("payload") if isinstance(event, dict) else None
    if isinstance(payload, dict):
        rt = payload.get("reflection_type")
        if isinstance(rt, str) and rt in ALL_REFLECTION_TYPES:
            return rt
    # 退化:从 event_type 推断
    et = event.get("event_type", "") if isinstance(event, dict) else ""
    if isinstance(et, str):
        if "daily" in et:
            return REFLECTION_TYPE_DAILY
        if "growth" in et:
            return REFLECTION_TYPE_GROWTH
        if "event" in et:
            return REFLECTION_TYPE_EVENT
    return REFLECTION_TYPE_DAILY


def _resolve_timestamp(event: Dict[str, Any]) -> float:
    """
    从 event 中提取时间戳(浮点秒)。
    优先级:payload.triggered_at > event.timestamp > event.received_at
    """
    if not isinstance(event, dict):
        return 0.0
    payload = event.get("payload")
    if isinstance(payload, dict):
        ts = payload.get("triggered_at")
        if ts is None:
            ts = payload.get("created_at")
        if isinstance(ts, (int, float)) and ts == ts:  # not NaN
            try:
                return float(ts)
            except Exception:
                pass
    ts = event.get("timestamp")
    if isinstance(ts, (int, float)) and ts == ts:
        try:
            return float(ts)
        except Exception:
            pass
    return 0.0


def _to_iso(ts: Any) -> Optional[str]:
    """将浮点时间戳转为 ISO 字符串。"""
    if ts is None:
        return None
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))
    except Exception:
        return None


def _reflection_view_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 IntegrationEvent 规范化为 Reflection 视图(用于 list_reflections)。
    """
    if not isinstance(event, dict):
        return {}
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ts = _resolve_timestamp(event)
    return {
        "reflection_id": str(payload.get("reflection_id", "") or event.get("event_id", "") or ""),
        "type": _resolve_reflection_type(event),
        "created_at": _to_iso(ts) or "",
        "created_ts": ts,
        "summary": str(payload.get("summary", "") or "")[:200],
        "insight_count": int(payload.get("insight_count", 0) or 0),
        "suggestion_count": int(payload.get("suggestion_count", 0) or 0),
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
        "evidence_strength": float(payload.get("evidence_strength", 0.0) or 0.0),
        "source_event_count": len(payload.get("source_event_ids", []) or []),
        "status": "completed",
        "fallback": False,
    }


def _event_summary(event: Dict[str, Any]) -> Dict[str, Any]:
    """从 event 中提取 evidence 节点的最小摘要。"""
    if not isinstance(event, dict):
        return {}
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ts = _resolve_timestamp(event)
    return {
        "event_id": str(event.get("event_id", "") or ""),
        "event_type": str(event.get("event_type", "") or ""),
        "source": str(event.get("source", "") or ""),
        "timestamp": _to_iso(ts) or "",
        "raw": dict(payload),
    }


# ============================================================
# 默认 Reflection Source(EventHub-based,只读)
# ============================================================

class EventHubReflectionSource:
    """
    通过 Admin 侧 EventHub 间接访问 IntegrationEvent 中的 reflection 数据。
    严格只读,绝不调用任何业务模块的写方法。
    """

    def __init__(self, event_hub: Optional[Any] = None) -> None:
        self._event_hub = event_hub

    def _get_hub(self) -> Optional[Any]:
        if self._event_hub is not None:
            return self._event_hub
        try:
            from src.admin.dashboard.event_hub import get_dashboard_event_hub
            self._event_hub = get_dashboard_event_hub()
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubReflectionSource: EventHub 不可用: %s", exc)
            self._event_hub = None
        return self._event_hub

    def list_reflection_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        """
        从 EventHub 拉取所有 reflection 相关的 IntegrationEvent。

        Returns:
            reflection event 列表(按时间倒序),失败时返回空列表。
        """
        hub = self._get_hub()
        if hub is None:
            return []
        # poll_recent 会触发 _refresh_from_host,内部访问 host.event_log
        # 但 poll_recent 的结果是 summarize 后的视图,缺失 payload
        # 因此我们尝试直接访问 _refresh_from_host / host.event_log 以获取完整 events
        try:
            # 尝试走"未简化"路径
            poll_fn = getattr(hub, "poll_recent", None)
            if callable(poll_fn):
                # poll_recent 仅返回 summarize 后的视图
                # 如果需要完整 payload,需要在 hub 上有相应方法
                # 我们用 poll_recent 作为基础;细节通过 event_hub 接口获取
                _ = poll_fn(limit=max(1, min(MAX_LIMIT, int(limit) or 0)))
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubReflectionSource: poll_recent 失败: %s", exc)
            return []

        # 现在尝试从 hub 内部获取完整 events
        # hub._refresh_from_host 会调用 _summarize_event
        # 但 hub 本身缓存了完整 events 的引用吗?需要查看
        # 实际上 hub 内部只缓存 summarize 后的视图
        # 因此我们直接走 hub 持有的 host.event_log
        try:
            host = getattr(hub, "_get_host", None)
            if callable(host):
                h = host()
                if h is None:
                    return []
                event_log = _safe_get(h, "event_log")
                if event_log is None:
                    return []
                # 优先使用 events(event_type=...) 精确过滤
                events_fn = _safe_get(event_log, "events")
                if callable(events_fn):
                    # 拉取所有 reflection 类型
                    out: List[Dict[str, Any]] = []
                    seen_ids = set()
                    for et in INTEGRATION_REFLECTION_EVENT_TYPES:
                        try:
                            sub = events_fn(event_type=et, limit=limit) or []
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("EventHubReflectionSource: events(%s) 失败: %s", et, exc)
                            continue
                        for ev in sub:
                            eid = str(_safe_get(ev, "event_id", "") or "")
                            if eid and eid not in seen_ids:
                                seen_ids.add(eid)
                                out.append(self._normalize(ev))
                    # 按 timestamp 倒序
                    out.sort(
                        key=lambda e: (_resolve_timestamp(e), str(e.get("event_id", "") or "")),
                        reverse=True,
                    )
                    return out
                # 退化:用 latest(limit) 全量拉,自己过滤
                latest_fn = _safe_get(event_log, "latest")
                if callable(latest_fn):
                    try:
                        all_events = latest_fn(limit=max(1, min(MAX_LIMIT, int(limit) or 0))) or []
                    except Exception:
                        return []
                    out2: List[Dict[str, Any]] = []
                    for ev in all_events:
                        et = str(_safe_get(ev, "event_type", "") or "")
                        if _is_reflection_event(et):
                            out2.append(self._normalize(ev))
                    out2.sort(
                        key=lambda e: (_resolve_timestamp(e), str(e.get("event_id", "") or "")),
                        reverse=True,
                    )
                    return out2
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubReflectionSource: 读取 host.event_log 失败: %s", exc)
        return []

    def _normalize(self, ev: Any) -> Dict[str, Any]:
        """把 IntegrationEvent 转换为 dict(包含 payload 等关键字段)。"""
        return {
            "event_id": str(_safe_get(ev, "event_id", "") or ""),
            "event_type": str(_safe_get(ev, "event_type", "") or ""),
            "source": str(_safe_get(ev, "source", "") or ""),
            "timestamp": _safe_get(ev, "timestamp", None),
            "payload": _safe_get(ev, "payload", {}) or {},
            "related_ids": list(_safe_get(ev, "related_ids", []) or []),
            "metadata": _safe_get(ev, "metadata", {}) or {},
        }


# ============================================================
# Provider
# ============================================================
class ReflectionDashboardProvider:
    """
    Reflection Dashboard 只读 Provider。

    注入:
        reflection_source: 任何暴露 list_reflection_events(limit) 接口的对象
                          (测试时可注入 mock;生产由 EventHubReflectionSource 提供)
    """

    def __init__(
        self,
        reflection_source: Optional[Any] = None,
        *,
        cache_capacity: int = DEFAULT_PROVIDER_CACHE_CAPACITY,
    ) -> None:
        self._lock = threading.RLock()
        self._reflection_source = reflection_source
        self._cache_capacity = max(0, int(cache_capacity))
        # 内部缓存:reflection_id -> {event, ...}
        self._cache: Dict[str, Dict[str, Any]] = {}
        # FIFO 淘汰顺序
        self._cache_order: deque = deque(maxlen=self._cache_capacity) if self._cache_capacity > 0 else deque()
        # 加载统计
        self._last_load_at: float = 0.0
        self._last_load_ok: bool = False
        self._last_error: str = ""

    # --------------------------------------------------------
    # 内部:获取 reflection source
    # --------------------------------------------------------
    def _get_source(self) -> Optional[Any]:
        with self._lock:
            if self._reflection_source is not None:
                return self._reflection_source
            try:
                self._reflection_source = EventHubReflectionSource()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ReflectionDashboardProvider: 默认 source 不可用: %s", exc)
                self._reflection_source = None
            return self._reflection_source

    def _refresh_cache(self) -> bool:
        """从 source 拉取并刷新缓存。"""
        src = self._get_source()
        if src is None:
            with self._lock:
                self._last_load_ok = False
                self._last_error = "reflection_source_unavailable"
            return False
        try:
            n = self._cache_capacity if self._cache_capacity > 0 else DEFAULT_PROVIDER_CACHE_CAPACITY
            events = src.list_reflection_events(limit=n) or []
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_load_ok = False
                self._last_error = f"load_error:{type(exc).__name__}"
            return False
        if not isinstance(events, list):
            with self._lock:
                self._last_load_ok = False
                self._last_error = "invalid_source_payload"
            return False
        # 按 reflection_id 聚合(同一 reflection_id 的多次出现合并为一条)
        new_cache: Dict[str, Dict[str, Any]] = {}
        order: List[str] = deque(maxlen=self._cache_capacity) if self._cache_capacity > 0 else deque()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            rid = str(payload.get("reflection_id", "") or "")
            if not rid:
                # 用 event_id 兜底
                rid = str(ev.get("event_id", "") or "")
            if not rid:
                continue
            # 同一 rid:取时间戳更新的
            existing = new_cache.get(rid)
            if existing is not None:
                if _resolve_timestamp(ev) <= _resolve_timestamp(existing):
                    continue
            new_cache[rid] = ev
            try:
                order.append(rid)
            except Exception:
                pass
        with self._lock:
            self._cache = new_cache
            try:
                self._cache_order = order
            except Exception:
                pass
            self._last_load_at = time.time()
            self._last_load_ok = True
            self._last_error = ""
        return True

    def _ensure_loaded(self) -> None:
        """确保缓存已被加载(若从未加载或太久没刷新,主动刷新一次)。"""
        with self._lock:
            last = self._last_load_at
        if last == 0.0:
            self._refresh_cache()
            return
        # 超过 10 秒则刷新一次
        if time.time() - last > 10.0:
            self._refresh_cache()

    def _get_event_by_id(self, reflection_id: str) -> Optional[Dict[str, Any]]:
        if not reflection_id or not isinstance(reflection_id, str):
            return None
        self._ensure_loaded()
        with self._lock:
            return self._cache.get(reflection_id)

    # --------------------------------------------------------
    # Summary(统计)
    # --------------------------------------------------------
    def get_summary(self) -> Dict[str, Any]:
        """
        汇总统计:总数 / 按类型分布 / 最新一次时间。

        Returns:
            {
                "available": bool,
                "total_count": int,
                "by_type": {daily: int, event: int, growth: int},
                "last_reflection_at": str|None,
                "last_reflection_id": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        self._ensure_loaded()
        with self._lock:
            cache = dict(self._cache)
            last_load_ok = self._last_load_ok
            last_error = self._last_error
        if not cache and not last_load_ok:
            return {
                "available": False,
                "total_count": 0,
                "by_type": {t: 0 for t in ALL_REFLECTION_TYPES},
                "last_reflection_at": None,
                "last_reflection_id": None,
                "fallback": True,
                "fallback_reason": last_error or "reflection_store_unavailable",
            }
        try:
            by_type: Dict[str, int] = {t: 0 for t in ALL_REFLECTION_TYPES}
            last_ts: float = 0.0
            last_id: str = ""
            for rid, ev in cache.items():
                rt = _resolve_reflection_type(ev)
                by_type[rt] = by_type.get(rt, 0) + 1
                ts = _resolve_timestamp(ev)
                if ts > last_ts:
                    last_ts = ts
                    last_id = rid
            return {
                "available": True,
                "total_count": len(cache),
                "by_type": by_type,
                "last_reflection_at": _to_iso(last_ts) if last_ts > 0 else None,
                "last_reflection_id": last_id or None,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total_count": 0,
                "by_type": {t: 0 for t in ALL_REFLECTION_TYPES},
                "last_reflection_at": None,
                "last_reflection_id": None,
                "fallback": True,
                "fallback_reason": f"summary_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # List
    # --------------------------------------------------------
    def list_reflections(
        self,
        limit: int = DEFAULT_LIMIT,
        type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        列出 reflection 列表(按时间倒序)。

        Args:
            limit: 返回数量上限(1..200)
            type:  可选过滤 "daily" / "event" / "growth"

        Returns:
            {
                "available": bool,
                "total": int,            # 过滤前的总数
                "total_unfiltered": int, # 缓存中的总数
                "items": [reflection_view],
                "filter_type": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = DEFAULT_LIMIT
        # 校验 type
        type_filter: Optional[str] = None
        if isinstance(type, str) and type in ALL_REFLECTION_TYPES:
            type_filter = type
        self._ensure_loaded()
        with self._lock:
            cache = dict(self._cache)
            last_load_ok = self._last_load_ok
            last_error = self._last_error
        if not cache and not last_load_ok:
            return {
                "available": False,
                "total": 0,
                "total_unfiltered": 0,
                "items": [],
                "filter_type": type_filter,
                "fallback": True,
                "fallback_reason": last_error or "reflection_store_unavailable",
            }
        try:
            # 先按 type 过滤
            filtered: List[Tuple[float, str, Dict[str, Any]]] = []
            for rid, ev in cache.items():
                rt = _resolve_reflection_type(ev)
                if type_filter and rt != type_filter:
                    continue
                ts = _resolve_timestamp(ev)
                filtered.append((ts, rid, ev))
            # 时间倒序
            filtered.sort(key=lambda x: (x[0], x[1]), reverse=True)
            total = len(filtered)
            top = filtered[:n]
            items = [_reflection_view_from_event(ev) for _, _, ev in top]
            return {
                "available": True,
                "total": total,
                "total_unfiltered": len(cache),
                "items": items,
                "filter_type": type_filter,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "total_unfiltered": 0,
                "items": [],
                "filter_type": type_filter,
                "fallback": True,
                "fallback_reason": f"list_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Detail
    # --------------------------------------------------------
    def get_reflection(self, reflection_id: str) -> Dict[str, Any]:
        """
        获取单条 reflection 详情。

        Returns:
            {
                "available": bool,
                "reflection": {...} | None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not reflection_id or not isinstance(reflection_id, str):
            return {
                "available": False,
                "reflection": None,
                "fallback": True,
                "fallback_reason": "invalid_reflection_id",
            }
        ev = self._get_event_by_id(reflection_id)
        if ev is None:
            # 主动再 refresh 一次(应对刚写入的新 reflection)
            self._refresh_cache()
            with self._lock:
                ev = self._cache.get(reflection_id)
        if ev is None:
            return {
                "available": False,
                "reflection": None,
                "fallback": True,
                "fallback_reason": "reflection_not_found",
            }
        try:
            view = _reflection_view_from_event(ev)
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            related = ev.get("related_ids") or []
            if not isinstance(related, list):
                related = []
            view.update({
                "content": str(payload.get("content", "") or ""),
                "source_events": list(payload.get("source_event_ids", []) or []),
                "related_memory": list(payload.get("related_memory", []) or []),
                "related_ids": list(related),
                "metadata": dict(payload.get("metadata", {}) or {}),
                "tick": payload.get("tick"),
            })
            # 简化:event_id 作为 event_id 字段
            view["event_id"] = str(ev.get("event_id", "") or "")
            return {
                "available": True,
                "reflection": view,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "reflection": None,
                "fallback": True,
                "fallback_reason": f"detail_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Insights
    # --------------------------------------------------------
    def get_insights(self, reflection_id: str) -> Dict[str, Any]:
        """
        获取 reflection 关联的 Insight 列表。

        约束:
        - 禁止重新计算 Insight
        - 禁止调用 LLM
        - 仅基于已有数据(payload / event 字段)展示

        由于 IntegrationEvent 的 payload 仅有 insight_count,
        完整 insight 内容需要 Reflection Engine 的结果。
        本 Provider 严格只读;若 payload 中包含 insight 列表则展示,
        否则返回 insight_count 摘要 + fallback 标记(告知 detail 不可用)。

        Returns:
            {
                "available": bool,
                "total": int,
                "items": [insight_view],
                "summary": {insight_count, suggestion_count, confidence, evidence_strength},
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not reflection_id or not isinstance(reflection_id, str):
            return {
                "available": False,
                "total": 0,
                "items": [],
                "summary": {},
                "fallback": True,
                "fallback_reason": "invalid_reflection_id",
            }
        ev = self._get_event_by_id(reflection_id)
        if ev is None:
            self._refresh_cache()
            with self._lock:
                ev = self._cache.get(reflection_id)
        if ev is None:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "summary": {},
                "fallback": True,
                "fallback_reason": "reflection_not_found",
            }
        try:
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            # 真实 insight 列表(若 payload 中带 insights 字段)
            raw_insights = payload.get("insights")
            items: List[Dict[str, Any]] = []
            if isinstance(raw_insights, list):
                for ins in raw_insights:
                    if not isinstance(ins, dict):
                        continue
                    items.append({
                        "insight_id": str(ins.get("insight_id", "") or ""),
                        "category": str(ins.get("category", "pattern") or "pattern"),
                        "description": str(ins.get("description", "") or ""),
                        "confidence": float(ins.get("confidence", 0.0) or 0.0),
                        "evidence_count": len(ins.get("supporting_event_ids", []) or []),
                        "supporting_event_ids": list(ins.get("supporting_event_ids", []) or []),
                    })
            summary = {
                "insight_count": int(payload.get("insight_count", 0) or 0),
                "suggestion_count": int(payload.get("suggestion_count", 0) or 0),
                "confidence": float(payload.get("confidence", 0.0) or 0.0),
                "evidence_strength": float(payload.get("evidence_strength", 0.0) or 0.0),
            }
            # 若有真实 items,则 available=True;否则降级为 summary 视图 + fallback
            if items:
                return {
                    "available": True,
                    "total": len(items),
                    "items": items,
                    "summary": summary,
                    "fallback": False,
                    "fallback_reason": None,
                }
            # 仅 summary(来自 event payload 的 insight_count)
            return {
                "available": True,
                "total": summary["insight_count"],
                "items": [],  # 不伪造,严格不编造
                "summary": summary,
                "fallback": True,
                "fallback_reason": "insight_detail_unavailable",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "items": [],
                "summary": {},
                "fallback": True,
                "fallback_reason": f"insight_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Evidence Chain
    # --------------------------------------------------------
    def get_evidence_chain(self, reflection_id: str) -> Dict[str, Any]:
        """
        获取 reflection 的证据链。

        链结构:
            Reflection
                ↓
            Events (source_event_ids)
                ↓
            Memories (related_memory)
                ↓
            Evidence (suggested_changes.evidence_event_ids)

        Returns:
            {
                "available": bool,
                "chain": {
                    "reflection": {...}|None,
                    "events": [event_summary],
                    "memories": [memory_summary],
                    "evidence": [evidence_summary]
                },
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not reflection_id or not isinstance(reflection_id, str):
            return {
                "available": False,
                "chain": {
                    "reflection": None,
                    "events": [],
                    "memories": [],
                    "evidence": [],
                },
                "fallback": True,
                "fallback_reason": "invalid_reflection_id",
            }
        ev = self._get_event_by_id(reflection_id)
        if ev is None:
            self._refresh_cache()
            with self._lock:
                ev = self._cache.get(reflection_id)
        if ev is None:
            return {
                "available": False,
                "chain": {
                    "reflection": None,
                    "events": [],
                    "memories": [],
                    "evidence": [],
                },
                "fallback": True,
                "fallback_reason": "reflection_not_found",
            }
        try:
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            source_event_ids = list(payload.get("source_event_ids", []) or [])
            related_memory = list(payload.get("related_memory", []) or [])

            # events:从 source_event_ids 构造节点(不伪造内容)
            event_nodes: List[Dict[str, Any]] = []
            for eid in source_event_ids:
                if not eid:
                    continue
                event_nodes.append({
                    "event_id": str(eid),
                    "kind": "event",
                    "available": False,
                    "fallback": True,
                    "fallback_reason": "event_detail_not_collected",
                })

            # memories:从 related_memory 构造节点
            memory_nodes: List[Dict[str, Any]] = []
            for mem in related_memory:
                if isinstance(mem, dict):
                    memory_nodes.append({
                        "memory_id": str(mem.get("id", "") or ""),
                        "kind": "memory",
                        "summary": str(mem.get("summary", "") or "")[:200],
                        "importance": float(mem.get("importance", 0.0) or 0.0),
                        "available": True,
                    })
                elif isinstance(mem, str) and mem:
                    memory_nodes.append({
                        "memory_id": mem,
                        "kind": "memory",
                        "available": False,
                        "fallback": True,
                        "fallback_reason": "memory_id_only",
                    })

            # evidence:从 suggested_changes.evidence_event_ids 聚合去重
            evidence_ids: List[str] = []
            seen_evidence = set()
            suggested = payload.get("suggested_changes")
            if isinstance(suggested, list):
                for sc in suggested:
                    if not isinstance(sc, dict):
                        continue
                    eids = sc.get("evidence_event_ids")
                    if isinstance(eids, list):
                        for eid in eids:
                            if eid and eid not in seen_evidence:
                                seen_evidence.add(str(eid))
                                evidence_ids.append(str(eid))
            # 若 source_event_ids 已经有,也合并进来(去重)
            for eid in source_event_ids:
                if eid and eid not in seen_evidence:
                    seen_evidence.add(str(eid))
                    evidence_ids.append(str(eid))

            evidence_nodes = [
                {"evidence_id": eid, "kind": "evidence", "event_id": eid, "available": False,
                 "fallback": True, "fallback_reason": "evidence_detail_not_collected"}
                for eid in evidence_ids
            ]

            # 构造 reflection 节点(简化版)
            ref_view = _reflection_view_from_event(ev)

            return {
                "available": True,
                "chain": {
                    "reflection": ref_view,
                    "events": event_nodes,
                    "memories": memory_nodes,
                    "evidence": evidence_nodes,
                },
                "links": [
                    {"from": "reflection", "to": "events"},
                    {"from": "events", "to": "memories"},
                    {"from": "memories", "to": "evidence"},
                ],
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "chain": {
                    "reflection": None,
                    "events": [],
                    "memories": [],
                    "evidence": [],
                },
                "fallback": True,
                "fallback_reason": f"chain_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # 测试/调试用
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """描述当前 provider 状态(调试用)。"""
        with self._lock:
            return {
                "cache_size": len(self._cache),
                "cache_capacity": self._cache_capacity,
                "last_load_at": self._last_load_at,
                "last_load_ok": self._last_load_ok,
                "last_error": self._last_error,
            }


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[ReflectionDashboardProvider] = None
_provider_lock = threading.Lock()


def get_reflection_dashboard_provider() -> ReflectionDashboardProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = ReflectionDashboardProvider()
    return _provider_instance


def reset_reflection_dashboard_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "ReflectionDashboardProvider",
    "EventHubReflectionSource",
    "get_reflection_dashboard_provider",
    "reset_reflection_dashboard_provider_for_testing",
    "ALL_REFLECTION_TYPES",
    "INTEGRATION_REFLECTION_EVENT_TYPES",
]
