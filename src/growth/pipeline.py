"""
成长流水线（GrowthPipeline）v0.9

浅雾羽依成长系统 v0.9

Phase 3.8.3-C 更新：
- 移除 _inject_compat_fields()：语义生成职责完全移交 GrowthNarrativeAdapter
- Pipeline 不再负责 meaning/narrative/schema 转换

Phase 3.8.3-B 更新：
- incremental_update() 中新增 Adapter 流程：
  GrowthRecord → GrowthNarrativeAdapter → PersonalityGrowthRecord → PersonalityGrowthHistory

Phase 3.8.2-B 更新：
- incremental_update() 中新增 Proposal 化流程：
  GrowthEvaluator → GrowthProposal → GrowthEngine.apply_proposal()
- apply_proposal() 是纯执行方法，不承担认知职责
- 旧 apply(event) 保留作为 fallback
- GROWTH_MAP 保留作为 ExperienceMeaning 失败时的回退

Phase 3.8.2-A 更新：
- 在 GrowthEvaluator 之前注入 deep_resolve_meaning() 的语义理解结果
- 经历意义随 GrowthEvent 流转到下游
- LLM 调用失败时自动回退到规则映射（无破坏性变更）

Phase 7.1 更新：
- 产生 PersonalityInfluence 影响记录
- 提供 collect_new_influences 供 Orchestrator 同步
- incremental_update 开头清理临时缓存
"""

from typing import Optional, Dict, List
import uuid
from datetime import datetime

from src.growth.event_extractor import EventExtractor
from src.growth.event_normalizer import EventNormalizer
from src.growth.event_validator import EventValidator
from src.growth.event_history_matcher import EventHistoryMatcher
from src.growth.growth_engine import GrowthEngine
from src.growth.growth_state import GrowthState
from src.growth.event_identity_resolver import resolve_event_identity
from src.growth.growth_evaluator import GrowthEvaluator

# Phase 3.8.2-A：经历意义理解
from src.growth.meaning_resolver import deep_resolve_meaning

# Phase 3.8.2-B：GrowthProposal 权威 schema
from src.contracts.growth_schema import GrowthProposal as ContractProposal, ChangeItem

# Phase 3.8.3-B：GrowthNarrativeAdapter — GrowthRecord → PersonalityGrowthRecord
from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

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
        relationship_state: Optional[RelationshipState] = None,
        growth_state: Optional[GrowthState] = None,
        growth_history: Optional[PersonalityGrowthHistory] = None,
    ):
        # 事件处理
        self.extractor = EventExtractor()
        self.normalizer = EventNormalizer()
        self.validator = EventValidator()
        self.matcher = EventHistoryMatcher()

        # 成长核心（Phase 4.3.3：支持外部注入 GrowthState）
        self.growth_engine = GrowthEngine(state=growth_state)

        # 成长评估器
        self.evaluator = GrowthEvaluator()

        # 成长记录存储（Phase 2.4 GrowthHistory Writer 收口）：
        # - 外部注入的 growth_history 优先（运行时权威实例，多入口共享）；
        # - 未注入时从 RuntimeContext（RuntimeBridge → PersonalityResolver）取共享实例；
        # - 二者都不可用时才回退旧硬编码默认（仅向后兼容）。
        self.growth_records = (
            growth_history
            if growth_history is not None
            else self._resolve_shared_growth_history()
        )

        # Phase 3.8.3-B：GrowthRecord → PersonalityGrowthRecord 适配器
        self.narrative_adapter = GrowthNarrativeAdapter()

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

    @staticmethod
    def _resolve_shared_growth_history() -> PersonalityGrowthHistory:
        """Phase 2.4: 从 RuntimeContext 复用权威 PersonalityGrowthHistory。

        RuntimeCore 构造 PersonalityResolver 时注入持久化 history 并保留
        可写引用 `_growth_history_store`。所有 GrowthPipeline 共享该实例，
        避免多实例对 personality_growth_history.json 的丢失更新。

        Bridge 不可用 / 共享实例无落盘路径时，回退旧硬编码默认
        （旧行为保留，仅向后兼容）。
        """
        try:
            from src.runtime.runtime_bridge import get_runtime_bridge

            _resolver = get_runtime_bridge().get_personality_resolver()
            _shared = getattr(_resolver, "_growth_history_store", None)
            if _shared is None:
                _shared = getattr(_resolver, "growth_history", None)
            if (
                _shared is not None
                and getattr(_shared, "_storage_path", None) is not None
            ):
                return _shared
        except Exception:
            pass
        return PersonalityGrowthHistory(
            storage_path=PersonalityGrowthHistory.DEFAULT_STORAGE_PATH
        )

    # =================================================
    # Phase 3.8.2-A：经历意义解析
    # =================================================
    def _resolve_experience_meaning(self, event: Dict) -> Optional[Dict]:
        """为事件解析经历意义。

        调用 deep_resolve_meaning() 进行 LLM 语义理解。
        失败时返回 None，调用方应回退到规则映射。

        Returns:
            ExperienceMeaning.to_dict() 的结果，或 None（失败时）
        """
        try:
            meaning = deep_resolve_meaning(
                event=event,
                current_growth_state=self.growth_engine.state.to_dict()
                if hasattr(self.growth_engine.state, "to_dict")
                else None,
            )
            if meaning.fallback_used:
                # 规则 fallback 不提升语义质量，返回 None 让 evaluator 用旧逻辑
                return None
            return meaning.to_dict()
        except Exception:
            # 静默失败，不影响主链路
            return None

    # =================================================
    # Phase 3.8.2-B：从评估结果构建 GrowthProposal
    # =================================================
    def _build_proposal_from_evaluated(self, evaluated: Dict, event: Dict) -> Optional[ContractProposal]:
        """从 GrowthEvaluator 的评估结果构建 GrowthProposal（权威 schema）。

        将 target_candidates + applied_delta 转换为 ChangeItem 列表。
        必须有 evidence 才能创建 proposal。
        """
        target_candidates = evaluated.get("target_candidates", [])
        applied_delta = evaluated.get("applied_delta", 0.0)
        confidence = evaluated.get("confidence", 0.5)
        growth_signal = evaluated.get("growth_signal", "")
        experience_meaning = evaluated.get("experience_meaning")

        if not target_candidates or applied_delta <= 0.0:
            return None

        # 构建证据ID列表
        evidence_ids = []
        for ev in event.get("evidence", []):
            eid = ev.get("id") or ev.get("source_id") or str(uuid.uuid4().hex[:8])
            evidence_ids.append(eid)
        if not evidence_ids:
            evidence_ids.append(f"ev_{event.get('event_id', 'unknown')}")

        # 构建 ChangeItem 列表
        changes = []
        primary_dim = target_candidates[0]
        primary_val = round(applied_delta, 4)
        changes.append(ChangeItem(
            path=primary_dim,
            before=None,
            after=primary_val,
            reason=f"growth_signal={growth_signal}, confidence={confidence:.2f}",
        ))

        # 次要维度（如果有）
        if len(target_candidates) > 1:
            secondary_dim = target_candidates[1]
            secondary_val = round(applied_delta * 0.5, 4)
            changes.append(ChangeItem(
                path=secondary_dim,
                before=None,
                after=secondary_val,
                reason=f"secondary dimension from {growth_signal}",
            ))

        # 构建 evaluator_meta（含 meaning 信息）
        evaluator_meta = {
            "growth_signal": growth_signal,
            "growth_level": evaluated.get("growth_level", ""),
            "growth_domain": evaluated.get("growth_domain", ""),
            "_governance_origin": "GrowthPipeline.incremental_update",
            "_source_schema": "growth_evaluator",
        }
        if experience_meaning:
            evaluator_meta["experience_meaning"] = {
                "surface": experience_meaning.get("surface_meaning", ""),
                "deeper": experience_meaning.get("deeper_significance", ""),
                "confidence": experience_meaning.get("confidence", 0),
            }

        return ContractProposal(
            source_event_id=event.get("event_id", ""),
            proposed_changes=changes,
            confidence=confidence,
            evidence_ids=evidence_ids,
            evaluator_meta=evaluator_meta,
            status="proposed",
        )

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

                evaluated = self.evaluator.evaluate(
                    event, history_events,
                    experience_meaning=self._resolve_experience_meaning(event),
                )

                if evaluated.get("growth_allowed", False):
                    # Phase 3.8.2-B：Proposal 化流程
                    proposal_applied = False
                    # 1. 从评估结果构建 GrowthProposal
                    proposal = self._build_proposal_from_evaluated(evaluated, event)
                    if proposal is not None:
                        # 2. 调用纯执行方法 apply_proposal()
                        proposal_result = self.growth_engine.apply_proposal(proposal)
                        if proposal_result.get("status") == "applied":
                            proposal_applied = True
                            applied.append({
                                **event,
                                "growth_mode": "proposal",
                                "proposal_id": proposal_result.get("proposal_id"),
                                "delta": proposal_result.get("delta"),
                            })

                    # 3. 同时生成 GrowthRecord（兼容 PersonalityResolver）
                    record = self.growth_engine.apply_evaluated(evaluated)
                    if record:
                        # 注入 proposal 信息
                        record["proposal_id"] = proposal.id if proposal else None
                        # Phase 3.8.3-B：通过 Adapter 转换为 PersonalityGrowthRecord
                        personality_record = self.narrative_adapter.convert(record)
                        self.growth_records.add(personality_record)
                        new_records.append(record)
                else:
                    proposal_applied = False

                # Phase 3.8.5 Step 5：根据 Proposal 结果选择路径
                # - Proposal 成功：跳过 apply() 的 GrowthState 更新，只做 Relationship + Influence
                # - Proposal 失败/未生成：legacy apply() 作为 fallback
                if proposal_applied:
                    # Proposal 路径：GrowthState 已由 apply_proposal() 更新
                    # 不再调用 apply(event)，避免双重 GrowthState 更新
                    # 关系更新和影响生成均委托给 GrowthEngine 统一入口
                    if event.get("is_first_occurrence", True):
                        self.growth_engine.apply_relationship(event, self.relationship_state)
                    self.new_influences.extend(
                        self.growth_engine.generate_personality_influence(event, proposal_result)
                    )
                else:
                    # Fallback 路径：legacy apply(event) 完整执行
                    result = self.growth_engine.apply(event)
                    if result.get("status") == "applied":
                        if result.get("mode") == "first":
                            self.growth_engine.apply_relationship(event, self.relationship_state)
                        applied.append({
                            **event,
                            "growth_mode": result.get("mode")
                        })
                        self.new_influences.extend(
                            self.growth_engine.generate_personality_influence(event, result)
                        )

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
    # Phase 3.8.5 Step 5：PersonalityInfluence 生成（兼容包装器，委托至 GrowthEngine）
    # =================================================
    def _record_influence(self, event, result):
        """人格影响生成（兼容包装器，委托至 GrowthEngine.generate_personality_influence()）"""
        influences = self.growth_engine.generate_personality_influence(event, result)
        self.new_influences.extend(influences)

    # =================================================
    # 关系更新（内部方法，Phase 3.8.5 已委托至 GrowthEngine）
    # =================================================
    def _update_relationship(self, event):
        """关系更新（兼容包装器，委托至 GrowthEngine.apply_relationship()）"""
        self.growth_engine.apply_relationship(event, self.relationship_state)

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

                evaluated = self.evaluator.evaluate(
                    event, history_events,
                    experience_meaning=self._resolve_experience_meaning(event),
                )
                if evaluated.get("growth_allowed", False):
                    record = self.growth_engine.apply_evaluated(evaluated)
                    if record:
                        # Phase 3.8.3-B：通过 Adapter 转换为 PersonalityGrowthRecord
                        personality_record = self.narrative_adapter.convert(record)
                        self.growth_records.add(personality_record)
                        new_records.append(record)

                result = self.growth_engine.apply(event)

                if result.get("status") == "applied":
                    applied += 1
                    processed.extend(event.get("source_ids", []))
                    self.growth_engine.apply_relationship(event, self.relationship_state)
                    self.new_influences.extend(
                        self.growth_engine.generate_personality_influence(event, result)
                    )

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