# -*- coding: utf-8 -*-
"""
src/admin/initiative_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 8.2 —— Initiative Dashboard Provider。

职责:
- 只读提供 Initiative 数据给 Dashboard
  - InterestSignal(兴趣信号)
  - PossibleAction(候选行动,含 pending / filtered / deferred / discarded)
  - ActionFilter 决策(过滤原因与 policy)
  - Initiative 历史(事件级时间线)
- 数据源:已有 Initiative 数据通路(通过 Admin 侧 EventHub 间接访问 IntegrationEvent)
  严禁直接 import 任何业务实现类
- 严格容错,任何子组件不可用时返回 fallback
- 不调用 LLM 生成 InterestSignal / PossibleAction
- 不修改 Initiative 状态
- 不触发 Initiative 变化
- 严格只读

API:
- get_summary() -> {interest_count, possible_action_count, filtered_count, ...}
- list_interests(limit=20, trend=None) -> {items, total, available, fallback}
- list_actions(limit=20, status=None) -> {items, total, available, fallback}
- list_filtered(limit=20) -> {items, total, available, fallback}
- get_history(limit=50) -> {items, total, available, fallback}

数据流:
    Dashboard UI
        ↓
    InitiativeRouter
        ↓
    InitiativeDashboardProvider
        ↓
    EventHub (Admin 侧只读访问层) → IntegrationEvent
        ↓
    Initiative Authority(由 initiative/goal/personality 业务模块内部维护,
    Dashboard 仅通过 EventHub 间接观察,不做任何穿透访问)

约束:
- 禁止 import 任何 src.runtime.initiative / src.runtime.goal / src.personality 业务模块
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

# InterestSignal 趋势(与业务模块定义保持一致)
INTEREST_TREND_NEW = "new"
INTEREST_TREND_RISING = "rising"
INTEREST_TREND_STABLE = "stable"
INTEREST_TREND_FADING = "fading"
ALL_INTEREST_TRENDS = (
    INTEREST_TREND_NEW,
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
    INTEREST_TREND_FADING,
)

# PossibleAction 状态
ACTION_STATUS_PENDING = "pending"
ACTION_STATUS_FILTERED = "filtered"
ACTION_STATUS_DEFERRED = "deferred"
ACTION_STATUS_DISCARDED = "discarded"
ALL_ACTION_STATUSES = (
    ACTION_STATUS_PENDING,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_DEFERRED,
    ACTION_STATUS_DISCARDED,
)

# PossibleAction 紧急度
ACTION_URGENCY_LOW = "low"
ACTION_URGENCY_NORMAL = "normal"
ACTION_URGENCY_HIGH = "high"
ALL_ACTION_URGENCIES = (ACTION_URGENCY_LOW, ACTION_URGENCY_NORMAL, ACTION_URGENCY_HIGH)

# PossibleAction 努力度
ACTION_EFFORT_LOW = "low"
ACTION_EFFORT_MEDIUM = "medium"
ACTION_EFFORT_HIGH = "high"
ALL_ACTION_EFFORTS = (ACTION_EFFORT_LOW, ACTION_EFFORT_MEDIUM, ACTION_EFFORT_HIGH)

# PossibleAction 类型
ACTION_TYPE_OBSERVE = "observe"
ACTION_TYPE_LEARN = "learn"
ACTION_TYPE_ASK = "ask"
ACTION_TYPE_RECOMMEND = "recommend"
ACTION_TYPE_REMIND = "remind"
ALL_ACTION_TYPES = (
    ACTION_TYPE_OBSERVE,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_ASK,
    ACTION_TYPE_RECOMMEND,
    ACTION_TYPE_REMIND,
)

# ActionFilter 政策名(从 FilterDecision.rule_applied 推断)
ALL_FILTER_POLICIES = (
    "low_confidence",
    "low_expected_value",
    "repeat_penalty",
    "energy_threshold",
    "none",
    "unknown",
)

# Integration Event 关联 Initiative 的事件类型
INTEGRATION_INITIATIVE_EVENT_TYPES = (
    "integration.interest_signal.created",
    "integration.initiative.created",
    "integration.initiative.filtered",
)

# 默认缓存容量
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
        logger.debug("initiative_dashboard_provider safe_call 失败: %s", exc)
        return None


def _is_initiative_event(event_type: Any) -> bool:
    """判断事件类型是否与 initiative 有关。"""
    if not event_type or not isinstance(event_type, str):
        return False
    return event_type in INTEGRATION_INITIATIVE_EVENT_TYPES


def _resolve_interest_trend(event: Dict[str, Any]) -> str:
    """从 interest_signal event payload 中提取 trend。"""
    payload = event.get("payload") if isinstance(event, dict) else None
    if isinstance(payload, dict):
        t = payload.get("trend")
        if isinstance(t, str) and t in ALL_INTEREST_TRENDS:
            return t
    return INTEREST_TREND_NEW


def _resolve_action_status(event: Dict[str, Any]) -> str:
    """从 action event payload 中提取 status。"""
    if not isinstance(event, dict):
        return ""
    et = str(event.get("event_type", "") or "")
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if et == "integration.initiative.created":
        return ACTION_STATUS_PENDING
    if et == "integration.initiative.filtered":
        # 在 filtered 事件中,业务模块会写入具体 status(filtered/deferred/discarded)
        st = payload.get("status")
        if isinstance(st, str) and st in (
            ACTION_STATUS_FILTERED,
            ACTION_STATUS_DEFERRED,
            ACTION_STATUS_DISCARDED,
        ):
            return st
        return ACTION_STATUS_FILTERED
    st = payload.get("status")
    if isinstance(st, str) and st in ALL_ACTION_STATUSES:
        return st
    return ""


def _resolve_filter_policy(payload: Dict[str, Any]) -> str:
    """
    从 filtered 事件 payload 中推断 policy。
    业务模块的 FilterDecision.rule_applied 会被原样写入 payload.rule_applied。
    """
    if not isinstance(payload, dict):
        return "unknown"
    r = payload.get("rule_applied")
    if isinstance(r, str) and r in ALL_FILTER_POLICIES:
        return r
    return "unknown"


def _resolve_timestamp(event: Dict[str, Any]) -> float:
    """
    从 event 中提取时间戳(浮点秒)。
    优先级:payload.created_at > payload.triggered_at > event.timestamp
    """
    if not isinstance(event, dict):
        return 0.0
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("created_at", "triggered_at"):
            ts = payload.get(key)
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


def _interest_view_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 IntegrationEvent 规范化为 InterestSignal 视图。
    """
    if not isinstance(event, dict):
        return {}
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ts = _resolve_timestamp(event)
    return {
        "signal_id": str(payload.get("signal_id", "") or event.get("event_id", "") or ""),
        "topic": str(payload.get("topic", "") or "")[:128],
        "trend": _resolve_interest_trend(event),
        "strength": float(payload.get("strength", 0.5) or 0.5),
        "confidence": float(payload.get("confidence", 0.5) or 0.5),
        "source": str(event.get("source", "") or ""),
        "source_event_ids": list(payload.get("source_event_ids", []) or []),
        "source_reflection_ids": list(payload.get("source_reflection_ids", []) or []),
        "rationale": str(payload.get("rationale", "") or "")[:512],
        "created_at": _to_iso(ts) or "",
        "created_ts": ts,
        "event_id": str(event.get("event_id", "") or ""),
        "fallback": False,
    }


def _action_view_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 IntegrationEvent 规范化为 PossibleAction 视图。
    """
    if not isinstance(event, dict):
        return {}
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ts = _resolve_timestamp(event)
    status = _resolve_action_status(event)
    return {
        "action_id": str(payload.get("action_id", "") or event.get("event_id", "") or ""),
        "action_type": str(payload.get("action_type", "") or ""),
        "topic": str(payload.get("topic", "") or "")[:128],
        "status": status,
        "urgency": str(payload.get("urgency", "") or ""),
        "effort_estimate": str(payload.get("effort_estimate", "") or ""),
        "expected_value": float(payload.get("expected_value", 0.0) or 0.0),
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
        "priority": float(payload.get("priority", 0.5) or 0.5),
        "supporting_signal_ids": list(payload.get("supporting_signal_ids", []) or []),
        "source_interest": (list(payload.get("supporting_signal_ids", []) or []) or [None])[0] or "",
        "rationale": str(payload.get("rationale", "") or "")[:512],
        "filter_reason": str(payload.get("filter_reason", "") or "")[:512],
        "filter_policy": _resolve_filter_policy(payload),
        "source": str(event.get("source", "") or ""),
        "event_id": str(event.get("event_id", "") or ""),
        "created_at": _to_iso(ts) or "",
        "created_ts": ts,
        "fallback": False,
    }


# ============================================================
# 默认 Initiative Source(EventHub-based,只读)
# ============================================================

class EventHubInitiativeSource:
    """
    通过 Admin 侧 EventHub 间接访问 IntegrationEvent 中的 initiative 数据。
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
            logger.debug("EventHubInitiativeSource: EventHub 不可用: %s", exc)
            self._event_hub = None
        return self._event_hub

    def list_initiative_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        """
        从 EventHub 拉取所有 initiative 相关的 IntegrationEvent。

        Returns:
            initiative event 列表(按时间倒序),失败时返回空列表。
        """
        hub = self._get_hub()
        if hub is None:
            return []
        # 触发 refresh
        try:
            poll_fn = getattr(hub, "poll_recent", None)
            if callable(poll_fn):
                _ = poll_fn(limit=max(1, min(MAX_LIMIT, int(limit) or 0)))
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubInitiativeSource: poll_recent 失败: %s", exc)
            return []

        # 通过 hub 内部访问 host.event_log
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
                    out: List[Dict[str, Any]] = []
                    seen_ids = set()
                    for et in INTEGRATION_INITIATIVE_EVENT_TYPES:
                        try:
                            sub = events_fn(event_type=et, limit=limit) or []
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                "EventHubInitiativeSource: events(%s) 失败: %s", et, exc
                            )
                            continue
                        for ev in sub:
                            eid = str(_safe_get(ev, "event_id", "") or "")
                            if eid and eid not in seen_ids:
                                seen_ids.add(eid)
                                out.append(self._normalize(ev))
                    # 按 timestamp 倒序
                    out.sort(
                        key=lambda e: (
                            _resolve_timestamp(e),
                            str(e.get("event_id", "") or ""),
                        ),
                        reverse=True,
                    )
                    return out
                # 退化:用 latest(limit) 全量拉,自己过滤
                latest_fn = _safe_get(event_log, "latest")
                if callable(latest_fn):
                    try:
                        all_events = (
                            latest_fn(limit=max(1, min(MAX_LIMIT, int(limit) or 0))) or []
                        )
                    except Exception:
                        return []
                    out2: List[Dict[str, Any]] = []
                    for ev in all_events:
                        et = str(_safe_get(ev, "event_type", "") or "")
                        if _is_initiative_event(et):
                            out2.append(self._normalize(ev))
                    out2.sort(
                        key=lambda e: (
                            _resolve_timestamp(e),
                            str(e.get("event_id", "") or ""),
                        ),
                        reverse=True,
                    )
                    return out2
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubInitiativeSource: 读取 host.event_log 失败: %s", exc)
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
class InitiativeDashboardProvider:
    """
    Initiative Dashboard 只读 Provider。

    注入:
        initiative_source: 任何暴露 list_initiative_events(limit) 接口的对象
                          (测试时可注入 mock;生产由 EventHubInitiativeSource 提供)
    """

    def __init__(
        self,
        initiative_source: Optional[Any] = None,
        *,
        cache_capacity: int = DEFAULT_PROVIDER_CACHE_CAPACITY,
    ) -> None:
        self._lock = threading.RLock()
        self._initiative_source = initiative_source
        self._cache_capacity = max(0, int(cache_capacity))
        # 内部缓存(完整 event 列表,按 event_id 索引)
        self._cache: Dict[str, Dict[str, Any]] = {}
        # FIFO 淘汰顺序
        if self._cache_capacity > 0:
            self._cache_order: deque = deque(maxlen=self._cache_capacity)
        else:
            self._cache_order = deque()
        # 加载统计
        self._last_load_at: float = 0.0
        self._last_load_ok: bool = False
        self._last_error: str = ""

    # --------------------------------------------------------
    # 内部:获取 initiative source
    # --------------------------------------------------------
    def _get_source(self) -> Optional[Any]:
        with self._lock:
            if self._initiative_source is not None:
                return self._initiative_source
            try:
                self._initiative_source = EventHubInitiativeSource()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "InitiativeDashboardProvider: 默认 source 不可用: %s", exc
                )
                self._initiative_source = None
            return self._initiative_source

    def _refresh_cache(self) -> bool:
        """从 source 拉取并刷新缓存。"""
        src = self._get_source()
        if src is None:
            with self._lock:
                self._last_load_ok = False
                self._last_error = "initiative_source_unavailable"
            return False
        try:
            n = (
                self._cache_capacity
                if self._cache_capacity > 0
                else DEFAULT_PROVIDER_CACHE_CAPACITY
            )
            events = src.list_initiative_events(limit=n) or []
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
        new_cache: Dict[str, Dict[str, Any]] = {}
        order: deque = (
            deque(maxlen=self._cache_capacity)
            if self._cache_capacity > 0
            else deque()
        )
        for ev in events:
            if not isinstance(ev, dict):
                continue
            eid = str(ev.get("event_id", "") or "")
            if not eid:
                continue
            existing = new_cache.get(eid)
            if existing is not None:
                if _resolve_timestamp(ev) <= _resolve_timestamp(existing):
                    continue
            new_cache[eid] = ev
            try:
                order.append(eid)
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

    # --------------------------------------------------------
    # Summary(统计)
    # --------------------------------------------------------
    def get_summary(self) -> Dict[str, Any]:
        """
        汇总统计:interest / possible_action / filtered 计数。

        Returns:
            {
                "available": bool,
                "interest_count": int,
                "possible_action_count": int,
                "filtered_count": int,
                "by_trend": {new, rising, stable, fading},
                "by_action_status": {pending, filtered, deferred, discarded},
                "by_action_type": {observe, learn, ask, recommend, remind},
                "last_interest_at": str|None,
                "last_interest_id": str|None,
                "last_action_at": str|None,
                "last_action_id": str|None,
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
                "interest_count": 0,
                "possible_action_count": 0,
                "filtered_count": 0,
                "by_trend": {t: 0 for t in ALL_INTEREST_TRENDS},
                "by_action_status": {s: 0 for s in ALL_ACTION_STATUSES},
                "by_action_type": {t: 0 for t in ALL_ACTION_TYPES},
                "last_interest_at": None,
                "last_interest_id": None,
                "last_action_at": None,
                "last_action_id": None,
                "fallback": True,
                "fallback_reason": last_error or "initiative_store_unavailable",
            }
        try:
            by_trend: Dict[str, int] = {t: 0 for t in ALL_INTEREST_TRENDS}
            by_status: Dict[str, int] = {s: 0 for s in ALL_ACTION_STATUSES}
            by_type: Dict[str, int] = {t: 0 for t in ALL_ACTION_TYPES}
            interest_count = 0
            possible_action_count = 0
            filtered_count = 0
            last_interest_ts: float = 0.0
            last_interest_id: str = ""
            last_action_ts: float = 0.0
            last_action_id: str = ""
            for eid, ev in cache.items():
                et = str(ev.get("event_type", "") or "")
                ts = _resolve_timestamp(ev)
                if et == "integration.interest_signal.created":
                    interest_count += 1
                    view = _interest_view_from_event(ev)
                    by_trend[view.get("trend", INTEREST_TREND_NEW)] = (
                        by_trend.get(view.get("trend", INTEREST_TREND_NEW), 0) + 1
                    )
                    if ts > last_interest_ts:
                        last_interest_ts = ts
                        last_interest_id = view.get("signal_id") or eid
                elif et in (
                    "integration.initiative.created",
                    "integration.initiative.filtered",
                ):
                    view = _action_view_from_event(ev)
                    possible_action_count += 1
                    st = view.get("status", "")
                    if st in ALL_ACTION_STATUSES:
                        by_status[st] = by_status.get(st, 0) + 1
                    at = view.get("action_type", "")
                    if at in ALL_ACTION_TYPES:
                        by_type[at] = by_type.get(at, 0) + 1
                    if st and st != ACTION_STATUS_PENDING:
                        filtered_count += 1
                    if ts > last_action_ts:
                        last_action_ts = ts
                        last_action_id = view.get("action_id") or eid
            return {
                "available": True,
                "interest_count": interest_count,
                "possible_action_count": possible_action_count,
                "filtered_count": filtered_count,
                "by_trend": by_trend,
                "by_action_status": by_status,
                "by_action_type": by_type,
                "last_interest_at": _to_iso(last_interest_ts)
                if last_interest_ts > 0
                else None,
                "last_interest_id": last_interest_id or None,
                "last_action_at": _to_iso(last_action_ts)
                if last_action_ts > 0
                else None,
                "last_action_id": last_action_id or None,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "interest_count": 0,
                "possible_action_count": 0,
                "filtered_count": 0,
                "by_trend": {t: 0 for t in ALL_INTEREST_TRENDS},
                "by_action_status": {s: 0 for s in ALL_ACTION_STATUSES},
                "by_action_type": {t: 0 for t in ALL_ACTION_TYPES},
                "last_interest_at": None,
                "last_interest_id": None,
                "last_action_at": None,
                "last_action_id": None,
                "fallback": True,
                "fallback_reason": f"summary_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # List Interests
    # --------------------------------------------------------
    def list_interests(
        self,
        limit: int = DEFAULT_LIMIT,
        trend: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        列出 InterestSignal(按时间倒序)。

        Args:
            limit: 返回数量上限(1..200)
            trend: 可选过滤 "new" / "rising" / "stable" / "fading"

        Returns:
            {
                "available": bool,
                "total": int,
                "total_unfiltered": int,
                "items": [interest_view],
                "filter_trend": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = DEFAULT_LIMIT
        trend_filter: Optional[str] = None
        if isinstance(trend, str) and trend in ALL_INTEREST_TRENDS:
            trend_filter = trend
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
                "filter_trend": trend_filter,
                "fallback": True,
                "fallback_reason": last_error or "initiative_store_unavailable",
            }
        try:
            filtered: List[Tuple[float, str, Dict[str, Any]]] = []
            unfiltered_count = 0
            for eid, ev in cache.items():
                et = str(ev.get("event_type", "") or "")
                if et != "integration.interest_signal.created":
                    continue
                unfiltered_count += 1
                view = _interest_view_from_event(ev)
                if trend_filter and view.get("trend") != trend_filter:
                    continue
                ts = _resolve_timestamp(ev)
                filtered.append((ts, eid, view))
            filtered.sort(key=lambda x: (x[0], x[1]), reverse=True)
            total = len(filtered)
            top = filtered[:n]
            return {
                "available": True,
                "total": total,
                "total_unfiltered": unfiltered_count,
                "items": [v for _, _, v in top],
                "filter_trend": trend_filter,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "total_unfiltered": 0,
                "items": [],
                "filter_trend": trend_filter,
                "fallback": True,
                "fallback_reason": f"list_interests_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # List Actions(PossibleAction 列表)
    # --------------------------------------------------------
    def list_actions(
        self,
        limit: int = DEFAULT_LIMIT,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        列出 PossibleAction(按时间倒序)。

        Args:
            limit: 返回数量上限(1..200)
            status: 可选过滤 "pending" / "filtered" / "deferred" / "discarded"

        Returns:
            {
                "available": bool,
                "total": int,
                "total_unfiltered": int,
                "items": [action_view],
                "filter_status": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = DEFAULT_LIMIT
        status_filter: Optional[str] = None
        if isinstance(status, str) and status in ALL_ACTION_STATUSES:
            status_filter = status
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
                "filter_status": status_filter,
                "fallback": True,
                "fallback_reason": last_error or "initiative_store_unavailable",
            }
        try:
            filtered: List[Tuple[float, str, Dict[str, Any]]] = []
            unfiltered_count = 0
            for eid, ev in cache.items():
                et = str(ev.get("event_type", "") or "")
                if et not in (
                    "integration.initiative.created",
                    "integration.initiative.filtered",
                ):
                    continue
                unfiltered_count += 1
                view = _action_view_from_event(ev)
                st = view.get("status", "")
                if status_filter and st != status_filter:
                    continue
                ts = _resolve_timestamp(ev)
                filtered.append((ts, eid, view))
            filtered.sort(key=lambda x: (x[0], x[1]), reverse=True)
            total = len(filtered)
            top = filtered[:n]
            return {
                "available": True,
                "total": total,
                "total_unfiltered": unfiltered_count,
                "items": [v for _, _, v in top],
                "filter_status": status_filter,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "total_unfiltered": 0,
                "items": [],
                "filter_status": status_filter,
                "fallback": True,
                "fallback_reason": f"list_actions_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # List Filtered(被过滤的行动 + 原因 + policy)
    # --------------------------------------------------------
    def list_filtered(self, limit: int = DEFAULT_LIMIT) -> Dict[str, Any]:
        """
        列出被过滤的行动(非 pending 状态,含 reason 与 policy)。

        Returns:
            {
                "available": bool,
                "total": int,
                "items": [{
                    "action_id", "action_type", "topic",
                    "status", "filter_reason", "filter_policy",
                    "urgency", "expected_value", "confidence", "priority",
                    "supporting_signal_ids", "rationale",
                    "created_at"
                }],
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = DEFAULT_LIMIT
        self._ensure_loaded()
        with self._lock:
            cache = dict(self._cache)
            last_load_ok = self._last_load_ok
            last_error = self._last_error
        if not cache and not last_load_ok:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": last_error or "initiative_store_unavailable",
            }
        try:
            out: List[Tuple[float, str, Dict[str, Any]]] = []
            for eid, ev in cache.items():
                et = str(ev.get("event_type", "") or "")
                if et != "integration.initiative.filtered":
                    continue
                view = _action_view_from_event(ev)
                ts = _resolve_timestamp(ev)
                out.append((ts, eid, view))
            out.sort(key=lambda x: (x[0], x[1]), reverse=True)
            items = [v for _, _, v in out[:n]]
            return {
                "available": True,
                "total": len(out),
                "items": items,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": f"list_filtered_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # History(主动性变化历史)
    # --------------------------------------------------------
    def get_history(self, limit: int = 50) -> Dict[str, Any]:
        """
        获取 initiative 变化历史(按时间倒序,事件级)。
        包含:
          - interest_signal.created
          - initiative.created
          - initiative.filtered
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = 50
        self._ensure_loaded()
        with self._lock:
            cache = dict(self._cache)
            last_load_ok = self._last_load_ok
            last_error = self._last_error
        src = self._get_source()
        all_events: List[Dict[str, Any]] = []
        if src is not None:
            try:
                fetched = (
                    src.list_initiative_events(limit=max(n * 4, MAX_LIMIT)) or []
                )
                if isinstance(fetched, list):
                    all_events = fetched
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "InitiativeDashboardProvider.get_history source 失败: %s", exc
                )
        if not all_events and not last_load_ok:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": last_error or "initiative_store_unavailable",
            }
        if not all_events and cache:
            for ev in cache.values():
                all_events.append(ev)
        try:
            all_events.sort(
                key=lambda e: (
                    _resolve_timestamp(e),
                    str(e.get("event_id", "") or ""),
                ),
                reverse=True,
            )
            history_items: List[Dict[str, Any]] = []
            for ev in all_events:
                if not isinstance(ev, dict):
                    continue
                et = str(ev.get("event_type", "") or "")
                if not _is_initiative_event(et):
                    continue
                payload = ev.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}
                ts = _resolve_timestamp(ev)
                if et == "integration.interest_signal.created":
                    history_items.append(
                        {
                            "event_id": str(ev.get("event_id", "") or ""),
                            "event_type": et,
                            "kind": "interest_signal",
                            "subject_id": str(payload.get("signal_id", "") or ""),
                            "topic": str(payload.get("topic", "") or "")[:128],
                            "trend": _resolve_interest_trend(ev),
                            "strength": float(payload.get("strength", 0.0) or 0.0),
                            "confidence": float(
                                payload.get("confidence", 0.0) or 0.0
                            ),
                            "rationale": str(payload.get("rationale", "") or "")[:512],
                            "source_event_count": len(
                                payload.get("source_event_ids", []) or []
                            ),
                            "timestamp": _to_iso(ts) or "",
                            "timestamp_ts": ts,
                        }
                    )
                else:
                    history_items.append(
                        {
                            "event_id": str(ev.get("event_id", "") or ""),
                            "event_type": et,
                            "kind": "possible_action",
                            "subject_id": str(payload.get("action_id", "") or ""),
                            "topic": str(payload.get("topic", "") or "")[:128],
                            "action_type": str(payload.get("action_type", "") or ""),
                            "status": _resolve_action_status(ev),
                            "urgency": str(payload.get("urgency", "") or ""),
                            "expected_value": float(
                                payload.get("expected_value", 0.0) or 0.0
                            ),
                            "confidence": float(
                                payload.get("confidence", 0.0) or 0.0
                            ),
                            "priority": float(payload.get("priority", 0.0) or 0.0),
                            "filter_reason": str(
                                payload.get("filter_reason", "") or ""
                            )[:512],
                            "filter_policy": _resolve_filter_policy(payload),
                            "rationale": str(payload.get("rationale", "") or "")[:512],
                            "supporting_signal_count": len(
                                payload.get("supporting_signal_ids", []) or []
                            ),
                            "timestamp": _to_iso(ts) or "",
                            "timestamp_ts": ts,
                        }
                    )
                if len(history_items) >= n:
                    break
            return {
                "available": True,
                "total": len(history_items),
                "items": history_items,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": f"history_error:{type(exc).__name__}",
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
_provider_instance: Optional[InitiativeDashboardProvider] = None
_provider_lock = threading.Lock()


def get_initiative_dashboard_provider() -> InitiativeDashboardProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = InitiativeDashboardProvider()
    return _provider_instance


def reset_initiative_dashboard_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "InitiativeDashboardProvider",
    "EventHubInitiativeSource",
    "get_initiative_dashboard_provider",
    "reset_initiative_dashboard_provider_for_testing",
    "ALL_INTEREST_TRENDS",
    "ALL_ACTION_STATUSES",
    "ALL_ACTION_URGENCIES",
    "ALL_ACTION_EFFORTS",
    "ALL_ACTION_TYPES",
    "ALL_FILTER_POLICIES",
    "INTEGRATION_INITIATIVE_EVENT_TYPES",
]
