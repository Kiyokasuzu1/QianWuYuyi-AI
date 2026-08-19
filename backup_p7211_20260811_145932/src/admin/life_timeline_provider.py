# -*- coding: utf-8 -*-
"""
src/admin/life_timeline_provider.py

Phase 5.0 Dashboard Upgrade Step 8.4.3 —— LifeTimelineProvider。

职责:
- 将以下七大领域统一映射到时间轴:
    Memory / Reflection / Interest / Action / Goal / Belief / TraitChange
- 形成 "Seven Lane Growth Timeline"(七泳道成长时间线)
- 不重复推理因果,优先复用 LifeGraphProvider 的边
- 识别成长里程碑(milestone),不创造成长,只识别已有事件关系

严格只读 / 不调用 LLM / 不修改业务状态 / 不重新计算成长结果。

数据流:
    Frontend
        ↓
    LifeTimelineRouter
        ↓
    LifeTimelineProvider
        ↓
    LifeGraphProvider(只读) + EventHub(只读)
"""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 七泳道固定顺序(下游 → 上游,展示顺序:时间起点 → 终点)
LANE_TYPES: Tuple[str, ...] = (
    "memory",
    "reflection",
    "interest",
    "action",
    "goal",
    "belief",
    "trait_change",
)

LANE_LABELS: Dict[str, str] = {
    "memory": "Memory · 记忆",
    "reflection": "Reflection · 反思",
    "interest": "Interest · 兴趣",
    "action": "Action · 行动",
    "goal": "Goal · 目标",
    "belief": "Belief · 信念",
    "trait_change": "TraitChange · 特质变化",
}

# 时间范围常量(秒)
RANGE_SECONDS: Dict[str, int] = {
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
    "90d": 90 * 24 * 60 * 60,
    "all": -1,  # -1 = 不限制
}
ALL_RANGES: Tuple[str, ...] = ("24h", "7d", "30d", "90d", "all")
DEFAULT_RANGE: str = "all"

# 关系 → 可视化标签
RELATION_LABELS: Dict[str, str] = {
    "memory_to_reflection": "记忆→反思",
    "reflection_to_interest": "反思→兴趣",
    "interest_to_goal": "兴趣→目标",
    "goal_to_action": "目标→行动",
    "reflection_to_belief": "反思→信念",
    "belief_to_trait_change": "信念→特质",
    "topic_link": "主题关联",
}

# Milestone 类型
MILESTONE_INTEREST_TO_GOAL = "interest_to_goal"
MILESTONE_REFLECTION_TURNING_POINT = "reflection_turning_point"
MILESTONE_BELIEF_CHANGE = "belief_change"
ALL_MILESTONE_TYPES: Tuple[str, ...] = (
    MILESTONE_INTEREST_TO_GOAL,
    MILESTONE_REFLECTION_TURNING_POINT,
    MILESTONE_BELIEF_CHANGE,
)

# 反射 turning_point:关联 memory 数 >= 阈值
TURNING_POINT_MIN_MEMORIES = 2

# 单条 lane 最多 item 数(防止 OOM)
DEFAULT_LANE_ITEM_LIMIT = 500
# 单次 milestone 数量上限
DEFAULT_MILESTONE_LIMIT = 50


# ============================================================
# 工具
# ============================================================

def _to_float_ts(value: Any) -> float:
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


def _resolve_range_seconds(range_key: Any) -> Tuple[int, bool]:
    """
    将 range 字符串解析为秒数。
    Returns: (seconds, is_fallback)
    """
    if not isinstance(range_key, str) or not range_key.strip():
        return 0, True
    k = range_key.strip().lower()
    if k in RANGE_SECONDS:
        sec = RANGE_SECONDS[k]
        return sec, False
    return 0, True


# ============================================================
# Provider
# ============================================================

class LifeTimelineProvider:
    """
    七泳道成长时间线 Provider(只读)。

    注入:
        graph_provider: LifeGraphProvider(可选,None 时使用全局单例)
    """

    def __init__(self, graph_provider: Optional[Any] = None) -> None:
        self._graph_provider = graph_provider

    def _get_graph(self) -> Any:
        if self._graph_provider is not None:
            return self._graph_provider
        try:
            from src.admin.life_graph_provider import get_life_graph_provider
            self._graph_provider = get_life_graph_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeTimelineProvider: LifeGraphProvider 不可用: %s", exc)
            self._graph_provider = None
        return self._graph_provider

    # --------------------------------------------------------
    # 内部:从 LifeGraphProvider 拿数据
    # --------------------------------------------------------
    def _fetch_graph_data(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool, str]:
        provider = self._get_graph()
        if provider is None:
            return [], [], False, "life_graph_unavailable"
        try:
            provider._ensure_loaded()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            return [], [], False, f"ensure_loaded_error:{type(exc).__name__}"
        try:
            with provider._lock:  # noqa: SLF001
                nodes = list(provider._cache_nodes)  # noqa: SLF001
                edges = list(provider._cache_edges)  # noqa: SLF001
                last_ok = provider._last_build_ok  # noqa: SLF001
                last_err = provider._last_error  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            return [], [], False, f"provider_lock_error:{type(exc).__name__}"
        if not nodes and not last_ok:
            return [], [], False, last_err or "life_graph_unavailable"
        return nodes, edges, True, ""

    def _filter_by_range(
        self,
        nodes: List[Dict[str, Any]],
        range_seconds: int,
    ) -> List[Dict[str, Any]]:
        """根据 range 过滤节点。range_seconds = -1 表示不限。"""
        if range_seconds < 0:
            return list(nodes)
        now = time.time()
        cutoff = now - float(range_seconds)
        out: List[Dict[str, Any]] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            ts = _to_float_ts(n.get("timestamp") or n.get("timestamp_ts"))
            if ts <= 0.0:
                # 无时间戳的节点保留(可能很重要)
                out.append(n)
                continue
            if ts >= cutoff:
                out.append(n)
        return out

    # --------------------------------------------------------
    # 接口 1:get_timeline
    # --------------------------------------------------------
    def get_timeline(
        self,
        *,
        range_key: str = DEFAULT_RANGE,
        lane_item_limit: int = DEFAULT_LANE_ITEM_LIMIT,
    ) -> Dict[str, Any]:
        """
        获取七泳道成长时间线。

        Returns:
            {
                "range": {"start": iso|None, "end": iso|None, "key": "30d"},
                "lanes": [
                    {"type": "memory", "label": "Memory · 记忆", "items": [...]},
                    ...
                ],
                "edges": [{"from": "...", "to": "...", "relation": "...", "confidence": 0.x}],
                "milestones": [...],  # 简短摘要,详情见 get_milestones()
                "counts": {"memory": N, "reflection": N, ...},
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        range_seconds, invalid = _resolve_range_seconds(range_key)
        nodes, edges, ok, err = self._fetch_graph_data()
        if not ok:
            return {
                "range": {"start": None, "end": None, "key": str(range_key or ""), "seconds": 0},
                "lanes": self._empty_lanes(),
                "edges": [],
                "milestones": [],
                "counts": {t: 0 for t in LANE_TYPES},
                "fallback": True,
                "fallback_reason": err or "life_graph_unavailable",
            }
        # 时间过滤
        filtered = self._filter_by_range(nodes, range_seconds)
        # range 描述
        range_view = self._build_range_view(filtered, range_key, range_seconds, invalid)
        # 分 lane
        lanes: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {t: 0 for t in LANE_TYPES}
        for lane_type in LANE_TYPES:
            lane_items = self._build_lane_items(filtered, lane_type, lane_item_limit)
            lanes.append({
                "type": lane_type,
                "label": LANE_LABELS.get(lane_type, lane_type),
                "items": lane_items,
            })
            counts[lane_type] = len(lane_items)
        # 因果边(来自 LifeGraphProvider)
        causal_edges = self._build_causal_edges(filtered, edges)
        # 里程碑(简短)
        milestones = self._detect_milestones(filtered, edges, limit=10)
        return {
            "range": range_view,
            "lanes": lanes,
            "edges": causal_edges,
            "milestones": milestones,
            "counts": counts,
            "fallback": False,
            "fallback_reason": None,
        }

    def _build_range_view(
        self,
        filtered: List[Dict[str, Any]],
        range_key: Any,
        range_seconds: int,
        invalid: bool,
    ) -> Dict[str, Any]:
        if invalid:
            return {
                "start": None,
                "end": None,
                "key": str(range_key or ""),
                "seconds": 0,
                "fallback": True,
                "fallback_reason": "invalid_range",
            }
        if not filtered:
            return {
                "start": None,
                "end": None,
                "key": str(range_key or DEFAULT_RANGE),
                "seconds": range_seconds,
            }
        ts_list = sorted(
            _to_float_ts(n.get("timestamp") or n.get("timestamp_ts"))
            for n in filtered
            if isinstance(n, dict)
        )
        ts_list = [t for t in ts_list if t > 0.0]
        if not ts_list:
            return {
                "start": None,
                "end": None,
                "key": str(range_key or DEFAULT_RANGE),
                "seconds": range_seconds,
            }
        return {
            "start": _to_iso(ts_list[0]),
            "end": _to_iso(ts_list[-1]),
            "key": str(range_key or DEFAULT_RANGE),
            "seconds": range_seconds,
        }

    def _build_lane_items(
        self,
        nodes: List[Dict[str, Any]],
        lane_type: str,
        limit: int,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            if str(n.get("type", "") or "") != lane_type:
                continue
            ts = _to_float_ts(n.get("timestamp") or n.get("timestamp_ts"))
            iso = _to_iso(ts) or str(n.get("timestamp") or "")
            items.append({
                "id": str(n.get("id", "") or ""),
                "type": lane_type,
                "timestamp": iso,
                "timestamp_ts": ts,
                "title": _truncate(n.get("label") or n.get("topic") or n.get("id") or "", 128),
                "summary": _truncate(n.get("evidence") or n.get("summary") or n.get("insight") or "", 256),
                "topic": str(n.get("topic", "") or ""),
                "importance": float(n.get("importance", 0.0) or 0.0) if lane_type == "memory" else 0.0,
                "confidence": float(n.get("confidence", 0.0) or 0.0) if lane_type in ("belief", "interest", "action", "goal") else 0.0,
                "delta": float(n.get("delta", 0.0) or 0.0) if lane_type == "trait_change" else 0.0,
                "source_event_ids": list(n.get("source_event_ids", []) or []),
            })
        items.sort(key=lambda x: (float(x.get("timestamp_ts") or 0.0), str(x.get("id") or "")))
        return items[: max(0, int(limit))]

    def _empty_lanes(self) -> List[Dict[str, Any]]:
        return [
            {"type": t, "label": LANE_LABELS.get(t, t), "items": []}
            for t in LANE_TYPES
        ]

    def _build_causal_edges(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """将 LifeGraphProvider 的边整理为可被时间线使用的因果边。"""
        node_ids: Set[str] = set()
        for n in nodes:
            if isinstance(n, dict) and n.get("id"):
                node_ids.add(str(n.get("id")))
        out: List[Dict[str, Any]] = []
        for e in edges or []:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            if not s or not t:
                continue
            if s not in node_ids or t not in node_ids:
                continue
            rel = str(e.get("relation", "") or "")
            # 计算 confidence(简化:基于 source_event_ids 数量)
            ev_ids = list(e.get("source_event_ids", []) or [])
            conf = 0.5 + min(0.45, 0.15 * len(ev_ids))
            out.append({
                "from": s,
                "to": t,
                "relation": rel,
                "relation_label": RELATION_LABELS.get(rel, rel),
                "confidence": round(float(conf), 3),
            })
        return out

    # --------------------------------------------------------
    # 接口 2:get_lanes
    # --------------------------------------------------------
    def get_lanes(self) -> List[Dict[str, Any]]:
        """返回七条泳道定义(包含 count)。"""
        nodes, edges, ok, err = self._fetch_graph_data()
        counts: Dict[str, int] = {t: 0 for t in LANE_TYPES}
        if ok:
            for n in nodes or []:
                if not isinstance(n, dict):
                    continue
                t = str(n.get("type", "") or "")
                if t in counts:
                    counts[t] += 1
        out: List[Dict[str, Any]] = []
        for lane_type in LANE_TYPES:
            out.append({
                "id": lane_type,
                "type": lane_type,
                "label": LANE_LABELS.get(lane_type, lane_type),
                "count": counts.get(lane_type, 0),
            })
        return out

    # --------------------------------------------------------
    # 接口 3:get_causality
    # --------------------------------------------------------
    def get_causality(
        self,
        start_time: Any = None,
        end_time: Any = None,
    ) -> Dict[str, Any]:
        """
        返回时间范围[start_time, end_time) 内的因果边。
        - start_time / end_time 可以是 epoch 浮点、ISO 字符串或 None
        - None 表示不限制

        Returns:
            {
                "edges": [{"from", "to", "relation", "confidence"}],
                "range": {"start": iso|None, "end": iso|None},
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        start_ts = _to_float_ts(start_time)
        end_ts = _to_float_ts(end_time) if end_time is not None else 0.0
        nodes, edges, ok, err = self._fetch_graph_data()
        if not ok:
            return {
                "edges": [],
                "range": {
                    "start": _to_iso(start_ts) if start_ts > 0 else None,
                    "end": _to_iso(end_ts) if end_ts > 0 else None,
                },
                "count": 0,
                "fallback": True,
                "fallback_reason": err or "life_graph_unavailable",
            }
        # 收集每个节点的 timestamp
        id_to_ts: Dict[str, float] = {}
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            nid = str(n.get("id", "") or "")
            if not nid:
                continue
            id_to_ts[nid] = _to_float_ts(n.get("timestamp") or n.get("timestamp_ts"))
        # 过滤边:两端的节点都在时间范围内
        out_edges: List[Dict[str, Any]] = []
        for e in edges or []:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            if not s or not t:
                continue
            s_ts = id_to_ts.get(s, 0.0)
            t_ts = id_to_ts.get(t, 0.0)
            # 时间过滤(对两端都生效)
            if start_ts > 0.0:
                if (s_ts > 0.0 and s_ts < start_ts) and (t_ts > 0.0 and t_ts < start_ts):
                    continue
            if end_ts > 0.0:
                if (s_ts > 0.0 and s_ts > end_ts) and (t_ts > 0.0 and t_ts > end_ts):
                    continue
            rel = str(e.get("relation", "") or "")
            ev_ids = list(e.get("source_event_ids", []) or [])
            conf = 0.5 + min(0.45, 0.15 * len(ev_ids))
            out_edges.append({
                "from": s,
                "to": t,
                "relation": rel,
                "relation_label": RELATION_LABELS.get(rel, rel),
                "confidence": round(float(conf), 3),
                "from_timestamp": _to_iso(s_ts) if s_ts > 0.0 else None,
                "to_timestamp": _to_iso(t_ts) if t_ts > 0.0 else None,
            })
        return {
            "edges": out_edges,
            "range": {
                "start": _to_iso(start_ts) if start_ts > 0.0 else None,
                "end": _to_iso(end_ts) if end_ts > 0.0 else None,
            },
            "count": len(out_edges),
            "fallback": False,
            "fallback_reason": None,
        }

    # --------------------------------------------------------
    # 接口 4:get_milestones
    # --------------------------------------------------------
    def get_milestones(self, limit: int = DEFAULT_MILESTONE_LIMIT) -> Dict[str, Any]:
        """
        检测成长里程碑(不创造成长,只识别已有事件关系)。

        规则:
        A. interest_to_goal
           - 存在边 interest::X → goal::Y (REL_INTEREST_TO_GOAL 关系)
        B. reflection_turning_point
           - 反思节点关联多个 memory
           - 取 source_memory_ids 数量 >= TURNING_POINT_MIN_MEMORIES
        C. belief_change
           - 存在边 belief::X → trait_change::Y (REL_TRAIT_FROM_BELIEF 关系)

        Returns:
            {
                "milestones": [
                    {
                        "id": "milestone_...",
                        "type": "interest_to_goal",
                        "title": "兴趣形成目标",
                        "timestamp": iso,
                        "chain": ["interest", "goal"],
                        "evidence": [node_id, ...],
                        "source_event_ids": [...]
                    }
                ],
                "counts": {"interest_to_goal": N, "reflection_turning_point": N, "belief_change": N},
                "fallback": bool,
                "fallback_reason": str|None
            }
        """
        nodes, edges, ok, err = self._fetch_graph_data()
        if not ok:
            return {
                "milestones": [],
                "counts": {t: 0 for t in ALL_MILESTONE_TYPES},
                "fallback": True,
                "fallback_reason": err or "life_graph_unavailable",
            }
        try:
            n_limit = max(0, int(limit))
        except (TypeError, ValueError):
            n_limit = DEFAULT_MILESTONE_LIMIT
        # 索引:id -> node
        id_to_node: Dict[str, Dict[str, Any]] = {}
        for n in nodes or []:
            if isinstance(n, dict) and n.get("id"):
                id_to_node[str(n.get("id"))] = n
        # 关系边索引:from -> [(to, rel, edge), ...]
        from_adj: Dict[str, List[Tuple[str, str, Dict[str, Any]]]] = defaultdict(list)
        for e in edges or []:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            rel = str(e.get("relation", "") or "")
            if s and t:
                from_adj[s].append((t, rel, e))
        milestones: List[Dict[str, Any]] = []
        # A. interest_to_goal
        a_count = 0
        for nid, n in id_to_node.items():
            if str(n.get("type", "") or "") != "interest":
                continue
            # 找下游 goal
            for target_id, rel, e in from_adj.get(nid, []):
                if rel != "interest_to_goal":
                    continue
                target_node = id_to_node.get(target_id)
                if not target_node or str(target_node.get("type", "") or "") != "goal":
                    continue
                # 链路:interest -> goal(并尝试回溯上游)
                chain: List[str] = []
                try:
                    srefl = list(n.get("source_reflection_ids", []) or [])
                    if srefl:
                        chain.append("reflection")
                except Exception:
                    pass
                chain = ["interest", "goal"] + chain
                # evidence:事件 ids
                ev_ids: List[str] = []
                for s_id in list(n.get("source_event_ids", []) or []):
                    if isinstance(s_id, str):
                        ev_ids.append(s_id)
                for t_id in list(target_node.get("source_event_ids", []) or []):
                    if isinstance(t_id, str):
                        ev_ids.append(t_id)
                ts = _to_float_ts(target_node.get("timestamp") or n.get("timestamp"))
                milestone = {
                    "id": f"milestone_i2g::{nid}->{target_id}",
                    "type": MILESTONE_INTEREST_TO_GOAL,
                    "title": "兴趣形成目标",
                    "timestamp": _to_iso(ts),
                    "timestamp_ts": ts,
                    "chain": chain,
                    "evidence": [nid, target_id],
                    "source_event_ids": ev_ids,
                    "summary": f"兴趣 {nid} 推动生成目标 {target_id}",
                }
                milestones.append(milestone)
                a_count += 1
                if n_limit > 0 and len(milestones) >= n_limit:
                    break
            if n_limit > 0 and len(milestones) >= n_limit:
                break
        # B. reflection_turning_point
        b_count = 0
        if n_limit <= 0 or len(milestones) < n_limit:
            for nid, n in id_to_node.items():
                if str(n.get("type", "") or "") != "reflection":
                    continue
                smem = list(n.get("source_memory_ids", []) or [])
                # 兼容:部分反射可能通过 source_event_ids 间接引用 memory
                if len(smem) < TURNING_POINT_MIN_MEMORIES:
                    # 退而求其次:用 source_event_ids 数量做参考
                    sev = list(n.get("source_event_ids", []) or [])
                    if len(sev) < TURNING_POINT_MIN_MEMORIES:
                        continue
                ts = _to_float_ts(n.get("timestamp") or n.get("timestamp_ts"))
                ev_ids: List[str] = []
                for s_id in list(n.get("source_event_ids", []) or []):
                    if isinstance(s_id, str):
                        ev_ids.append(s_id)
                milestone = {
                    "id": f"milestone_rtp::{nid}",
                    "type": MILESTONE_REFLECTION_TURNING_POINT,
                    "title": "反思转折点",
                    "timestamp": _to_iso(ts),
                    "timestamp_ts": ts,
                    "chain": ["memory", "reflection"],
                    "evidence": [nid] + [m for m in smem[:5]],
                    "source_event_ids": ev_ids,
                    "summary": f"反思 {nid} 整合 {len(smem)} 个记忆,形成转折点",
                }
                milestones.append(milestone)
                b_count += 1
                if n_limit > 0 and len(milestones) >= n_limit:
                    break
        # C. belief_change
        c_count = 0
        if n_limit <= 0 or len(milestones) < n_limit:
            for nid, n in id_to_node.items():
                if str(n.get("type", "") or "") != "belief":
                    continue
                for target_id, rel, e in from_adj.get(nid, []):
                    if rel != "belief_to_trait_change":
                        continue
                    target_node = id_to_node.get(target_id)
                    if not target_node or str(target_node.get("type", "") or "") != "trait_change":
                        continue
                    # 链路: belief -> trait_change
                    chain: List[str] = ["belief", "trait_change"]
                    ev_ids: List[str] = []
                    for s_id in list(n.get("source_event_ids", []) or []):
                        if isinstance(s_id, str):
                            ev_ids.append(s_id)
                    for t_id in list(target_node.get("source_event_ids", []) or []):
                        if isinstance(t_id, str):
                            ev_ids.append(t_id)
                    ts = _to_float_ts(target_node.get("timestamp") or n.get("timestamp"))
                    milestone = {
                        "id": f"milestone_bc::{nid}->{target_id}",
                        "type": MILESTONE_BELIEF_CHANGE,
                        "title": "信念驱动人格变化",
                        "timestamp": _to_iso(ts),
                        "timestamp_ts": ts,
                        "chain": chain,
                        "evidence": [nid, target_id],
                        "source_event_ids": ev_ids,
                        "summary": f"信念 {nid} 引发特质变化 {target_id}",
                    }
                    milestones.append(milestone)
                    c_count += 1
                    if n_limit > 0 and len(milestones) >= n_limit:
                        break
                if n_limit > 0 and len(milestones) >= n_limit:
                    break
        # 按时间排序
        milestones.sort(
            key=lambda m: (
                float(m.get("timestamp_ts") or 0.0),
                str(m.get("id") or ""),
            )
        )
        return {
            "milestones": milestones,
            "counts": {
                MILESTONE_INTEREST_TO_GOAL: a_count,
                MILESTONE_REFLECTION_TURNING_POINT: b_count,
                MILESTONE_BELIEF_CHANGE: c_count,
            },
            "fallback": False,
            "fallback_reason": None,
        }

    # --------------------------------------------------------
    # 内部:检测简短里程碑(给 get_timeline 用)
    # --------------------------------------------------------
    def _detect_milestones(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        给 get_timeline() 用的简短里程碑。
        """
        id_to_node: Dict[str, Dict[str, Any]] = {}
        for n in nodes or []:
            if isinstance(n, dict) and n.get("id"):
                id_to_node[str(n.get("id"))] = n
        from_adj: Dict[str, List[Tuple[str, str, Dict[str, Any]]]] = defaultdict(list)
        for e in edges or []:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source", "") or "")
            t = str(e.get("target", "") or "")
            rel = str(e.get("relation", "") or "")
            if s and t:
                from_adj[s].append((t, rel, e))
        out: List[Dict[str, Any]] = []
        # A. interest_to_goal
        for nid, n in id_to_node.items():
            if str(n.get("type", "") or "") != "interest":
                continue
            for target_id, rel, _e in from_adj.get(nid, []):
                if rel != "interest_to_goal":
                    continue
                target_node = id_to_node.get(target_id)
                if not target_node or str(target_node.get("type", "") or "") != "goal":
                    continue
                ts = _to_float_ts(target_node.get("timestamp") or n.get("timestamp"))
                out.append({
                    "id": f"milestone_i2g::{nid}->{target_id}",
                    "type": MILESTONE_INTEREST_TO_GOAL,
                    "title": "兴趣形成目标",
                    "timestamp": _to_iso(ts),
                    "timestamp_ts": ts,
                })
                break
        # C. belief_change
        for nid, n in id_to_node.items():
            if str(n.get("type", "") or "") != "belief":
                continue
            for target_id, rel, _e in from_adj.get(nid, []):
                if rel != "belief_to_trait_change":
                    continue
                target_node = id_to_node.get(target_id)
                if not target_node or str(target_node.get("type", "") or "") != "trait_change":
                    continue
                ts = _to_float_ts(target_node.get("timestamp") or n.get("timestamp"))
                out.append({
                    "id": f"milestone_bc::{nid}->{target_id}",
                    "type": MILESTONE_BELIEF_CHANGE,
                    "title": "信念驱动人格变化",
                    "timestamp": _to_iso(ts),
                    "timestamp_ts": ts,
                })
                break
        # 按时间
        out.sort(key=lambda m: (float(m.get("timestamp_ts") or 0.0), str(m.get("id") or "")))
        return out[: max(0, int(limit))]


# ============================================================
# 模块级单例
# ============================================================

_timeline_instance: Optional[LifeTimelineProvider] = None
_timeline_lock = None


def _get_timeline_lock():
    global _timeline_lock
    if _timeline_lock is None:
        import threading
        _timeline_lock = threading.Lock()
    return _timeline_lock


def get_life_timeline_provider() -> LifeTimelineProvider:
    global _timeline_instance
    if _timeline_instance is None:
        with _get_timeline_lock():
            if _timeline_instance is None:
                _timeline_instance = LifeTimelineProvider()
    return _timeline_instance


def reset_life_timeline_provider_for_testing() -> None:
    global _timeline_instance
    with _get_timeline_lock():
        _timeline_instance = None


__all__ = [
    "LifeTimelineProvider",
    "get_life_timeline_provider",
    "reset_life_timeline_provider_for_testing",
    "LANE_TYPES",
    "LANE_LABELS",
    "ALL_RANGES",
    "DEFAULT_RANGE",
    "RANGE_SECONDS",
    "RELATION_LABELS",
    "ALL_MILESTONE_TYPES",
    "MILESTONE_INTEREST_TO_GOAL",
    "MILESTONE_REFLECTION_TURNING_POINT",
    "MILESTONE_BELIEF_CHANGE",
    "TURNING_POINT_MIN_MEMORIES",
    "DEFAULT_LANE_ITEM_LIMIT",
    "DEFAULT_MILESTONE_LIMIT",
]
