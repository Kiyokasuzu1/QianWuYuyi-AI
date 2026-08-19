# -*- coding: utf-8 -*-
"""
src/admin/life_graph_provider.py

Phase 5.0 Dashboard Upgrade Step 8.3 —— LifeGraphProvider。

职责:
- 只读聚合六大系统的跨域关联:
    Memory / Reflection / Interest / Action / Goal / Belief / TraitChange
- 节点 (nodes) 来自:
    Memory  -  MemoryDashboardProvider.list_recent
    Reflection - EventHub 中 reflection.* 事件
    Interest - EventHub 中 interest_signal.created 事件
    Action  -  EventHub 中 initiative.created/filtered 事件
    Goal  -    EventHub 中 goal.created/updated/plan_created/desire.created 事件
    Belief  -  SelfModelDashboardProvider.get_beliefs
    TraitChange - SelfModelDashboardProvider.get_evolution_timeline
- 边 (edges) 来自:
    - 各 event payload 中的 source_event_ids / source_reflection_ids
    - 各 event payload 中的 supporting_signal_ids / source_desire_ids
    - topic 模糊匹配(同主题连接)
- 时间线:按 ts 升序合并所有节点
- 路径查询:BFS over edges

约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime core / self_model 业务模块
- 仅依赖: src.admin.dashboard.* (现有 dashboard 间接访问层) + src.admin.*_dashboard_provider
- 严格只读
- 不调用 LLM
- 不修改任何 Authority
- 不创建业务事件
- 所有失败 fallback=true 且有 fallback_reason
- 禁止"空数据当真实": 任何空返回必须带 fallback=true

数据流:
    Dashboard UI
        ↓
    LifeGraphRouter
        ↓
    LifeGraphProvider
        ↓
    MemoryDashboardProvider / SelfModelDashboardProvider
        EventHub (read-only) → IntegrationEvent
        ↓
    Business Authorities(由各自模块维护,Dashboard 仅观察)
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 节点类型
NODE_TYPE_MEMORY = "memory"
NODE_TYPE_REFLECTION = "reflection"
NODE_TYPE_INTEREST = "interest"
NODE_TYPE_ACTION = "action"
NODE_TYPE_GOAL = "goal"
NODE_TYPE_BELIEF = "belief"
NODE_TYPE_TRAIT_CHANGE = "trait_change"
ALL_NODE_TYPES = (
    NODE_TYPE_MEMORY,
    NODE_TYPE_REFLECTION,
    NODE_TYPE_INTEREST,
    NODE_TYPE_ACTION,
    NODE_TYPE_GOAL,
    NODE_TYPE_BELIEF,
    NODE_TYPE_TRAIT_CHANGE,
)

# 关系类型
REL_REFL_FROM_MEMORY = "memory_to_reflection"
REL_INTEREST_FROM_REFL = "reflection_to_interest"
REL_GOAL_FROM_INTEREST = "interest_to_goal"
REL_ACTION_FROM_GOAL = "goal_to_action"
REL_BELIEF_FROM_REFL = "reflection_to_belief"
REL_TRAIT_FROM_BELIEF = "belief_to_trait_change"
REL_TOPIC_LINK = "topic_link"
ALL_RELATIONS = (
    REL_REFL_FROM_MEMORY,
    REL_INTEREST_FROM_REFL,
    REL_GOAL_FROM_INTEREST,
    REL_ACTION_FROM_GOAL,
    REL_BELIEF_FROM_REFL,
    REL_TRAIT_FROM_BELIEF,
    REL_TOPIC_LINK,
)

# Integration Event 涉及的 Life Graph 事件类型
INTEGRATION_LIFEGRAH_EVENT_TYPES = (
    "integration.interest_signal.created",
    "integration.initiative.created",
    "integration.initiative.filtered",
    "integration.reflection.completed",
    "integration.reflection.daily_completed",
    "integration.reflection.event_completed",
    "integration.reflection.growth_completed",
    "integration.goal.created",
    "integration.goal.updated",
    "integration.goal.plan_created",
    "integration.desire.created",
)

# 默认 limit
DEFAULT_GRAPH_LIMIT = 200
DEFAULT_TIMELINE_LIMIT = 100
DEFAULT_PATH_LIMIT = 200
MAX_LIMIT = 500
# Step 8.4.2 —— 邻居查询最大 depth
MAX_NEIGHBOR_DEPTH = 3
DEFAULT_NEIGHBOR_DEPTH = 1
NEIGHBOR_NODE_LIMIT = 100


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
        logger.debug("life_graph_provider safe_call 失败: %s", exc)
        return None


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
        # 数字字符串
        try:
            return float(s)
        except (TypeError, ValueError):
            pass
        # ISO 字符串
        try:
            from datetime import datetime
            iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
            dt = datetime.fromisoformat(iso)
            try:
                return dt.timestamp()
            except Exception:
                # 没有 tz 信息的 datetime
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


def _topic_norm(topic: Any) -> str:
    """归一化 topic,用于 topic 匹配。"""
    if not topic or not isinstance(topic, str):
        return ""
    t = topic.strip().lower()
    if not t:
        return ""
    # 去除空格与标点
    t = re.sub(r"[\s\u3000\.,;:!\?\-\(\)\[\]\"'\u201c\u201d\u2018\u2019/\\\\]+", "", t)
    return t


def _truncate(s: Any, n: int = 128) -> str:
    if s is None:
        return ""
    try:
        v = str(s)
    except Exception:
        return ""
    if len(v) > n:
        return v[:n]
    return v


# ============================================================
# Domain Collector(只读聚合,测试可注入)
# ============================================================

class EventHubDomainCollector:
    """
    通过 EventHub 拉取所有相关 IntegrationEvent。
    严格只读,从不调用任何业务模块的写方法。
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
            logger.debug("EventHubDomainCollector: EventHub 不可用: %s", exc)
            self._event_hub = None
        return self._event_hub

    def list_events(self, limit: int = 500) -> List[Dict[str, Any]]:
        """从 EventHub 拉取所有相关 IntegrationEvent。"""
        hub = self._get_hub()
        if hub is None:
            return []
        try:
            poll_fn = getattr(hub, "poll_recent", None)
            if callable(poll_fn):
                _ = poll_fn(limit=max(1, min(MAX_LIMIT, int(limit) or 0)))
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubDomainCollector: poll_recent 失败: %s", exc)
            return []
        try:
            host = getattr(hub, "_get_host", None)
            if not callable(host):
                return []
            h = host()
            if h is None:
                return []
            event_log = _safe_get(h, "event_log")
            if event_log is None:
                return []
            events_fn = _safe_get(event_log, "events")
            latest_fn = _safe_get(event_log, "latest")
            out: List[Dict[str, Any]] = []
            seen: Set[str] = set()
            if callable(events_fn):
                for et in INTEGRATION_LIFEGRAH_EVENT_TYPES:
                    try:
                        sub = events_fn(event_type=et, limit=limit) or []
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "EventHubDomainCollector: events(%s) 失败: %s", et, exc
                        )
                        continue
                    for ev in sub:
                        eid = str(_safe_get(ev, "event_id", "") or "")
                        if not eid or eid in seen:
                            continue
                        seen.add(eid)
                        out.append(self._normalize(ev))
            elif callable(latest_fn):
                try:
                    all_events = latest_fn(limit=limit) or []
                except Exception:
                    return []
                for ev in all_events:
                    et = str(_safe_get(ev, "event_type", "") or "")
                    if et in INTEGRATION_LIFEGRAH_EVENT_TYPES:
                        eid = str(_safe_get(ev, "event_id", "") or "")
                        if not eid or eid in seen:
                            continue
                        seen.add(eid)
                        out.append(self._normalize(ev))
            out.sort(
                key=lambda e: (
                    _to_float_ts(e.get("timestamp")),
                    str(e.get("event_id", "") or ""),
                ),
                reverse=True,
            )
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("EventHubDomainCollector: 读取 host.event_log 失败: %s", exc)
            return []

    def _normalize(self, ev: Any) -> Dict[str, Any]:
        return {
            "event_id": str(_safe_get(ev, "event_id", "") or ""),
            "event_type": str(_safe_get(ev, "event_type", "") or ""),
            "source": str(_safe_get(ev, "source", "") or ""),
            "timestamp": _safe_get(ev, "timestamp", None),
            "payload": _safe_get(ev, "payload", {}) or {},
            "related_ids": list(_safe_get(ev, "related_ids", []) or []),
            "metadata": _safe_get(ev, "metadata", {}) or {},
        }


class DomainCollector:
    """
    默认域数据聚合器:
    - Event 域(Reflection / Interest / Action / Goal) 通过 EventHubDomainCollector 拉取
    - Memory 域 通过 MemoryDashboardProvider 读取
    - SelfModel 域 通过 SelfModelDashboardProvider 读取(Belief / TraitChange)
    - 全部只读
    """

    def __init__(
        self,
        event_hub: Optional[Any] = None,
        memory_provider: Optional[Any] = None,
        selfmodel_provider: Optional[Any] = None,
    ) -> None:
        self._event_collector = EventHubDomainCollector(event_hub=event_hub)
        self._memory_provider = memory_provider
        self._selfmodel_provider = selfmodel_provider

    def _get_memory_provider(self):
        if self._memory_provider is not None:
            return self._memory_provider
        try:
            from src.admin.memory_dashboard_provider import get_memory_dashboard_provider
            self._memory_provider = get_memory_dashboard_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("DomainCollector: MemoryDashboardProvider 不可用: %s", exc)
            self._memory_provider = None
        return self._memory_provider

    def _get_selfmodel_provider(self):
        if self._selfmodel_provider is not None:
            return self._selfmodel_provider
        try:
            from src.admin.selfmodel_dashboard_provider import get_selfmodel_dashboard_provider
            self._selfmodel_provider = get_selfmodel_dashboard_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("DomainCollector: SelfModelDashboardProvider 不可用: %s", exc)
            self._selfmodel_provider = None
        return self._selfmodel_provider

    def list_event_nodes(self, limit: int = 500) -> List[Dict[str, Any]]:
        return self._event_collector.list_events(limit=limit)

    def list_memory_nodes(self, limit: int = 50) -> List[Dict[str, Any]]:
        p = self._get_memory_provider()
        if p is None:
            return []
        try:
            res = _safe_call(p.list_recent, limit=limit)
        except Exception:
            res = None
        if not isinstance(res, dict) or not res.get("available"):
            return []
        items = res.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = str(it.get("id") or it.get("memory_id") or "")
            if not mid:
                continue
            out.append({
                "id": mid,
                "type": NODE_TYPE_MEMORY,
                "label": _truncate(it.get("summary") or it.get("content") or it.get("topic") or "", 128),
                "topic": str(it.get("topic") or ""),
                "timestamp": it.get("timestamp") or it.get("created_at") or "",
                "importance": it.get("importance", 0.0),
                "source_event_ids": list(it.get("source_event_ids", []) or []),
                "evidence": str(it.get("summary") or it.get("content") or "")[:256],
                "domain": "memory",
                "raw": {k: v for k, v in it.items() if k not in ("raw",)},
            })
        return out

    def list_belief_nodes(self, limit: int = 50) -> List[Dict[str, Any]]:
        p = self._get_selfmodel_provider()
        if p is None:
            return []
        try:
            res = _safe_call(p.get_beliefs, limit=limit)
        except Exception:
            res = None
        if not isinstance(res, dict) or not res.get("available"):
            return []
        items = res.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            bid = str(it.get("belief_id") or it.get("id") or "")
            if not bid:
                continue
            out.append({
                "id": bid,
                "type": NODE_TYPE_BELIEF,
                "label": _truncate(it.get("statement") or it.get("content") or "", 128),
                "topic": str(it.get("domain") or ""),
                "domain": str(it.get("domain") or ""),
                "confidence": it.get("confidence", 0.0),
                "status": str(it.get("status") or ""),
                "timestamp": it.get("updated_at") or it.get("created_at") or "",
                "source_event_ids": list(it.get("source_event_ids", []) or []),
                "source_reflection_ids": list(it.get("source_reflection_ids", []) or []),
                "evidence": _truncate(it.get("statement") or it.get("content") or "", 256),
                "raw": {k: v for k, v in it.items() if k != "raw"},
            })
        return out

    def list_trait_change_nodes(self, limit: int = 50) -> List[Dict[str, Any]]:
        p = self._get_selfmodel_provider()
        if p is None:
            return []
        try:
            res = _safe_call(p.get_evolution_timeline, limit=limit)
        except Exception:
            res = None
        if not isinstance(res, dict) or not res.get("available"):
            return []
        items = res.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            tcid = str(it.get("change_id") or it.get("id") or "")
            if not tcid:
                continue
            out.append({
                "id": tcid,
                "type": NODE_TYPE_TRAIT_CHANGE,
                "label": _truncate(it.get("trait_name") or it.get("name") or it.get("description") or "", 128),
                "topic": str(it.get("trait_name") or it.get("domain") or ""),
                "trait_name": str(it.get("trait_name") or it.get("name") or ""),
                "delta": it.get("delta", it.get("change", 0.0)),
                "old_value": it.get("old_value"),
                "new_value": it.get("new_value"),
                "timestamp": it.get("timestamp") or it.get("created_at") or "",
                "source_event_ids": list(it.get("source_event_ids", []) or []),
                "source_belief_ids": list(it.get("source_belief_ids", []) or []),
                "evidence": _truncate(it.get("description") or it.get("reason") or "", 256),
                "raw": {k: v for k, v in it.items() if k != "raw"},
            })
        return out


# ============================================================
# Event → Node 转换
# ============================================================

def _event_to_node(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """根据 event_type 把 IntegrationEvent 转换为 LifeGraph node。"""
    if not isinstance(event, dict):
        return None
    et = str(event.get("event_type", "") or "")
    eid = str(event.get("event_id", "") or "")
    if not eid:
        return None
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    ts = _to_float_ts(event.get("timestamp"))
    if et == "integration.interest_signal.created":
        topic = str(payload.get("topic", "") or "")
        sid = str(payload.get("signal_id", "") or eid)
        return {
            "id": f"interest::{sid}",
            "type": NODE_TYPE_INTEREST,
            "label": _truncate(topic or payload.get("rationale", "") or "Interest", 128),
            "topic": topic,
            "trend": str(payload.get("trend", "") or ""),
            "strength": payload.get("strength", 0.0),
            "confidence": payload.get("confidence", 0.0),
            "timestamp": event.get("timestamp"),
            "timestamp_ts": ts,
            "timestamp_iso": _to_iso(ts) or "",
            "source_event_ids": list(payload.get("source_event_ids", []) or []),
            "source_reflection_ids": list(payload.get("source_reflection_ids", []) or []),
            "evidence": _truncate(payload.get("rationale", "") or "", 256),
            "event_id": eid,
            "event_type": et,
            "raw_payload": payload,
        }
    if et == "integration.initiative.created" or et == "integration.initiative.filtered":
        action_id = str(payload.get("action_id", "") or eid)
        status = "pending"
        if et == "integration.initiative.filtered":
            st = payload.get("status")
            if isinstance(st, str) and st in ("filtered", "deferred", "discarded"):
                status = st
            else:
                status = "filtered"
        topic = str(payload.get("topic", "") or "")
        return {
            "id": f"action::{action_id}",
            "type": NODE_TYPE_ACTION,
            "label": _truncate(
                payload.get("action_type", "") + " " + (topic or payload.get("rationale", "") or "Action"),
                128,
            ),
            "topic": topic,
            "action_type": str(payload.get("action_type", "") or ""),
            "status": status,
            "urgency": str(payload.get("urgency", "") or ""),
            "expected_value": payload.get("expected_value", 0.0),
            "confidence": payload.get("confidence", 0.0),
            "priority": payload.get("priority", 0.5),
            "timestamp": event.get("timestamp"),
            "timestamp_ts": ts,
            "timestamp_iso": _to_iso(ts) or "",
            "source_event_ids": list(payload.get("source_event_ids", []) or []),
            "supporting_signal_ids": list(payload.get("supporting_signal_ids", []) or []),
            "filter_reason": _truncate(payload.get("filter_reason", "") or "", 256),
            "filter_policy": str(payload.get("rule_applied", "") or "unknown"),
            "evidence": _truncate(payload.get("rationale", "") or "", 256),
            "event_id": eid,
            "event_type": et,
            "raw_payload": payload,
        }
    if et in (
        "integration.reflection.completed",
        "integration.reflection.daily_completed",
        "integration.reflection.event_completed",
        "integration.reflection.growth_completed",
    ):
        rid = str(payload.get("reflection_id", "") or eid)
        rtype = str(payload.get("reflection_type", "") or et.split(".")[-2] or "daily")
        topic = str(payload.get("topic", "") or "")
        return {
            "id": f"reflection::{rid}",
            "type": NODE_TYPE_REFLECTION,
            "label": _truncate(
                topic or payload.get("summary", "") or payload.get("insight", "") or "Reflection",
                128,
            ),
            "topic": topic,
            "reflection_type": rtype,
            "depth": str(payload.get("depth", "") or ""),
            "summary": _truncate(payload.get("summary", "") or "", 256),
            "insight": _truncate(payload.get("insight", "") or "", 256),
            "timestamp": event.get("timestamp"),
            "timestamp_ts": ts,
            "timestamp_iso": _to_iso(ts) or "",
            "source_event_ids": list(payload.get("source_event_ids", []) or []),
            "source_memory_ids": list(payload.get("source_memory_ids", []) or []),
            "evidence": _truncate(payload.get("summary", "") or payload.get("insight", "") or "", 256),
            "event_id": eid,
            "event_type": et,
            "raw_payload": payload,
        }
    if et in (
        "integration.goal.created",
        "integration.goal.updated",
        "integration.goal.plan_created",
        "integration.desire.created",
    ):
        is_desire = (et == "integration.desire.created")
        gid = str(payload.get("goal_id", "") or payload.get("desire_id", "") or eid)
        gtype = str(payload.get("goal_type", "") or ("desire" if is_desire else ""))
        topic = str(payload.get("title", "") or payload.get("topic", "") or "")
        status = str(payload.get("status", "") or ("candidate" if is_desire else "active"))
        return {
            "id": f"goal::{gid}",
            "type": NODE_TYPE_GOAL,
            "label": _truncate(topic or payload.get("rationale", "") or "Goal", 128),
            "topic": topic,
            "goal_type": gtype,
            "status": status,
            "lifecycle": str(payload.get("lifecycle", "") or ""),
            "is_desire": is_desire,
            "importance": payload.get("importance", 0.0),
            "confidence": payload.get("confidence", 0.0),
            "timestamp": event.get("timestamp"),
            "timestamp_ts": ts,
            "timestamp_iso": _to_iso(ts) or "",
            "source_event_ids": list(payload.get("source_event_ids", []) or []),
            "source_desire_ids": list(payload.get("source_desire_ids", []) or []),
            "source_interest_ids": list(payload.get("source_interest_ids", []) or []),
            "evidence": _truncate(payload.get("reason", "") or payload.get("rationale", "") or "", 256),
            "event_id": eid,
            "event_type": et,
            "raw_payload": payload,
        }
    return None


# ============================================================
# Provider
# ============================================================

class LifeGraphProvider:
    """
    生命关系图(只读)Provider。

    注入:
        collector: DomainCollector(或任何暴露 list_event_nodes / list_memory_nodes /
                  list_belief_nodes / list_trait_change_nodes 的对象),测试可注入 mock。
    """

    def __init__(self, collector: Optional[Any] = None) -> None:
        self._lock = threading.RLock()
        self._collector = collector
        # 缓存 nodes / edges
        self._cache_nodes: List[Dict[str, Any]] = []
        self._cache_edges: List[Dict[str, Any]] = []
        self._last_build_at: float = 0.0
        self._last_build_ok: bool = False
        self._last_error: str = ""

    def _get_collector(self) -> Any:
        with self._lock:
            if self._collector is not None:
                return self._collector
            try:
                self._collector = DomainCollector()
            except Exception as exc:  # noqa: BLE001
                logger.debug("LifeGraphProvider: 默认 collector 不可用: %s", exc)
                self._collector = None
            return self._collector

    def _refresh(self) -> bool:
        c = self._get_collector()
        if c is None:
            with self._lock:
                self._last_build_ok = False
                self._last_error = "collector_unavailable"
                self._cache_nodes = []
                self._cache_edges = []
            return False
        try:
            event_dicts = _safe_call(c.list_event_nodes, limit=MAX_LIMIT) or []
            mem_list = _safe_call(c.list_memory_nodes, limit=100) or []
            belief_list = _safe_call(c.list_belief_nodes, limit=100) or []
            trait_list = _safe_call(c.list_trait_change_nodes, limit=100) or []
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_build_ok = False
                self._last_error = f"collector_error:{type(exc).__name__}"
            return False
        # 转 node
        nodes: List[Dict[str, Any]] = []
        for ev in event_dicts or []:
            n = _event_to_node(ev)
            if n is not None:
                nodes.append(n)
        for n in mem_list or []:
            if isinstance(n, dict) and n.get("id"):
                # 规范化时间戳
                if "timestamp_ts" not in n:
                    n["timestamp_ts"] = _to_float_ts(n.get("timestamp"))
                n.setdefault("timestamp_iso", _to_iso(n.get("timestamp_ts")) or "")
                nodes.append(n)
        for n in belief_list or []:
            if isinstance(n, dict) and n.get("id"):
                if "timestamp_ts" not in n:
                    n["timestamp_ts"] = _to_float_ts(n.get("timestamp"))
                n.setdefault("timestamp_iso", _to_iso(n.get("timestamp_ts")) or "")
                nodes.append(n)
        for n in trait_list or []:
            if isinstance(n, dict) and n.get("id"):
                if "timestamp_ts" not in n:
                    n["timestamp_ts"] = _to_float_ts(n.get("timestamp"))
                n.setdefault("timestamp_iso", _to_iso(n.get("timestamp_ts")) or "")
                nodes.append(n)
        # 边构建
        edges = self._build_edges(nodes)
        with self._lock:
            self._cache_nodes = nodes
            self._cache_edges = edges
            self._last_build_at = time.time()
            self._last_build_ok = True
            self._last_error = ""
        return True

    def _ensure_loaded(self) -> None:
        with self._lock:
            last = self._last_build_at
        if last == 0.0 or (time.time() - last) > 5.0:
            self._refresh()

    # --------------------------------------------------------
    # 边构建
    # --------------------------------------------------------
    def _build_edges(self, nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        根据 source_event_ids / source_reflection_ids / supporting_signal_ids
        / source_desire_ids / source_belief_ids / source_interest_ids / topic 匹配,
        构建节点之间的有向边。
        """
        edges: List[Tuple[str, str, str, List[str], str]] = []
        # 索引:id -> node
        id_index: Dict[str, Dict[str, Any]] = {}
        # event_id -> node id(用于 source_event_ids 跨域回溯)
        event_id_index: Dict[str, str] = {}
        for n in nodes:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", "") or "")
            if not nid:
                continue
            id_index[nid] = n
            ev_id = str(n.get("event_id", "") or "")
            if ev_id:
                event_id_index[ev_id] = nid
            # memory / belief / trait_change 的原始 id 也可被 source_event_ids 引用
            for alias_key in ("id",):
                pass

        def _add_edge(
            src: str,
            tgt: str,
            rel: str,
            evidence: str = "",
            extra_ids: Optional[List[str]] = None,
        ) -> None:
            if not src or not tgt or src == tgt:
                return
            ev_list: List[str] = []
            if extra_ids:
                for x in extra_ids:
                    if isinstance(x, str) and x:
                        ev_list.append(x)
            edges.append((src, tgt, rel, ev_list, evidence))

        # 1) 通过 source_event_ids 跨域连接
        for n in nodes:
            if not isinstance(n, dict):
                continue
            tgt = str(n.get("id", "") or "")
            if not tgt:
                continue
            ntype = n.get("type", "")
            src_ids = list(n.get("source_event_ids", []) or [])
            for sid in src_ids:
                if not isinstance(sid, str) or not sid:
                    continue
                # 1) sid 是另一个 node 的 event_id
                if sid in event_id_index and event_id_index[sid] != tgt:
                    src = event_id_index[sid]
                    src_type = id_index.get(src, {}).get("type", "")
                    rel = self._infer_relation(src_type, ntype)
                    if rel:
                        _add_edge(src, tgt, rel, evidence=f"source_event_ids:{sid}")
                # 2) sid 是 memory/belief/trait_change 的 id(需要空间匹配)
                if sid in id_index and id_index[sid].get("id") != tgt:
                    src2 = id_index[sid].get("id")
                    src_type2 = id_index[sid].get("type", "")
                    rel2 = self._infer_relation(src_type2, ntype)
                    if rel2:
                        _add_edge(src2, tgt, rel2, evidence=f"source_event_ids:{sid}")

        # 2) reflection -> interest: 通过 supporting_signal_ids 或 source_reflection_ids
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("type") != NODE_TYPE_INTEREST:
                continue
            tgt = str(n.get("id", "") or "")
            srefl = list(n.get("source_reflection_ids", []) or [])
            for rid in srefl:
                if not isinstance(rid, str) or not rid:
                    continue
                # rid 直接就是 reflection 的 id
                src = f"reflection::{rid}"
                if src in id_index:
                    _add_edge(src, tgt, REL_INTEREST_FROM_REFL, evidence=f"source_reflection_ids:{rid}")

        # 3) goal -> action: 通过 supporting_signal_ids 的反向(goal -> action via interest 中转)
        #    我们采用更简单的实现: action -> goal 关系(在第 1 步已通过 source_event_ids 处理)
        #    同时对于 interest -> goal: 通过 source_interest_ids / topic
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("type") != NODE_TYPE_GOAL:
                continue
            tgt = str(n.get("id", "") or "")
            sint = list(n.get("source_interest_ids", []) or [])
            for iid in sint:
                if not isinstance(iid, str) or not iid:
                    continue
                src = f"interest::{iid}"
                if src in id_index:
                    _add_edge(src, tgt, REL_GOAL_FROM_INTEREST, evidence=f"source_interest_ids:{iid}")
            # source_desire_ids 不会直接指向 interest(都指向 desire),不连边
        # 4) action -> goal: 已经有 source_event_ids 路径,无需特化
        # 5) reflection -> belief: 通过 belief.source_reflection_ids
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("type") != NODE_TYPE_BELIEF:
                continue
            tgt = str(n.get("id", "") or "")
            srefl = list(n.get("source_reflection_ids", []) or [])
            for rid in srefl:
                if not isinstance(rid, str) or not rid:
                    continue
                src = f"reflection::{rid}"
                if src in id_index:
                    _add_edge(src, tgt, REL_BELIEF_FROM_REFL, evidence=f"source_reflection_ids:{rid}")
        # 6) belief -> trait_change: 通过 trait_change.source_belief_ids
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if n.get("type") != NODE_TYPE_TRAIT_CHANGE:
                continue
            tgt = str(n.get("id", "") or "")
            sbids = list(n.get("source_belief_ids", []) or [])
            for bid in sbids:
                if not isinstance(bid, str) or not bid:
                    continue
                # belief 节点的 id 是原 belief id(没有 prefix)
                if bid in id_index:
                    _add_edge(bid, tgt, REL_TRAIT_FROM_BELIEF, evidence=f"source_belief_ids:{bid}")

        # 7) topic 模糊连接(作为兜底):同 topic 的相邻类型节点相连
        edges = self._augment_by_topic(nodes, edges, id_index)

        # 去重 + 序列化
        seen: Set[Tuple[str, str, str]] = set()
        out: List[Dict[str, Any]] = []
        for src, tgt, rel, ev_ids, evi in edges:
            key = (src, tgt, rel)
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "source": src,
                "target": tgt,
                "relation": rel,
                "evidence": evi,
                "source_event_ids": ev_ids,
            })
        return out

    def _infer_relation(self, src_type: str, tgt_type: str) -> Optional[str]:
        """根据两个节点类型推断关系类型。"""
        if not src_type or not tgt_type:
            return None
        pair = (src_type, tgt_type)
        table = {
            (NODE_TYPE_MEMORY, NODE_TYPE_REFLECTION): REL_REFL_FROM_MEMORY,
            (NODE_TYPE_REFLECTION, NODE_TYPE_INTEREST): REL_INTEREST_FROM_REFL,
            (NODE_TYPE_INTEREST, NODE_TYPE_GOAL): REL_GOAL_FROM_INTEREST,
            (NODE_TYPE_GOAL, NODE_TYPE_ACTION): REL_ACTION_FROM_GOAL,
            (NODE_TYPE_REFLECTION, NODE_TYPE_BELIEF): REL_BELIEF_FROM_REFL,
            (NODE_TYPE_BELIEF, NODE_TYPE_TRAIT_CHANGE): REL_TRAIT_FROM_BELIEF,
            (NODE_TYPE_MEMORY, NODE_TYPE_INTEREST): REL_TOPIC_LINK,
            (NODE_TYPE_MEMORY, NODE_TYPE_GOAL): REL_TOPIC_LINK,
            (NODE_TYPE_REFLECTION, NODE_TYPE_GOAL): REL_TOPIC_LINK,
            (NODE_TYPE_REFLECTION, NODE_TYPE_ACTION): REL_TOPIC_LINK,
            (NODE_TYPE_INTEREST, NODE_TYPE_ACTION): REL_TOPIC_LINK,
            (NODE_TYPE_INTEREST, NODE_TYPE_TRAIT_CHANGE): REL_TOPIC_LINK,
            (NODE_TYPE_GOAL, NODE_TYPE_BELIEF): REL_TOPIC_LINK,
        }
        return table.get(pair)

    def _augment_by_topic(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Tuple[str, str, str, List[str], str]],
        id_index: Dict[str, Dict[str, Any]],
    ) -> List[Tuple[str, str, str, List[str], str]]:
        """根据 topic 兜底连接,产生 topic_link 关系。"""
        if not nodes:
            return edges
        # 按 topic 索引
        topic_index: Dict[str, List[str]] = {}
        for n in nodes:
            t = _topic_norm(n.get("topic"))
            if not t:
                continue
            topic_index.setdefault(t, []).append(str(n.get("id", "") or ""))
        existing_keys: Set[Tuple[str, str, str]] = set()
        for s, t, r, _, _ in edges:
            existing_keys.add((s, t, r))
        # 已知完整链路的优先序
        priority_pairs = [
            (NODE_TYPE_MEMORY, NODE_TYPE_REFLECTION),
            (NODE_TYPE_MEMORY, NODE_TYPE_INTEREST),
            (NODE_TYPE_MEMORY, NODE_TYPE_GOAL),
            (NODE_TYPE_REFLECTION, NODE_TYPE_INTEREST),
            (NODE_TYPE_INTEREST, NODE_TYPE_GOAL),
            (NODE_TYPE_GOAL, NODE_TYPE_ACTION),
            (NODE_TYPE_REFLECTION, NODE_TYPE_BELIEF),
            (NODE_TYPE_BELIEF, NODE_TYPE_TRAIT_CHANGE),
        ]
        for t_norm, ids in topic_index.items():
            if len(ids) < 2:
                continue
            # 取 topic 内的节点
            id_to_type: Dict[str, str] = {}
            for nid in ids:
                node = id_index.get(nid)
                if node is not None:
                    id_to_type[nid] = str(node.get("type", "") or "")
            # 按 priority pair 顺序配对
            for src_type, tgt_type in priority_pairs:
                src_ids = [nid for nid, nt in id_to_type.items() if nt == src_type]
                tgt_ids = [nid for nid, nt in id_to_type.items() if nt == tgt_type]
                if not src_ids or not tgt_ids:
                    continue
                # 用最新的一条 src 连到最早的一条 tgt(代表 "这个主题促成了那个")
                src = src_ids[0]  # 默认 nodes 已按时序
                tgt = tgt_ids[-1]
                key = (src, tgt, REL_TOPIC_LINK)
                if key in existing_keys:
                    continue
                # 是否已有其它更具体的关系
                has_specific = any(
                    (src == s and tgt == t)
                    for s, t, _ in existing_keys
                )
                if has_specific:
                    continue
                existing_keys.add(key)
                edges.append((src, tgt, REL_TOPIC_LINK, [], f"topic_link:{t_norm[:32]}"))
        return edges

    # --------------------------------------------------------
    # 公开方法
    # --------------------------------------------------------
    def build_graph(
        self,
        *,
        node_limit: int = DEFAULT_GRAPH_LIMIT,
        edge_limit: int = DEFAULT_GRAPH_LIMIT,
        include_node_types: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        构建生命关系图。

        Returns:
            {
                "available": bool,
                "nodes": [{id, type, label, topic, timestamp, timestamp_iso, source_event_ids, evidence, ...}],
                "edges": [{source, target, relation, evidence, source_event_ids}],
                "counts": {by_type: {memory, reflection, interest, action, goal, belief, trait_change}},
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n_limit = max(1, min(MAX_LIMIT, int(node_limit)))
        except (TypeError, ValueError):
            n_limit = DEFAULT_GRAPH_LIMIT
        try:
            e_limit = max(1, min(MAX_LIMIT, int(edge_limit)))
        except (TypeError, ValueError):
            e_limit = DEFAULT_GRAPH_LIMIT
        allowed_types: Optional[Set[str]] = None
        if include_node_types:
            allowed_types = {t for t in include_node_types if t in ALL_NODE_TYPES}
            if not allowed_types:
                allowed_types = None
        self._ensure_loaded()
        with self._lock:
            nodes = list(self._cache_nodes)
            edges = list(self._cache_edges)
            last_ok = self._last_build_ok
            last_err = self._last_error
        if not nodes and not last_ok:
            return {
                "available": False,
                "nodes": [],
                "edges": [],
                "counts": {t: 0 for t in ALL_NODE_TYPES},
                "node_count": 0,
                "edge_count": 0,
                "fallback": True,
                "fallback_reason": last_err or "life_graph_unavailable",
            }
        try:
            # 类型过滤
            if allowed_types is not None:
                nodes = [n for n in nodes if n.get("type") in allowed_types]
                allowed_ids = {str(n.get("id", "") or "") for n in nodes}
                edges = [e for e in edges if e.get("source") in allowed_ids and e.get("target") in allowed_ids]
            # 按时间倒序截取节点(展示优先级:时间近在前)
            nodes_sorted = sorted(
                nodes,
                key=lambda n: (
                    float(n.get("timestamp_ts", 0.0) or 0.0),
                    str(n.get("id", "") or ""),
                ),
                reverse=True,
            )
            top_nodes = nodes_sorted[:n_limit]
            top_ids = {str(n.get("id", "") or "") for n in top_nodes}
            edges = [e for e in edges if e.get("source") in top_ids and e.get("target") in top_ids]
            edges = edges[:e_limit]
            counts: Dict[str, int] = {t: 0 for t in ALL_NODE_TYPES}
            for n in top_nodes:
                t = str(n.get("type", "") or "")
                if t in counts:
                    counts[t] += 1
            # 节点序列化:清洗多余字段
            clean_nodes: List[Dict[str, Any]] = []
            for n in top_nodes:
                clean_nodes.append(self._clean_node_view(n))
            return {
                "available": True,
                "nodes": clean_nodes,
                "edges": edges,
                "counts": counts,
                "node_count": len(clean_nodes),
                "edge_count": len(edges),
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "nodes": [],
                "edges": [],
                "counts": {t: 0 for t in ALL_NODE_TYPES},
                "node_count": 0,
                "edge_count": 0,
                "fallback": True,
                "fallback_reason": f"graph_error:{type(exc).__name__}",
            }

    def get_timeline(self, limit: int = DEFAULT_TIMELINE_LIMIT) -> Dict[str, Any]:
        """
        生命时间线:按时间升序合并所有节点,附带 summary。

        Returns:
            {
                "available": bool,
                "total": int,
                "items": [
                    {node, ts, ts_iso, day_bucket, summary, kind},
                    ...
                ],
                "buckets": {day: count},
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        try:
            n = max(1, min(MAX_LIMIT, int(limit)))
        except (TypeError, ValueError):
            n = DEFAULT_TIMELINE_LIMIT
        self._ensure_loaded()
        with self._lock:
            nodes = list(self._cache_nodes)
            last_ok = self._last_build_ok
            last_err = self._last_error
        if not nodes and not last_ok:
            return {
                "available": False,
                "total": 0,
                "items": [],
                "buckets": {},
                "fallback": True,
                "fallback_reason": last_err or "life_graph_unavailable",
            }
        try:
            # 升序
            nodes_sorted = sorted(
                nodes,
                key=lambda x: (
                    float(x.get("timestamp_ts", 0.0) or 0.0),
                    str(x.get("id", "") or ""),
                ),
            )
            items: List[Dict[str, Any]] = []
            buckets: Dict[str, int] = {}
            for node in nodes_sorted:
                ts = float(node.get("timestamp_ts", 0.0) or 0.0)
                iso = _to_iso(ts) or str(node.get("timestamp", "") or "")
                day = iso[:10] if isinstance(iso, str) and len(iso) >= 10 else ""
                kind = str(node.get("type", "") or "")
                summary = self._one_line_summary(node)
                item = {
                    "id": str(node.get("id", "") or ""),
                    "type": kind,
                    "label": str(node.get("label", "") or ""),
                    "topic": str(node.get("topic", "") or ""),
                    "timestamp": iso,
                    "timestamp_ts": ts,
                    "day_bucket": day,
                    "summary": summary,
                    "source_event_ids": list(node.get("source_event_ids", []) or []),
                    "evidence": str(node.get("evidence", "") or ""),
                }
                items.append(item)
                if day:
                    buckets[day] = buckets.get(day, 0) + 1
                if len(items) >= n:
                    break
            return {
                "available": True,
                "total": len(nodes),
                "returned": len(items),
                "items": items,
                "buckets": buckets,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "total": 0,
                "items": [],
                "buckets": {},
                "fallback": True,
                "fallback_reason": f"timeline_error:{type(exc).__name__}",
            }

    def find_path(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 8,
    ) -> Dict[str, Any]:
        """
        在图上找 from_id -> to_id 的最短形成路径(BFS)。

        Returns:
            {
                "available": bool,
                "found": bool,
                "from": str,
                "to": str,
                "depth": int,
                "path": [
                    {"id", "type", "label", "topic", "timestamp", "via_relation"},
                    ...
                ],
                "edges_used": [{source, target, relation, evidence}],
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not isinstance(from_id, str) or not from_id or not isinstance(to_id, str) or not to_id:
            return {
                "available": False,
                "found": False,
                "from": str(from_id or ""),
                "to": str(to_id or ""),
                "depth": 0,
                "path": [],
                "edges_used": [],
                "fallback": True,
                "fallback_reason": "invalid_input",
            }
        self._ensure_loaded()
        with self._lock:
            nodes = list(self._cache_nodes)
            edges = list(self._cache_edges)
            last_ok = self._last_build_ok
            last_err = self._last_error
        if not nodes and not last_ok:
            return {
                "available": False,
                "found": False,
                "from": from_id,
                "to": to_id,
                "depth": 0,
                "path": [],
                "edges_used": [],
                "fallback": True,
                "fallback_reason": last_err or "life_graph_unavailable",
            }
        # 邻接表(双向都加,允许反向 BFS)
        adj: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {n.get("id"): [] for n in nodes}
        for e in edges:
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            if s in adj and t in adj:
                adj[s].append((t, e))
                adj[t].append((s, {
                    "source": t,
                    "target": s,
                    "relation": "reverse_" + str(e.get("relation", "")),
                    "evidence": "reverse_path",
                    "source_event_ids": list(e.get("source_event_ids", []) or []),
                }))
        # 起点终点支持多个别名(用户可能传 raw id 而不是 prefix)
        from_candidates = self._resolve_id_candidates(from_id, nodes)
        to_candidates = set(self._resolve_id_candidates(to_id, nodes))
        if not from_candidates or not to_candidates:
            return {
                "available": True,
                "found": False,
                "from": from_id,
                "to": to_id,
                "depth": 0,
                "path": [],
                "edges_used": [],
                "fallback": False,
                "fallback_reason": "id_not_found",
            }
        try:
            # BFS
            md = max(1, min(16, int(max_depth)))
        except (TypeError, ValueError):
            md = 8
        try:
            visited: Set[str] = set()
            # 路径回溯
            parent: Dict[str, Tuple[str, Dict[str, Any]]] = {}
            queue: Deque[str] = deque()
            for cand in from_candidates:
                if cand not in visited:
                    visited.add(cand)
                    queue.append(cand)
                    parent[cand] = ("__start__", {"relation": "start", "evidence": "", "source_event_ids": []})
            found_target: Optional[str] = None
            while queue and found_target is None:
                if len(visited) > 5000:
                    break
                cur = queue.popleft()
                if cur in to_candidates:
                    found_target = cur
                    break
                # 限制深度
                depth_so_far = self._reconstruct_depth(cur, parent, nodes)
                if depth_so_far >= md:
                    continue
                for nxt, e in adj.get(cur, []):
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    parent[nxt] = (cur, e)
                    if nxt in to_candidates:
                        found_target = nxt
                        break
                    queue.append(nxt)
            if found_target is None:
                return {
                    "available": True,
                    "found": False,
                    "from": from_id,
                    "to": to_id,
                    "depth": 0,
                    "path": [],
                    "edges_used": [],
                    "fallback": False,
                    "fallback_reason": "no_path",
                }
            # 回溯
            path_ids: List[str] = [found_target]
            edges_used: List[Dict[str, Any]] = []
            cur = found_target
            while cur in parent and parent[cur][0] != "__start__":
                prev, e = parent[cur]
                edges_used.append(e)
                path_ids.append(prev)
                cur = prev
            path_ids.reverse()
            edges_used.reverse()
            id_to_node = {n.get("id"): n for n in nodes if n.get("id") is not None}
            path_view: List[Dict[str, Any]] = []
            for i, nid in enumerate(path_ids):
                n = id_to_node.get(nid, {})
                via = ""
                if i < len(edges_used):
                    via = str(edges_used[i].get("relation", "") or "")
                path_view.append({
                    "id": nid,
                    "type": n.get("type", ""),
                    "label": n.get("label", ""),
                    "topic": n.get("topic", ""),
                    "timestamp": n.get("timestamp_iso", "") or n.get("timestamp", ""),
                    "via_relation": via,
                })
            # 边方向:按正向展示
            forward_edges: List[Dict[str, Any]] = []
            for e in edges_used:
                rel = str(e.get("relation", "") or "")
                if rel.startswith("reverse_"):
                    rel2 = rel[len("reverse_"):]
                    forward_edges.append({
                        "source": e.get("target"),
                        "target": e.get("source"),
                        "relation": rel2,
                        "evidence": e.get("evidence", ""),
                        "source_event_ids": list(e.get("source_event_ids", []) or []),
                    })
                else:
                    forward_edges.append({
                        "source": e.get("source"),
                        "target": e.get("target"),
                        "relation": rel,
                        "evidence": e.get("evidence", ""),
                        "source_event_ids": list(e.get("source_event_ids", []) or []),
                    })
            return {
                "available": True,
                "found": True,
                "from": from_id,
                "to": to_id,
                "depth": len(path_ids) - 1,
                "path": path_view,
                "edges_used": forward_edges,
                "fallback": False,
                "fallback_reason": None,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "available": False,
                "found": False,
                "from": from_id,
                "to": to_id,
                "depth": 0,
                "path": [],
                "edges_used": [],
                "fallback": True,
                "fallback_reason": f"path_error:{type(exc).__name__}",
            }

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------
    def _clean_node_view(self, n: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(n, dict):
            return {}
        keep_keys = (
            "id", "type", "label", "topic", "trend", "action_type", "status",
            "urgency", "expected_value", "confidence", "priority", "reflection_type",
            "depth", "summary", "insight", "goal_type", "lifecycle", "is_desire",
            "importance", "trait_name", "delta", "old_value", "new_value", "domain",
            "filter_reason", "filter_policy", "source_event_ids",
            "source_reflection_ids", "supporting_signal_ids", "source_desire_ids",
            "source_interest_ids", "source_belief_ids", "source_memory_ids",
            "timestamp", "timestamp_ts", "timestamp_iso", "evidence",
            "event_id", "event_type",
        )
        out: Dict[str, Any] = {}
        for k in keep_keys:
            if k in n:
                out[k] = n[k]
        return out

    def _one_line_summary(self, node: Dict[str, Any]) -> str:
        if not isinstance(node, dict):
            return ""
        t = node.get("type", "")
        topic = str(node.get("topic", "") or "")
        label = str(node.get("label", "") or "")
        if t == NODE_TYPE_MEMORY:
            return f"记忆:{topic or label}"
        if t == NODE_TYPE_REFLECTION:
            return f"反思:{topic or label}"
        if t == NODE_TYPE_INTEREST:
            trend = node.get("trend", "")
            return f"兴趣{str(trend) and '·' + str(trend)}:{topic or label}"
        if t == NODE_TYPE_ACTION:
            at = node.get("action_type", "")
            return f"行动{str(at) and '·' + str(at)}:{topic or label}"
        if t == NODE_TYPE_GOAL:
            return f"目标:{topic or label}"
        if t == NODE_TYPE_BELIEF:
            return f"信念:{topic or label}"
        if t == NODE_TYPE_TRAIT_CHANGE:
            tn = node.get("trait_name", "")
            return f"特质变化:{str(tn) or label}"
        return label

    def _resolve_id_candidates(self, raw_id: str, nodes: List[Dict[str, Any]]) -> List[str]:
        """
        把用户输入的 id 解析为图中的实际 node id(支持多别名)。
        """
        if not raw_id:
            return []
        # 完全匹配
        direct = [n.get("id") for n in nodes if n.get("id") == raw_id]
        if direct:
            return [d for d in direct if d]
        # 不带 prefix 的话,尝试加 prefix
        cands: List[str] = []
        for n in nodes:
            nid = str(n.get("id", "") or "")
            if "::" in nid:
                prefix, tail = nid.split("::", 1)
                if tail == raw_id or prefix + "::" + raw_id == nid:
                    cands.append(nid)
            else:
                if nid == raw_id:
                    cands.append(nid)
        # 尝试作为 event_id 查找
        for n in nodes:
            if n.get("event_id") == raw_id:
                cands.append(n.get("id"))
        # 去重
        seen: Set[str] = set()
        out: List[str] = []
        for c in cands:
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def _reconstruct_depth(
        self,
        target: str,
        parent: Dict[str, Tuple[str, Dict[str, Any]]],
        nodes: List[Dict[str, Any]],
    ) -> int:
        depth = 0
        cur = target
        while cur in parent and parent[cur][0] != "__start__":
            cur = parent[cur][0]
            depth += 1
            if depth > 32:
                break
        return depth

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "node_count": len(self._cache_nodes),
                "edge_count": len(self._cache_edges),
                "last_build_at": self._last_build_at,
                "last_build_ok": self._last_build_ok,
                "last_error": self._last_error,
            }

    # --------------------------------------------------------
    # Step 8.4.2 —— 节点详情 & 邻居查询
    # --------------------------------------------------------
    def get_node_detail(self, node_id: str) -> Dict[str, Any]:
        """
        获取节点的完整详情,包含 evidence 与邻居预览。

        Returns:
            {
                "node": {id, type, title, summary, timestamp, importance, ...},
                "evidence": [{source_type, source_id, reason}, ...],
                "neighbors": [edge summaries ...],
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not isinstance(node_id, str) or not node_id:
            return {
                "node": {},
                "evidence": [],
                "neighbors": [],
                "fallback": True,
                "fallback_reason": "invalid_input",
            }
        self._ensure_loaded()
        with self._lock:
            nodes = list(self._cache_nodes)
            edges = list(self._cache_edges)
            last_ok = self._last_build_ok
            last_err = self._last_error
        if not nodes and not last_ok:
            return {
                "node": {},
                "evidence": [],
                "neighbors": [],
                "fallback": True,
                "fallback_reason": last_err or "life_graph_unavailable",
            }
        # 解析 id(支持别名 / event_id)
        candidates = self._resolve_id_candidates(node_id, nodes)
        if not candidates:
            return {
                "node": {},
                "evidence": [],
                "neighbors": [],
                "fallback": True,
                "fallback_reason": "node_not_found",
            }
        target_id = candidates[0]
        id_to_node = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id")}
        target = id_to_node.get(target_id) or {}
        # 构造详情
        view = self._clean_node_view(target)
        # title:优先 label,其次 topic,其次 id
        title = (
            str(view.get("label") or view.get("topic") or target_id)[:128]
        )
        node_view: Dict[str, Any] = dict(view)
        node_view["title"] = title
        node_view["id"] = target_id
        # 构造 evidence
        evidence: List[Dict[str, Any]] = []
        for eid in list(target.get("source_event_ids") or []):
            if isinstance(eid, str) and eid:
                evidence.append({
                    "source_type": "event",
                    "source_id": eid,
                    "reason": f"event_id 来自 {view.get('event_type') or 'IntegrationEvent'}",
                })
        for rid in list(target.get("source_reflection_ids") or []):
            if isinstance(rid, str) and rid:
                evidence.append({
                    "source_type": "reflection",
                    "source_id": f"reflection::{rid}",
                    "reason": "上游反思",
                })
        for mid in list(target.get("source_memory_ids") or []):
            if isinstance(mid, str) and mid:
                evidence.append({
                    "source_type": "memory",
                    "source_id": f"memory::{mid}",
                    "reason": "上游记忆",
                })
        for bid in list(target.get("source_belief_ids") or []):
            if isinstance(bid, str) and bid:
                evidence.append({
                    "source_type": "belief",
                    "source_id": bid,
                    "reason": "上游信念",
                })
        for iid in list(target.get("source_interest_ids") or []):
            if isinstance(iid, str) and iid:
                evidence.append({
                    "source_type": "interest",
                    "source_id": f"interest::{iid}",
                    "reason": "上游兴趣",
                })
        for sid in list(target.get("supporting_signal_ids") or []):
            if isinstance(sid, str) and sid:
                evidence.append({
                    "source_type": "interest",
                    "source_id": f"interest::{sid}",
                    "reason": "支持信号",
                })
        for did in list(target.get("source_desire_ids") or []):
            if isinstance(did, str) and did:
                evidence.append({
                    "source_type": "goal",
                    "source_id": f"goal::{did}",
                    "reason": "上游 desire",
                })
        # 若 evidence 为空,使用 evidence 字段(legacy)
        if not evidence and target.get("evidence"):
            evidence.append({
                "source_type": "summary",
                "source_id": "",
                "reason": str(target.get("evidence") or "")[:256],
            })
        # 邻居预览(仅一层)
        neighbors: List[Dict[str, Any]] = []
        for e in edges:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            if s == target_id or t == target_id:
                other_id = t if s == target_id else s
                direction = "out" if s == target_id else "in"
                other = id_to_node.get(other_id, {})
                neighbors.append({
                    "neighbor_id": other_id,
                    "neighbor_type": other.get("type", ""),
                    "neighbor_label": other.get("label", other.get("topic", "")),
                    "relation": e.get("relation", ""),
                    "direction": direction,
                    "evidence": e.get("evidence", ""),
                })
        return {
            "node": node_view,
            "evidence": evidence,
            "neighbors": neighbors,
            "fallback": False,
            "fallback_reason": None,
        }

    def get_neighbors(
        self,
        node_id: str,
        depth: int = DEFAULT_NEIGHBOR_DEPTH,
    ) -> Dict[str, Any]:
        """
        BFS 获取 node_id 半径为 depth 的邻居子图。
        depth 必须 1 ≤ depth ≤ MAX_NEIGHBOR_DEPTH=3。

        Returns:
            {
                "center": str,
                "nodes": [node_view, ...],
                "edges": [edge_view, ...],
                "depth": int,
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        if not isinstance(node_id, str) or not node_id:
            return {
                "center": str(node_id or ""),
                "nodes": [],
                "edges": [],
                "depth": 0,
                "fallback": True,
                "fallback_reason": "invalid_input",
            }
        try:
            d = int(depth)
        except (TypeError, ValueError):
            d = DEFAULT_NEIGHBOR_DEPTH
        if d < 1:
            d = 1
        if d > MAX_NEIGHBOR_DEPTH:
            return {
                "center": node_id,
                "nodes": [],
                "edges": [],
                "depth": d,
                "fallback": True,
                "fallback_reason": "depth_limit",
            }
        self._ensure_loaded()
        with self._lock:
            nodes = list(self._cache_nodes)
            edges = list(self._cache_edges)
            last_ok = self._last_build_ok
            last_err = self._last_error
        if not nodes and not last_ok:
            return {
                "center": node_id,
                "nodes": [],
                "edges": [],
                "depth": d,
                "fallback": True,
                "fallback_reason": last_err or "life_graph_unavailable",
            }
        # 解析 id
        candidates = self._resolve_id_candidates(node_id, nodes)
        if not candidates:
            return {
                "center": node_id,
                "nodes": [],
                "edges": [],
                "depth": d,
                "fallback": True,
                "fallback_reason": "node_not_found",
            }
        center_id = candidates[0]
        id_to_node = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("id")}
        # 邻接表
        adj: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {n.get("id"): [] for n in nodes}
        for e in edges:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            if s in adj and t in adj:
                adj[s].append((t, e))
                adj[t].append((s, {
                    "source": t,
                    "target": s,
                    "relation": "reverse_" + str(e.get("relation", "")),
                    "evidence": "reverse_path",
                    "source_event_ids": list(e.get("source_event_ids", []) or []),
                }))
        # BFS 按 depth 扩展
        visited: Set[str] = {center_id}
        frontier: List[str] = [center_id]
        collected_edges: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for _ in range(d):
            next_frontier: List[str] = []
            for cur in frontier:
                for nxt, e in adj.get(cur, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        next_frontier.append(nxt)
                    if isinstance(e, dict):
                        key = (
                            str(e.get("source", "") or ""),
                            str(e.get("target", "") or ""),
                            str(e.get("relation", "") or ""),
                        )
                        if key not in collected_edges:
                            collected_edges[key] = e
            if not next_frontier:
                break
            frontier = next_frontier
        if len(visited) > NEIGHBOR_NODE_LIMIT:
            # 截断(只保留前 NEIGHBOR_NODE_LIMIT 个)
            keep = list(visited)[:NEIGHBOR_NODE_LIMIT]
            visited = set(keep)
            if center_id not in visited:
                visited.add(center_id)
        # 构造 nodes
        out_nodes: List[Dict[str, Any]] = []
        for nid in visited:
            n = id_to_node.get(nid)
            if not n:
                continue
            v = self._clean_node_view(n)
            v["is_center"] = (nid == center_id)
            out_nodes.append(v)
        # 构造 edges(只保留 visited 内)
        out_edges: List[Dict[str, Any]] = []
        for k, e in collected_edges.items():
            s, t, rel = k
            if s in visited and t in visited:
                out_edges.append({
                    "source": s,
                    "target": t,
                    "relation": rel,
                    "evidence": e.get("evidence", ""),
                    "source_event_ids": list(e.get("source_event_ids", []) or []),
                })
        return {
            "center": center_id,
            "nodes": out_nodes,
            "edges": out_edges,
            "depth": d,
            "fallback": False,
            "fallback_reason": None,
        }


# ============================================================
# 模块级单例
# ============================================================
_provider_instance: Optional[LifeGraphProvider] = None
_provider_lock = threading.Lock()


def get_life_graph_provider() -> LifeGraphProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = LifeGraphProvider()
    return _provider_instance


def reset_life_graph_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "LifeGraphProvider",
    "DomainCollector",
    "EventHubDomainCollector",
    "get_life_graph_provider",
    "reset_life_graph_provider_for_testing",
    "NODE_TYPE_MEMORY",
    "NODE_TYPE_REFLECTION",
    "NODE_TYPE_INTEREST",
    "NODE_TYPE_ACTION",
    "NODE_TYPE_GOAL",
    "NODE_TYPE_BELIEF",
    "NODE_TYPE_TRAIT_CHANGE",
    "ALL_NODE_TYPES",
    "ALL_RELATIONS",
    "INTEGRATION_LIFEGRAH_EVENT_TYPES",
    "MAX_NEIGHBOR_DEPTH",
    "DEFAULT_NEIGHBOR_DEPTH",
    "NEIGHBOR_NODE_LIMIT",
]
