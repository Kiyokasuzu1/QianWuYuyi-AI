# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_engine.py

Phase 5.0-D3-C: InitiativeEngine 主动意图生成引擎。

职责:
- 编排生成 InterestSignal
- 编排生成 PossibleAction
- 通过 ActionFilter 过滤
- 送入 InitiativeQueue

输入:
- SelfModel 状态(通过 InternalState 注入)
- ReflectionResult(来源)
- IntegrationEvent(来源)

输出:
- InterestSignal
- PossibleAction
- 不执行任何动作

约束:
- enable_initiative = False 时全部短路
- 规则实现,无 LLM
- 全部 Clock 注入
- 确定性:相同输入产生相同输出
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.initiative.action_filter import ActionFilter, FilterDecision
from src.runtime.initiative.interest_signal import (
    DEFAULT_TREND,
    INTEREST_TREND_FADING,
    INTEREST_TREND_NEW,
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
    InterestSignal,
    InterestSignalRegistry,
)
from src.runtime.initiative.internal_state import InternalState
from src.runtime.initiative.possible_action import (
    ACTION_EFFORT_LOW,
    ACTION_EFFORT_MEDIUM,
    ACTION_STATUS_DISCARDED,
    ACTION_STATUS_FILTERED,
    ACTION_STATUS_PENDING,
    ACTION_TYPE_ASK,
    ACTION_TYPE_LEARN,
    ACTION_TYPE_OBSERVE,
    ACTION_TYPE_RECOMMEND,
    ACTION_TYPE_REMIND,
    ACTION_URGENCY_HIGH,
    ACTION_URGENCY_LOW,
    ACTION_URGENCY_NORMAL,
    PossibleAction,
)
from src.runtime.initiative.initiative_queue import InitiativeQueue
from src.runtime.lifecycle.internal.clock import Clock, SystemClock
from src.runtime.reflection.reflection_result import (
    ReflectionResult,
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    REFLECTION_TYPE_GROWTH,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
INITIATIVE_ENGINE_SCHEMA_VERSION = "1.0"

# 兴趣强度阈值
DEFAULT_INTEREST_MIN_STRENGTH = 0.35
DEFAULT_INTEREST_MIN_CONFIDENCE = 0.30

# 增长生成行动
DEFAULT_STRENGTH_TO_ACTION = 0.55
DEFAULT_RISING_BONUS = 0.05

# 兴趣 → action_type 映射
INTEREST_TO_ACTION_TYPE = {
    "ai_art": ACTION_TYPE_LEARN,
    "music": ACTION_TYPE_LEARN,
    "code": ACTION_TYPE_LEARN,
    "writing": ACTION_TYPE_LEARN,
    "reading": ACTION_TYPE_LEARN,
    "news": ACTION_TYPE_OBSERVE,
    "weather": ACTION_TYPE_OBSERVE,
    "social": ACTION_TYPE_ASK,
    "reminder": ACTION_TYPE_REMIND,
    "recommend": ACTION_TYPE_RECOMMEND,
    "default": ACTION_TYPE_OBSERVE,
}

# payload 主题键
_PAYLOAD_TOPIC_KEYS = ("topic", "subject", "theme", "interest", "name", "title", "tag")
# reflection topic 提示
_REFLECTION_TOPIC_HINTS = ("interest.", "topic", "subject", "theme")


# ============================================================
# 配置
# ============================================================
@dataclass
class InitiativeConfig:
    """Initiative 引擎配置。"""

    enabled: bool = True
    interest_min_strength: float = DEFAULT_INTEREST_MIN_STRENGTH
    interest_min_confidence: float = DEFAULT_INTEREST_MIN_CONFIDENCE
    strength_to_action: float = DEFAULT_STRENGTH_TO_ACTION
    rising_bonus: float = DEFAULT_RISING_BONUS
    max_signals_per_tick: int = 32
    max_actions_per_tick: int = 32
    max_topics_per_signal: int = 8

    def __post_init__(self) -> None:
        self.interest_min_strength = _clip01(self.interest_min_strength, default=DEFAULT_INTEREST_MIN_STRENGTH)
        self.interest_min_confidence = _clip01(self.interest_min_confidence, default=DEFAULT_INTEREST_MIN_CONFIDENCE)
        self.strength_to_action = _clip01(self.strength_to_action, default=DEFAULT_STRENGTH_TO_ACTION)
        self.rising_bonus = _clip01(self.rising_bonus, default=DEFAULT_RISING_BONUS)
        try:
            self.max_signals_per_tick = int(self.max_signals_per_tick)
        except Exception:
            self.max_signals_per_tick = 32
        if self.max_signals_per_tick < 0:
            self.max_signals_per_tick = 0
        try:
            self.max_actions_per_tick = int(self.max_actions_per_tick)
        except Exception:
            self.max_actions_per_tick = 32
        if self.max_actions_per_tick < 0:
            self.max_actions_per_tick = 0
        try:
            self.max_topics_per_signal = int(self.max_topics_per_signal)
        except Exception:
            self.max_topics_per_signal = 8
        if self.max_topics_per_signal < 1:
            self.max_topics_per_signal = 1


# ============================================================
# Tick 输出
# ============================================================
@dataclass
class InitiativeTickOutput:
    """一次 tick 的产出。"""

    created_signals: List[InterestSignal] = field(default_factory=list)
    created_actions: List[PossibleAction] = field(default_factory=list)
    filtered_actions: List[PossibleAction] = field(default_factory=list)
    deferred_actions: List[PossibleAction] = field(default_factory=list)
    discarded_actions: List[PossibleAction] = field(default_factory=list)
    queued_actions: List[PossibleAction] = field(default_factory=list)
    decisions: List[FilterDecision] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    source_event_count: int = 0
    source_reflection_count: int = 0
    error: str = ""


# ============================================================
# InitiativeEngine
# ============================================================
class InitiativeEngine:
    """主动意图生成引擎。"""

    def __init__(
        self,
        *,
        name: str = "initiative_engine",
        config: Optional[InitiativeConfig] = None,
        clock: Optional[Clock] = None,
        state_store: Optional[Any] = None,
        signal_registry: Optional[InterestSignalRegistry] = None,
        action_filter: Optional[ActionFilter] = None,
        queue: Optional[InitiativeQueue] = None,
    ) -> None:
        self._name = str(name or "initiative_engine")
        self._config = config if isinstance(config, InitiativeConfig) else InitiativeConfig()
        self._clock: Clock = clock or SystemClock()
        self._state_store = state_store  # 不要求类型(仅 duck-typed)
        self._registry = signal_registry if isinstance(signal_registry, InterestSignalRegistry) else InterestSignalRegistry()
        self._filter = action_filter if isinstance(action_filter, ActionFilter) else ActionFilter()
        self._queue = queue if isinstance(queue, InitiativeQueue) else InitiativeQueue()
        self._lock = threading.RLock()
        # 统计
        self._tick_count = 0
        self._signals_created = 0
        self._actions_created = 0
        self._actions_queued = 0
        self._actions_filtered = 0
        self._actions_deferred = 0
        self._actions_discarded = 0
        self._last_error = ""
        self._last_tick_at = 0.0
        self._last_signals: List[str] = []
        self._last_actions: List[str] = []

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def config(self) -> InitiativeConfig:
        return self._config

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def state_store(self) -> Any:
        return self._state_store

    @property
    def registry(self) -> InterestSignalRegistry:
        return self._registry

    @property
    def filter(self) -> ActionFilter:
        return self._filter

    @property
    def queue(self) -> InitiativeQueue:
        return self._queue

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def tick_count(self) -> int:
        with self._lock:
            return self._tick_count

    @property
    def signals_created(self) -> int:
        with self._lock:
            return self._signals_created

    @property
    def actions_created(self) -> int:
        with self._lock:
            return self._actions_created

    @property
    def actions_queued(self) -> int:
        with self._lock:
            return self._actions_queued

    @property
    def actions_filtered(self) -> int:
        with self._lock:
            return self._actions_filtered

    @property
    def actions_deferred(self) -> int:
        with self._lock:
            return self._actions_filtered

    @property
    def actions_deferred_count(self) -> int:
        with self._lock:
            return self._actions_deferred

    @property
    def actions_discarded(self) -> int:
        with self._lock:
            return self._actions_discarded

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def last_tick_at(self) -> float:
        with self._lock:
            return self._last_tick_at

    @property
    def last_signal_ids(self) -> List[str]:
        with self._lock:
            return list(self._last_signals)

    @property
    def last_action_ids(self) -> List[str]:
        with self._lock:
            return list(self._last_actions)

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------
    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._config.enabled = bool(enabled)

    def configure(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._config, k):
                    try:
                        setattr(self._config, k, v)
                    except Exception:
                        pass

    # --------------------------------------------------------
    # 核心:tick
    # --------------------------------------------------------
    def tick(
        self,
        events: Optional[Sequence[IntegrationEvent]] = None,
        reflections: Optional[Sequence[ReflectionResult]] = None,
        *,
        now: Optional[float] = None,
    ) -> InitiativeTickOutput:
        """执行一次 tick。"""
        out = InitiativeTickOutput()
        try:
            now_f = float(now) if now is not None else self._safe_now()
        except Exception:
            now_f = 0.0
        out.started_at = now_f
        try:
            with self._lock:
                self._tick_count += 1
                self._last_tick_at = now_f

            if not self._config.enabled:
                out.skipped = True
                out.skip_reason = "disabled"
                out.finished_at = self._safe_now()
                return out

            evs = list(events) if events else []
            refs = list(reflections) if reflections else []
            out.source_event_count = len(evs)
            out.source_reflection_count = len(refs)

            # 1) 读 internal state
            state = self._read_state()

            # 2) 生成 InterestSignal
            signals = self._build_signals(events=evs, reflections=refs, now=now_f)
            out.created_signals = list(signals)
            for s in signals:
                self._registry.add(s)
            with self._lock:
                self._signals_created += len(signals)
                self._last_signals = [s.signal_id for s in signals]

            # 3) 推进已有 signal trend(根据再次出现)
            self._advance_trends(signals=signals, now=now_f)

            # 4) 生成 PossibleAction
            actions = self._build_actions(signals=list(self._registry.list()), state=state, now=now_f)
            out.created_actions = list(actions)
            with self._lock:
                self._actions_created += len(actions)
                self._last_actions = [a.action_id for a in actions]

            # 5) Filter
            decisions = self._filter.filter_batch(actions, state=state, now=now_f)
            out.decisions = list(decisions)
            for action, dec in zip(actions, decisions):
                self._filter.apply(action, dec, now=now_f)
                if dec.new_status == ACTION_STATUS_PENDING:
                    out.queued_actions.append(action)
                elif dec.new_status == ACTION_STATUS_FILTERED:
                    out.filtered_actions.append(action)
                elif dec.new_status == ACTION_STATUS_DISCARDED:
                    out.discarded_actions.append(action)
                elif dec.new_status == "deferred":
                    out.deferred_actions.append(action)

            with self._lock:
                self._actions_queued += len(out.queued_actions)
                self._actions_filtered += len(out.filtered_actions)
                self._actions_deferred += len(out.deferred_actions)
                self._actions_discarded += len(out.discarded_actions)

            # 6) 入队
            for a in out.queued_actions:
                self._queue.enqueue(a)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"tick 异常: {exc}"
            out.error = str(exc)
        finally:
            out.finished_at = self._safe_now()
        return out

    # --------------------------------------------------------
    # Signal 生成
    # --------------------------------------------------------
    def _build_signals(
        self,
        *,
        events: Sequence[IntegrationEvent],
        reflections: Sequence[ReflectionResult],
        now: float,
    ) -> List[InterestSignal]:
        """从事件 / 反思中提取 InterestSignal。"""
        signals: List[InterestSignal] = []
        # 1) 直接从 event payload 提取 topic
        topic_to_events: Dict[str, List[str]] = {}
        topic_to_refs: Dict[str, List[str]] = {}
        for ev in events:
            if not isinstance(ev, IntegrationEvent):
                continue
            topic = self._extract_topic_from_event(ev)
            if not topic:
                continue
            topic_to_events.setdefault(topic, []).append(ev.event_id)
        # 2) 从 reflection 的 source_event_ids 推断 topic(复用 event)
        for ref in reflections:
            if not isinstance(ref, ReflectionResult):
                continue
            topics_in_ref = self._extract_topics_from_reflection(ref, events=events)
            for t in topics_in_ref:
                topic_to_refs.setdefault(t, []).append(ref.reflection_id)
                if t not in topic_to_events:
                    # 收集 source_event_ids
                    for sid in ref.source_event_ids:
                        topic_to_events.setdefault(t, []).append(sid)
        # 3) 构造
        for topic, ev_ids in topic_to_events.items():
            ref_ids = topic_to_refs.get(topic, [])
            # strength/confidence
            count = len(ev_ids) + len(ref_ids)
            if count <= 0:
                continue
            strength = min(1.0, 0.3 + 0.1 * count)
            confidence = min(1.0, 0.35 + 0.07 * count)
            if strength < self._config.interest_min_strength:
                continue
            if confidence < self._config.interest_min_confidence:
                continue
            sig = InterestSignal(
                topic=topic,
                strength=strength,
                source_event_ids=list(ev_ids),
                source_reflection_ids=list(ref_ids),
                trend=DEFAULT_TREND,
                confidence=confidence,
                created_at=now,
                last_seen_at=now,
                rationale=f"由 {count} 条来源触发的兴趣信号",
            )
            signals.append(sig)
            if 0 < self._config.max_signals_per_tick <= len(signals):
                break
        return signals

    def _extract_topic_from_event(self, ev: IntegrationEvent) -> str:
        payload = ev.payload if isinstance(ev.payload, dict) else {}
        for k in _PAYLOAD_TOPIC_KEYS:
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                t = v.strip()
                if len(t) > 128:
                    t = t[:128]
                return t
        # 退化:event_type 后缀
        et = ev.event_type or ""
        if "." in et:
            tail = et.rsplit(".", 1)[-1]
            if tail and tail not in ("", "completed", "created", "recorded"):
                return tail[:64]
        return ""

    def _extract_topics_from_reflection(
        self,
        ref: ReflectionResult,
        *,
        events: Sequence[IntegrationEvent],
    ) -> List[str]:
        """从 ReflectionResult 提取 topics。

        策略:
        - 优先用 insight.description 包含的 topic
        - 退化:用 source_event_ids 对应 events 的 topic
        """
        topics: List[str] = []
        try:
            for ins in ref.insights:
                desc = getattr(ins, "description", "") or ""
                # 简单从 description 中提取关键词(空格的片段,过滤短)
                for seg in desc.split():
                    s = seg.strip(" ,.;:!?\"'()[]{}<>")
                    if not s:
                        continue
                    if len(s) < 3 or len(s) > 64:
                        continue
                    if s in topics:
                        continue
                    topics.append(s)
                    if len(topics) >= self._config.max_topics_per_signal:
                        return topics
        except Exception:
            pass
        if not topics:
            # 退化:从 events 中找 topic
            ev_by_id: Dict[str, IntegrationEvent] = {}
            for ev in events:
                if isinstance(ev, IntegrationEvent):
                    ev_by_id[ev.event_id] = ev
            for sid in ref.source_event_ids:
                ev = ev_by_id.get(sid)
                if ev is None:
                    continue
                t = self._extract_topic_from_event(ev)
                if t and t not in topics:
                    topics.append(t)
                    if len(topics) >= self._config.max_topics_per_signal:
                        return topics
        return topics

    def _advance_trends(
        self,
        *,
        signals: Sequence[InterestSignal],
        now: float,
    ) -> None:
        """把新出现的 signal 与已有 registry 中的合并:同 topic 标记 rising / stable / fading。"""
        new_topics = {s.topic for s in signals}
        with self._lock:
            existing = list(self._registry.list())
        for sig in existing:
            t = sig.topic
            if t in new_topics:
                # 同 topic 再次出现 -> rising
                sig.set_trend(INTEREST_TREND_RISING)
                # 提升 strength
                try:
                    new_strength = min(1.0, sig.strength + self._config.rising_bonus)
                    sig.strength = new_strength
                except Exception:
                    pass
                sig.touch(now)
            else:
                # 若已是 rising,转为 stable;若 stable 转为 fading(简单)
                if sig.trend == INTEREST_TREND_RISING:
                    sig.set_trend(INTEREST_TREND_STABLE)
                elif sig.trend == INTEREST_TREND_STABLE:
                    sig.set_trend(INTEREST_TREND_FADING)
                sig.decay(0.95)

    # --------------------------------------------------------
    # Action 生成
    # --------------------------------------------------------
    def _build_actions(
        self,
        *,
        signals: Sequence[InterestSignal],
        state: Optional[InternalState],
        now: float,
    ) -> List[PossibleAction]:
        """根据 InterestSignal 生成 PossibleAction(仅 rising/new + 强度足够)。"""
        actions: List[PossibleAction] = []
        for sig in signals:
            if sig.trend not in (INTEREST_TREND_RISING, INTEREST_TREND_NEW, INTEREST_TREND_STABLE):
                continue
            if sig.strength < self._config.strength_to_action:
                continue
            action_type = self._map_topic_to_action_type(sig.topic)
            urgency = self._decide_urgency(sig, state=state)
            effort = self._decide_effort(action_type)
            # 期望价值:strength * confidence
            expected_value = max(0.0, min(1.0, sig.strength * sig.confidence))
            confidence = sig.confidence
            # 优先级:expected_value + urgency 增益
            priority = expected_value + URGENCY_NUMERIC.get(urgency, 0.0) * 0.2
            priority = max(0.0, min(1.0, priority))
            action = PossibleAction(
                action_type=action_type,
                topic=sig.topic,
                rationale=(
                    f"兴趣趋势 {sig.trend},strength={sig.strength:.2f},"
                    f"confidence={sig.confidence:.2f}"
                ),
                supporting_signal_ids=[sig.signal_id],
                urgency=urgency,
                effort_estimate=effort,
                expected_value=expected_value,
                confidence=confidence,
                status=ACTION_STATUS_PENDING,
                priority=priority,
                created_at=now,
                updated_at=now,
                metadata={"source_count": len(sig.source_event_ids) + len(sig.source_reflection_ids)},
            )
            actions.append(action)
            if 0 < self._config.max_actions_per_tick <= len(actions):
                break
        return actions

    def _map_topic_to_action_type(self, topic: str) -> str:
        if not topic:
            return INTEREST_TO_ACTION_TYPE["default"]
        t_lower = topic.lower()
        for key, at in INTEREST_TO_ACTION_TYPE.items():
            if key == "default":
                continue
            if key in t_lower:
                return at
        return INTEREST_TO_ACTION_TYPE["default"]

    def _decide_urgency(
        self,
        sig: InterestSignal,
        *,
        state: Optional[InternalState],
    ) -> str:
        if state is not None and state.curiosity_drive >= 0.75:
            return ACTION_URGENCY_HIGH
        if sig.trend == INTEREST_TREND_RISING:
            return ACTION_URGENCY_NORMAL
        if sig.trend == INTEREST_TREND_FADING:
            return ACTION_URGENCY_LOW
        return ACTION_URGENCY_NORMAL

    def _decide_effort(self, action_type: str) -> str:
        if action_type in (ACTION_TYPE_LEARN, ACTION_TYPE_RECOMMEND, ACTION_TYPE_ASK):
            return ACTION_EFFORT_MEDIUM
        if action_type == ACTION_TYPE_REMIND:
            return ACTION_EFFORT_LOW
        return ACTION_EFFORT_LOW

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def _read_state(self) -> Optional[InternalState]:
        store = self._state_store
        if store is None:
            return None
        # 优先调用 get()
        for method in ("get", "snapshot", "state"):
            fn = getattr(store, method, None)
            if callable(fn):
                try:
                    v = fn()
                except Exception:
                    v = None
                if isinstance(v, InternalState):
                    return v
        # 退化:state 属性
        st = getattr(store, "state", None)
        if isinstance(st, InternalState):
            return st
        return None

    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "enabled": self._config.enabled,
                "tick_count": self._tick_count,
                "signals_created": self._signals_created,
                "actions_created": self._actions_created,
                "actions_queued": self._actions_queued,
                "actions_filtered": self._actions_filtered,
                "actions_deferred": self._actions_deferred,
                "actions_discarded": self._actions_discarded,
                "last_error": self._last_error,
                "last_tick_at": self._last_tick_at,
                "registry": self._registry.describe(),
                "filter": self._filter.describe(),
                "queue": self._queue.describe(),
                "config": {
                    "interest_min_strength": self._config.interest_min_strength,
                    "interest_min_confidence": self._config.interest_min_confidence,
                    "strength_to_action": self._config.strength_to_action,
                    "rising_bonus": self._config.rising_bonus,
                    "max_signals_per_tick": self._config.max_signals_per_tick,
                    "max_actions_per_tick": self._config.max_actions_per_tick,
                },
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"InitiativeEngine(name={self._name!r}, enabled={self._config.enabled}, "
                f"ticks={self._tick_count}, signals={self._signals_created}, "
                f"actions={self._actions_created})"
            )


# ============================================================
# 内部:urgency numeric 映射
# ============================================================
URGENCY_NUMERIC = {
    ACTION_URGENCY_LOW: 0.2,
    ACTION_URGENCY_NORMAL: 0.5,
    ACTION_URGENCY_HIGH: 0.8,
}


# ============================================================
# 工厂
# ============================================================
def build_default_initiative_engine(
    *,
    clock: Optional[Clock] = None,
    state_store: Optional[Any] = None,
    signal_registry: Optional[InterestSignalRegistry] = None,
    action_filter: Optional[ActionFilter] = None,
    queue: Optional[InitiativeQueue] = None,
    config: Optional[InitiativeConfig] = None,
) -> InitiativeEngine:
    return InitiativeEngine(
        clock=clock,
        state_store=state_store,
        signal_registry=signal_registry,
        action_filter=action_filter,
        queue=queue,
        config=config,
    )


# ============================================================
# 工具
# ============================================================
def _clip01(value: Any, *, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        return default
    if v != v:
        return default
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


__all__ = [
    # 常量
    "INITIATIVE_ENGINE_SCHEMA_VERSION",
    # 数据类
    "InitiativeConfig",
    "InitiativeTickOutput",
    # 类
    "InitiativeEngine",
    # 工厂
    "build_default_initiative_engine",
]
