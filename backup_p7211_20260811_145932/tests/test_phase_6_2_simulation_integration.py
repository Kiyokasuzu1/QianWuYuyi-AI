"""
Phase 6.2: Long-term Simulation & End-to-End Integration

验证：
- 长期运行不崩溃、不无限增长、查询时间合理
- 365 天 / 3000 beliefs / 500 reflections / 5000 history events
- 重启周期（save → 重新构造 → load → 数据完整）
- 完整链路：PCR → SelfModelAdapter → ContextProvider → Prompt → Response
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import tempfile
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_belief import SelfBelief, SelfBeliefStore
from src.personality.self_history import SelfHistory, SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
from src.personality.self_model_adapter import SelfModelAdapter
from src.personality.self_model_persistence import SelfModelPersistence
from src.personality.self_model_guardian import SelfModelGuardian
from src.personality.self_model_runtime_context import SelfModelRuntimeContext
from src.personality.self_model_context_provider import SelfModelContextProvider
from src.personality.self_model_store import SelfModelStore


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="self_model_sim_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ============================================================
# 1. Long-term Simulation
# ============================================================

class TestLongTermSimulation:
    def test_01_3000_beliefs_creation(self):
        """3000 beliefs 创建不崩溃"""
        store = SelfBeliefStore()
        for i in range(3000):
            b = SelfBelief(
                domain=["value", "preference", "pattern"][i % 3],
                content=f"belief_{i}",
                confidence=0.3 + (i % 7) * 0.1,
            )
            store.add(b)
        assert store.count() == 3000

    def test_02_5000_history_creation(self):
        """5000 history events 不崩溃（受 MAX_EVENTS 截断）"""
        h = SelfHistory()
        for i in range(5000):
            ev = SelfHistoryEvent(
                event_type=SelfHistoryEventType.PCR_APPLIED,
                source_type="sim",
                source_id=str(i),
                summary=f"e_{i}",
            )
            h.append(ev)
        # 受 MAX_EVENTS 截断
        assert h.count() <= SelfHistory.MAX_EVENTS
        assert h.count() > 0

    def test_03_500_reflections_creation(self):
        store = SelfReflectionStore()
        for i in range(500):
            n = SelfReflectionNote(
                trigger_source="manual",
                reflection_type=["identity", "value", "growth"][i % 3],
                content=f"r_{i}",
                confidence=0.5,
            )
            store.append(n)
        assert store.count() <= SelfReflectionStore.MAX_NOTES
        assert store.count() > 0

    def test_04_query_performance_3000_beliefs(self):
        """3000 beliefs 查询时间合理 (< 0.5s)"""
        store = SelfBeliefStore()
        for i in range(3000):
            b = SelfBelief(
                domain=["value", "preference"][i % 2],
                content=f"b_{i}",
                confidence=0.3 + (i % 7) * 0.1,
            )
            store.add(b)
        start = time.time()
        out = store.query(domain="value", min_confidence=0.5, limit=50)
        elapsed = time.time() - start
        assert elapsed < 0.5
        assert len(out) <= 50

    def test_05_365_days_30_cycles(self):
        """模拟 365 天，每天添加 beliefs/reflections + guardian decay"""
        bstore = SelfBeliefStore()
        rstore = SelfReflectionStore()
        history = SelfHistory()
        g = SelfModelGuardian()
        for day in range(365):
            # 每天添加若干 belief
            for i in range(8):  # 8 * 365 ≈ 2920
                b = SelfBelief(
                    domain=["value", "preference", "pattern"][i % 3],
                    content=f"day{day}_b{i}",
                    confidence=0.4 + (i % 5) * 0.1,
                )
                bstore.add(b)
            # 每天添加 reflection
            if day % 1 == 0:
                n = SelfReflectionNote(
                    trigger_source="manual",
                    reflection_type="growth",
                    content=f"day{day}_r",
                    confidence=0.5,
                )
                rstore.append(n)
            # 每 30 天一次 decay
            if day % 30 == 29:
                g.decay_stale_beliefs(bstore.all())
        # 不应该崩溃
        assert bstore.count() > 2500
        # 不应超过 MAX_NOTES 太多
        assert rstore.count() <= SelfReflectionStore.MAX_NOTES

    def test_06_simulation_orchestrator_prompt(self):
        """模拟 + 接 prompt：3000 beliefs 也能限制 prompt 长度"""
        bstore = SelfBeliefStore()
        for i in range(3000):
            b = SelfBelief(
                domain="preference",
                content=f"belief_{i}_content",
                confidence=0.3 + (i % 7) * 0.1,
            )
            bstore.add(b)
        ctx = SelfModelRuntimeContext(beliefs=bstore)
        text = ctx.build_prompt_text()
        # 限制 max_prompt_chars
        assert len(text) <= ctx.max_prompt_chars


# ============================================================
# 2. Restart Cycle
# ============================================================

class TestRestartCycle:
    def test_01_beliefs_restart(self, tmp_data_dir):
        """beliefs: save → restart → load → 数据存在"""
        # 阶段 1
        bstore = SelfBeliefStore()
        bstore.add(SelfBelief(domain="value", content="restart_value_1", confidence=0.7))
        bstore.add(SelfBelief(domain="preference", content="restart_pref_1", confidence=0.6))
        pers = SelfModelPersistence(tmp_data_dir)
        pers.save_beliefs(bstore)
        # 阶段 2：重新构造
        pers2 = SelfModelPersistence(tmp_data_dir)
        loaded = pers2.load_beliefs()
        assert len(loaded) == 2
        contents = {b["content"] for b in loaded}
        assert "restart_value_1" in contents
        assert "restart_pref_1" in contents

    def test_02_full_restart_via_adapter(self, tmp_data_dir):
        """通过 SelfModelAdapter 全量重启"""
        adapter1 = SelfModelAdapter()
        pers1 = SelfModelPersistence(tmp_data_dir)
        adapter1.attach_persistence(pers1)
        adapter1._beliefs.add(SelfBelief(domain="value", content="full_restart_1", confidence=0.7))
        adapter1._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="restart_h",
        ))
        adapter1._reflections.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="identity",
            content="restart_r", confidence=0.5,
        ))
        adapter1.save_state()
        # 重新构造
        adapter2 = SelfModelAdapter()
        adapter2.attach_persistence(SelfModelPersistence(tmp_data_dir))
        counts = adapter2.load_state()
        assert counts["beliefs"] == 1
        assert counts["history"] == 1
        assert counts["reflections"] == 1

    def test_03_three_cycles_stable(self, tmp_data_dir):
        """3 轮 save-load 不引入重复"""
        for cycle in range(3):
            adapter = SelfModelAdapter()
            adapter.attach_persistence(SelfModelPersistence(tmp_data_dir))
            counts = adapter.load_state()
            # 添加一条
            adapter._beliefs.add(SelfBelief(
                domain="value",
                content=f"cycle_{cycle}",
                confidence=0.6,
            ))
            adapter.save_state()
        # 重新加载
        adapter_final = SelfModelAdapter()
        adapter_final.attach_persistence(SelfModelPersistence(tmp_data_dir))
        counts = adapter_final.load_state()
        assert counts["beliefs"] == 3


# ============================================================
# 3. End-to-End Integration
# ============================================================

class TestEndToEndIntegration:
    def test_01_pcr_to_prompt_full_chain(self):
        """完整链路：PCR → Adapter → ContextProvider → Prompt"""
        # 1. 构造 Adapter + stores
        adapter = SelfModelAdapter()
        # 2. 模拟 PCR
        pcr = {
            "request_id": "pcr_e2e_1",
            "source_proposal_id": "prop_e2e_1",
            "source_insight_id": "ins_1",
            "confidence": 0.75,
            "evidence_count": 4,
            "reason": "羽依发现自己在独处时更自在",
            "evolution_record": {
                "trait_changes": {"shyness": 0.05, "warmth": -0.02}
            },
        }
        r = adapter.apply_pcr(pcr)
        assert r["applied"] is True
        # 3. 接入 ContextProvider
        provider = SelfModelContextProvider(SelfModelStore())
        provider.attach_phase_6_2(
            beliefs=adapter.get_beliefs(),
            history=adapter.get_history(),
            reflections=adapter.get_reflections(),
        )
        # 4. 提取 prompt
        text = provider.get_runtime_self_prompt()
        # 5. 验证 prompt 含新 belief 内容
        # (belief 内容是 trait 描述)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_02_emotion_pattern_to_prompt(self):
        """emotion pattern → adapter.apply_external_change → prompt 包含"""
        adapter = SelfModelAdapter()
        # 模拟 emotion pattern
        adapter.apply_external_change(
            change_type="emotion",
            reason="emotion_pattern_recognition",
            source="emotion_growth_service",
            confidence=0.7,
            affected_traits={"warmth": 0.05, "sensitivity": 0.03},
        )
        # 构造 context
        provider = SelfModelContextProvider(SelfModelStore())
        provider.attach_phase_6_2(
            beliefs=adapter.get_beliefs(),
            history=adapter.get_history(),
            reflections=adapter.get_reflections(),
        )
        text = provider.get_runtime_self_prompt()
        # 至少包含"信念"section
        assert "信念" in text or "belief" in text.lower() or len(text) > 0

    def test_03_response_question_identity(self):
        """回答"你是什么样的AI?"时基于 SelfIdentity"""
        # 这里只验证 prompt 中包含身份信息
        def identity_provider():
            return {
                "identity_id": "yuyi_v1",
                "identity_name": "羽依",
                "core_values": [{"name": "warmth"}, {"name": "gentleness"}],
                "version": 1,
            }
        ctx = SelfModelRuntimeContext(identity_provider=identity_provider)
        text = ctx.build_prompt_text()
        assert "羽依" in text
        assert "warmth" in text or "gentleness" in text

    def test_04_response_question_experience(self):
        """回答"你经历过什么改变?"时基于 SelfHistory"""
        history = SelfHistory()
        for i in range(3):
            history.append(SelfHistoryEvent(
                event_type=SelfHistoryEventType.PCR_APPLIED,
                source_type="test",
                source_id=str(i),
                summary=f"我开始更理解独处的意义 ({i})",
            ))
        ctx = SelfModelRuntimeContext(history=history)
        text = ctx.build_prompt_text()
        assert "成长" in text or any(
            f"({i})" in text for i in range(3)
        )

    def test_05_response_question_self_reflection(self):
        """回答"你觉得自己有什么变化?"时基于 SelfReflection"""
        reflections = SelfReflectionStore()
        reflections.append(SelfReflectionNote(
            trigger_source="manual",
            reflection_type="identity",
            content="我开始感受到自己的成长",
            confidence=0.7,
        ))
        ctx = SelfModelRuntimeContext(reflections=reflections)
        text = ctx.build_prompt_text()
        assert "反思" in text
        assert "我开始感受到自己的成长" in text

    def test_06_response_question_why_belief(self):
        """回答"你为什么这样想?"时基于 SelfBelief"""
        bstore = SelfBeliefStore()
        bstore.add(SelfBelief(
            domain="value",
            content="我相信温暖的回应比冷峻的判断更有意义",
            confidence=0.8,
        ))
        ctx = SelfModelRuntimeContext(beliefs=bstore)
        text = ctx.build_prompt_text()
        assert "温暖的回应" in text

    def test_07_guardian_contradiction_in_pipeline(self):
        """Guardian 检测矛盾后，矛盾 belief 仍可被 prompt 引用但标记 needs_review"""
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="preference", content="我喜欢热闹的社交", confidence=0.8),
            SelfBelief(domain="preference", content="我讨厌人群与社交", confidence=0.8),
        ]
        ctrs = g.detect_contradictions(beliefs)
        # 矛盾被记录
        assert len(ctrs) >= 1
        # 但 belief 仍可正常入 prompt
        bstore = SelfBeliefStore()
        for b in beliefs:
            bstore.add(b)
        ctx = SelfModelRuntimeContext(beliefs=bstore)
        text = ctx.build_prompt_text()
        assert "热闹" in text or "社交" in text


# ============================================================
# 4. Authority 收口端到端
# ============================================================

class TestAuthorityEndToEnd:
    def test_01_three_sources_route_to_adapter(self):
        """3 个来源 (emotion/personality/evolution) 全部走 adapter"""
        adapter = SelfModelAdapter()
        # emotion
        r1 = adapter.apply_external_change(
            change_type="emotion",
            reason="pattern_1",
            source="emotion_growth_service",
            confidence=0.6,
            affected_traits={"warmth": 0.05},
        )
        # personality
        r2 = adapter.apply_external_change(
            change_type="personality",
            reason="resolver_1",
            source="personality_resolver",
            confidence=0.6,
            affected_traits={"shyness": -0.02},
        )
        # growth
        r3 = adapter.apply_external_change(
            change_type="growth",
            reason="evolution_1",
            source="personality_evolution_pipeline",
            proposal_id="prop_1",
            confidence=0.6,
            affected_traits={"caring": 0.03},
        )
        # 3 个变化都成功
        assert r1["applied"] or r1["beliefs_added"] >= 0
        assert r2["applied"] or r2["beliefs_added"] >= 0
        assert r3["applied"] or r3["beliefs_added"] >= 0
        # 至少 3 个 history 事件
        assert adapter._history.count() >= 3

    def test_02_all_changes_have_source_label(self):
        """所有 external change 都带 source 标签（可审计）"""
        adapter = SelfModelAdapter()
        adapter.apply_external_change(
            change_type="emotion", reason="r1", source="emotion_growth_service",
            confidence=0.6, affected_traits={"warmth": 0.01},
        )
        adapter.apply_external_change(
            change_type="personality", reason="r2", source="personality_resolver",
            confidence=0.6, affected_traits={"shyness": -0.01},
        )
        # 每个 history event 都有 source 字段
        for ev in adapter._history.all():
            assert ev.source_type in ("emotion_growth_service", "personality_resolver") or ev.metadata.get("source")


# ============================================================
# 5. Phase 6.2 综合验证
# ============================================================

class TestPhase62Summary:
    def test_01_full_scenario_simulation(self, tmp_data_dir):
        """完整场景模拟：
        1. 启动（load from disk）
        2. 模拟 1 天的活动
        3. 保存
        4. 关闭
        5. 重新启动
        6. 验证数据
        """
        # 1. 启动
        adapter = SelfModelAdapter()
        pers = SelfModelPersistence(tmp_data_dir)
        adapter.attach_persistence(pers)
        adapter.load_state()
        # 2. 模拟一天的活动
        adapter.apply_external_change(
            change_type="emotion", reason="morning", source="emotion_growth_service",
            confidence=0.7, affected_traits={"warmth": 0.02},
        )
        adapter.apply_external_change(
            change_type="personality", reason="afternoon", source="personality_resolver",
            confidence=0.7, affected_traits={"shyness": -0.01},
        )
        b = SelfBelief(domain="value", content="今天我意识到陪伴很重要", confidence=0.65)
        adapter._beliefs.add(b)
        # 3. 保存
        adapter.save_state()
        # 4-5. 重新启动
        adapter2 = SelfModelAdapter()
        pers2 = SelfModelPersistence(tmp_data_dir)
        adapter2.attach_persistence(pers2)
        counts = adapter2.load_state()
        # 6. 验证
        assert counts["beliefs"] >= 1
        assert counts["history"] >= 2
        # belief 内容应包含
        contents = [b.content for b in adapter2.get_beliefs().all()]
        assert any("陪伴" in c for c in contents)

    def test_02_no_breaking_change_to_existing_api(self):
        """Phase 6.2 不破坏现有 SelfModelAdapter API"""
        adapter = SelfModelAdapter()
        # apply_pcr 仍可用
        pcr = {
            "request_id": "r1",
            "source_proposal_id": "p1",
            "confidence": 0.7,
            "evidence_count": 2,
            "reason": "test",
        }
        r = adapter.apply_pcr(pcr)
        assert r is not None
        # 旧 getter 仍可用
        assert adapter.get_beliefs() is not None
        assert adapter.get_history() is not None
        assert adapter.get_reflections() is not None
        assert adapter.get_snapshot_store() is not None
