# -*- coding: utf-8 -*-
"""
src/admin/event_stream_provider.py

Phase 5.0 Dashboard Upgrade Step 8.4.4 —— EventStreamProvider。

职责:
- 提供统一生命事件流(Unified Life Event Stream)
- 数据源:EventHub (IntegrationEvent) —— 仅读取
- 支持按事件类型 / 关键词 / 时间 过滤
- 自动提取 summary(纯规则,不调用 LLM)
- 暴露事件订阅能力(WebSocket 推送 / 实时刷新)
- 严格只读 / 不修改任何业务状态 / 不创建业务事件 / 不调用 LLM

数据流:
    Frontend
        ↓
    EventStreamRouter
        ↓
    EventStreamProvider
        ↓
    EventHubDomainCollector → EventHub → IntegrationEvent(只读)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 统一事件流覆盖的事件类型前缀
# 涵盖:Dashboard 想观察的全部 7 大领域
SUPPORTED_EVENT_PREFIXES: Tuple[str, ...] = (
    "integration.memory.",
    "integration.reflection.",
    "integration.emotion.",
    "integration.growth.",
    "integration.goal.",
    "integration.initiative.",
    "integration.self_model.",
    "integration.lifecycle.",
    "integration.interest_signal.",
    "integration.desire.",
)

# 完整事件类型(用于静态类型列表 & 健康检查)
SUPPORTED_EVENT_TYPES: Tuple[str, ...] = (
    # memory
    "integration.memory.created",
    "integration.memory.important",
    "integration.memory.recalled",
    # reflection
    "integration.reflection.completed",
    "integration.reflection.daily_completed",
    "integration.reflection.event_completed",
    "integration.reflection.growth_completed",
    # emotion
    "integration.emotion.detected",
    "integration.emotion.changed",
    # growth
    "integration.growth.recorded",
    "integration.growth.threshold",
    # goal
    "integration.goal.created",
    "integration.goal.updated",
    "integration.goal.completed",
    "integration.goal.plan_created",
    "integration.desire.created",
    # initiative
    "integration.interest_signal.created",
    "integration.initiative.created",
    "integration.initiative.filtered",
    "integration.initiative.executed",
    # self_model
    "integration.self_model.belief_updated",
    "integration.self_model.trait_changed",
    # lifecycle
    "integration.lifecycle.tick_complete",
    "integration.lifecycle.task_complete",
)

# limit 边界
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
MIN_LIMIT = 1

# summary 提取最长截断
SUMMARY_MAX_LEN = 256

# 事件缓存(供 WS 推送)
DEFAULT_STREAM_CACHE_CAPACITY = 200


# ============================================================
# 工具
# ============================================================

def _to_float_ts(value: Any) -> float:
    """从任意时间字段解析 epoch 浮点秒。"""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        try:
            v = float(value)
            return v if v == v else 0.0
        except (TypeError, ValueError):
            return 0.0
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return 0.0
        try:
            return float(s)
        except (TypeError, ValueError):
            pass
        try:
            from datetime import datetime
            iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
            dt = datetime.fromisoformat(iso)
            try:
                return dt.timestamp()
            except Exception:
                try:
                    import calendar
                    return float(calendar.timegm(dt.timetuple()))
                except Exception:
                    return 0.0
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _to_iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    try:
        v = float(ts)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(v))
    except Exception:
        return None


def _truncate(s: Any, n: int = SUMMARY_MAX_LEN) -> str:
    if s is None:
        return ""
    try:
        v = str(s)
    except Exception:
        return ""
    if len(v) > n:
        return v[:n]
    return v


def _normalize_types(types: Any) -> List[str]:
    """
    规范化 types 参数:
    - 字符串:逗号分隔
    - 列表:每项 strip
    - 包含通配符前缀:如 "memory.*" → 展开为 SUPPORTED_EVENT_TYPES 中匹配的前缀
    """
    if not types:
        return []
    if isinstance(types, str):
        raw = [t.strip() for t in types.split(",") if t.strip()]
    elif isinstance(types, (list, tuple, set)):
        raw = []
        for t in types:
            if isinstance(t, str):
                s = t.strip()
                if s:
                    raw.append(s)
    else:
        return []
    if not raw:
        return []
    out: List[str] = []
    for t in raw:
        if "*" in t:
            # 通配符:展开
            prefix = t.split("*", 1)[0]
            for full in SUPPORTED_EVENT_TYPES:
                if full.startswith(prefix) and full not in out:
                    out.append(full)
        else:
            if t not in out:
                out.append(t)
    return out


def _matches_type(event_type: str, type_filter: List[str]) -> bool:
    """判断 event_type 是否匹配 type_filter(任一命中即通过)。"""
    if not type_filter:
        return True
    if event_type in type_filter:
        return True
    # 也允许通配符前缀
    for f in type_filter:
        if f.endswith(".*"):
            if event_type.startswith(f[:-2] + "."):
                return True
        elif f.endswith("*"):
            if event_type.startswith(f[:-1]):
                return True
    return False


def _build_summary(event: Dict[str, Any]) -> str:
    """
    按规则提取 summary(禁止调用 LLM)。

    优先级:
      1. payload.summary
      2. payload.title
      3. payload.content / payload.text(取前 N 字)
      4. event_type + timestamp(简单组合)
    """
    if not isinstance(event, dict):
        return ""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    # 1. summary
    s = payload.get("summary")
    if isinstance(s, str) and s.strip():
        return _truncate(s.strip(), SUMMARY_MAX_LEN)
    # 2. title
    t = payload.get("title")
    if isinstance(t, str) and t.strip():
        return _truncate(t.strip(), SUMMARY_MAX_LEN)
    # 3. content / text
    for k in ("content", "text", "message"):
        c = payload.get(k)
        if isinstance(c, str) and c.strip():
            return _truncate(c.strip(), SUMMARY_MAX_LEN)
    # 4. event_type + timestamp
    et = str(event.get("event_type", "") or "")
    if et:
        ts = event.get("timestamp")
        if ts is None:
            return f"Event: {et}"
        return f"{et} @ {ts}"
    return ""


def _extract_source_refs(event: Dict[str, Any]) -> List[str]:
    """
    从 payload 中提取 source_refs(用于 trace.source_refs)。

    来源:
      - payload.source_event_ids
      - payload.source_reflection_ids
      - payload.source_memory_ids
      - payload.related_ids
      - payload.supporting_signal_ids
    """
    if not isinstance(event, dict):
        return []
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = []
    out: List[str] = []
    seen: Set[str] = set()
    keys = (
        "source_event_ids",
        "source_reflection_ids",
        "source_memory_ids",
        "related_ids",
        "supporting_signal_ids",
        "source_interest_ids",
        "source_belief_ids",
        "source_desire_ids",
    )
    for k in keys:
        v = payload.get(k) if isinstance(payload, dict) else None
        if isinstance(v, (list, tuple)):
            for x in v:
                if isinstance(x, str) and x and x not in seen:
                    out.append(x)
                    seen.add(x)
    return out


def _keyword_match(event: Dict[str, Any], keyword: str) -> bool:
    """在 event_type / summary / payload.topic / payload.title 中搜索 keyword。"""
    if not keyword:
        return True
    kw = str(keyword).strip().lower()
    if not kw:
        return True
    # 1. event_type
    et = str(event.get("event_type", "") or "").lower()
    if kw in et:
        return True
    # 2. summary(已在 event 中)
    s = str(event.get("summary", "") or "").lower()
    if kw in s:
        return True
    # 3. payload 字段
    payload = event.get("payload")
    if isinstance(payload, dict):
        for k in ("topic", "title", "content", "text", "summary", "name", "rationale"):
            v = payload.get(k)
            if isinstance(v, str) and kw in v.lower():
                return True
    return False


# ============================================================
# Provider
# ============================================================

class EventStreamProvider:
    """
    统一生命事件流 Provider(只读)。

    注入:
        collector: 任何暴露 list_events(limit) -> List[dict] 的对象
        event_hub: 任何暴露 subscribe(callback) / poll_recent(limit) 的对象
    """

    def __init__(
        self,
        collector: Optional[Any] = None,
        event_hub: Optional[Any] = None,
    ) -> None:
        self._collector = collector
        self._event_hub = event_hub
        self._lock = threading.RLock()
        # 事件缓存(只读,用于 WS 推送/查询)
        self._cache: Deque[Dict[str, Any]] = deque(maxlen=DEFAULT_STREAM_CACHE_CAPACITY)
        self._cache_ids: Set[str] = set()
        self._subscribers: List[Callable[[List[Dict[str, Any]]], None]] = []
        self._last_refresh_ts: float = 0.0
        self._ws_subscribed: bool = False
        # 已知事件类型缓存
        self._known_types_cache: List[str] = list(SUPPORTED_EVENT_TYPES)

    # --------------------------------------------------------
    # 内部:获取 collector / event_hub
    # --------------------------------------------------------
    def _get_collector(self) -> Optional[Any]:
        if self._collector is not None:
            return self._collector
        try:
            from src.admin.life_graph_provider import EventHubDomainCollector
            self._collector = EventHubDomainCollector(event_hub=self._event_hub)
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventStreamProvider: collector 不可用: %s", exc)
            self._collector = None
        return self._collector

    def _get_event_hub(self) -> Optional[Any]:
        if self._event_hub is not None:
            return self._event_hub
        try:
            from src.admin.dashboard.event_hub import get_dashboard_event_hub
            self._event_hub = get_dashboard_event_hub()
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventStreamProvider: EventHub 不可用: %s", exc)
            self._event_hub = None
        return self._event_hub

    # --------------------------------------------------------
    # 内部:从 collector / hub 拉取事件
    # --------------------------------------------------------
    def _fetch_raw_events(self, limit: int) -> List[Dict[str, Any]]:
        """从 collector 或 event_hub 拉取原始事件(未经处理)。"""
        n = max(MIN_LIMIT, min(MAX_LIMIT, int(limit) or DEFAULT_LIMIT))
        # 1. 优先:collector.list_events
        collector = self._get_collector()
        if collector is not None:
            try:
                fn = getattr(collector, "list_events", None)
                if callable(fn):
                    raw = fn(limit=n)
                    if isinstance(raw, list):
                        return raw
            except Exception as exc:  # noqa: BLE001
                logger.debug("EventStreamProvider: collector.list_events 失败: %s", exc)
        # 2. fallback:event_hub.poll_recent
        hub = self._get_event_hub()
        if hub is not None:
            try:
                fn = getattr(hub, "poll_recent", None)
                if callable(fn):
                    raw = fn(limit=n)
                    if isinstance(raw, list):
                        return raw
            except Exception as exc:  # noqa: BLE001
                logger.debug("EventStreamProvider: hub.poll_recent 失败: %s", exc)
        return []

    # --------------------------------------------------------
    # 内部:标准化 / 装饰
    # --------------------------------------------------------
    def _normalize(self, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """把原始事件 dict 标准化为统一格式。返回 None 表示丢弃。"""
        if not isinstance(raw, dict):
            return None
        eid = str(raw.get("event_id", "") or "")
        if not eid:
            return None
        et = str(raw.get("event_type", "") or "")
        if not et:
            return None
        ts_raw = raw.get("timestamp")
        ts_float = _to_float_ts(ts_raw)
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        # 构造标准化事件
        ev: Dict[str, Any] = {
            "event_id": eid,
            "event_type": et,
            "source": str(raw.get("source", "") or ""),
            "timestamp": ts_raw,
            "timestamp_ts": ts_float,
            "timestamp_iso": _to_iso(ts_float) or (str(ts_raw) if ts_raw is not None else None),
            "summary": _build_summary({"event_type": et, "timestamp": ts_raw, "payload": payload}),
            "payload": dict(payload),  # 深拷贝,防止外部修改
        }
        return ev

    def _enrich_trace(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        """在标准化事件上附加 trace / confidence 字段(只读增强,不修改 payload)。"""
        if not isinstance(ev, dict):
            return ev
        source_refs = _extract_source_refs(ev)
        # evidence_count
        ev_count = 0
        try:
            payload = ev.get("payload") or {}
            if isinstance(payload, dict):
                # 优先 source_event_ids
                sei = payload.get("source_event_ids")
                if isinstance(sei, list):
                    ev_count = max(ev_count, len(sei))
                # 相关字段总数
                for k in ("source_reflection_ids", "source_memory_ids",
                          "related_ids", "supporting_signal_ids",
                          "source_interest_ids", "source_belief_ids"):
                    v = payload.get(k)
                    if isinstance(v, list):
                        ev_count = max(ev_count, len(v))
        except Exception:
            ev_count = len(source_refs)
        if ev_count == 0:
            ev_count = len(source_refs)
        # confidence
        try:
            payload = ev.get("payload") or {}
            confidence = 0.0
            if isinstance(payload, dict):
                c = payload.get("confidence")
                if isinstance(c, (int, float)):
                    confidence = max(0.0, min(1.0, float(c)))
                else:
                    # 基于 evidence 估算
                    confidence = min(0.95, 0.5 + 0.1 * ev_count)
            else:
                confidence = 0.5
        except Exception:
            confidence = 0.5
        # 挂载 trace / confidence / traceable(不污染 payload)
        ev["trace"] = {
            "source_refs": source_refs,
            "evidence_count": int(ev_count),
        }
        ev["confidence"] = round(float(confidence), 3)
        ev["traceable"] = len(source_refs) > 0 or ev_count > 0
        return ev

    # --------------------------------------------------------
    # 公开 API 1:list_events
    # --------------------------------------------------------
    def list_events(
        self,
        *,
        types: Any = None,
        keyword: Optional[str] = None,
        since_ts: Optional[Any] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        """
        获取事件流。

        Args:
            types:   事件类型过滤(str 逗号分隔 / list / 含 * 通配符)
            keyword: 在 event_type / summary / payload.topic / payload.title 搜索
            since_ts: 时间过滤(>= 该 ts,接受 epoch 浮点 / ISO 字符串)
            limit:   返回数量上限(1~200)

        Returns:
            {
                "items": List[event_dict],
                "available_types": List[str],
                "total": int,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        # 1. 规范化 limit
        try:
            n_limit = int(limit)
        except (TypeError, ValueError):
            return {
                "items": [],
                "available_types": self._known_types_cache,
                "total": 0,
                "fallback": True,
                "fallback_reason": "invalid_limit",
            }
        if n_limit < MIN_LIMIT or n_limit > MAX_LIMIT:
            return {
                "items": [],
                "available_types": self._known_types_cache,
                "total": 0,
                "fallback": True,
                "fallback_reason": "invalid_limit",
            }
        # 2. 拉取原始事件(拉比 limit 多一些,用于过滤后裁剪)
        raw = self._fetch_raw_events(limit=max(n_limit * 2, MAX_LIMIT))
        if not raw:
            return {
                "items": [],
                "available_types": self._known_types_cache,
                "total": 0,
                "fallback": True,
                "fallback_reason": "no_events",
            }
        # 3. 规范化
        normalized: List[Dict[str, Any]] = []
        for r in raw:
            ev = self._normalize(r)
            if ev is None:
                continue
            normalized.append(ev)
        # 4. 过滤
        type_filter = _normalize_types(types)
        since_float = _to_float_ts(since_ts) if since_ts is not None else 0.0
        filtered: List[Dict[str, Any]] = []
        for ev in normalized:
            if not _matches_type(ev.get("event_type", ""), type_filter):
                continue
            if not _keyword_match(ev, keyword or ""):
                continue
            if since_float > 0.0:
                ts = float(ev.get("timestamp_ts") or 0.0)
                if 0.0 < ts < since_float:
                    continue
            filtered.append(ev)
        # 5. 排序(按 timestamp_ts 降序,最新的在前)
        filtered.sort(
            key=lambda e: (
                float(e.get("timestamp_ts") or 0.0),
                str(e.get("event_id", "") or ""),
            ),
            reverse=True,
        )
        total = len(filtered)
        items = filtered[:n_limit]
        # 6. 附加 trace / confidence
        items = [self._enrich_trace(ev) for ev in items]
        # 7. 写缓存(只追加新事件)
        self._update_cache(items)
        # 8. 更新 known_types
        for ev in items:
            et = str(ev.get("event_type", "") or "")
            if et and et not in self._known_types_cache:
                self._known_types_cache.append(et)
        return {
            "items": items,
            "available_types": list(self._known_types_cache),
            "total": total,
            "fallback": False,
            "fallback_reason": None,
        }

    def _update_cache(self, items: List[Dict[str, Any]]) -> None:
        """把新事件写入内部缓存(用于 WS 推送)。"""
        if not items:
            return
        with self._lock:
            for ev in items:
                eid = str(ev.get("event_id", "") or "")
                if not eid or eid in self._cache_ids:
                    continue
                self._cache.append(ev)
                self._cache_ids.add(eid)
            self._last_refresh_ts = time.time()

    # --------------------------------------------------------
    # 公开 API 2:list_available_types
    # --------------------------------------------------------
    def list_available_types(self) -> List[str]:
        """返回当前已知的事件类型列表(静态 + 动态发现)。"""
        # 与当前缓存合并
        seen: Set[str] = set(self._known_types_cache)
        for ev in list(self._cache):
            et = str(ev.get("event_type", "") or "")
            if et and et not in seen:
                self._known_types_cache.append(et)
                seen.add(et)
        # 排序
        return sorted(self._known_types_cache)

    # --------------------------------------------------------
    # 公开 API 3:health
    # --------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        """EventStream 健康检查。"""
        collector_ok = self._get_collector() is not None
        hub_ok = self._get_event_hub() is not None
        with self._lock:
            cache_size = len(self._cache)
            last_refresh = self._last_refresh_ts
        return {
            "ok": collector_ok or hub_ok,
            "collector_available": collector_ok,
            "event_hub_available": hub_ok,
            "cache_size": cache_size,
            "last_refresh_ts": last_refresh,
            "last_refresh_iso": _to_iso(last_refresh) if last_refresh > 0 else None,
            "subscribers": len(self._subscribers),
            "supported_prefixes": list(SUPPORTED_EVENT_PREFIXES),
        }

    # --------------------------------------------------------
    # 公开 API 4:WS 订阅
    # --------------------------------------------------------
    def subscribe(self, callback: Callable[[List[Dict[str, Any]]], None]) -> bool:
        """订阅新事件(批量推送)。返回是否成功。"""
        if not callable(callback):
            return False
        with self._lock:
            if callback in self._subscribers:
                return True
            self._subscribers.append(callback)
        return True

    def unsubscribe(self, callback: Callable[[List[Dict[str, Any]]], None]) -> bool:
        """取消订阅。"""
        if not callable(callback):
            return False
        with self._lock:
            try:
                self._subscribers.remove(callback)
                return True
            except ValueError:
                return False

    def get_recent_cache(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近缓存的事件(供 WebSocket 连接初始化时全量推送)。"""
        try:
            n = max(0, int(limit))
        except (TypeError, ValueError):
            n = 50
        with self._lock:
            data = list(self._cache)
        return data[-n:] if n > 0 else []

    def push_event(self, ev: Dict[str, Any]) -> bool:
        """
        推入单个新事件(供 EventHub 回调使用)。
        自动 enrich + 通知订阅者。

        Returns:
            是否成功添加
        """
        if not isinstance(ev, dict):
            return False
        norm = self._normalize(ev)
        if norm is None:
            return False
        enriched = self._enrich_trace(norm)
        with self._lock:
            eid = str(enriched.get("event_id", "") or "")
            if not eid or eid in self._cache_ids:
                return False
            self._cache.append(enriched)
            self._cache_ids.add(eid)
            et = str(enriched.get("event_type", "") or "")
            if et and et not in self._known_types_cache:
                self._known_types_cache.append(et)
            subs = list(self._subscribers)
        # 锁外通知
        if subs:
            self._notify_subscribers([enriched])
        return True

    def _notify_subscribers(self, events: List[Dict[str, Any]]) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(events)
            except Exception as exc:  # noqa: BLE001
                logger.debug("EventStreamProvider 订阅者回调失败: %s", exc)


# ============================================================
# 模块级单例
# ============================================================

_stream_instance: Optional[EventStreamProvider] = None
_stream_lock = threading.Lock()


def get_event_stream_provider() -> EventStreamProvider:
    """获取 EventStreamProvider 单例(懒加载)。"""
    global _stream_instance
    if _stream_instance is None:
        with _stream_lock:
            if _stream_instance is None:
                _stream_instance = EventStreamProvider()
                # 启动时自动订阅 EventHub
                try:
                    _auto_subscribe_hub(_stream_instance)
                except Exception:  # noqa: BLE001
                    pass
    return _stream_instance


def _auto_subscribe_hub(provider: EventStreamProvider) -> None:
    """
    自动把 provider 订阅到 EventHub,使新事件自动流入 provider 缓存。
    不会修改任何业务状态;只是被动接收。
    """
    try:
        from src.admin.dashboard.event_hub import get_dashboard_event_hub
    except Exception:
        return
    try:
        hub = get_dashboard_event_hub()
    except Exception:
        return
    if hub is None:
        return
    # 订阅 hub 推过来的新事件
    try:
        # 包装一下:只关心 event_id / event_type / timestamp
        def _on_new(events: List[Dict[str, Any]]) -> None:
            for ev in events or []:
                try:
                    provider.push_event(ev)
                except Exception:  # noqa: BLE001
                    pass
        hub.subscribe(_on_new)
    except Exception:  # noqa: BLE001
        pass


def reset_event_stream_provider_for_testing() -> None:
    """测试用:重置单例。"""
    global _stream_instance
    with _stream_lock:
        _stream_instance = None


__all__ = [
    "EventStreamProvider",
    "get_event_stream_provider",
    "reset_event_stream_provider_for_testing",
    "SUPPORTED_EVENT_PREFIXES",
    "SUPPORTED_EVENT_TYPES",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "MIN_LIMIT",
    "DEFAULT_STREAM_CACHE_CAPACITY",
]
