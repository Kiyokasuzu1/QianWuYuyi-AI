"""
成长流水线（GrowthPipeline）v0.7

浅雾羽依成长系统 v0.7

Phase 7.1 更新：
- 产生 PersonalityInfluence 影响记录
- 提供 collect_new_influences 供 Orchestrator 同步
- incremental_update 开头清理临时缓存
"""

from typing import Optional
import uuid
from datetime import datetime

from src.growth.event_extractor import EventExtractor
from src.growth.event_normalizer import EventNormalizer
from src.growth.event_validator import EventValidator
from src.growth.event_history_matcher import EventHistoryMatcher
from src.growth.growth_engine import GrowthEngine
from src.growth.event_identity_resolver import resolve_event_identity
from src.growth.growth_evaluator import GrowthEvaluator

from src.personality.personality_resolver import PersonalityResolver
from src.personality.relationship_state import RelationshipState
from src.personality.personality_growth_record import PersonalityGrowthHistory

# Phase 7.1 新增
from src.personality.personality_influence import PersonalityInfluence, InfluenceType


class GrowthPipeline:

    def __init__(
        self,
        event_memory=None,
        memory_store=None,
        user_id="366648462",
        relationship_state: Optional[RelationshipState] = None
    ):
        # 事件处理
        self.extractor = EventExtractor()
        self.normalizer = EventNormalizer()
        self.validator = EventValidator()
        self.matcher = EventHistoryMatcher()

        # 成长核心
        self.growth_engine = GrowthEngine()

        # 成长评估器
        self.evaluator = GrowthEvaluator()

        # 成长记录存储
        self.growth_records = PersonalityGrowthHistory()

        # Phase 7.1：临时影响记录缓存
        self.new_influences = []

        # 关系系统
        self.relationship_state = (
            relationship_state or RelationshipState()
        )

        # 人格系统
        self.resolver = PersonalityResolver(
            state=self.growth_engine.state,
            relationship_state=self.relationship_state
        )

        # 外部依赖
        self.store = memory_store
        self.event_memory = event_memory
        self.target_user_id = user_id

        self.matcher.set_growth_state(self.growth_engine.state)

    # =================================================
    # 增量成长更新（实时聊天入口）
    # =================================================
    def incremental_update(self, user_message: str):
        """单次聊天后的快速成长入口"""

        # Phase 7.1：每次调用清理临时缓存
        self.new_influences.clear()

        try:
            events = self.extractor.extract_from_text(user_message)

            if not events:
                return {
                    "events": [],
                    "personality": self.resolver.resolve(),
                    "growth_records": [],
                }

            events = self.normalizer.normalize(events)
            events = self.validator.validate(events)

            if not events:
                return {
                    "events": [],
                    "personality": self.resolver.resolve(),
                    "growth_records": [],
                }

            for e in events:
                resolve_event_identity(e)

            events = self.matcher.track(events, False)

            applied = []
            new_records = []

            for event in events:
                apply_flag = event.get("metadata", {}).get("validator_apply", True)
                if not apply_flag:
                    continue

                # 成长资格评估
                canonical_topic = event.get("canonical_topic", event.get("topic", ""))
                event_type = event.get("event_type", "")
                history_events = self.matcher.get_history(canonical_topic, event_type)

                evaluated = self.evaluator.evaluate(event, history_events)

                if evaluated.get("growth_allowed", False):
                    record = self.growth_engine.apply_evaluated(evaluated)
                    if record:
                        self.growth_records.add(record)
                        new_records.append(record)

                # 原有 GrowthState 更新逻辑
                result = self.growth_engine.apply(event)

                if result.get("status") == "applied":
                    if result.get("mode") == "first":
                        self._update_relationship(event)

                    applied.append({
                        **event,
                        "growth_mode": result.get("mode")
                    })

                    # Phase 7.1：产生人格影响记录
                    if result.get("delta"):
                        for dim, delta in result["delta"].items():
                            if abs(delta) > 0.001:
                                confidence = self._calculate_influence_confidence(event, result)
                                influence_type = result.get("change_type", InfluenceType.POSITIVE_GROWTH)
                                if isinstance(influence_type, str):
                                    try:
                                        influence_type = InfluenceType(influence_type)
                                    except ValueError:
                                        influence_type = InfluenceType.POSITIVE_GROWTH

                                influence = PersonalityInfluence(
                                    influence_id=f"inf_{uuid.uuid4().hex[:8]}",
                                    timestamp=datetime.now().isoformat(),
                                    source_event_id=event.get("event_id", ""),
                                    source_event_description=event.get("canonical_topic", ""),
                                    affected_dimension=dim,
                                    before_value=result.get("before", {}).get(dim, 0.5),
                                    after_value=result.get("before", {}).get(dim, 0.5) + delta,
                                    delta=delta,
                                    influence_type=influence_type,
                                    impact_weight=min(abs(delta), 1.0),
                                    confidence=confidence,
                                    evidence=event.get("source_ids", []),
                                )
                                self.new_influences.append(influence)

                if self.store and event.get("source_ids"):
                    try:
                        self.store.mark_processed_batch(event["source_ids"])
                    except AttributeError:
                        pass

            return {
                "events": applied,
                "personality": self.resolver.resolve(),
                "growth_records": new_records,
            }

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"⚠️ 增量成长失败: {e}")
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

    # =================================================
    # Phase 7.1：收集本轮新产生的影响记录
    # =================================================
    def collect_new_influences(self):
        """收集本轮新产生的影响记录，供 Orchestrator 同步到 RelationshipProfile"""
        result = self.new_influences[:]
        self.new_influences.clear()
        return result

    # =================================================
    # Phase 7.1：计算影响记录的可信度
    # =================================================
    def _calculate_influence_confidence(self, event, result) -> float:
        """基于证据链计算影响记录的可信度"""
        confidence = 0.5
        if event.get("validation_status") == "confirmed":
            confidence += 0.3
        if len(event.get("source_ids", [])) > 1:
            confidence += 0.1
        if result.get("status") == "applied":
            confidence += 0.1
        deltas = result.get("delta", {}).values()
        if deltas:
            max_delta = max(abs(v) for v in deltas)
            if max_delta > 0.05:
                confidence += 0.05
        return min(confidence, 1.0)

    # =================================================
    # 关系更新（内部方法）
    # =================================================
    def _update_relationship(self, event):
        event_type = event.get("event_type", "")

        raw_importance = event.get("importance", 0.5)
        if isinstance(raw_importance, (list, tuple)):
            importance = float(raw_importance[0]) if len(raw_importance) > 0 else 0.5
        elif isinstance(raw_importance, (int, float)):
            importance = float(raw_importance)
        else:
            importance = 0.5

        category = event.get("category", "")
        topic = event.get("canonical_topic", event.get("topic", ""))
        event_id = event.get("event_id", "")

        if event_type == "relationship":
            self.relationship_state.update_trust(0.05 * importance)
            self.relationship_state.update_bond(0.06 * importance)
            self.relationship_state.update_familiarity(0.04 * importance)
            self.relationship_state.update_history(0.03 * importance)

        elif event_type == "commitment":
            self.relationship_state.update_trust(0.06 * importance)
            self.relationship_state.update_bond(0.08 * importance)
            self.relationship_state.update_promise(0.10 * importance)
            self.relationship_state.update_history(0.05 * importance)

        elif event_type == "milestone":
            if category == "羽依诞生阶段":
                self.relationship_state.update_bond(0.04 * importance)
                self.relationship_state.update_history(0.05 * importance)
                self.relationship_state.add_important_event({
                    "event_id": event_id,
                    "topic": topic,
                    "type": "birth"
                })

        elif event_type == "identity":
            self.relationship_state.update_history(0.02 * importance)

        if importance >= 0.85:
            self.relationship_state.add_milestone(event_id, topic)

    # =================================================
    # 完整整合（批量处理）
    # =================================================
    def run_full_consolidation(self, limit=None, force_first_run=False):
        print("📂 开始成长整理")

        try:
            events = self.extractor.extract(limit)
        except Exception as e:
            print(f"❌ 事件提取失败: {e}")
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

        if not events:
            print("⚠️ 没有提取到事件")
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

        print(f"📝 原始事件 {len(events)} 个")
        events = self.normalizer.normalize(events)
        print(f"🧹 标准化完成 {len(events)} 个")

        before = len(events)
        events = self.validator.validate(events)
        print(f"🔍 验证后 {len(events)} 个 (过滤 {before - len(events)} 个)")

        if not events:
            return {
                "events": [],
                "personality": self.resolver.resolve(),
                "growth_records": [],
            }

        for e in events:
            resolve_event_identity(e)

        events = self.matcher.track(events, force_first_run)

        applied = 0
        processed = []
        new_records = []

        for event in events:
            try:
                if not event.get("is_first_occurrence", True):
                    continue

                apply_flag = event.get("metadata", {}).get("validator_apply", True)
                if not apply_flag:
                    print(f"⏭️ 跳过 event {event.get('event_id', '')} (validator_apply=False)")
                    continue

                canonical_topic = event.get("canonical_topic", event.get("topic", ""))
                event_type = event.get("event_type", "")
                history_events = self.matcher.get_history(canonical_topic, event_type)

                evaluated = self.evaluator.evaluate(event, history_events)
                if evaluated.get("growth_allowed", False):
                    record = self.growth_engine.apply_evaluated(evaluated)
                    if record:
                        self.growth_records.add(record)
                        new_records.append(record)

                result = self.growth_engine.apply(event)

                if result.get("status") == "applied":
                    applied += 1
                    processed.extend(event.get("source_ids", []))
                    self._update_relationship(event)

                    # 产生人格影响记录
                    if result.get("delta"):
                        for dim, delta in result["delta"].items():
                            if abs(delta) > 0.001:
                                confidence = self._calculate_influence_confidence(event, result)
                                influence_type = result.get("change_type", InfluenceType.POSITIVE_GROWTH)
                                if isinstance(influence_type, str):
                                    try:
                                        influence_type = InfluenceType(influence_type)
                                    except ValueError:
                                        influence_type = InfluenceType.POSITIVE_GROWTH

                                influence = PersonalityInfluence(
                                    influence_id=f"inf_{uuid.uuid4().hex[:8]}",
                                    timestamp=datetime.now().isoformat(),
                                    source_event_id=event.get("event_id", ""),
                                    source_event_description=event.get("canonical_topic", ""),
                                    affected_dimension=dim,
                                    before_value=result.get("before", {}).get(dim, 0.5),
                                    after_value=result.get("before", {}).get(dim, 0.5) + delta,
                                    delta=delta,
                                    influence_type=influence_type,
                                    impact_weight=min(abs(delta), 1.0),
                                    confidence=confidence,
                                    evidence=event.get("source_ids", []),
                                )
                                self.new_influences.append(influence)

            except Exception as e:
                print(f"⚠️ 成长事件失败: {event.get('topic')}, {e}")

        print(f"🌱 应用成长事件 {applied} 个")
        personality = self.resolver.resolve()

        if self.store and processed:
            self.store.mark_processed_batch(processed)

        if self.event_memory:
            self.event_memory.refresh()

        return {
            "events": events,
            "personality": personality,
            "growth_records": new_records,
        }

    def get_current_personality(self):
        return self.resolver.resolve()

    @property
    def state(self):
        return self.growth_engine.state