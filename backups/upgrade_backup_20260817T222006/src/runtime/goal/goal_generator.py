# -*- coding: utf-8 -*-
"""
src/runtime/goal/goal_generator.py

Phase 5.0-D3-D: GoalGenerator 目标生成器。

职责:
- 输入:SelfModel / ReflectionResult / InterestSignal / PossibleAction / Desire
- 输出:GoalState candidate
- 规则实现,不调用 LLM
- 决定何时从 Desire 提升为 Goal

约束:
- 不依赖业务模块
- 不调用 LLM / DB / Network
- 所有 Goal 必须有 evidence
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from src.runtime.goal.desire import (
    DESIRE_TREND_RISING,
    DESIRE_TREND_STABLE,
    DEFAULT_DESIRE_TREND,
    Desire,
    DesireRegistry,
)
from src.runtime.goal.goal_state import (
    ALL_GOAL_TYPES,
    DEFAULT_GOAL_TYPE,
    GOAL_TYPE_CREATIVE,
    GOAL_TYPE_EXPLORATION,
    GOAL_TYPE_LEARNING,
    GOAL_TYPE_PERSONAL_GROWTH,
    GOAL_TYPE_RELATIONSHIP,
    GoalState,
    build_candidate_goal,
)
from src.runtime.initiative.interest_signal import (
    INTEREST_TREND_RISING,
    INTEREST_TREND_STABLE,
    InterestSignal,
)
from src.runtime.initiative.possible_action import PossibleAction
from src.runtime.reflection.reflection_result import ReflectionResult
from src.runtime.lifecycle.internal.clock import Clock, SystemClock


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GOAL_GENERATOR_SCHEMA_VERSION = "1.0"

# 提升阈值
DEFAULT_DESIRE_MIN_STRENGTH = 0.45
DEFAULT_DESIRE_MIN_CONFIDENCE = 0.40
DEFAULT_REFLECTION_MIN_COUNT = 2  # 至少 2 次反思支撑
DEFAULT_SIGNAL_MIN_COUNT = 2
DEFAULT_MAX_CANDIDATES_PER_TICK = 8

# topic -> goal_type 映射
TOPIC_TO_GOAL_TYPE = {
    "ai_art": GOAL_TYPE_CREATIVE,
    "music": GOAL_TYPE_EXPLORATION,
    "code": GOAL_TYPE_LEARNING,
    "writing": GOAL_TYPE_CREATIVE,
    "reading": GOAL_TYPE_LEARNING,
    "news": GOAL_TYPE_EXPLORATION,
    "weather": GOAL_TYPE_EXPLORATION,
    "social": GOAL_TYPE_RELATIONSHIP,
    "reminder": GOAL_TYPE_PERSONAL_GROWTH,
    "recommend": GOAL_TYPE_PERSONAL_GROWTH,
    "default": GOAL_TYPE_PERSONAL_GROWTH,
}


# ============================================================
# 配置
# ============================================================
@dataclass
class GoalGeneratorConfig:
    """GoalGenerator 配置。"""

    desire_min_strength: float = DEFAULT_DESIRE_MIN_STRENGTH
    desire_min_confidence: float = DEFAULT_DESIRE_MIN_CONFIDENCE
    reflection_min_count: int = DEFAULT_REFLECTION_MIN_COUNT
    signal_min_count: int = DEFAULT_SIGNAL_MIN_COUNT
    max_candidates_per_tick: int = DEFAULT_MAX_CANDIDATES_PER_TICK
    enabled: bool = True

    def __post_init__(self) -> None:
        try:
            self.desire_min_strength = _clip01(self.desire_min_strength, default=DEFAULT_DESIRE_MIN_STRENGTH)
        except Exception:
            self.desire_min_strength = DEFAULT_DESIRE_MIN_STRENGTH
        try:
            self.desire_min_confidence = _clip01(self.desire_min_confidence, default=DEFAULT_DESIRE_MIN_CONFIDENCE)
        except Exception:
            self.desire_min_confidence = DEFAULT_DESIRE_MIN_CONFIDENCE
        try:
            self.reflection_min_count = max(1, int(self.reflection_min_count))
        except Exception:
            self.reflection_min_count = DEFAULT_REFLECTION_MIN_COUNT
        try:
            self.signal_min_count = max(1, int(self.signal_min_count))
        except Exception:
            self.signal_min_count = DEFAULT_SIGNAL_MIN_COUNT
        try:
            self.max_candidates_per_tick = max(1, int(self.max_candidates_per_tick))
        except Exception:
            self.max_candidates_per_tick = DEFAULT_MAX_CANDIDATES_PER_TICK


# ============================================================
# 生成输入/输出
# ============================================================
@dataclass
class GoalGeneratorInput:
    """生成器输入。"""

    desires: List[Desire] = field(default_factory=list)
    reflections: List[ReflectionResult] = field(default_factory=list)
    signals: List[InterestSignal] = field(default_factory=list)
    actions: List[PossibleAction] = field(default_factory=list)
    integration_events: List[Any] = field(default_factory=list)
    now: float = 0.0


@dataclass
class GoalGeneratorOutput:
    """生成器输出。"""

    candidates: List[GoalState] = field(default_factory=list)
    skipped_desires: List[Desire] = field(default_factory=list)
    skipped_reason: Dict[str, str] = field(default_factory=dict)
    started_at: float = 0.0
    finished_at: float = 0.0
    error: str = ""


# ============================================================
# GoalGenerator
# ============================================================
class GoalGenerator:
    """从 Desire/Reflection/InterestSignal 提升出 Goal candidate。"""

    def __init__(
        self,
        *,
        config: Optional[GoalGeneratorConfig] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self._config = config if isinstance(config, GoalGeneratorConfig) else GoalGeneratorConfig()
        self._clock: Clock = clock or SystemClock()
        self._lock = threading.RLock()
        # 统计
        self._generated_count = 0
        self._skipped_count = 0
        self._last_error = ""
        # 反查:topic -> desire_count 用于支持 reflection/signal 计数
        self._seen_signal_count_by_topic: Dict[str, int] = {}
        self._seen_reflection_count_by_topic: Dict[str, int] = {}

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def config(self) -> GoalGeneratorConfig:
        return self._config

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def generated_count(self) -> int:
        with self._lock:
            return self._generated_count

    @property
    def skipped_count(self) -> int:
        with self._lock:
            return self._skipped_count

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._config.enabled = bool(enabled)

    # --------------------------------------------------------
    # 核心:generate
    # --------------------------------------------------------
    def generate(
        self,
        *,
        desires: Optional[Sequence[Desire]] = None,
        reflections: Optional[Sequence[ReflectionResult]] = None,
        signals: Optional[Sequence[InterestSignal]] = None,
        actions: Optional[Sequence[PossibleAction]] = None,
        integration_events: Optional[Sequence[Any]] = None,
        now: Optional[float] = None,
    ) -> GoalGeneratorOutput:
        """从一组输入生成 Goal candidate 列表。"""
        out = GoalGeneratorOutput()
        try:
            out.started_at = float(now) if now is not None else self._safe_now()
        except Exception:
            out.started_at = 0.0
        try:
            if not self._config.enabled:
                out.skipped_reason["global"] = "disabled"
                out.finished_at = self._safe_now()
                return out

            ds = [d for d in (desires or []) if isinstance(d, Desire)]
            sigs = [s for s in (signals or []) if isinstance(s, InterestSignal)]
            refs = [r for r in (reflections or []) if isinstance(r, ReflectionResult)]
            acts = [a for a in (actions or []) if isinstance(a, PossibleAction)]
            evs = list(integration_events or [])

            # 统计 topic -> count
            sig_count: Dict[str, int] = {}
            for s in sigs:
                t = getattr(s, "topic", "")
                if t:
                    sig_count[t] = sig_count.get(t, 0) + 1
            ref_count: Dict[str, int] = {}
            for r in refs:
                # 简化:从 source_event_ids + insights 推断 topic
                for ev in evs:
                    if not isinstance(ev, object):
                        continue
                # 用 insight description 中包含的 topic
                for ins in getattr(r, "insights", []) or []:
                    desc = getattr(ins, "description", "") or ""
                    for t in sig_count.keys():
                        if t and t in desc:
                            ref_count[t] = ref_count.get(t, 0) + 1
                # 退化:用 source_event_ids 中的 event_id 关联
                for sid in getattr(r, "source_event_ids", []) or []:
                    for ev in evs:
                        if not isinstance(ev, object):
                            continue
                        ev_id = getattr(ev, "event_id", None)
                        if ev_id and ev_id == sid:
                            t = self._extract_topic_from_event(ev)
                            if t:
                                ref_count[t] = ref_count.get(t, 0) + 1

            for d in ds:
                topic = d.topic
                if not topic:
                    out.skipped_desires.append(d)
                    out.skipped_reason[d.desire_id] = "no_topic"
                    with self._lock:
                        self._skipped_count += 1
                    continue
                # 强度/置信度阈值
                if d.strength < self._config.desire_min_strength:
                    out.skipped_desires.append(d)
                    out.skipped_reason[d.desire_id] = "low_strength"
                    with self._lock:
                        self._skipped_count += 1
                    continue
                if d.confidence < self._config.desire_min_confidence:
                    out.skipped_desires.append(d)
                    out.skipped_reason[d.desire_id] = "low_confidence"
                    with self._lock:
                        self._skipped_count += 1
                    continue
                # evidence 校验
                if not d.has_evidence():
                    out.skipped_desires.append(d)
                    out.skipped_reason[d.desire_id] = "no_evidence"
                    with self._lock:
                        self._skipped_count += 1
                    continue
                # 重复支撑信号
                s_count = sig_count.get(topic, 0)
                r_count = ref_count.get(topic, 0)
                # evidence 数量统计:来自 desire 自身 + signals + reflections
                total_evidence = (
                    len(d.supporting_signal_ids or [])
                    + len(d.supporting_reflection_ids or [])
                    + s_count
                    + r_count
                )
                # 至少需要 1 条 evidence 才生成 goal
                if total_evidence < 1:
                    out.skipped_desires.append(d)
                    out.skipped_reason[d.desire_id] = "insufficient_evidence_count"
                    with self._lock:
                        self._skipped_count += 1
                    continue
                # 构造 Goal
                goal_type = self._map_topic_to_goal_type(topic)
                title = self._build_title(topic, goal_type)
                description = self._build_description(topic, d, s_count, r_count)
                reason = self._build_reason(d, s_count, r_count)
                importance = self._calc_importance(d, s_count, r_count)
                confidence = self._calc_confidence(d, s_count, r_count)
                priority = self._calc_priority(d, s_count, r_count)
                # 收集 source_event_ids:从 desire 支撑信号 + reflection/event 关联
                source_event_ids = self._collect_source_events(d, evs)
                # 构造
                g = build_candidate_goal(
                    title=title,
                    description=description,
                    goal_type=goal_type,
                    source_event_ids=source_event_ids,
                    source_desire_ids=[d.desire_id],
                    reason=reason,
                    importance=importance,
                    confidence=confidence,
                    now=out.started_at,
                )
                g.update_priority(priority, now=out.started_at)
                out.candidates.append(g)
                with self._lock:
                    self._generated_count += 1
                if 0 < self._config.max_candidates_per_tick <= len(out.candidates):
                    break
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._last_error = f"generate 异常: {exc}"
            out.error = str(exc)
        finally:
            out.finished_at = self._safe_now()
        return out

    # --------------------------------------------------------
    # 内部:从 IntegrationEvent 提取 topic
    # --------------------------------------------------------
    def _extract_topic_from_event(self, ev: Any) -> str:
        if not isinstance(ev, object):
            return ""
        payload = getattr(ev, "payload", None)
        if not isinstance(payload, dict):
            return ""
        for k in ("topic", "subject", "theme", "interest", "name", "title", "tag"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                t = v.strip()
                return t[:128]
        return ""

    def _collect_source_events(self, desire: Desire, evs: Sequence[Any]) -> List[str]:
        """从 desire + events 中收集 source_event_ids。"""
        out: List[str] = []
        seen = set()
        for sid in desire.supporting_signal_ids or []:
            s = str(sid)
            if s and s not in seen:
                out.append(s)
                seen.add(s)
        for rid in desire.supporting_reflection_ids or []:
            s = str(rid)
            if s and s not in seen:
                out.append(s)
                seen.add(s)
        for ev in evs or []:
            if not isinstance(ev, object):
                continue
            t = self._extract_topic_from_event(ev)
            if t and t == desire.topic:
                eid = getattr(ev, "event_id", None)
                if eid:
                    s = str(eid)
                    if s not in seen:
                        out.append(s)
                        seen.add(s)
        return out

    # --------------------------------------------------------
    # 内部:topic -> goal_type
    # --------------------------------------------------------
    def _map_topic_to_goal_type(self, topic: str) -> str:
        if not topic:
            return TOPIC_TO_GOAL_TYPE["default"]
        t_lower = topic.lower()
        for key, gt in TOPIC_TO_GOAL_TYPE.items():
            if key == "default":
                continue
            if key in t_lower:
                return gt
        return TOPIC_TO_GOAL_TYPE["default"]

    def _build_title(self, topic: str, goal_type: str) -> str:
        # 简单 title 模板
        prefix_map = {
            GOAL_TYPE_PERSONAL_GROWTH: "成长:",
            GOAL_TYPE_LEARNING: "学习:",
            GOAL_TYPE_CREATIVE: "创造:",
            GOAL_TYPE_RELATIONSHIP: "关系:",
            GOAL_TYPE_EXPLORATION: "探索:",
        }
        prefix = prefix_map.get(goal_type, "目标:")
        return f"{prefix}{topic}"

    def _build_description(
        self,
        topic: str,
        desire: Desire,
        signal_count: int,
        reflection_count: int,
    ) -> str:
        return (
            f"围绕 {topic} 的目标;由 {signal_count} 条兴趣信号、"
            f"{reflection_count} 条反思与 1 个愿望支撑"
        )

    def _build_reason(
        self,
        desire: Desire,
        signal_count: int,
        reflection_count: int,
    ) -> str:
        parts = []
        if signal_count > 0:
            parts.append(f"{signal_count} 次兴趣")
        if reflection_count > 0:
            parts.append(f"{reflection_count} 次反思")
        if desire.trend and desire.trend != DEFAULT_DESIRE_TREND:
            parts.append(f"趋势={desire.trend}")
        if not parts:
            parts.append("由 Desire 触发")
        return f"基于 {' + '.join(parts)} 产生目标"

    def _calc_importance(
        self,
        desire: Desire,
        signal_count: int,
        reflection_count: int,
    ) -> float:
        # 基础:strength * 0.5 + 趋势加成 + 信号/反思加成
        base = desire.strength * 0.5
        bonus = 0.0
        if desire.trend in (DESIRE_TREND_RISING, DESIRE_TREND_STABLE):
            bonus += 0.1
        bonus += min(0.2, signal_count * 0.05)
        bonus += min(0.2, reflection_count * 0.05)
        v = base + bonus
        return _clip01(v, default=0.5)

    def _calc_confidence(
        self,
        desire: Desire,
        signal_count: int,
        reflection_count: int,
    ) -> float:
        base = desire.confidence * 0.6
        bonus = 0.0
        bonus += min(0.2, signal_count * 0.04)
        bonus += min(0.2, reflection_count * 0.04)
        v = base + bonus
        return _clip01(v, default=0.5)

    def _calc_priority(
        self,
        desire: Desire,
        signal_count: int,
        reflection_count: int,
    ) -> float:
        # priority = importance * 0.6 + confidence * 0.4
        imp = self._calc_importance(desire, signal_count, reflection_count)
        cf = self._calc_confidence(desire, signal_count, reflection_count)
        v = imp * 0.6 + cf * 0.4
        return _clip01(v, default=0.5)

    # --------------------------------------------------------
    # 工具
    # --------------------------------------------------------
    def _safe_now(self) -> float:
        try:
            return float(self._clock.now())
        except Exception:
            return 0.0

    def describe(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._config.enabled,
                "generated_count": self._generated_count,
                "skipped_count": self._skipped_count,
                "last_error": self._last_error,
                "config": {
                    "desire_min_strength": self._config.desire_min_strength,
                    "desire_min_confidence": self._config.desire_min_confidence,
                    "reflection_min_count": self._config.reflection_min_count,
                    "signal_min_count": self._config.signal_min_count,
                    "max_candidates_per_tick": self._config.max_candidates_per_tick,
                },
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"GoalGenerator(enabled={self._config.enabled}, "
                f"generated={self._generated_count}, skipped={self._skipped_count})"
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


# ============================================================
# 工厂
# ============================================================
def build_default_goal_generator() -> GoalGenerator:
    return GoalGenerator()


__all__ = [
    # 常量
    "GOAL_GENERATOR_SCHEMA_VERSION",
    "TOPIC_TO_GOAL_TYPE",
    "DEFAULT_DESIRE_MIN_STRENGTH",
    "DEFAULT_DESIRE_MIN_CONFIDENCE",
    "DEFAULT_REFLECTION_MIN_COUNT",
    "DEFAULT_SIGNAL_MIN_COUNT",
    "DEFAULT_MAX_CANDIDATES_PER_TICK",
    # 配置
    "GoalGeneratorConfig",
    # 数据
    "GoalGeneratorInput",
    "GoalGeneratorOutput",
    # 类
    "GoalGenerator",
    # 工厂
    "build_default_goal_generator",
]
