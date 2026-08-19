# -*- coding: utf-8 -*-
"""
src/admin/life_state_provider.py

Phase 5.0 Dashboard Upgrade Step 8.4.1 —— LifeStateProvider。

职责:
- 跨域聚合 Overview 所需的结构化 state_summary:
    current_focus / emotion / growth / active_goals / recent_memories /
    recent_reflections / interests / risks / stats
- 不生成自然语言 headline:仅输出纯结构化字段,由前端负责渲染可读文本
- 数据来源:
    InitiativeDashboardProvider       (interests)
    SelfModelDashboardProvider        (identity / evolution_timeline)
    GoalDashboardProvider             (summary)
    MemoryDashboardProvider           (recent)
    LifeGraphProvider                 (timeline → reflection items)
    RuntimeProvider                   (emotion_summary)
    EventHub                          (emotion.* 因果链)
- 严格只读:
    - 不修改任何 Authority
    - 不调用 LLM
    - 不创建业务事件
- 容错:
    - 任何子 Provider 失败不影响整体
    - 空数据 / 异常时返回 fallback
- 接口与 trace 装饰:
    - data 字段纯净(无 _meta)
    - trace 在 data 之外

数据流:
    Frontend
        ↓
    LifeStateRouter
        ↓
    LifeStateProvider
        ↓
    已有 Dashboard Providers / EventHub
        ↓
    Business Authorities(由各自模块维护,Dashboard 仅观察)
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# Interest trends(与 InitiativeDashboardProvider 对齐)
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

# Emotion.* 事件类型(用于因果链)
EMOTION_EVENT_TYPES = (
    "integration.emotion.should_decay",
    "integration.emotion.state_changed",
    "integration.emotion.detected",
)

# 风险等级
RISK_LEVEL_LOW = "low"
RISK_LEVEL_MEDIUM = "medium"
RISK_LEVEL_HIGH = "high"
ALL_RISK_LEVELS = (RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH)

# 默认 limit
DEFAULT_RECENT_LIMIT = 5
DEFAULT_INTEREST_LIMIT = 20
DEFAULT_GOAL_LIMIT = 5
DEFAULT_REFLECTION_LIMIT = 5
DEFAULT_RISK_MAX = 10
DEFAULT_CACHE_TTL = 4.0  # 秒

# 默认 stage 列表(成长阶段)
GROWTH_STAGE_EXPLORING = "exploring"
GROWTH_STAGE_DEEPENING = "deepening"
GROWTH_STAGE_MASTERING = "mastering"
ALL_GROWTH_STAGES = (GROWTH_STAGE_EXPLORING, GROWTH_STAGE_DEEPENING, GROWTH_STAGE_MASTERING)


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
        logger.debug("life_state_provider safe_call 失败: %s", exc)
        return None


def _clip(s: Any, n: int = 256) -> str:
    if s is None:
        return ""
    try:
        v = str(s)
    except Exception:
        return ""
    if len(v) > n:
        return v[:n]
    return v


def _to_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        v = float(value)
        return v if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def _is_emotion_event(event_type: Any) -> bool:
    if not event_type or not isinstance(event_type, str):
        return False
    return event_type in EMOTION_EVENT_TYPES


# ============================================================
# Domain Collector(只读聚合,测试可注入)
# ============================================================

class _DefaultEmotionCollector:
    """
    从 EventHub 拉取 emotion.* 事件的因果链。
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
            logger.debug("LifeStateProvider: EventHub 不可用: %s", exc)
            self._event_hub = None
        return self._event_hub

    def list_emotion_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        hub = self._get_hub()
        if hub is None:
            return []
        try:
            _ = _safe_call(hub.poll_recent, limit=max(1, min(200, int(limit) or 0)))
        except Exception:
            return []
        try:
            host = _safe_get(hub, "_get_host")
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
                for et in EMOTION_EVENT_TYPES:
                    try:
                        sub = events_fn(event_type=et, limit=limit) or []
                    except Exception:
                        continue
                    for ev in sub:
                        eid = str(_safe_get(ev, "event_id", "") or "")
                        if not eid or eid in seen:
                            continue
                        seen.add(eid)
                        out.append({
                            "event_id": eid,
                            "event_type": str(_safe_get(ev, "event_type", "") or ""),
                            "timestamp": _safe_get(ev, "timestamp", None),
                            "payload": _safe_get(ev, "payload", {}) or {},
                        })
            elif callable(latest_fn):
                try:
                    all_events = latest_fn(limit=limit) or []
                except Exception:
                    return []
                for ev in all_events:
                    et = str(_safe_get(ev, "event_type", "") or "")
                    if not _is_emotion_event(et):
                        continue
                    eid = str(_safe_get(ev, "event_id", "") or "")
                    if not eid or eid in seen:
                        continue
                    seen.add(eid)
                    out.append({
                        "event_id": eid,
                        "event_type": et,
                        "timestamp": _safe_get(ev, "timestamp", None),
                        "payload": _safe_get(ev, "payload", {}) or {},
                    })
            out.sort(
                key=lambda e: (
                    _to_float(e.get("timestamp")),
                    str(e.get("event_id", "") or ""),
                ),
                reverse=True,
            )
            return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: 读取 emotion 事件失败: %s", exc)
            return []


# ============================================================
# Provider
# ============================================================

class LifeStateProvider:
    """
    生命状态 Provider(只读)。

    注入(测试可注入):
        initiative_provider / selfmodel_provider / goal_provider /
        memory_provider / life_graph_provider / runtime_provider /
        emotion_collector

    不传则走默认:
        from src.admin.initiative_dashboard_provider import get_initiative_dashboard_provider
        from src.admin.selfmodel_dashboard_provider import get_selfmodel_dashboard_provider
        from src.admin.goal_dashboard_provider import get_goal_dashboard_provider
        from src.admin.memory_dashboard_provider import get_memory_dashboard_provider
        from src.admin.life_graph_provider import get_life_graph_provider
        from src.admin.runtime_provider import get_runtime_provider
    """

    def __init__(
        self,
        *,
        initiative_provider: Optional[Any] = None,
        selfmodel_provider: Optional[Any] = None,
        goal_provider: Optional[Any] = None,
        memory_provider: Optional[Any] = None,
        life_graph_provider: Optional[Any] = None,
        runtime_provider: Optional[Any] = None,
        emotion_collector: Optional[Any] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._initiative = initiative_provider
        self._selfmodel = selfmodel_provider
        self._goal = goal_provider
        self._memory = memory_provider
        self._life_graph = life_graph_provider
        self._runtime = runtime_provider
        self._emotion_collector = emotion_collector
        self._clock = clock or time.time

    # --------------------------------------------------------
    # Provider 懒加载
    # --------------------------------------------------------
    def _get_initiative(self):
        if self._initiative is not None:
            return self._initiative
        try:
            from src.admin.initiative_dashboard_provider import get_initiative_dashboard_provider
            self._initiative = get_initiative_dashboard_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: Initiative Provider 不可用: %s", exc)
            self._initiative = None
        return self._initiative

    def _get_selfmodel(self):
        if self._selfmodel is not None:
            return self._selfmodel
        try:
            from src.admin.selfmodel_dashboard_provider import get_selfmodel_dashboard_provider
            self._selfmodel = get_selfmodel_dashboard_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: SelfModel Provider 不可用: %s", exc)
            self._selfmodel = None
        return self._selfmodel

    def _get_goal(self):
        if self._goal is not None:
            return self._goal
        try:
            from src.admin.goal_dashboard_provider import get_goal_dashboard_provider
            self._goal = get_goal_dashboard_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: Goal Provider 不可用: %s", exc)
            self._goal = None
        return self._goal

    def _get_memory(self):
        if self._memory is not None:
            return self._memory
        try:
            from src.admin.memory_dashboard_provider import get_memory_dashboard_provider
            self._memory = get_memory_dashboard_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: Memory Provider 不可用: %s", exc)
            self._memory = None
        return self._memory

    def _get_life_graph(self):
        if self._life_graph is not None:
            return self._life_graph
        try:
            from src.admin.life_graph_provider import get_life_graph_provider
            self._life_graph = get_life_graph_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: LifeGraph Provider 不可用: %s", exc)
            self._life_graph = None
        return self._life_graph

    def _get_runtime(self):
        if self._runtime is not None:
            return self._runtime
        try:
            from src.admin.runtime_provider import get_runtime_provider
            self._runtime = get_runtime_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider: Runtime Provider 不可用: %s", exc)
            self._runtime = None
        return self._runtime

    def _get_emotion_collector(self) -> Any:
        if self._emotion_collector is not None:
            return self._emotion_collector
        self._emotion_collector = _DefaultEmotionCollector()
        return self._emotion_collector

    # --------------------------------------------------------
    # 子模块聚合(全部带容错,返回 None 表示不可用)
    # --------------------------------------------------------
    def _fetch_interests(self, limit: int) -> Optional[Dict[str, Any]]:
        p = self._get_initiative()
        if p is None:
            return None
        try:
            res = _safe_call(p.list_interests, limit=limit)
        except Exception:
            return None
        if not isinstance(res, dict):
            return None
        if not res.get("available"):
            return None
        return res

    def _fetch_goals(self, limit: int) -> Optional[Dict[str, Any]]:
        p = self._get_goal()
        if p is None:
            return None
        try:
            res = _safe_call(p.list_goals, limit=limit, status="active")
        except Exception:
            res = None
        if not isinstance(res, dict) or not res.get("available"):
            # 退化:使用 summary 拿 active_count
            try:
                res2 = _safe_call(p.get_summary)
            except Exception:
                return None
            if not isinstance(res2, dict) or not res2.get("available"):
                return None
            # 包装为统一结构
            return {
                "available": True,
                "items": [],
                "active_count": res2.get("active_count", 0),
                "_via": "summary_fallback",
            }
        return res

    def _fetch_memories(self, limit: int) -> Optional[Dict[str, Any]]:
        p = self._get_memory()
        if p is None:
            return None
        try:
            res = _safe_call(p.list_recent, limit=limit)
        except Exception:
            return None
        if not isinstance(res, dict):
            return None
        if not res.get("available"):
            return None
        return res

    def _fetch_identity(self) -> Optional[Dict[str, Any]]:
        p = self._get_selfmodel()
        if p is None:
            return None
        try:
            res = _safe_call(p.get_identity)
        except Exception:
            return None
        if not isinstance(res, dict):
            return None
        if not res.get("available"):
            return None
        return res

    def _fetch_evolution_timeline(self, limit: int) -> Optional[Dict[str, Any]]:
        p = self._get_selfmodel()
        if p is None:
            return None
        try:
            res = _safe_call(p.get_evolution_timeline, limit=limit)
        except Exception:
            return None
        if not isinstance(res, dict):
            return None
        if not res.get("available"):
            return None
        return res

    def _fetch_emotion_summary(self) -> Optional[Dict[str, Any]]:
        p = self._get_runtime()
        if p is None:
            return None
        try:
            res = _safe_call(p.get_emotion_summary)
        except Exception:
            return None
        if not isinstance(res, dict):
            return None
        if not res.get("available"):
            return None
        return res

    def _fetch_reflection_items(self, limit: int) -> List[Dict[str, Any]]:
        p = self._get_life_graph()
        if p is None:
            return []
        try:
            res = _safe_call(p.get_timeline, limit=max(limit * 4, 30))
        except Exception:
            return []
        if not isinstance(res, dict) or not res.get("available"):
            return []
        items = res.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("type") != "reflection":
                continue
            out.append(it)
            if len(out) >= limit:
                break
        return out

    def _fetch_emotion_causality(self, limit: int = 10) -> List[Dict[str, Any]]:
        ec = self._get_emotion_collector()
        if ec is None:
            return []
        try:
            res = _safe_call(ec.list_emotion_events, limit=limit)
        except Exception:
            return []
        if not isinstance(res, list):
            return []
        return res

    # --------------------------------------------------------
    # 业务逻辑
    # --------------------------------------------------------
    def _select_current_focus(
        self,
        interests_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        选择 current_focus:
        1) trend=rising 中 strength 最高
        2) 无 rising 时 strength 最高
        3) 都为空 topic=null
        附带 selection_reason 供 LifeGraph 解释
        """
        empty = {
            "topic": None,
            "score": 0.0,
            "trend": None,
            "interest_node_id": None,
            "selection_reason": "no_interests",
        }
        if not isinstance(interests_payload, dict):
            return dict(empty)
        items = interests_payload.get("items", []) or []
        if not isinstance(items, list) or not items:
            return dict(empty)
        # 1) trend=rising
        rising = [it for it in items if isinstance(it, dict) and it.get("trend") == INTEREST_TREND_RISING]
        if rising:
            best = max(rising, key=lambda x: _to_float(x.get("strength")))
            return {
                "topic": _clip(best.get("topic") or best.get("label") or "", 128),
                "score": _to_float(best.get("strength")),
                "trend": INTEREST_TREND_RISING,
                "interest_node_id": str(best.get("signal_id") or best.get("interest_id") or ""),
                "selection_reason": "rising_highest_strength",
            }
        # 2) 无 rising:strength 最高
        any_items = [it for it in items if isinstance(it, dict)]
        if any_items:
            best = max(any_items, key=lambda x: _to_float(x.get("strength")))
            return {
                "topic": _clip(best.get("topic") or best.get("label") or "", 128),
                "score": _to_float(best.get("strength")),
                "trend": str(best.get("trend") or ""),
                "interest_node_id": str(best.get("signal_id") or best.get("interest_id") or ""),
                "selection_reason": "highest_strength_no_rising",
            }
        return dict(empty)

    def _build_emotion(
        self,
        emotion_summary: Optional[Dict[str, Any]],
        causality_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        构造 emotion 字段。
        数据源:RuntimeProvider.get_emotion_summary 优先(EventHub emotion.* 作因果补充)。
        """
        if not isinstance(emotion_summary, dict):
            return {
                "name": None,
                "intensity": 0.0,
                "valence": 0.0,
                "arousal": 0.0,
                "source": "none",
                "causality": {
                    "event_ids": [],
                    "memory_ids": [],
                },
            }
        recent = emotion_summary.get("recent") or []
        recent_event_ids: List[str] = []
        recent_memory_ids: List[str] = []
        for ev in causality_events[:10]:
            eid = str(ev.get("event_id", "") or "")
            if eid:
                recent_event_ids.append(eid)
            payload = ev.get("payload") or {}
            if isinstance(payload, dict):
                mid = payload.get("memory_id") or payload.get("source_memory_id")
                if isinstance(mid, str) and mid:
                    recent_memory_ids.append(mid)
        for rc in recent[:5]:
            if not isinstance(rc, dict):
                continue
            mid = rc.get("memory_id") or rc.get("source_memory_id")
            if isinstance(mid, str) and mid and mid not in recent_memory_ids:
                recent_memory_ids.append(mid)
        name = str(emotion_summary.get("current") or "") or None
        intensity = _to_float(emotion_summary.get("intensity"))
        # valence/arousal 推断:若 recent 里有字段则取最近
        valence = 0.0
        arousal = 0.0
        for rc in recent:
            if not isinstance(rc, dict):
                continue
            if "valence" in rc:
                valence = _to_float(rc.get("valence"))
            if "arousal" in rc:
                arousal = _to_float(rc.get("arousal"))
        return {
            "name": name,
            "intensity": intensity,
            "valence": valence,
            "arousal": arousal,
            "source": "runtime_provider" if emotion_summary.get("available") else "fallback",
            "causality": {
                "event_ids": recent_event_ids,
                "memory_ids": recent_memory_ids,
            },
        }

    def _build_growth(
        self,
        identity: Optional[Dict[str, Any]],
        evolution: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(identity, dict) and not isinstance(evolution, dict):
            return {
                "stage": None,
                "stage_score": 0.0,
                "identity_hash": None,
                "last_milestone": None,
            }
        # stage 推断:优先 evolution 中最近一个 trait_change,否则 identity.version
        stage = None
        stage_score = 0.0
        last_milestone: Optional[Dict[str, Any]] = None
        if isinstance(evolution, dict):
            items = evolution.get("items", []) or []
            if isinstance(items, list) and items:
                first = items[0] if isinstance(items[0], dict) else None
                if first is not None:
                    last_milestone = {
                        "id": str(first.get("change_id") or first.get("id") or ""),
                        "summary": _clip(
                            first.get("description") or first.get("reason") or
                            first.get("trait_name") or "", 256
                        ),
                        "timestamp": str(first.get("timestamp") or ""),
                    }
                    delta = _to_float(first.get("delta", first.get("change", 0.0)))
                    stage_score = abs(delta) if delta != 0.0 else 0.5
        # 简单规则:stage_score >= 0.5 → mastering;0.2~0.5 → deepening;<0.2 → exploring
        if stage_score >= 0.5:
            stage = GROWTH_STAGE_MASTERING
        elif stage_score >= 0.2:
            stage = GROWTH_STAGE_DEEPENING
        elif stage_score > 0:
            stage = GROWTH_STAGE_EXPLORING
        # 兜底:有 identity 但无 evolution
        if stage is None and isinstance(identity, dict):
            stage = GROWTH_STAGE_EXPLORING
            stage_score = 0.1
        return {
            "stage": stage,
            "stage_score": stage_score,
            "identity_hash": str(identity.get("identity_id") or "") if isinstance(identity, dict) else None,
            "last_milestone": last_milestone,
        }

    def _build_active_goals(self, goals_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(goals_payload, dict):
            return []
        items = goals_payload.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append({
                "id": str(it.get("goal_id") or it.get("id") or ""),
                "title": _clip(it.get("title") or it.get("topic") or it.get("rationale") or "", 128),
                "progress": _to_float(it.get("progress", it.get("priority", 0.0))),
                "status": str(it.get("status") or "active"),
            })
        return out

    def _build_recent_memories(self, memories_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(memories_payload, dict):
            return []
        items = memories_payload.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append({
                "id": str(it.get("id") or it.get("memory_id") or ""),
                "summary": _clip(it.get("summary") or it.get("content") or it.get("topic") or "", 256),
                "importance": _to_float(it.get("importance", 0.0)),
                "timestamp": str(it.get("timestamp") or it.get("created_at") or ""),
            })
        return out

    def _build_recent_reflections(self, reflection_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for it in reflection_items:
            if not isinstance(it, dict):
                continue
            out.append({
                "id": str(it.get("id", "") or ""),
                "topic": _clip(it.get("topic") or "", 128),
                "insight": _clip(it.get("summary") or it.get("evidence") or "", 256),
                "timestamp": str(it.get("timestamp") or ""),
            })
        return out

    def _build_interests(self, interests_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(interests_payload, dict):
            return []
        items = interests_payload.get("items", []) or []
        if not isinstance(items, list):
            return []
        out: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            out.append({
                "topic": _clip(it.get("topic") or it.get("label") or "", 128),
                "strength": _to_float(it.get("strength")),
                "trend": str(it.get("trend") or ""),
            })
        return out

    def _compute_risks(
        self,
        interests: List[Dict[str, Any]],
        emotion: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        风险计算规则:
        - interest trend=declining + emotion valence<0 → medium risk
        - emotion intensity > 0.7 升级 high
        - 每个 risk 必须携带 evidence/source
        """
        risks: List[Dict[str, Any]] = []
        declining = [it for it in interests if it.get("trend") == INTEREST_TREND_FADING]
        if declining and emotion.get("valence", 0.0) < 0:
            for it in declining[:3]:
                level = RISK_LEVEL_MEDIUM
                if _to_float(emotion.get("intensity")) > 0.7:
                    level = RISK_LEVEL_HIGH
                risks.append({
                    "level": level,
                    "reason": f"兴趣『{it.get('topic', '')}』正在消退,同时情绪偏负向",
                    "evidence": {
                        "interest": {
                            "topic": it.get("topic"),
                            "strength": it.get("strength"),
                            "trend": it.get("trend"),
                        },
                        "emotion": {
                            "name": emotion.get("name"),
                            "intensity": emotion.get("intensity"),
                            "valence": emotion.get("valence"),
                        },
                    },
                    "source": "LifeStateProvider._compute_risks",
                })
        if not risks and _to_float(emotion.get("intensity")) > 0.85 and emotion.get("valence", 0.0) < -0.3:
            risks.append({
                "level": RISK_LEVEL_MEDIUM,
                "reason": "情绪强度极高且偏负向,无明确兴趣消退关联",
                "evidence": {
                    "emotion": {
                        "name": emotion.get("name"),
                        "intensity": emotion.get("intensity"),
                        "valence": emotion.get("valence"),
                    },
                },
                "source": "LifeStateProvider._compute_risks",
            })
        return risks[:DEFAULT_RISK_MAX]

    def _build_stats(
        self,
        interests_payload: Optional[Dict[str, Any]],
        memories_payload: Optional[Dict[str, Any]],
        goals_payload: Optional[Dict[str, Any]],
        reflection_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        def _total(payload: Optional[Dict[str, Any]]) -> int:
            if not isinstance(payload, dict):
                return 0
            items = payload.get("items", []) or []
            if isinstance(items, list):
                return len(items)
            total = payload.get("total", 0)
            try:
                return int(total)
            except (TypeError, ValueError):
                return 0
        return {
            "interest_count": _total(interests_payload),
            "memory_count": _total(memories_payload),
            "active_goal_count": _total(goals_payload),
            "reflection_count_7d": len(reflection_items),
        }

    # --------------------------------------------------------
    # 公开方法
    # --------------------------------------------------------
    def get_state_summary(self) -> Dict[str, Any]:
        """
        聚合所有域,返回 state_summary 的 envelope。

        Returns:
            {
                "data": {
                    "current_focus": {...},
                    "emotion": {...},
                    "growth": {...},
                    "active_goals": [...],
                    "recent_memories": [...],
                    "recent_reflections": [...],
                    "interests": [...],
                    "risks": [...],
                    "stats": {...}
                },
                "fallback": bool,
                "fallback_reason": str | None,
                "trace": {...}  # 由 @traceable 装饰器注入
            }
        """
        # 注意:此方法不直接使用 @traceable 装饰器,因为它跨多个 Provider 调用,
        # 需要在内部累积 trace.sources,然后手动构造 envelope。
        sources: List[Dict[str, Any]] = []
        any_unavailable = False
        try:
            interests_payload = self._fetch_interests(DEFAULT_INTEREST_LIMIT)
            if interests_payload is None:
                any_unavailable = True
            sources.append({
                "provider": "InitiativeDashboardProvider",
                "method": "list_interests",
                "ok": interests_payload is not None,
            })
            goals_payload = self._fetch_goals(DEFAULT_GOAL_LIMIT)
            if goals_payload is None:
                any_unavailable = True
            sources.append({
                "provider": "GoalDashboardProvider",
                "method": "list_active/get_summary",
                "ok": goals_payload is not None,
            })
            memories_payload = self._fetch_memories(DEFAULT_RECENT_LIMIT)
            if memories_payload is None:
                any_unavailable = True
            sources.append({
                "provider": "MemoryDashboardProvider",
                "method": "list_recent",
                "ok": memories_payload is not None,
            })
            identity_payload = self._fetch_identity()
            if identity_payload is None:
                any_unavailable = True
            sources.append({
                "provider": "SelfModelDashboardProvider",
                "method": "get_identity",
                "ok": identity_payload is not None,
            })
            evolution_payload = self._fetch_evolution_timeline(DEFAULT_RECENT_LIMIT)
            if evolution_payload is None:
                any_unavailable = True
            sources.append({
                "provider": "SelfModelDashboardProvider",
                "method": "get_evolution_timeline",
                "ok": evolution_payload is not None,
            })
            emotion_summary = self._fetch_emotion_summary()
            if emotion_summary is None:
                any_unavailable = True
            sources.append({
                "provider": "RuntimeProvider",
                "method": "get_emotion_summary",
                "ok": emotion_summary is not None,
            })
            causality_events = self._fetch_emotion_causality(limit=10)
            sources.append({
                "provider": "EventHubDomainCollector",
                "method": "list_emotion_events",
                "ok": True,  # 空数据也是合法状态
            })
            reflection_items = self._fetch_reflection_items(DEFAULT_REFLECTION_LIMIT)
            sources.append({
                "provider": "LifeGraphProvider",
                "method": "get_timeline",
                "ok": True,
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("LifeStateProvider.get_state_summary 异常: %s", exc)
            from src.admin.dashboard._meta import build_envelope
            return build_envelope(
                data={},
                sources=sources,
                fallback=True,
                fallback_reason=f"aggregate_error:{type(exc).__name__}",
                ok=False,
            )

        # 计算各字段
        current_focus = self._select_current_focus(interests_payload)
        emotion = self._build_emotion(emotion_summary, causality_events)
        growth = self._build_growth(identity_payload, evolution_payload)
        active_goals = self._build_active_goals(goals_payload)
        recent_memories = self._build_recent_memories(memories_payload)
        recent_reflections = self._build_recent_reflections(reflection_items)
        interests = self._build_interests(interests_payload)
        risks = self._compute_risks(interests, emotion)
        stats = self._build_stats(interests_payload, memories_payload, goals_payload, reflection_items)

        data: Dict[str, Any] = {
            "current_focus": current_focus,
            "emotion": emotion,
            "growth": growth,
            "active_goals": active_goals,
            "recent_memories": recent_memories,
            "recent_reflections": recent_reflections,
            "interests": interests,
            "risks": risks,
            "stats": stats,
        }
        # 关键:data 永远纯净,不混入 _meta
        from src.admin.dashboard._meta import build_envelope, calc_confidence
        # 至少有一个数据源 → available=true
        all_empty = (
            not interests and not active_goals and not recent_memories and
            not recent_reflections and (emotion.get("name") is None) and
            not growth.get("last_milestone") and not risks
        )
        if all_empty:
            return build_envelope(
                data=data,
                sources=sources,
                fallback=True,
                fallback_reason="all_sources_empty",
                ok=False,
            )
        return build_envelope(
            data=data,
            sources=sources,
            fallback=False,
            fallback_reason=None,
            ok=True,
        )

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "initiative": self._initiative is not None,
                "selfmodel": self._selfmodel is not None,
                "goal": self._goal is not None,
                "memory": self._memory is not None,
                "life_graph": self._life_graph is not None,
                "runtime": self._runtime is not None,
                "emotion_collector": self._emotion_collector is not None,
            }


# ============================================================
# 模块级单例
# ============================================================

_provider_instance: Optional[LifeStateProvider] = None
_provider_lock = threading.Lock()


def get_life_state_provider() -> LifeStateProvider:
    global _provider_instance
    if _provider_instance is None:
        with _provider_lock:
            if _provider_instance is None:
                _provider_instance = LifeStateProvider()
    return _provider_instance


def reset_life_state_provider_for_testing() -> None:
    global _provider_instance
    with _provider_lock:
        _provider_instance = None


__all__ = [
    "LifeStateProvider",
    "get_life_state_provider",
    "reset_life_state_provider_for_testing",
    "INTEREST_TREND_NEW",
    "INTEREST_TREND_RISING",
    "INTEREST_TREND_STABLE",
    "INTEREST_TREND_FADING",
    "ALL_INTEREST_TRENDS",
    "RISK_LEVEL_LOW",
    "RISK_LEVEL_MEDIUM",
    "RISK_LEVEL_HIGH",
    "ALL_RISK_LEVELS",
    "GROWTH_STAGE_EXPLORING",
    "GROWTH_STAGE_DEEPENING",
    "GROWTH_STAGE_MASTERING",
    "ALL_GROWTH_STAGES",
    "EMOTION_EVENT_TYPES",
]
