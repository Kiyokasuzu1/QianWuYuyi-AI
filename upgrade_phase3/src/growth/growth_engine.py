"""
羽依成长引擎（GrowthEngine）v1.7

职责:
人生事件 → 成长意义识别 → GrowthState 统计更新 + GrowthRecord 生成

v1.7 更新:
- 新增 apply_evaluated 方法，基于 GrowthEvaluator 的评估结果生成 GrowthRecord
- 原有 apply 方法保留，用于 GrowthState 的历史统计和关系状态更新
- GrowthRecord 由 PersonalityResolver 在 Phase 3.2 消费，Engine 不直接修改人格维度
- Phase 7.1：apply 方法末尾调用 self.state.save() 确保成长状态持久化
"""

from typing import Dict, Optional
from datetime import datetime
import uuid

from src.growth.growth_state import GrowthState
from src.growth.meaning_resolver import resolve_meaning
from src.growth.event_identity_resolver import resolve_event_identity
from src.growth.growth_record import GrowthRecord, create_growth_record
from src.growth.growth_schema import MAX_SINGLE_EVENT_DELTA


class GrowthEngine:

    def __init__(self):
        self.state = GrowthState()

    GROWTH_MAP = {
        "birth": {
            "metrics": {"self_awareness": 0.18, "identity_strength": 0.15, "curiosity": 0.10},
            "milestone": True
        },
        "identity_creation": {
            "metrics": {"identity_strength": 0.20, "self_awareness": 0.15, "self_confidence": 0.10},
            "milestone": True
        },
        "relationship_start": {
            "metrics": {"trust": 0.08, "warmth": 0.08, "closeness": 0.12, "emotional_memory": 0.10}
        },
        "emotional_expression": {
            "metrics": {"trust": 0.10, "attachment": 0.12, "security": 0.08, "emotional_memory": 0.15}
        },
        "promise": {
            "metrics": {"attachment": 0.15, "trust": 0.10, "security": 0.12}
        },
        "creation": {
            "metrics": {"self_confidence": 0.08, "self_expression": 0.10, "initiative": 0.06, "creativity": 0.08}
        },
        "growth_support": {
            "metrics": {"trust": 0.08, "self_confidence": 0.10, "closeness": 0.08}
        },
        "companionship": {
            "metrics": {"closeness": 0.02, "warmth": 0.01}
        }
    }

    REPEAT_BONUS = {
        "promise": {"trust": 0.02, "security": 0.02},
        "relationship_start": {"closeness": 0.02, "warmth": 0.01},
        "emotional_expression": {"emotional_memory": 0.02},
        "creation": {"self_expression": 0.02, "creativity": 0.02},
        "companionship": {"warmth": 0.01, "closeness": 0.01}
    }

    def _history_key(self, event):
        return resolve_event_identity(event)

    def _already_grown(self, event):
        if event.get("is_first_occurrence") is False:
            return True
        if event.get("is_first_occurrence") is True:
            return False

        history = self.state.get().setdefault("growth_history", [])
        key = self._history_key(event)

        for item in history:
            if item.get("history_key") == key:
                return True
        return False

    def _calc_growth(self, value, importance, current):
        return round(value * importance * (1 - current), 4)

    def _apply_metrics(self, metrics, importance):
        before = {}
        delta = {}
        for key, value in metrics.items():
            current = self.state.get_metric(key)
            before[key] = current
            amount = self._calc_growth(value, importance, current)
            if amount > 0.0001:
                delta[key] = amount
        if delta:
            self.state.update_metrics(delta)
        return before, delta

    def _record_history(self, event, mode, before, delta):
        history = self.state.get().setdefault("growth_history", [])

        if mode == "repeat":
            key = self._history_key(event)
            for item in history:
                if item.get("history_key") == key:
                    item["reinforcement_count"] = item.get("reinforcement_count", 0) + 1
                    item["last_reinforced_at"] = datetime.now().isoformat()
                    return

        history.append({
            "meaning": event.get("meaning", ""),
            "topic": event.get("canonical_topic", event.get("topic", "")),
            "event_identity": (
                event.get("event_identity")
                or resolve_event_identity(event)
            ),
            "history_key": self._history_key(event),
            "mode": mode,
            "reinforcement_count": 0,
            "last_reinforced_at": None,
            "before": before,
            "delta": delta,
            "time": datetime.now().isoformat()
        })

    def apply(self, event: Dict):
        """
        原有方法：更新 GrowthState 统计指标（保留用于历史统计和关系状态）
        Phase 7.1：末尾调用 self.state.save() 确保成长状态持久化
        """
        if event.get("event_scope") == "system":
            return {"status": "ignored", "reason": "system_event"}

        # 1. 确保事件身份存在（兜底）
        if not event.get("event_identity"):
            resolve_event_identity(event)

        # 2. 使用身份解析含义
        meaning = resolve_meaning(event)
        event["meaning"] = meaning
        
        if not meaning:
            meaning = event.get("meaning") or event.get("event_type", "unknown")
            event["meaning"] = meaning
            if not meaning or meaning == "unknown":
                return {"status": "skipped", "reason": "unknown_meaning"}

        rule = self.GROWTH_MAP.get(meaning)
        if not rule:
            return {"status": "skipped", "reason": "no_rule"}

        importance = event.get("importance", 0.5)
        existed = self._already_grown(event)

        if existed:
            metrics = self.REPEAT_BONUS.get(meaning, {})
            mode = "repeat"
        else:
            metrics = rule.get("metrics", {})
            mode = "first"

        before, delta = self._apply_metrics(metrics, importance)

        if not existed and rule.get("milestone", False):
            self.state.add_milestone(event.get("event_id"), event.get("topic", ""))

        self._record_history(event, mode, before, delta)
        self.state.save()  # Phase 7.1: 确保成长状态持久化

        return {
            "status": "applied",
            "mode": mode,
            "meaning": meaning,
            "topic": event.get("topic"),
            "delta": delta,
            "before": before,
        }

    def apply_evaluated(self, evaluated_event: Dict) -> Optional[GrowthRecord]:
        """
        Phase 3.1 新增：基于 GrowthEvaluator 评估结果生成 GrowthRecord。
        仅生成记录，不修改人格数值。人格变更由 PersonalityResolver 在 Phase 3.2 统一处理。
        """
        if not evaluated_event.get("growth_allowed", False):
            return None

        growth_signal = evaluated_event.get("growth_signal", "")
        source_type = evaluated_event.get("event_type", "")
        growth_level = evaluated_event.get("growth_level", "context")
        confidence = evaluated_event.get("confidence", 0.5)
        target_candidates = evaluated_event.get("target_candidates", [])
        applied_delta = evaluated_event.get("applied_delta", 0.0)
        event_id = evaluated_event.get("event_id", "")
        canonical_topic = evaluated_event.get("canonical_topic", "")

        if not target_candidates or applied_delta <= 0.0:
            return None

        affected_dimensions = {}
        primary_dimension = target_candidates[0]
        affected_dimensions[primary_dimension] = applied_delta

        if len(target_candidates) > 1:
            secondary_dimension = target_candidates[1]
            affected_dimensions[secondary_dimension] = round(applied_delta * 0.5, 4)

        record = create_growth_record(
            record_id=str(uuid.uuid4())[:8],
            source_event_id=event_id,
            growth_signal=growth_signal,
            source_type=source_type,
            growth_level=growth_level,
            affected_dimensions=affected_dimensions,
            confidence=confidence,
            reason=f"[{growth_level}] {growth_signal} - {canonical_topic} (confidence={confidence:.2f})",
            created_at=datetime.now().isoformat(),
        )
        return record

    def apply_batch(self, events: list):
        return [self.apply(e) for e in events]

    def get_state(self):
        return self.state.get()

    def reset(self):
        self.state.reset()
        print("🔄 GrowthState 已重置")