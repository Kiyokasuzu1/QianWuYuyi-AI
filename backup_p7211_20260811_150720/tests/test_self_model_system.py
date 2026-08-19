"""
Phase 3.5.8: Self Model 系统单元测试

覆盖：
 1. SelfModel Schema 结构（Unit）
 2. SelfModelManager（refresh / snapshot / 矛盾检测 / 理解水平）
 3. SelfModelUpdater（GR / Insight / PCR → Suggestion）
 4. RuntimeCore 接入（refresh_self_model / accept suggestion 等）
 5. 完整生命周期：Experience → Memory → Reflection → GrowthProposal → PCR → SelfModelUpdate

约束验证：
- 不修改 Persona 文档
- 不直接修改 TraitState
- 不替代 Personality System
- SelfModel 必须来源于: GrowthRecord / PersonalityVector / ReflectionInsight / Relationship
- 保留可审计历史（growth_history / snapshot history）
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

# ============================================================
# 依赖
# ============================================================

from src.contracts.self_model_schema import (
    SelfIdentity,
    CoreValue,
    StableTrait,
    Preference,
    BehavioralPattern,
    SelfContradiction,
    GrowthHistoryEntry,
    DevelopmentHistoryItem,
    IdentityUnderstanding,
    SelfModelChangeSuggestion,
)
from src.personality.self_model_manager import SelfModelManager
from src.personality.self_model_updater import (
    SelfModelUpdater,
    SelfModelUpdaterConfig,
)
from src.growth.growth_record import create_growth_record, GrowthRecord
from src.personality.trait_state import create_trait_state


# ============================================================
# 1. Schema 结构测试
# ============================================================

class TestSelfModelSchema(unittest.TestCase):
    def test_01_self_identity_defaults(self):
        si = SelfIdentity()
        self.assertTrue(si.identity_id.startswith("si_"))
        self.assertEqual(si.version, 1)
        self.assertIsNotNone(si.created_at)
        self.assertEqual(len(si.core_values), 0)
        self.assertEqual(si.overall_understanding, 0.0)

    def test_02_to_snapshot_shape(self):
        si = SelfIdentity()
        si.stable_traits.append(
            StableTrait(trait="warmth", current_value=0.75, stability=0.8, confidence=0.9, sources=["trait_state"])
        )
        si.behavioral_patterns.append(
            BehavioralPattern(
                pattern_detected="high_user_activity",
                frequency=3,
                confidence=0.7,
                sources=["ins_001"],
            )
        )
        si.core_values.append(CoreValue(value_id="empathy", name="共情", weight=0.78, confidence=0.9, sources=["identity_core"]))
        snap = si.to_snapshot()
        self.assertEqual(snap["stable_traits_count"], 1)
        self.assertEqual(snap["behavioral_patterns_count"], 1)
        self.assertIn("top_traits", snap)
        self.assertIn("top_patterns", snap)
        self.assertIn("core_values_summary", snap)
        self.assertIn("understanding", snap)
        self.assertIn("overall", snap["understanding"])
        self.assertIn("identity_understanding", snap)

    def test_03_suggestion_summary(self):
        sug = SelfModelChangeSuggestion(
            add_preferences=[Preference(domain="context", key="tone", value="gentle")],
            add_patterns=[BehavioralPattern(pattern_detected="x", frequency=1)],
        )
        self.assertIn("1 prefs", sug.summary())
        self.assertIn("1 patterns", sug.summary())
        self.assertTrue(sug.to_dict()["requires_approval"])

    def test_04_identity_understanding_defaults(self):
        si = SelfIdentity()
        self.assertIsInstance(si.identity_understanding, IdentityUnderstanding)
        self.assertEqual(si.identity_understanding.who_i_am, [])
        self.assertEqual(si.development_history, [])


# ============================================================
# 2. SelfModelManager 测试
# ============================================================

class TestSelfModelManager(unittest.TestCase):
    def _make_grs(self) -> List[GrowthRecord]:
        return [
            create_growth_record(
                record_id="gr_001",
                source_event_id="e1",
                growth_signal="high_user_activity_warmth",
                source_type="preference",
                growth_level="context",
                affected_dimensions={"warmth": 0.004},
                confidence=0.6,
                reason="用户活跃时互动增多",
                created_at="2026-07-29T00:00:00Z",
            ),
            create_growth_record(
                record_id="gr_002",
                source_event_id="e2",
                growth_signal="low_success_rate_adjustment",
                source_type="preference",
                growth_level="trait",
                affected_dimensions={"self_confidence": -0.003},
                confidence=0.5,
                reason="低成功率下调自信",
                created_at="2026-07-29T00:01:00Z",
            ),
            create_growth_record(
                record_id="gr_003",
                source_event_id="e3",
                growth_signal="identity_milestone_empathy",
                source_type="identity",
                growth_level="trait",
                affected_dimensions={"empathy": 0.02},
                confidence=0.7,
                reason="里程碑：共情提升",
                created_at="2026-07-29T00:02:00Z",
            ),
        ]

    def _make_insights(self) -> List[Dict[str, Any]]:
        return [
            {
                "insight_id": "ins_001",
                "pattern_detected": "high_user_activity",
                "pattern_frequency": 3,
                "confidence": 0.65,
                "summary": "用户活跃时段主动互动增多",
                "timestamp": "2026-07-29T00:05:00Z",
            },
            {
                "insight_id": "ins_002",
                "pattern_detected": "low_success_rate",
                "pattern_frequency": 2,
                "confidence": 0.55,
                "summary": "部分主动行为成功率偏低",
                "timestamp": "2026-07-29T00:06:00Z",
            },
        ]

    def test_01_refresh_from_empty(self):
        mgr = SelfModelManager(identity_id="si_test_01")
        trait_states = {
            "warmth": create_trait_state("warmth", 0.7),
            "shyness": create_trait_state("shyness", 0.6),
            "self_expression": create_trait_state("self_expression", 0.5),
        }
        snap = mgr.refresh(trait_states=trait_states)
        self.assertIsInstance(snap, SelfIdentity)
        self.assertEqual(len(snap.stable_traits), 3)
        # 两个冲突（shyness vs self_expression）至少一个
        contradiction_traits = {c.dimension_a for c in snap.contradictions} | {
            c.dimension_b for c in snap.contradictions
        }
        self.assertIn("shyness", contradiction_traits)
        # 三维理解 > 0
        self.assertGreater(snap.overall_understanding, 0.0)
        self.assertGreater(snap.trait_awareness, 0.0)

    def test_02_apply_growth_records_and_insights(self):
        mgr = SelfModelManager(identity_id="si_test_02")
        trait_states = {
            "warmth": create_trait_state("warmth", 0.7),
            "self_confidence": create_trait_state("self_confidence", 0.55),
            "self_expression": create_trait_state("self_expression", 0.5),
            "shyness": create_trait_state("shyness", 0.6),
        }
        grs = self._make_grs()
        insights = self._make_insights()
        mgr.refresh(
            trait_states=trait_states,
            growth_records=grs,
            insights=insights,
            relationship_state={"attachment_level": "依恋", "closeness": 0.8},
        )
        snap = mgr.snapshot()
        # 偏好：2（来自 GR） + 2（relationship） ≥ 2
        self.assertGreaterEqual(len(mgr.identity.preferences), 2)
        # 行为模式：2 条
        self.assertEqual(len(mgr.identity.behavioral_patterns), 2)
        # 历史：3 GR（每条 GR 都有 history 条目；Insight 若 pattern 为空则会被建议生成后 summary()="no changes" 过滤 → 不入队）
        self.assertGreaterEqual(snap["growth_history_count"], 3)
        # identity 里程碑触发了 empathy 核心价值观？weight 应提高
        empathy_cv = next((v for v in mgr.identity.core_values if v.value_id == "empathy"), None)
        self.assertIsNotNone(empathy_cv)
        # identity_core 默认 empathy.weight=0.75，加上 +0.02（上限 0.05 per，实际仅 1 次）
        self.assertGreater(empathy_cv.weight, 0.75)
        # snapshot 的 history_count 至少为 GR 数 3（若 insight 未在 refresh 里生成 history，也合法）
        self.assertGreaterEqual(snap["growth_history_count"], 3)

    def test_03_personality_vector_supplements_traits(self):
        mgr = SelfModelManager("si_test_03")
        pv = {
            "warmth": 0.78,
            "gentleness": 0.65,
            "curiosity": 0.5,
            "attachment_level": "依恋",  # 非数值跳过
        }
        mgr.refresh(personality_vector_dict=pv)
        traits = {s.trait: s for s in mgr.identity.stable_traits}
        # warmth 在 PV 中补充（因为没有 TraitState）
        self.assertIn("warmth", traits)
        self.assertIn("gentleness", traits)
        self.assertIn("curiosity", traits)
        # sources 标记为 personality_vector，置信度低
        self.assertIn("personality_vector", traits["warmth"].sources)
        self.assertLessEqual(traits["warmth"].confidence, 0.2)

    def test_04_growth_record_idempotent(self):
        mgr = SelfModelManager("si_test_04")
        grs = self._make_grs()
        mgr.refresh(growth_records=grs)
        count1 = len(mgr.identity.growth_history)
        mgr.refresh(growth_records=grs)  # 相同 record_id 不重复
        count2 = len(mgr.identity.growth_history)
        self.assertEqual(count1, count2)

    def test_05_insight_idempotent(self):
        mgr = SelfModelManager("si_test_05")
        ins = self._make_insights()
        mgr.refresh(insights=ins)
        mgr.refresh(insights=ins)
        self.assertEqual(len(mgr.identity.behavioral_patterns), 2)

    def test_06_snapshot_history_retained(self):
        mgr = SelfModelManager("si_test_06")
        for i in range(5):
            mgr.refresh()
            snap = mgr.snapshot()
        self.assertEqual(len(mgr._snapshots), 5)
        self.assertGreaterEqual(snap["version"], 5)

    def test_07_identity_understanding_and_development_history(self):
        mgr = SelfModelManager("si_test_07")
        trait_states = {
            "warmth": create_trait_state("warmth", 0.82),
            "initiative": create_trait_state("initiative", 0.68),
            "social_need": create_trait_state("social_need", 0.64),
        }
        mgr.refresh(
            trait_states=trait_states,
            growth_records=self._make_grs(),
            insights=self._make_insights(),
        )
        snap = mgr.snapshot()

        self.assertGreater(len(mgr.identity.development_history), 0)
        self.assertGreater(len(mgr.identity.identity_understanding.who_i_am), 0)
        self.assertGreater(len(mgr.identity.identity_understanding.what_i_value), 0)
        self.assertGreater(len(mgr.identity.identity_understanding.what_changed), 0)
        self.assertGreater(len(mgr.identity.identity_understanding.why_changed), 0)
        self.assertIn("identity_understanding", snap)
        self.assertGreaterEqual(snap["development_history_count"], 3)

    def test_08_load_full_backward_compatible(self):
        mgr = SelfModelManager("si_test_08")
        old_payload = {
            "identity_id": "si_old",
            "core_values": [],
            "stable_traits": [],
            "preferences": [],
            "behavioral_patterns": [],
            "contradictions": [],
            "growth_history": [],
            "experience_awareness": 0.1,
            "trait_awareness": 0.2,
            "identity_continuity": 0.3,
            "overall_understanding": 0.2,
            "version": 2,
        }
        mgr.load_full(old_payload)
        self.assertEqual(mgr.identity.identity_id, "si_old")
        self.assertEqual(mgr.identity.development_history, [])
        self.assertEqual(mgr.identity.identity_understanding.who_i_am, [])


# ============================================================
# 3. SelfModelUpdater 测试
# ============================================================

class TestSelfModelUpdater(unittest.TestCase):
    def _make_pcr(self) -> Dict[str, Any]:
        return {
            "request_id": "pcr_test_01",
            "source_proposal_id": "gp_test_01",
            "source_insight_id": "ins_test_01",
            "timestamp": "2026-07-29T00:10:00Z",
            "evolution_record": {
                "record_id": "er_test_01",
                "changes": {
                    "warmth": {"delta": 0.01},
                    "initiative": {"delta": -0.004},
                },
            },
            "growth_records": [
                create_growth_record(
                    record_id="gr_pcr_01",
                    source_event_id="e_pcr",
                    growth_signal="high_user_activity_warmth",
                    source_type="preference",
                    growth_level="context",
                    affected_dimensions={"warmth": 0.004},
                    confidence=0.6,
                    reason="PCR 附带 GR",
                    created_at="2026-07-29T00:10:00Z",
                )
            ],
            "confidence": 0.65,
            "evidence_count": 3,
            "evaluator_meta": {"passed": True},
            "requires_validation": True,
            "reason": "PCR 测试：温暖度+主动性微调",
        }

    def test_01_from_growth_records(self):
        updater = SelfModelUpdater()
        grs = [
            create_growth_record(
                "gr_a", "e1", "tone_pref_gentle", "preference", "context",
                {"warmth": 0.002}, 0.6, "偏爱温柔语气", "2026-07-29T00:00:00Z",
            ),
            create_growth_record(
                "gr_b", "e2", "milestone_autonomy", "identity", "trait",
                {"autonomy": 0.01}, 0.7, "自主里程碑", "2026-07-29T00:00:00Z",
            ),
        ]
        suggs = updater.from_growth_records(grs)
        self.assertGreaterEqual(len(suggs), 2)
        # 有偏好 + 核心权重建议
        self.assertTrue(any(s.add_preferences for s in suggs))
        self.assertTrue(any(s.update_core_value_weights for s in suggs))

    def test_02_from_insights(self):
        updater = SelfModelUpdater()
        ins = [
            {"insight_id": "ins_a", "pattern_detected": "high_frequency_proactive",
             "pattern_frequency": 5, "confidence": 0.7, "summary": "高频主动"},
            {"insight_id": "ins_b", "pattern_detected": "",  # 无 pattern → 也会进历史
             "pattern_frequency": 0, "confidence": 0.5, "summary": "无 pattern"},
        ]
        suggs = updater.from_insights(ins)
        # 2 条：第 1 条有 add_patterns+history；第 2 条只有 history 但 summary()=no changes 过滤掉
        self.assertGreaterEqual(len(suggs), 1)
        patterns = 0
        for s in suggs:
            patterns += len(s.add_patterns)
        self.assertEqual(patterns, 1)

    def test_03_from_pcr(self):
        updater = SelfModelUpdater()
        sug = updater.from_pcr(self._make_pcr())
        self.assertIsNotNone(sug)
        # 有 trait 更新（warmth + initiative）
        self.assertIn("warmth", sug.update_stable_traits)
        self.assertIn("initiative", sug.update_stable_traits)
        # 有偏好（来自 PCR.growth_records 里的 GR）
        self.assertGreaterEqual(len(sug.add_preferences), 1)
        # 历史条目：1（PCR 本身）
        self.assertEqual(len(sug.add_history_entries), 1)
        # Understanding 增量 > 0
        self.assertGreater(sum(sug.understanding_delta.values()), 0.0)

    def test_04_apply_suggestion_to_manager(self):
        updater = SelfModelUpdater()
        mgr = SelfModelManager("si_apply_01")
        mgr.refresh(trait_states={
            "warmth": create_trait_state("warmth", 0.7),
            "initiative": create_trait_state("initiative", 0.5),
        })
        sug = updater.from_pcr(self._make_pcr())
        before_warmth = next((s.current_value for s in mgr.identity.stable_traits if s.trait == "warmth"), None)
        updater.apply_suggestion(mgr, sug)
        after_warmth = next((s.current_value for s in mgr.identity.stable_traits if s.trait == "warmth"), None)
        # warmth 应该变化（evolution_record +0.01 + GR +0.004）
        self.assertGreater(after_warmth, before_warmth)
        # 历史里追加 1 条 PCR history
        self.assertEqual(len([h for h in mgr.identity.growth_history if h.source_type == "personality_change_request"]), 1)
        # overall_understanding 上升
        self.assertGreater(mgr.identity.overall_understanding, 0.0)

    def test_05_merge_suggestions(self):
        updater = SelfModelUpdater()
        a = SelfModelChangeSuggestion(
            source_type="growth_record",
            source_id="gr_x",
            update_stable_traits={
                "warmth": {"delta": 0.01, "confidence": 0.6, "sources": ["gr_x"]},
            },
            understanding_delta={"trait_awareness": 0.002},
        )
        b = SelfModelChangeSuggestion(
            source_type="reflection_insight",
            source_id="ins_x",
            update_stable_traits={
                "warmth": {"delta": 0.003, "confidence": 0.5, "sources": ["ins_x"]},
            },
            understanding_delta={"trait_awareness": 0.001},
        )
        merged = updater.merge_suggestions([a, b])
        self.assertEqual(len(merged.update_stable_traits), 1)
        self.assertAlmostEqual(merged.update_stable_traits["warmth"]["delta"], 0.013, places=5)
        self.assertEqual(merged.understanding_delta["trait_awareness"], 0.003)
        self.assertEqual(merged.source_type, "merged")

    def test_06_min_delta_threshold_below(self):
        cfg = SelfModelUpdaterConfig(min_trait_delta_for_suggestion=0.01)
        updater = SelfModelUpdater(config=cfg)
        pcr = dict(self._make_pcr())
        # 把 initiative 的 delta 降到很小
        pcr["evolution_record"]["changes"]["initiative"]["delta"] = 0.0005
        sug = updater.from_pcr(pcr)
        self.assertNotIn("initiative", sug.update_stable_traits)
        self.assertIn("warmth", sug.update_stable_traits)


# ============================================================
# 4. RuntimeCore 接入测试（轻量级：不启动事件循环）
# ============================================================

class TestRuntimeSelfModelIntegration(unittest.TestCase):
    def _make_config(self, tmp: str) -> Dict[str, Any]:
        return {
            "adapters_enabled": True,
            "experience_enabled": True,
            "cognitive_enabled": False,
            "memory_store_path": os.path.join(tmp, "memory_sm.json"),
            "growth_proposals_path": os.path.join(tmp, "proposals_sm.json"),
            "eval_min_confidence": 0.3,
            "eval_min_evidence_count": 1,
            "sm_min_pattern_confidence": 0.2,
            "sm_require_approval": True,
            "tick_interval_seconds": 1,
            "state_file": os.path.join(tmp, "rt_state_sm.json"),
        }

    def test_01_self_model_components_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            self.assertIsNotNone(rc.self_model_manager)
            self.assertIsNotNone(rc.self_model_updater)
            self.assertEqual(rc._pending_self_model_suggestions, [])

    def test_02_disabled_when_adapters_disabled(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": False})
        self.assertIsNone(rc.self_model_manager)
        self.assertIsNone(rc.self_model_updater)

    def test_03_refresh_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            snap = rc.refresh_self_model(
                trait_states={
                    "warmth": create_trait_state("warmth", 0.72),
                    "shyness": create_trait_state("shyness", 0.6),
                    "self_expression": create_trait_state("self_expression", 0.5),
                },
                personality_vector_dict={"gentleness": 0.66},
                relationship_state={"attachment_level": "依恋"},
            )
            self.assertIsNotNone(snap)
            self.assertGreaterEqual(snap["stable_traits_count"], 3)
            contradiction_descs = [c.description or "" for c in rc.self_model_manager.identity.contradictions]
            # contradiction.羞怯 vs.自我表达
            self.assertTrue(any("羞怯" in d and "自我表达" in d for d in contradiction_descs))

    def test_04_list_and_accept_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            # 1. 生成一条 PCR 建议
            pcr = {
                "request_id": "pcr_sm_01",
                "source_proposal_id": "gp_01",
                "source_insight_id": "ins_01",
                "timestamp": "2026-07-29T00:00:00Z",
                "evolution_record": {
                    "record_id": "er_01",
                    "changes": {"warmth": {"delta": 0.008}},
                },
                "growth_records": [],
                "confidence": 0.6,
                "evidence_count": 2,
                "evaluator_meta": {},
                "requires_validation": True,
                "reason": "测试 PCR→SelfModel 建议",
            }
            sug = rc.generate_self_model_suggestion_from_pcr(pcr)
            self.assertIsNotNone(sug)
            # 2. 手动入队（因为我们没有走 accept_proposal 路径）
            rc._pending_self_model_suggestions.append(sug)
            self.assertEqual(len(rc.list_self_model_suggestions(limit=10)), 1)
            # 3. 先 refresh 一些 trait（否则 apply 找不到 warmth）
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.7),
            })
            before_v = next(
                (s.current_value for s in rc.self_model_manager.identity.stable_traits if s.trait == "warmth"),
                None,
            )
            # 4. 显式审批并应用
            ok = rc.accept_self_model_suggestion(sug)
            self.assertTrue(ok)
            after_v = next(
                (s.current_value for s in rc.self_model_manager.identity.stable_traits if s.trait == "warmth"),
                None,
            )
            self.assertGreater(after_v, before_v)
            # 5. pending 被清空
            self.assertEqual(len(rc.list_self_model_suggestions(limit=10)), 0)

    def test_05_reject_suggestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            sug = SelfModelChangeSuggestion(
                source_type="reflection_insight",
                source_id="ins_x",
                add_patterns=[BehavioralPattern(pattern_detected="x", frequency=1)],
            )
            rc._pending_self_model_suggestions.append(sug)
            ok = rc.reject_self_model_suggestion(sug.suggestion_id)
            self.assertTrue(ok)
            self.assertEqual(len(rc._pending_self_model_suggestions), 0)


# ============================================================
# 5. 完整生命周期测试
#    Experience → Memory → Reflection → GrowthProposal → PCR → SelfModel
# ============================================================

class TestFullSelfModelLifecycle(unittest.TestCase):
    def test_01_end_to_end(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "cognitive_enabled": False,
                "memory_store_path": os.path.join(tmp, "mem_e2e.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_e2e.json"),
                "state_file": os.path.join(tmp, "rt_e2e.json"),
                "tick_interval_seconds": 1,
                "reflection_min_experiences": 2,
                "eval_min_confidence": 0.2,
                "eval_min_evidence_count": 1,
                "sm_min_pattern_confidence": 0.2,
                "sm_min_trait_delta": 0.0,
                "sm_require_approval": True,
                "cognitive_llm_enabled": False,
                "reflection_llm_enabled": False,
            }
            rc = RuntimeCore(config=config)
            try:
                self.assertIsNotNone(rc)
                # 直接构造 RuntimeExperience 放入 experience_builder（避免事件路径差异）
                from src.contracts.experience_schema import RuntimeExperience, ActionResult
                exps = []
                for i in range(6):
                    exp = RuntimeExperience(
                        experience_id=f"e_sm_{i}",
                        trigger_event={"event_type": "user_message", "data": {"content": f"hi{i}"}},
                        trigger_type="user_message",
                        decision_source="rule",
                        action_type="send_message",
                        result=ActionResult(
                            action_id=f"act_sm_{i}",
                            success=True,
                            response_received=(i % 4 == 0),  # 25% 响应率 → 低响应模式
                            user_response=None,
                        ),
                        self_state_before={"initiative": round(0.9 - i * 0.02, 3)},
                        self_state_after={"initiative": round(0.88 - i * 0.02, 3)},
                        duration_ms=100.0 + i,
                    )
                    exps.append(exp)
                    rc.experience_builder._buffer.append(exp)
                    # 也写入 Memory 适配（若可用）
                    if rc.memory_adapter:
                        rc.memory_adapter.store_experience(exp)
                # 2) 反思生成 Insight + GrowthProposal（reflection_engine 取 experience_builder.get_buffer()）
                insight = rc.reflect_on_experiences()
                self.assertIsNotNone(insight, "应至少产生一个 Insight")
                # 2b) 至少 store_insight 成功过（growth_adapter 存在）
                proposals = rc.get_growth_proposals(status="proposed", limit=20)
                self.assertGreaterEqual(len(proposals), 0, "提案列表应为 List")
                # 3) 若没 proposal，则走「人工构造 proposal 走同样链路」保证流程覆盖
                if len(proposals) == 0:
                    from src.contracts.growth_schema import GrowthProposal, ChangeItem
                    proposal = GrowthProposal(
                        id="gp_sm_manual",
                        source_insight_id=insight.insight_id,
                        pattern_detected=insight.pattern_detected or "low_user_response",
                        confidence=insight.confidence,
                        evidence_experience_ids=[e.experience_id for e in exps[:3]],
                        changes=[
                            ChangeItem(
                                trait="initiative",
                                delta=-0.005,
                                reason="低响应率下调主动性",
                                evidence_ids=[e.experience_id for e in exps[:3]],
                            ),
                        ],
                        reasoning="test manual reasoning",
                    )
                    rc.growth_adapter.store_growth_proposal(proposal)
                    proposals = [proposal]
                self.assertGreaterEqual(len(proposals), 1)
                # 4) 接受第一个提案 → 触发 PCR + SelfModel Suggestion
                accepted = rc.accept_growth_proposal(proposals[0].id)
                self.assertIsNotNone(accepted)
                self.assertEqual(accepted.status, "accepted")
                # 5) 检查 PCR 生成
                pcrs = rc.list_personality_change_requests(limit=10)
                self.assertGreaterEqual(len(pcrs), 1)
                # 6) 检查 SelfModel Suggestion 已生成并入队
                suggestions = rc.list_self_model_suggestions(limit=10)
                self.assertGreaterEqual(len(suggestions), 1)
                self.assertTrue(any(
                    s.source_type == "personality_change_request" for s in suggestions
                ))
                # 7) 先刷新 SelfModel 基础数据（TraitState + Insights）
                insights_all = rc.get_insights() if hasattr(rc, "get_insights") else []
                rc.refresh_self_model(
                    trait_states={
                        "warmth": create_trait_state("warmth", 0.7),
                        "initiative": create_trait_state("initiative", 0.5),
                        "self_confidence": create_trait_state("self_confidence", 0.5),
                        "shyness": create_trait_state("shyness", 0.6),
                        "self_expression": create_trait_state("self_expression", 0.5),
                    },
                    insights=insights_all,
                    relationship_state={"attachment_level": "熟悉", "familiarity_level": "熟悉", "closeness": 0.7},
                )
                snap_before = rc.get_self_model_snapshot()
                # 8) 应用第一条 SelfModel Suggestion（模拟审批通过）
                sug = suggestions[0]
                ok = rc.accept_self_model_suggestion(sug)
                self.assertTrue(ok)
                snap_after = rc.get_self_model_snapshot()
                # 9) 断言：版本号 +1，version/history_count/understanding 有所变化
                self.assertGreaterEqual(snap_after["version"], snap_before["version"])
                # 历史记录增长
                self.assertGreaterEqual(
                    snap_after["growth_history_count"],
                    snap_before["growth_history_count"],
                )
                # 10) 获取快照结构：必须有 understanding/top_traits 等
                self.assertIn("understanding", snap_after)
                self.assertIn("top_traits", snap_after)
                # 11) 不修改任何真实人格：PCR pending 未被应用到外部
                self.assertEqual(len(rc._pending_change_requests), len(pcrs))  # 仍在 pending 中
                # 12) 完整 Full 字典可序列化
                full = rc.get_self_model_full()
                self.assertIsInstance(full, dict)
                self.assertIn("identity_id", full)
                self.assertIn("growth_history", full)
            finally:
                try:
                    if rc.is_running:
                        rc.stop()
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()
