# -*- coding: utf-8 -*-
"""
src/runtime/reflection/daily_reflection.py

Phase 5.0-D3-B: 每日反思策略 (24小时窗口)。

职责:
- 总结近期模式
- 触发: 24 小时窗口
- 输出: 多个 Insight + 多个 Suggestion

约束:
- 不调用 LLM
- 使用规则分析(计数 / 频率 / 类别统计)
- 所有 Insight/Suggestion 必含 evidence_event_ids
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.reflection.reflection_result import (
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
DAILY_REFLECTION_SCHEMA_VERSION = "1.0"

DEFAULT_DAILY_WINDOW_SECONDS = 24 * 60 * 60  # 24 小时

# 频率阈值(事件数 >= N 时,产生 preference/pattern insight)
PREFERENCE_FREQUENCY_THRESHOLD = 3
PATTERN_FREQUENCY_THRESHOLD = 5

# confidence 计算常量
BASE_FREQUENCY_CONFIDENCE = 0.5
CONFIDENCE_PER_EXTRA_EVENT = 0.05
MAX_FREQUENCY_CONFIDENCE = 0.95

# delta 计算常量
BASE_INTEREST_DELTA = 0.05
MAX_INTEREST_DELTA = 0.2
BASE_TRAIT_DELTA = 0.02
MAX_TRAIT_DELTA = 0.1

# payload 中用于识别 topic / interest 的字段
TOPIC_KEYS = ("topic", "interest", "category", "subject")
TRAIT_KEYS = ("trait", "name", "tag")


# ============================================================
# DailyReflection
# ============================================================
class DailyReflection(BaseReflectionStrategy):
    """每日反思策略(规则分析)。"""

    DEFAULT_NAME = "daily_reflection"
    DEFAULT_REFLECTION_TYPE = "daily"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        window_seconds: Optional[float] = None,
        preference_threshold: Optional[int] = None,
        pattern_threshold: Optional[int] = None,
    ) -> None:
        super().__init__(
            name=name or self.DEFAULT_NAME,
            reflection_type=self.DEFAULT_REFLECTION_TYPE,
        )
        try:
            ws = float(window_seconds) if window_seconds is not None else DEFAULT_DAILY_WINDOW_SECONDS
        except Exception:
            ws = DEFAULT_DAILY_WINDOW_SECONDS
        if ws <= 0:
            ws = DEFAULT_DAILY_WINDOW_SECONDS
        self._window_seconds = ws
        try:
            self._preference_threshold = (
                int(preference_threshold) if preference_threshold is not None
                else PREFERENCE_FREQUENCY_THRESHOLD
            )
        except Exception:
            self._preference_threshold = PREFERENCE_FREQUENCY_THRESHOLD
        if self._preference_threshold < 1:
            self._preference_threshold = 1
        try:
            self._pattern_threshold = (
                int(pattern_threshold) if pattern_threshold is not None
                else PATTERN_FREQUENCY_THRESHOLD
            )
        except Exception:
            self._pattern_threshold = PATTERN_FREQUENCY_THRESHOLD
        if self._pattern_threshold < 1:
            self._pattern_threshold = 1

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    @property
    def preference_threshold(self) -> int:
        return self._preference_threshold

    @property
    def pattern_threshold(self) -> int:
        return self._pattern_threshold

    @property
    def schema_version(self) -> str:
        return DAILY_REFLECTION_SCHEMA_VERSION

    # --------------------------------------------------------
    # 决策
    # --------------------------------------------------------
    def should_run(self, context: ReflectionContext) -> bool:
        if not isinstance(context, ReflectionContext):
            return False
        # 至少 1 个事件
        return context.event_count > 0

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
            # 空事件:无 insight / suggestion
            return result

        # 1. 按 event_type 统计
        type_counter = self._count_event_types(events)
        # 2. 按 topic 统计(payload 中)
        topic_counter = self._count_topics(events)
        # 3. 按 trait 统计(payload 中)
        trait_counter = self._count_traits(events)
        # 4. 生成 insights
        insights: List[Insight] = []
        for et, cnt in type_counter.items():
            if cnt >= self._pattern_threshold:
                insights.append(self._build_pattern_insight(et, cnt, events, context))
            elif cnt >= self._preference_threshold:
                insights.append(self._build_preference_insight(et, cnt, events, context))
        for topic, cnt in topic_counter.items():
            if cnt >= self._preference_threshold:
                insights.append(self._build_topic_preference_insight(topic, cnt, events, context))
        # 5. 生成 suggestions
        suggestions: List[StateChangeSuggestion] = []
        for topic, cnt in topic_counter.items():
            if cnt >= self._preference_threshold:
                suggestions.append(
                    self._build_interest_suggestion(topic, cnt, events, context)
                )
        for trait, cnt in trait_counter.items():
            if cnt >= self._preference_threshold:
                suggestions.append(
                    self._build_trait_suggestion(trait, cnt, events, context)
                )
        result.insights = insights
        result.suggested_changes = suggestions

        # 计算 confidence / evidence_strength
        result.confidence = self._calc_confidence(events)
        result.evidence_strength = self._calc_evidence_strength(events)
        return result

    # --------------------------------------------------------
    # 内部:统计
    # --------------------------------------------------------
    def _count_event_types(self, events: List[IntegrationEvent]) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for e in events:
            et = str(e.event_type or "")
            if not et:
                continue
            c[et] = c.get(et, 0) + 1
        return c

    def _count_topics(self, events: List[IntegrationEvent]) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for e in events:
            topic = self._extract_topic(e)
            if topic:
                c[topic] = c.get(topic, 0) + 1
        return c

    def _count_traits(self, events: List[IntegrationEvent]) -> Dict[str, int]:
        c: Dict[str, int] = {}
        for e in events:
            trait = self._extract_trait(e)
            if trait:
                c[trait] = c.get(trait, 0) + 1
        return c

    def _extract_topic(self, event: IntegrationEvent) -> str:
        """从 payload 中抽取 topic 字段。"""
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
        # 退化: 使用 event_type
        return ""

    def _extract_trait(self, event: IntegrationEvent) -> str:
        if not isinstance(event, IntegrationEvent):
            return ""
        payload = event.payload or {}
        if not isinstance(payload, dict):
            return ""
        for k in TRAIT_KEYS:
            v = payload.get(k)
            if v is not None:
                s = str(v).strip()
                if s:
                    return s
        return ""

    # --------------------------------------------------------
    # 内部:生成 insight
    # --------------------------------------------------------
    def _build_pattern_insight(
        self,
        event_type: str,
        count: int,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> Insight:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and e.event_type == event_type
        ][:32]
        confidence = min(
            MAX_FREQUENCY_CONFIDENCE,
            BASE_FREQUENCY_CONFIDENCE + (count - self._pattern_threshold) * CONFIDENCE_PER_EXTRA_EVENT,
        )
        return Insight(
            category=INSIGHT_CATEGORY_PATTERN,
            description=f"事件类型 {event_type} 在近期出现 {count} 次,形成稳定模式",
            supporting_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"event_type": event_type, "count": count, "window": "daily"},
        )

    def _build_preference_insight(
        self,
        event_type: str,
        count: int,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> Insight:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and e.event_type == event_type
        ][:32]
        confidence = min(
            MAX_FREQUENCY_CONFIDENCE,
            BASE_FREQUENCY_CONFIDENCE + (count - self._preference_threshold) * CONFIDENCE_PER_EXTRA_EVENT,
        )
        return Insight(
            category=INSIGHT_CATEGORY_PREFERENCE,
            description=f"近期对 {event_type} 类事件表现出偏好(共 {count} 次)",
            supporting_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"event_type": event_type, "count": count, "window": "daily"},
        )

    def _build_topic_preference_insight(
        self,
        topic: str,
        count: int,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> Insight:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and self._extract_topic(e) == topic
        ][:32]
        confidence = min(
            MAX_FREQUENCY_CONFIDENCE,
            BASE_FREQUENCY_CONFIDENCE + (count - self._preference_threshold) * CONFIDENCE_PER_EXTRA_EVENT,
        )
        return Insight(
            category=INSIGHT_CATEGORY_PREFERENCE,
            description=f"近期对主题 {topic} 表现出偏好(共 {count} 次)",
            supporting_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"topic": topic, "count": count, "window": "daily"},
        )

    # --------------------------------------------------------
    # 内部:生成 suggestion
    # --------------------------------------------------------
    def _build_interest_suggestion(
        self,
        topic: str,
        count: int,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> StateChangeSuggestion:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and self._extract_topic(e) == topic
        ][:32]
        # delta 随 count 增长
        delta = min(
            MAX_INTEREST_DELTA,
            BASE_INTEREST_DELTA + (count - self._preference_threshold) * 0.02,
        )
        confidence = min(
            MAX_FREQUENCY_CONFIDENCE,
            BASE_FREQUENCY_CONFIDENCE + (count - self._preference_threshold) * CONFIDENCE_PER_EXTRA_EVENT,
        )
        return StateChangeSuggestion(
            target_field=f"interest_state.{topic}",
            old_value=None,
            suggested_value=None,
            delta=delta,
            reason=f"近期主题 {topic} 相关事件增加({count} 次)",
            evidence_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"topic": topic, "count": count, "kind": "interest"},
        )

    def _build_trait_suggestion(
        self,
        trait: str,
        count: int,
        events: List[IntegrationEvent],
        context: ReflectionContext,
    ) -> StateChangeSuggestion:
        evidence_ids = [
            e.event_id for e in events
            if isinstance(e, IntegrationEvent) and self._extract_trait(e) == trait
        ][:32]
        delta = min(
            MAX_TRAIT_DELTA,
            BASE_TRAIT_DELTA + (count - self._preference_threshold) * 0.01,
        )
        confidence = min(
            MAX_FREQUENCY_CONFIDENCE,
            BASE_FREQUENCY_CONFIDENCE + (count - self._preference_threshold) * CONFIDENCE_PER_EXTRA_EVENT,
        )
        return StateChangeSuggestion(
            target_field=f"trait_state.{trait}",
            old_value=None,
            suggested_value=None,
            delta=delta,
            reason=f"近期特质 {trait} 反复出现({count} 次)",
            evidence_event_ids=evidence_ids,
            confidence=max(0.0, min(1.0, confidence)),
            created_at=context.clock_now,
            metadata={"trait": trait, "count": count, "kind": "trait"},
        )

    # --------------------------------------------------------
    # 内部:计算 confidence / evidence_strength
    # --------------------------------------------------------
    def _calc_confidence(self, events: List[IntegrationEvent]) -> float:
        if not events:
            return 0.0
        n = len(events)
        # 简单线性: 事件越多,置信度越高(但有上限)
        return min(1.0, 0.4 + n * 0.05)

    def _calc_evidence_strength(self, events: List[IntegrationEvent]) -> float:
        if not events:
            return 0.0
        # 事件多样性: 唯一 event_type 数 / 总数
        if not isinstance(events, list):
            return 0.0
        types = set()
        for e in events:
            if isinstance(e, IntegrationEvent):
                types.add(str(e.event_type or ""))
        if not types:
            return 0.0
        # 越集中(单一类型越多)强度越高
        return min(1.0, len(types) / max(1, len(events)) + 0.2)

    # --------------------------------------------------------
    # 描述
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        d = super().describe()
        d.update({
            "window_seconds": self._window_seconds,
            "preference_threshold": self._preference_threshold,
            "pattern_threshold": self._pattern_threshold,
            "schema_version": self.schema_version,
        })
        return d


__all__ = [
    "DAILY_REFLECTION_SCHEMA_VERSION",
    "DEFAULT_DAILY_WINDOW_SECONDS",
    "PREFERENCE_FREQUENCY_THRESHOLD",
    "PATTERN_FREQUENCY_THRESHOLD",
    "DailyReflection",
]
