# -*- coding: utf-8 -*-
"""
src/runtime/reflection/growth_reflection.py

Phase 5.0-D3-B: 长期增长反思策略 (7天以上窗口)。

职责:
- 总结长期变化
- 触发: 7 天以上窗口
- 输出: 长期 trend Insight + Suggestion

约束:
- 不调用 LLM
- 使用规则分析(趋势计算)
- 所有 Insight/Suggestion 必含 evidence_event_ids
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.reflection.reflection_result import (
    INSIGHT_CATEGORY_GROWTH,
    INSIGHT_CATEGORY_PATTERN,
    INSIGHT_CATEGORY_PREFERENCE,
    Insight,
    ReflectionResult,
    StateChangeSuggestion,
    build_empty_reflection_result,
)
from src.runtime.reflection.reflection_strategy import (
    BaseReflectionStrategy,
    ReflectionContext,
)


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
GROWTH_REFLECTION_SCHEMA_VERSION = "1.0"

DEFAULT_GROWTH_WINDOW_SECONDS = 7 * 24 * 60 * 60  # 7 天

# 时间分段数(用于趋势分析)
DEFAULT_NUM_BUCKETS = 7

# 增长判定的最小事件数
MIN_GROWTH_EVENTS = 5
# 衰减判定的最小事件数
MIN_DECAY_EVENTS = 3

# 趋势判定阈值(后段 vs 前段)
GROWTH_RATIO = 1.5
DECAY_RATIO = 0.5

# confidence / delta 上限
MAX_GROWTH_CONFIDENCE = 0.95
MIN_GROWTH_CONFIDENCE = 0.5
MAX_GROWTH_DELTA = 0.15
BASE_GROWTH_DELTA = 0.05

TOPIC_KEYS = ("topic", "interest", "category", "subject")
TRAIT_KEYS = ("trait", "name", "tag")


# ============================================================
# GrowthReflection
# ============================================================
class GrowthReflection(BaseReflectionStrategy):
    """长期增长反思策略(规则分析)。"""

    DEFAULT_NAME = "growth_reflection"
    DEFAULT_REFLECTION_TYPE = "growth"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        window_seconds: Optional[float] = None,
        num_buckets: Optional[int] = None,
    ) -> None:
        super().__init__(
            name=name or self.DEFAULT_NAME,
            reflection_type=self.DEFAULT_REFLECTION_TYPE,
        )
        try:
            ws = float(window_seconds) if window_seconds is not None else DEFAULT_GROWTH_WINDOW_SECONDS
        except Exception:
            ws = DEFAULT_GROWTH_WINDOW_SECONDS
        if ws <= 0:
            ws = DEFAULT_GROWTH_WINDOW_SECONDS
        self._window_seconds = ws
        try:
            nb = int(num_buckets) if num_buckets is not None else DEFAULT_NUM_BUCKETS
        except Exception:
            nb = DEFAULT_NUM_BUCKETS
        if nb < 2:
            nb = 2
        if nb > 64:
            nb = 64
        self._num_buckets = nb

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    @property
    def num_buckets(self) -> int:
        return self._num_buckets

    @property
    def schema_version(self) -> str:
        return GROWTH_REFLECTION_SCHEMA_VERSION

    # --------------------------------------------------------
    # 决策
    # --------------------------------------------------------
    def should_run(self, context: ReflectionContext) -> bool:
        if not isinstance(context, ReflectionContext):
            return False
        # 至少 5 个事件 + 窗口 >= 7 天
        if context.event_count < MIN_GROWTH_EVENTS:
            return False
        return context.window_seconds() >= self._window_seconds

    # --------------------------------------------------------
    # 反思
    # --------------------------------------------------------
    def reflect(self, context: ReflectionContext) -> ReflectionResult:
        if not isinstance(context, ReflectionContext):
            return build_empty_reflection_result(
                reflection_type=self._reflection_type,
                now=0.0,
            )
        result = build_empty_reflection_result(
            reflection_type=self._reflection_type,
            now=context.clock_now,
        )
        result.window_start = context.window_start
        result.window_end = context.window_end
        result.source_event_ids = context.event_ids()
        events = context.events
        if not events:
            return result

        # 按 topic 统计 + 趋势分析
        topic_buckets = self._bucket_topics(events, context)
        insights: List[Insight] = []
        suggestions: List[StateChangeSuggestion] = []
        for topic, buckets in topic_buckets.items():
            trend = self._compute_trend(buckets)
            if trend == "growing":
                insights.append(
                    self._build_growth_insight(topic, buckets, events, context)
                )
                suggestions.append(
                    self._build_interest_suggestion(topic, trend="growing", events=events, context=context)
                )
            elif trend == "decaying":
                insights.append(
                    self._build_decay_insight(topic, buckets, events, context)
                )
                suggestions.append(
                    self._build_interest_suggestion(topic, trend="decaying", events=events, context=context)
                )
        result.insights = insights
        result.suggested_changes = suggestions

        # 整体 confidence / evidence_strength
        result.confidence = self._calc_confidence(events, context)
        result.evidence_strength = self._calc_evidence_strength(events, context)
        return result

    # --------------------------------------------------------
    # 内部:桶
    # --------------------------------------------------------
    def _bucket_topics(
        self,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> Dict[str, List[int]]:
        """按 topic 分桶(每个 topic 形成一个时间序列)。"""
        start = float(context.window_start)
        end = float(context.window_end)
        if end <= start:
            return {}
        window = end - start
        if window <= 0:
            return {}
        n = self._num_buckets
        bucket_size = window / n
        topics: Dict[str, List[int]] = {}
        for e in events:
            topic = self._extract_topic(e)
            if not topic:
                continue
            try:
                ts = float(e.timestamp or 0.0)
            except Exception:
                continue
            # 计算桶 index
            try:
                idx = int((ts - start) // bucket_size)
            except Exception:
                continue
            if idx < 0:
                idx = 0
            if idx >= n:
                idx = n - 1
            if topic not in topics:
                topics[topic] = [0] * n
            topics[topic][idx] += 1
        return topics

    def _compute_trend(self, buckets: List[int]) -> str:
        """简单趋势计算: 前半均值 vs 后半均值。"""
        if not buckets or len(buckets) < 2:
            return "stable"
        n = len(buckets)
        half = n // 2
        if half < 1:
            return "stable"
        front_sum = sum(buckets[:half])
        back_sum = sum(buckets[half:])
        # 加 1 防止除零
        front = front_sum / max(1, half)
        back = back_sum / max(1, n - half)
        if front <= 0.01 and back <= 0.01:
            return "stable"
        if front <= 0.01 and back > 0.01:
            return "growing"
        ratio = back / max(0.01, front)
        if ratio >= GROWTH_RATIO:
            return "growing"
        if ratio <= DECAY_RATIO:
            return "decaying"
        return "stable"

    # --------------------------------------------------------
    # 内部:insight
    # --------------------------------------------------------
    def _build_growth_insight(
        self,
        topic: str,
        buckets: List[int],
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> Insight:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and self._extract_topic(e) == topic
        ][:64]
        total = sum(buckets)
        confidence = min(MAX_GROWTH_CONFIDENCE, MIN_GROWTH_CONFIDENCE + total * 0.01)
        return Insight(
            category=INSIGHT_CATEGORY_GROWTH,
            description=f"长期观察:主题 {topic} 出现增长趋势(总 {total} 次)",
            supporting_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"topic": topic, "total": total, "buckets": list(buckets), "trend": "growing"},
        )

    def _build_decay_insight(
        self,
        topic: str,
        buckets: List[int],
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> Insight:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and self._extract_topic(e) == topic
        ][:64]
        total = sum(buckets)
        confidence = min(MAX_GROWTH_CONFIDENCE, MIN_GROWTH_CONFIDENCE + total * 0.01)
        return Insight(
            category=INSIGHT_CATEGORY_PATTERN,
            description=f"长期观察:主题 {topic} 出现衰减趋势(总 {total} 次)",
            supporting_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"topic": topic, "total": total, "buckets": list(buckets), "trend": "decaying"},
        )

    # --------------------------------------------------------
    # 内部:suggestion
    # --------------------------------------------------------
    def _build_interest_suggestion(
        self,
        topic: str,
        *,
        trend: str,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> StateChangeSuggestion:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and self._extract_topic(e) == topic
        ][:64]
        if trend == "growing":
            delta = min(MAX_GROWTH_DELTA, BASE_GROWTH_DELTA + 0.02 * len(evidence_ids))
            reason = f"长期趋势:主题 {topic} 增长,兴趣强化建议"
        elif trend == "decaying":
            delta = -min(MAX_GROWTH_DELTA, BASE_GROWTH_DELTA)
            reason = f"长期趋势:主题 {topic} 衰减,兴趣弱化建议"
        else:
            delta = 0.0
            reason = f"长期趋势:主题 {topic} 稳定"
        confidence = min(MAX_GROWTH_CONFIDENCE, MIN_GROWTH_CONFIDENCE + len(evidence_ids) * 0.01)
        return StateChangeSuggestion(
            target_field=f"interest_state.{topic}",
            old_value=None,
            suggested_value=None,
            delta=delta,
            reason=reason,
            evidence_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"topic": topic, "trend": trend, "kind": "interest"},
        )

    # --------------------------------------------------------
    # 内部:辅助
    # --------------------------------------------------------
    def _extract_topic(self, event: IntegrationEvent) -> str:
        if not isinstance(event, IntegrationEvent):
            return ""
        payload = event.payload or {}
        if not isinstance(payload, dict):
            return ""
        for k in TOPIC_KEYS:
            v = payload.get(k)
            if v is not None:
                s = str(v).strip()
                if s:
                    return s
        return ""

    # --------------------------------------------------------
    # 内部:confidence
    # --------------------------------------------------------
    def _calc_confidence(
        self,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> float:
        if not events:
            return 0.0
        n = len(events)
        window = context.window_seconds()
        # 事件密度高 / 窗口大 → 置信度高
        if window <= 0:
            return 0.5
        density = n / (window / 3600.0)  # 每小时事件数
        return min(1.0, 0.5 + min(density, 5.0) * 0.05)

    def _calc_evidence_strength(
        self,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> float:
        if not events:
            return 0.0
        n = len(events)
        # 事件越多,证据越强
        return min(1.0, n * 0.05)

    # --------------------------------------------------------
    # 描述
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        d = super().describe()
        d.update({
            "window_seconds": self._window_seconds,
            "num_buckets": self._num_buckets,
            "schema_version": self.schema_version,
        })
        return d


__all__ = [
    "GROWTH_REFLECTION_SCHEMA_VERSION",
    "DEFAULT_GROWTH_WINDOW_SECONDS",
    "DEFAULT_NUM_BUCKETS",
    "MIN_GROWTH_EVENTS",
    "GrowthReflection",
]
