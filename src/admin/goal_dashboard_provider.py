# -*- coding: utf-8 -*-
"""
src/admin/goal_dashboard_provider.py

Phase 5.0 Dashboard Upgrade Step 8.1 —— Goal Dashboard Provider。

职责:
- 只读提供 Goal 数据给 Dashboard
- 数据源:已有 Goal 数据通路(通过 Admin 侧 EventHub 间接访问 IntegrationEvent)
  严禁直接 import 任何业务实现类
- 严格容错,任何子组件不可用时返回 fallback
- 不调用 LLM 生成 Goal
- 不修改 Goal 状态
- 不触发 Goal 变化
- 严格只读

API:
- get_summary() -> {total_count, by_status, by_type, available, fallback}
- get_current() -> {current: goal_view, available, fallback}
- list_goals(limit=20, status=None) -> {items, total, available, fallback}
- get_goal(goal_id) -> {goal, available, fallback}
- get_history(limit=50) -> {items, total, available, fallback}

数据流:
    Dashboard UI
        ↓
    GoalRouter
        ↓
    GoalDashboardProvider
        ↓
    EventHub (Admin 侧只读访问层) → IntegrationEvent
        ↓
    Goal Authority(由 goal/initiative/personality 业务模块内部维护,
    Dashboard 仅通过 EventHub 间接观察,不做任何穿透访问)

约束:
- 禁止 import 任何 src.runtime.goal / src.runtime.initiative / src.personality 业务模块
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

# Goal 状态(与业务模块定义保持一致)
GOAL_STATUS_CANDIDATE = "candidate"
GOAL_STATUS_ACTIVE = "active"
GOAL_STATUS_PAUSED = "paused"
GOAL_STATUS_COMPLETED = "completed"
GOAL_STATUS_ABANDONED = "abandoned"

ALL_GOAL_STATUSES = (
    GOAL_STATUS_CANDIDATE,
    GOAL_STATUS_ACTIVE,
    GOAL_STATUS_PAUSED,
    GOAL_STATUS_COMPLETED,
    GOAL_STATUS_ABANDONED,
)

# Goal 类型
GOAL_TYPE_PERSONAL_GROWTH = "personal_growth"
GOAL_TYPE_LEARNING = "learning"
GOAL_TYPE_CREATIVE = "creative"
GOAL_TYPE_RELATIONSHIP = "relationship"
GOAL_TYPE_EXPLORATION = "exploration"

ALL_GOAL_TYPES = (
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_RELATIONSHIP,
    GOAL_TYPE_EXPLORATION,
)

# Integration Event 关联 Goal 的事件类型
INTEGRATION_GOAL_EVENT_TYPES = (
    "integration.goal.created",
    "integration.goal.updated",
    "integration.goal.plan_created",
    "integration.desire.created",
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
        logger.debug("goal_dashboard_provider safe_call 失败: %s", exc)
        return None


def _is_goal_event(event_type: Any) -> bool:
    """判断事件类型是否与 goal/desire 相关。"""
    if not event_type or not isinstance(event_type, str):
        return False
    return event_type in INTEGRATION_GOAL_EVENT_TYPES


def _resolve_goal_status(event: Dict[str, Any]) -> str:
    """从 goal event payload 中提取状态。"""
    payload = event.get("payload") if isinstance(event, dict) else None
    if isinstance(payload, dict):
        st = payload.get("status")
        if isinstance(st, str) and st in ALL_GOAL_STATUSES:
            return st
    return GOAL_STATUS_CANDIDATE


def _resolve_goal_type(event: Dict[str, Any]) -> str:
    """从 goal event payload 中提取类型。"""
    payload = event.get("payload") if isinstance(event, dict) else None
    if isinstance(payload, dict):
        gt = payload.get("goal_type")
        if isinstance(gt, str) and gt in ALL_GOAL_TYPES:
            return gt
    return GOAL_TYPE_PERSONAL_GROWTH


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


def _goal_view_from_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 IntegrationEvent 规范化为 Goal 视图(用于 list_goals / get_current)。
    """
    if not isinstance(event, dict):
        return {}
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ts = _resolve_timestamp(event)
    return {
        "goal_id": str(payload.get("goal_id", "") or event.get("event_id", "") or ""),
        "title": str(payload.get("title", "") or "")[:128],
        "description": str(payload.get("description", "") or "")[:1024],
        "goal_type": _resolve_goal_type(event),
        "status": _resolve_goal_status(event),
        "priority": float(payload.get("priority", 0.5) or 0.5),
        "importance": float(payload.get("importance", 0.5) or 0.5),
        "confidence": float(payload.get("confidence", 0.5) or 0.5),
        "created_at": _to_iso(ts) or "",
        "created_ts": ts,
        "updated_at": _to_iso(float(payload.get("updated_at", 0.0) or 0.0)) if payload.get("updated_at") else "",
        "completed_at": _to_iso(float(payload.get("completed_at", 0.0) or 0.0)) if payload.get("completed_at") else "",
        "lifecycle": str(payload.get("lifecycle", "") or ""),
        "reason": str(payload.get("reason", "") or "")[:512],
        "source_event_count": len(payload.get("source_event_ids", []) or []),
        "source_desire_count": len(payload.get("source_desire_ids", []) or []),
        "has_plan": False,  # 由 plan 数据补全
        "fallback": False,
    }


# ============================================================
# 默认 Goal Source(EventHub-based,只读)
# ============================================================

class EventHubGoalSource:
    """
    通过 Admin 侧 EventHub 间接访问 IntegrationEvent 中的 goal 数据。
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
            logger.debug("EventHubGoalSource: EventHub 不可用: %s", exc)
            self._event_hub = None
        return self._event_hub

    def list_goal_events(self, limit: int = 200) -> List[Dict[str, Any]]:
        """
        从 EventHub 拉取所有 goal/desire 相关的 IntegrationEvent。

        Returns:
            goal event 列表(按时间倒序),失败时返回空列表。
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
            logger.debug("EventHubGoalSource: poll_recent 失败: %s", exc)
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
                    for et in INTEGRATION_GOAL_EVENT_TYPES:
                        try:
                            sub = events_fn(event_type=et, limit=limit) or []
                        except Exception as exc:  # noqa: BLE001
                            logger.debug("EventHubGoalSource: events(%s) 失败: %s", et, exc)
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
                        if _is_goal_event(et):
                            out2.append(self._normalize(ev))
                    out2.sort(
                        key=lambda e: (_resolve_timestamp(e), str(e.get("event_id", "") or "")),
                        reverse=True,
                    )
                    return out2
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubGoalSource: 读取 host.event_log 失败: %s", exc)
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
class GoalDashboardProvider:
    """
    Goal Dashboard 只读 Provider。

    注入:
        goal_source: 任何暴露 list_goal_events(limit) 接口的对象
                     (测试时可注入 mock;生产由 EventHubGoalSource 提供)
    """

    def __init__(
        self,
        goal_source: Optional[Any] = None,
        *,
        cache_capacity: int = DEFAULT_PROVIDER_CACHE_CAPACITY,
    ) -> None:
        self._lock = threading.RLock()
        self._goal_source = goal_source
        self._cache_capacity = max(0, int(cache_capacity))
        # 内部缓存:goal_id -> {event, ...}
        self._cache: Dict[str, Dict[str, Any]] = {}
        # FIFO 淘汰顺序
        self._cache_order: deque = deque(maxlen=self._cache_capacity) if self._cache_capacity > 0 else deque()
        # 加载统计
        self._last_load_at: float = 0.0
        self._last_load_ok: bool = False
        self._last_error: str = ""

    # --------------------------------------------------------
    # 内部:获取 goal source
    # --------------------------------------------------------
    def _get_source(self) -> Optional[Any]:
        with self._lock:
            if self._goal_source is not None:
                return self._goal_source
            try:
                self._goal_source = EventHubGoalSource()
            except Exception as exc:  # noqa: BLE001
                logger.debug("GoalDashboardProvider: 默认 source 不可用: %s", exc)
                self._goal_source = None
            return self._goal_source

    def _refresh_cache(self) -> bool:
        """从 source 拉取并刷新缓存。"""
        src = self._get_source()
        if src is None:
            with self._lock:
                self._last_load_ok = False
                self._last_error = "goal_source_unavailable"
            return False
        try:
            n = self._cache_capacity if self._cache_capacity > 0 else DEFAULT_PROVIDER_CACHE_CAPACITY
            events = src.list_goal_events(limit=n) or []
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
        # 按 goal_id 聚合(同一 goal_id 的多次出现合并为最新一条)
        new_cache: Dict[str, Dict[str, Any]] = {}
        order: deque = deque(maxlen=self._cache_capacity) if self._cache_capacity > 0 else deque()
        # 单独维护 plan map:goal_id -> {plan_id, step_count, progress, ...}
        plan_map: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            if not isinstance(ev, dict):
                continue
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            ev_type = ev.get("event_type", "")
            rid = str(payload.get("goal_id", "") or "")
            if not rid:
                rid = str(ev.get("event_id", "") or "")
            if not rid:
                continue
            # plan 事件:单独收集
            if ev_type == "integration.goal.plan_created":
                plan_map[rid] = {
                    "plan_id": str(payload.get("plan_id", "") or ""),
                    "step_count": int(payload.get("step_count", 0) or 0),
                    "progress": float(payload.get("progress", 0.0) or 0.0),
                    "title": str(payload.get("title", "") or ""),
                }
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
        # 合并 plan 信息
        for gid, plan in plan_map.items():
            if gid in new_cache:
                pl = new_cache[gid].get("payload") or {}
                if isinstance(pl, dict):
                    pl["_plan"] = plan
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

    def _get_event_by_id(self, goal_id: str) -> Optional[Dict[str, Any]]:
        if not goal_id or not isinstance(goal_id, str):
            return None
        self._ensure_loaded()
        with self._lock:
            return self._cache.get(goal_id)

    # --------------------------------------------------------
    # Summary(统计)
    # --------------------------------------------------------
    def get_summary(self) -> Dict[str, Any]:
        """
        汇总统计:总数 / 按状态分布 / 按类型分布 / 最新一次时间。

        Returns:
            {
                "available": bool,
                "total_count": int,
                "active_count": int,
                "by_status": {candidate, active, paused, completed, abandoned},
                "by_type": {personal_growth, learning, creative, relationship, exploration},
                "last_goal_at": str|None,
                "last_goal_id": str|None,
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
                "active_count": 0,
                "by_status": {s: 0 for s in ALL_GOAL_STATUSES},
                "by_type": {t: 0 for t in ALL_GOAL_TYPES},
                "last_goal_at": None,
                "last_goal_id": None,
                "fallback": True,
                "fallback_reason": last_error or "goal_store_unavailable",
            }
        try:
            by_status: Dict[str, int] = {s: 0 for s in ALL_GOAL_STATUSES}
            by_type: Dict[str, int] = {t: 0 for t in ALL_GOAL_TYPES}
            last_ts: float = 0.0
            last_id: str = ""
            active_count = 0
            for rid, ev in cache.items():
                st = _resolve_goal_status(ev)
                gt = _resolve_goal_type(ev)
                by_status[st] = by_status.get(st, 0) + 1
                by_type[gt] = by_type.get(gt, 0) + 1
                if st == GOAL_STATUS_ACTIVE:
                    active_count += 1
                ts = _resolve_timestamp(ev)
                if ts > last_ts:
                    last_ts = ts
                    last_id = rid
            return {
                "available": True,
                "total_count": len(cache),
                "active_count": active_count,
                "by_status": by_status,
                "by_type": by_type,
                "last_goal_at": _to_iso(last_ts) if last_ts > 0 else None,
                "last_goal_id": last_id or None,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total_count": 0,
                "active_count": 0,
                "by_status": {s: 0 for s in ALL_GOAL_STATUSES},
                "by_type": {t: 0 for t in ALL_GOAL_TYPES},
                "last_goal_at": None,
                "last_goal_id": None,
                "fallback": True,
                "fallback_reason": f"summary_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Current(当前活跃目标)
    # --------------------------------------------------------
    def get_current(self) -> Dict[str, Any]:
        """
        获取当前活跃目标(优先级最高的 active 目标)。
        多个 active 目标时,选择最近更新的。

        Returns:
            {
                "available": bool,
                "current": goal_view | None,
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
                "current": None,
                "fallback": True,
                "fallback_reason": last_error or "goal_store_unavailable",
            }
        try:
            # 找 active 状态且 priority 最高的
            best_view: Optional[Dict[str, Any]] = None
            best_score: Tuple[float, float] = (-1.0, 0.0)
            for rid, ev in cache.items():
                st = _resolve_goal_status(ev)
                if st != GOAL_STATUS_ACTIVE:
                    continue
                view = _goal_view_from_event(ev)
                ts = _resolve_timestamp(ev)
                # 排序:priority 倒序,时间倒序
                score = (float(view.get("priority", 0.0) or 0.0), ts)
                if score > best_score:
                    best_score = score
                    best_view = view
            if best_view is None:
                # 没有 active 时,降级:取最近更新的任何 goal(非终止态优先)
                fallback_view: Optional[Dict[str, Any]] = None
                fallback_ts: float = 0.0
                for rid, ev in cache.items():
                    st = _resolve_goal_status(ev)
                    if st in (GOAL_STATUS_COMPLETED, GOAL_STATUS_ABANDONED):
                        continue
                    view = _goal_view_from_event(ev)
                    ts = _resolve_timestamp(ev)
                    if ts > fallback_ts:
                        fallback_ts = ts
                        fallback_view = view
                if fallback_view is None:
                    return {
                        "available": True,
                        "current": None,
                        "fallback": False,
                        "fallback_reason": "no_active_goal",
                    }
                return {
                    "available": True,
                    "current": fallback_view,
                    "fallback": True,
                    "fallback_reason": "no_active_goal_fallback_to_recent",
                }
            return {
                "available": True,
                "current": best_view,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "current": None,
                "fallback": True,
                "fallback_reason": f"current_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # List
    # --------------------------------------------------------
    def list_goals(
        self,
        limit: int = DEFAULT_LIMIT,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        列出 goal 列表(按时间倒序)。

        Args:
            limit: 返回数量上限(1..200)
            status: 可选过滤 "candidate" / "active" / "paused" / "completed" / "abandoned"

        Returns:
            {
                "available": bool,
                "total": int,
                "total_unfiltered": int,
                "items": [goal_view],
                "filter_status": str|None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = DEFAULT_LIMIT
        # 校验 status
        status_filter: Optional[str] = None
        if isinstance(status, str) and status in ALL_GOAL_STATUSES:
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
                "fallback_reason": last_error or "goal_store_unavailable",
            }
        try:
            filtered: List[Tuple[float, str, Dict[str, Any]]] = []
            for rid, ev in cache.items():
                st = _resolve_goal_status(ev)
                if status_filter and st != status_filter:
                    continue
                ts = _resolve_timestamp(ev)
                filtered.append((ts, rid, ev))
            filtered.sort(key=lambda x: (x[0], x[1]), reverse=True)
            total = len(filtered)
            top = filtered[:n]
            items = [_goal_view_from_event(ev) for _, _, ev in top]
            return {
                "available": True,
                "total": total,
                "total_unfiltered": len(cache),
                "items": items,
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
                "fallback_reason": f"list_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # Detail
    # --------------------------------------------------------
    def get_goal(self, goal_id: str) -> Dict[str, Any]:
        """
        获取单条 goal 详情。

        Returns:
            {
                "available": bool,
                "goal": {...} | None,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not goal_id or not isinstance(goal_id, str):
            return {
                "available": False,
                "goal": None,
                "fallback": True,
                "fallback_reason": "invalid_goal_id",
            }
        ev = self._get_event_by_id(goal_id)
        if ev is None:
            # 主动再 refresh 一次(应对刚写入的新 goal)
            self._refresh_cache()
            with self._lock:
                ev = self._cache.get(goal_id)
        if ev is None:
            return {
                "available": False,
                "goal": None,
                "fallback": True,
                "fallback_reason": "goal_not_found",
            }
        try:
            view = _goal_view_from_event(ev)
            payload = ev.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            # 附加 source 列表
            view.update({
                "source_event_ids": list(payload.get("source_event_ids", []) or []),
                "source_desire_ids": list(payload.get("source_desire_ids", []) or []),
                "related_ids": list(ev.get("related_ids") or []),
                "metadata": dict(payload.get("metadata", {}) or {}),
                "version": str(payload.get("version", "") or ""),
            })
            # 附加 plan(若有)
            plan = payload.get("_plan")
            if isinstance(plan, dict):
                view["plan"] = {
                    "plan_id": str(plan.get("plan_id", "") or ""),
                    "step_count": int(plan.get("step_count", 0) or 0),
                    "progress": float(plan.get("progress", 0.0) or 0.0),
                    "title": str(plan.get("title", "") or ""),
                }
                view["has_plan"] = True
            # event_id 作为 event_id 字段
            view["event_id"] = str(ev.get("event_id", "") or "")
            return {
                "available": True,
                "goal": view,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "goal": None,
                "fallback": True,
                "fallback_reason": f"detail_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # History(目标变化历史)
    # --------------------------------------------------------
    def get_history(self, limit: int = 50) -> Dict[str, Any]:
        """
        获取 goal 变化历史(按时间倒序,包含 created / updated / plan_created / desire 事件)。

        严格基于 EventHub 中的 IntegrationEvent,不调用业务模块的 history API。
        这是"事件级历史",反映 Dashboard 观察到的变化序列。

        Returns:
            {
                "available": bool,
                "total": int,
                "items": [
                    {
                        "event_id": str,
                        "event_type": str,
                        "goal_id": str,
                        "lifecycle": str,
                        "from_status": str|None,
                        "to_status": str|None,
                        "reason": str,
                        "timestamp": str,
                        "timestamp_ts": float
                    },
                    ...
                ],
                "fallback": bool,
                "fallback_reason": str|None
            }
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
        # history 来自 source 的所有事件,不只是 cache 中去重后的
        src = self._get_source()
        all_events: List[Dict[str, Any]] = []
        if src is not None:
            try:
                # 多拉一些以保证 history 完整
                fetched = src.list_goal_events(limit=max(n * 4, MAX_LIMIT)) or []
                if isinstance(fetched, list):
                    all_events = fetched
            except Exception as exc:  # noqa: BLE001
                logger.debug("GoalDashboardProvider.get_history source 失败: %s", exc)
        if not all_events and not last_load_ok:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "fallback": True,
                "fallback_reason": last_error or "goal_store_unavailable",
            }
        if not all_events and cache:
            # 退化使用 cache(去重后的)
            for ev in cache.values():
                all_events.append(ev)
        try:
            # 按时间倒序
            all_events.sort(
                key=lambda e: (_resolve_timestamp(e), str(e.get("event_id", "") or "")),
                reverse=True,
            )
            history_items: List[Dict[str, Any]] = []
            for ev in all_events:
                if not isinstance(ev, dict):
                    continue
                payload = ev.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}
                et = str(ev.get("event_type", "") or "")
                ts = _resolve_timestamp(ev)
                history_items.append({
                    "event_id": str(ev.get("event_id", "") or ""),
                    "event_type": et,
                    "goal_id": str(payload.get("goal_id", "") or ""),
                    "lifecycle": str(payload.get("lifecycle", "") or ""),
                    "title": str(payload.get("title", "") or "")[:128],
                    "status": _resolve_goal_status(ev) if et.startswith("integration.goal") else "",
                    "from_status": "",  # 不伪造,业务层不直接提供 from 状态
                    "to_status": _resolve_goal_status(ev) if et.startswith("integration.goal") else "",
                    "reason": str(payload.get("reason", "") or "")[:512],
                    "confidence": float(payload.get("confidence", 0.0) or 0.0),
                    "importance": float(payload.get("importance", 0.0) or 0.0),
                    "source_event_count": len(payload.get("source_event_ids", []) or []),
                    "source_desire_count": len(payload.get("source_desire_ids", []) or []),
                    "timestamp": _to_iso(ts) or "",
                    "timestamp_ts": ts,
                })
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
_provider_instance: Optional[GoalDashboardProvider] = None
_provider_lock = threading.Lock()


def get_goal_dashboard_provider() -> GoalDashboardProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = GoalDashboardProvider()
    return _provider_instance


def reset_goal_dashboard_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "GoalDashboardProvider",
    "EventHubGoalSource",
    "get_goal_dashboard_provider",
    "reset_goal_dashboard_provider_for_testing",
    "ALL_GOAL_STATUSES",
    "ALL_GOAL_TYPES",
    "INTEGRATION_GOAL_EVENT_TYPES",
]
