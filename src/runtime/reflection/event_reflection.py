# -*- coding: utf-8 -*-
"""
src/runtime/reflection/event_reflection.py

Phase 5.0-D3-B: 单事件反思策略。

职责:
- 对单个重要事件做特殊分析
- 触发: 1 个重要事件
- 输出: 多个 Insight(对该事件的解读) + Suggestion(基于该事件的建议)

约束:
- 不调用 LLM
- 使用规则分析
- 所有 Insight/Suggestion 必含 evidence_event_ids
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.integration.integration_event import IntegrationEvent
from src.runtime.reflection.reflection_result import (
    INSIGHT_CATEGORY_CONCERN,
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
EVENT_REFLECTION_SCHEMA_VERSION = "1.0"

# 默认认为"重要"的事件: severity >= notice
IMPORTANT_SEVERITIES = frozenset({"notice", "warning", "critical"})


# ============================================================
# EventReflection
# ============================================================
class EventReflection(BaseReflectionStrategy):
    """单事件反思策略(规则分析)。"""

    DEFAULT_NAME = "event_reflection"
    DEFAULT_REFLECTION_TYPE = "event"

    def __init__(
        self,
        *,
        name: Optional[str] = None,
        important_severities: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            name=name or self.DEFAULT_NAME,
            reflection_type=self.DEFAULT_REFLECTION_TYPE,
        )
        if important_severities is None:
            self._important_severities = frozenset(IMPORTANT_SEVERITIES)
        else:
            clean: List[str] = []
            for s in important_severities:
                if s is None:
                    continue
                v = str(s)
                if v and v not in clean:
                    clean.append(v)
            self._important_severities = frozenset(clean) if clean else frozenset(IMPORTANT_SEVERITIES)

    @property
    def schema_version(self) -> str:
        return EVENT_REFLECTION_SCHEMA_VERSION

    @property
    def important_severities(self) -> frozenset:
        return self._important_severities

    # --------------------------------------------------------
    # 决策
    # --------------------------------------------------------
    def should_run(self, context: ReflectionContext) -> bool:
        if not isinstance(context, ReflectionContext):
            return False
        # 至少 1 个事件
        return context.event_count > 0

    def is_important(self, event: IntegrationEvent) -> bool:
        """判断事件是否"重要"。"""
        if not isinstance(event, IntegrationEvent):
            return False
        sev = str(event.severity or "info")
        if sev in self._important_severities:
            return True
        # payload 标记
        payload = event.payload or {}
        if isinstance(payload, dict):
            if payload.get("important") is True or payload.get("critical") is True:
                return True
        return False

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
        events = context.events
        result.source_event_ids = context.event_ids()

        if not events:
            return result

        # 单事件反思: 选第一个事件(或最严重)
        target = self._pick_target(events)
        if target is None:
            return result

        # 生成 1-3 个 insight
        insights: List[Insight] = []
        # 1) pattern insight(事件类型说明)
        insights.append(self._build_pattern_insight(target, context))
        # 2) preference / concern insight(基于严重度)
        if str(target.severity or "info") in self._important_severities:
            insights.append(self._build_concern_insight(target, context))
        else:
            insights.append(self._build_preference_insight(target, context))
        # 3) growth insight(若包含 'level'/'progress'/'achievement' 关键字)
        if self._is_growth_related(target):
            insights.append(self._build_growth_insight(target, context))
        result.insights = insights

        # 生成 suggestion: 基于事件类型 + 严重度
        suggestions: List[StateChangeSuggestion] = []
        sev = str(target.severity or "info")
        target_field = f"capability_state.{self._safe_event_type_key(target)}"
        if sev in ("warning", "critical"):
            # 负面: 能力置信度下降
            suggestions.append(
                self._build_capability_suggestion(
                    target,
                    target_field=target_field,
                    delta=-0.05,
                    reason=f"经历 {target.event_type} 警示事件,建议谨慎相关能力",
                    context=context,
                )
            )
        elif self._is_growth_related(target):
            # 正面: 兴趣或能力提升
            topic = self._extract_topic(target)
            if topic:
                suggestions.append(
                    self._build_interest_suggestion(
                        target,
                        topic=topic,
                        context=context,
                    )
                )
        result.suggested_changes = suggestions

        # confidence / evidence_strength
        result.confidence = self._calc_confidence(target)
        result.evidence_strength = self._calc_evidence_strength(target)
        return result

    # --------------------------------------------------------
    # 内部:选择目标
    # --------------------------------------------------------
    def _pick_target(self, events: List[IntegrationEvent]) -> Optional[IntegrationEvent]:
        """选最严重的事件。"""
        if not events:
            return None
        # 优先级: critical > warning > notice > info
        order = {"critical": 4, "warning": 3, "notice": 2, "info": 1}
        best: Optional[IntegrationEvent] = None
        best_score = -1
        for e in events:
            if not isinstance(e, IntegrationEvent):
                continue
            score = order.get(str(e.severity or "info"), 0)
            if score > best_score:
                best = e
                best_score = score
        return best

    # --------------------------------------------------------
    # 内部:insight 构建
    # --------------------------------------------------------
    def _build_pattern_insight(
        self,
        event: IntegrationEvent,
        context: ReflectionContext,
    ) -> Insight:
        return Insight(
            category=INSIGHT_CATEGORY_PATTERN,
            description=f"记录到 {event.event_type} 类型事件,严重度 {event.severity}",
            supporting_event_ids=[event.event_id],
            confidence=0.7,
            created_at=context.clock_now,
            metadata={"event_type": event.event_type, "severity": event.severity},
        )

    def _build_preference_insight(
        self,
        event: IntegrationEvent,
        context: ReflectionContext,
    ) -> Insight:
        topic = self._extract_topic(event) or "通用"
        return Insight(
            category=INSIGHT_CATEGORY_PREFERENCE,
            description=f"对主题 {topic} 表现出持续兴趣",
            supporting_event_ids=[event.event_id],
            confidence=0.6,
            created_at=context.clock_now,
            metadata={"topic": topic, "event_type": event.event_type},
        )

    def _build_concern_insight(
        self,
        event: IntegrationEvent,
        context: ReflectionContext,
    ) -> Insight:
        return Insight(
            category=INSIGHT_CATEGORY_CONCERN,
            description=f"经历严重事件 {event.event_type}({event.severity}),需要关注",
            supporting_event_ids=[event.event_id],
            confidence=0.75,
            created_at=context.clock_now,
            metadata={"event_type": event.event_type, "severity": event.severity},
        )

    def _build_growth_insight(
        self,
        event: IntegrationEvent,
        context: ReflectionContext,
    ) -> Insight:
        topic = self._extract_topic(event) or "通用"
        return Insight(
            category=INSIGHT_CATEGORY_GROWTH,
            description=f"在 {topic} 方向上取得进展({event.event_type})",
            supporting_event_ids=[event.event_id],
            confidence=0.65,
            created_at=context.clock_now,
            metadata={"topic": topic, "event_type": event.event_type},
        )

    # --------------------------------------------------------
    # 内部:suggestion 构建
    # --------------------------------------------------------
    def _build_capability_suggestion(
        self,
        event: IntegrationEvent,
        *,
        target_field: str,
        delta: float,
        reason: str,
        context: ReflectionContext,
    ) -> StateChangeSuggestion:
        return StateChangeSuggestion(
            target_field=target_field,
            old_value=None,
            suggested_value=None,
            delta=delta,
            reason=reason,
            evidence_event_ids=[event.event_id],
            confidence=0.7,
            created_at=context.clock_now,
            metadata={"event_id": event.event_id, "event_type": event.event_type},
        )

    def _build_interest_suggestion(
        self,
        event: IntegrationEvent,
        *,
        topic: str,
        context: ReflectionContext,
    ) -> StateChangeSuggestion:
        return StateChangeSuggestion(
            target_field=f"interest_state.{topic}",
            old_value=None,
            suggested_value=None,
            delta=0.1,
            reason=f"经历 {event.event_type} 增长事件,主题 {topic} 兴趣提升",
            evidence_event_ids=[event.event_id],
            confidence=0.65,
            created_at=context.clock_now,
            metadata={"topic": topic, "event_id": event.event_id},
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
        for k in ("topic", "interest", "category", "subject"):
            v = payload.get(k)
            if v is not None:
                s = str(v).strip()
                if s:
                    return s
        return ""

    def _safe_event_type_key(self, event: IntegrationEvent) -> str:
        et = str(event.event_type or "")
        # 把 . 替换为 _
        return et.replace(".", "_").replace(" ", "_")

    def _is_growth_related(self, event: IntegrationEvent) -> bool:
        if not isinstance(event, IntegrationEvent):
            return False
        payload = event.payload or {}
        if not isinstance(payload, dict):
            return False
        # 关键字
        for k in ("level", "progress", "achievement", "milestone"):
            if k in payload:
                return True
        et = str(event.event_type or "")
        if any(tag in et for tag in ("growth", "achievement", "milestone", "progress", "level_up")):
            return True
        return False

    # --------------------------------------------------------
    # 内部:confidence 计算
    # --------------------------------------------------------
    def _calc_confidence(self, event: IntegrationEvent) -> float:
        if not isinstance(event, IntegrationEvent):
            return 0.0
        sev = str(event.severity or "info")
        if sev == "critical":
            return 0.85
        if sev == "warning":
            return 0.75
        if sev == "notice":
            return 0.7
        return 0.6

    def _calc_evidence_strength(self, event: IntegrationEvent) -> float:
        # 单事件证据强度取决于 payload 完整性
        if not isinstance(event, IntegrationEvent):
            return 0.0
        payload = event.payload or {}
        if not isinstance(payload, dict):
            return 0.3
        n = len(payload)
        return min(1.0, 0.3 + n * 0.1)

    # --------------------------------------------------------
    # 描述
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        d = super().describe()
        d.update({
            "schema_version": self.schema_version,
            "important_severities": sorted(list(self._important_severities)),
        })
        return d


__all__ = [
    "EVENT_REFLECTION_SCHEMA_VERSION",
    "IMPORTANT_SEVERITIES",
    "EventReflection",
]
