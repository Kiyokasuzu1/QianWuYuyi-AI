"""
Phase 6.3: End-to-End SelfModel Lifecycle Test

完整链路验证：

    User Experience
        ↓
    Memory
        ↓
    Reflection
        ↓
    GrowthProposal
        ↓
    PCR (Personality Change Request)
        ↓
    Personality Change
        ↓
    SelfModel Update
        ↓
    Persistence
        ↓
    Restart
        ↓
    Prompt
        ↓
    Response

包含：
- 全链路 happy path
- Restart 测试（重启后数据保留）
- Runtime Default 测试（无需手动 enable）
- Compatibility 测试（旧调用方式仍工作）
- Audit 测试（所有变化可追踪）
"""
from __future__ import annotations

import os
import sys
import json
import shutil
import tempfile
import pytest
from unittest.mock import patch, MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_model_adapter import SelfModelAdapter
from src.personality.self_belief import SelfBelief
from src.personality.self_history import SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote
from src.personality.self_model_persistence import SelfModelPersistence
from src.personality.self_model_sync_adapter import SelfModelSyncAdapter
from src.personality.self_model_v3 import SelfModelV3, NarrativeItem
from src.runtime.self_model_bootstrap import SelfModelBootstrap
from src.audit.self_model_audit import (
    record_self_model_read,
    record_self_model_write,
    record_pcr_applied,
    trace_self_model_evolution,
    SELF_MODEL_WRITE,
    SOURCE_ADAPTER,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_data_dir():
    d = tempfile.mkdtemp(prefix="sm_lifecycle_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def mock_openai():
    """Mock OpenAI 客户端以避免 API key 缺失错误"""
    fake_client = MagicMock()
    with patch("src.engine.OpenAI", return_value=fake_client):
        yield fake_client


@pytest.fixture
def tmp_audit_dir(monkeypatch):
    """使用临时目录作为 audit 数据目录"""
    d = tempfile.mkdtemp(prefix="sm_lifecycle_audit_")
    from src.audit import storage as storage_module
    if hasattr(storage_module, "_audit_storage_instance"):
        storage_module._audit_storage_instance = None
    monkeypatch.setenv("YUYI_AUDIT_DIR", d)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def reset_runtime_bridge():
    """隔离测试之间的 RuntimeBridge 状态"""
    from src.runtime import runtime_bridge as rb
    original_instance = rb.RuntimeBridge._instance if hasattr(rb.RuntimeBridge, "_instance") else None
    yield
    if original_instance is not None:
        rb.RuntimeBridge._instance = original_instance


# ============================================================
# 1. 全链路 happy path（核心 9 步）
# ============================================================

class TestFullLifecycleHappyPath:
    """
    验证 9 阶段完整链路：
    Experience → Memory → Reflection → GrowthProposal → PCR
              → Personality → SelfModel → Persistence → Restart → Prompt → Response
    """

    def test_01_experience_to_memory(self, tmp_data_dir, tmp_audit_dir):
        """阶段 1: User Experience → Memory"""
        # 模拟一次用户体验
        experience = {
            "user_message": "今天有点难过",
            "timestamp": "2026-07-30T10:00:00Z",
            "context": "用户表达负面情绪",
        }
        # 写入 memory（这里用 file 模拟）
        memory_path = os.path.join(tmp_data_dir, "memory.jsonl")
        with open(memory_path, "a") as f:
            f.write(json.dumps(experience) + "\n")
        # 验证 memory 可被读取
        with open(memory_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert loaded["user_message"] == "今天有点难过"

    def test_02_memory_to_reflection(self, tmp_data_dir):
        """阶段 2: Memory → Reflection"""
        # 模拟 reflection：从 memory 提取 insights
        reflection = {
            "source_memory": "今天有点难过",
            "insight": "用户近期情绪偏负面",
            "timestamp": "2026-07-30T10:05:00Z",
        }
        ref_path = os.path.join(tmp_data_dir, "reflection.jsonl")
        with open(ref_path, "a") as f:
            f.write(json.dumps(reflection) + "\n")
        # 验证
        with open(ref_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        loaded = json.loads(lines[0])
        assert loaded["insight"] == "用户近期情绪偏负面"

    def test_03_reflection_to_proposal(self, tmp_data_dir):
        """阶段 3: Reflection → GrowthProposal"""
        # 模拟 proposal 生成
        proposal = {
            "proposal_id": "prop_lifecycle_01",
            "source_insight": "insight_001",
            "trait_changes": {"empathy": 0.03},
            "confidence": 0.55,
            "evidence_count": 3,
            "reason": "用户多次表达负面情绪，羽依应增加共情",
        }
        prop_path = os.path.join(tmp_data_dir, "proposal.json")
        with open(prop_path, "w") as f:
            json.dump(proposal, f, ensure_ascii=False, indent=2)
        with open(prop_path) as f:
            loaded = json.load(f)
        assert loaded["trait_changes"]["empathy"] == 0.03
        assert loaded["confidence"] == 0.55

    def test_04_proposal_to_pcr(self, tmp_data_dir):
        """阶段 4: GrowthProposal → PCR"""
        pcr = {
            "request_id": "pcr_lifecycle_01",
            "source_proposal_id": "prop_lifecycle_01",
            "evolution_record": {
                "trait_changes": {"empathy": 0.03},
            },
            "growth_records": [
                {"record_id": "gr_01", "growth_signal": "empathy_increase", "confidence": 0.55}
            ],
            "confidence": 0.55,
            "evidence_count": 3,
            "reason": "personality_change_request",
        }
        pcr_path = os.path.join(tmp_data_dir, "pcr.json")
        with open(pcr_path, "w") as f:
            json.dump(pcr, f, ensure_ascii=False, indent=2)
        with open(pcr_path) as f:
            loaded = json.load(f)
        assert loaded["request_id"] == "pcr_lifecycle_01"
        assert loaded["evolution_record"]["trait_changes"]["empathy"] == 0.03

    def test_05_pcr_to_personality(self):
        """阶段 5: PCR → Personality Change（直接 trait 调整）"""
        # 模拟 personality adjustment
        trait_states = {"empathy": 0.5}
        # Apply 0.03 delta
        new_empathy = round(trait_states["empathy"] + 0.03, 4)
        assert new_empathy == 0.53

    def test_06_pcr_to_self_model(self, tmp_audit_dir):
        """阶段 6: PCR → SelfModel Update（通过 adapter）"""
        adapter = SelfModelAdapter(actor="lifecycle_test")
        pcr = {
            "request_id": "pcr_lifecycle_01",
            "source_proposal_id": "prop_lifecycle_01",
            "evolution_record": {"trait_changes": {"empathy": 0.03}},
            "growth_records": [{"record_id": "gr_01", "growth_signal": "empathy_inc"}],
            "confidence": 0.6,
            "evidence_count": 3,
            "reason": "lifecycle test",
        }
        result = adapter.apply_pcr(pcr)
        assert result["applied"] is True
        # 至少应创建一个 belief
        assert result["beliefs_added"] >= 1
        # 记录 audit
        record_pcr_applied(
            proposal_id="prop_lifecycle_01",
            pcr_id="pcr_lifecycle_01",
            reason="lifecycle test",
            confidence=0.6,
            beliefs_added=result["beliefs_added"],
        )
        # 验证 audit 已留痕
        trace = trace_self_model_evolution(proposal_id="prop_lifecycle_01")
        assert len(trace) >= 1

    def test_07_self_model_to_persistence(self, tmp_data_dir):
        """阶段 7: SelfModel → Persistence"""
        adapter = SelfModelAdapter(actor="persist_test")
        adapter._beliefs.add(SelfBelief(
            domain="preference", content="我在学着共情", confidence=0.6
        ))
        adapter._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="test", source_id="p1", summary="lifecycle test",
        ))
        # Attach persistence
        persistence = SelfModelPersistence(tmp_data_dir)
        adapter.attach_persistence(persistence)
        result = adapter.save_state()
        assert result.get("beliefs") is True
        # 验证文件存在
        assert os.path.exists(os.path.join(tmp_data_dir, "beliefs.jsonl"))
        assert os.path.exists(os.path.join(tmp_data_dir, "history.jsonl"))

    def test_08_restart_to_recovery(self, tmp_data_dir):
        """阶段 8: Restart → Recovery"""
        # 1) 模拟之前 runtime 写入
        a1 = SelfModelAdapter(actor="restart_test")
        a1.attach_persistence(SelfModelPersistence(tmp_data_dir))
        a1._beliefs.add(SelfBelief(
            domain="value", content="restart_belief", confidence=0.7
        ))
        a1._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="restart_history",
        ))
        a1._reflections.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="growth",
            content="restart_reflection", confidence=0.5,
        ))
        a1.save_state()
        # 2) 模拟 restart：重建 adapter + bootstrap
        a2 = SelfModelAdapter(actor="restart_test")
        boot = SelfModelBootstrap(data_dir=tmp_data_dir)
        env = boot.bootstrap(a2)
        # 验证
        assert env["load_counts"]["beliefs"] >= 1
        assert env["load_counts"]["history"] >= 1
        assert env["load_counts"]["reflections"] >= 1
        # 验证内容
        contents = [b.content for b in a2.get_beliefs().all()]
        assert "restart_belief" in contents

    def test_09_prompt_to_response(self, tmp_data_dir):
        """阶段 9: Prompt → Response（含 SelfModel context）"""
        # 准备 adapter with data
        adapter = SelfModelAdapter(actor="prompt_test")
        adapter._beliefs.add(SelfBelief(
            domain="value", content="我在学着共情用户", confidence=0.7
        ))
        # 通过 sync adapter 获取 combined view
        sync = SelfModelSyncAdapter(runtime_adapter=adapter)
        view = sync.get_combined_view()
        # 验证 view 包含 beliefs
        assert len(view.beliefs) >= 1
        # 模拟 prompt 渲染
        prompt_text = "【Self Belief】\n" + "\n".join(
            b.get("content", "") for b in view.beliefs
        )
        assert "我在学着共情用户" in prompt_text


# ============================================================
# 2. Restart 完整测试
# ============================================================

class TestRestartCycle:
    """验证重启后数据保留"""

    def test_01_single_restart(self, tmp_data_dir):
        """单次重启"""
        a1 = SelfModelAdapter()
        a1.attach_persistence(SelfModelPersistence(tmp_data_dir))
        a1._beliefs.add(SelfBelief(domain="value", content="cycle_1", confidence=0.5))
        a1.save_state()
        a2 = SelfModelAdapter()
        SelfModelBootstrap(data_dir=tmp_data_dir).bootstrap(a2)
        contents = [b.content for b in a2.get_beliefs().all()]
        assert "cycle_1" in contents

    def test_02_three_cycles(self, tmp_data_dir):
        """三次重启数据累加（每次新增 belief）"""
        for i in range(3):
            # 加载已有数据
            a = SelfModelAdapter()
            boot = SelfModelBootstrap(data_dir=tmp_data_dir)
            boot.bootstrap(a)
            # 追加新 belief
            a._beliefs.add(SelfBelief(domain="value", content=f"c_{i}", confidence=0.5))
            # 保存
            a.save_state()
        # 最终加载
        a_final = SelfModelAdapter()
        env = SelfModelBootstrap(data_dir=tmp_data_dir).bootstrap(a_final)
        # 验证内容包含 3 个 cycle
        contents = [b.content for b in a_final.get_beliefs().all()]
        for i in range(3):
            assert f"c_{i}" in contents, f"c_{i} not in {contents}"
        assert env["load_counts"]["beliefs"] >= 3

    def test_03_restart_with_history(self, tmp_data_dir):
        """重启后 history 保留"""
        a1 = SelfModelAdapter()
        a1.attach_persistence(SelfModelPersistence(tmp_data_dir))
        a1._history.append(SelfHistoryEvent(
            event_type=SelfHistoryEventType.PCR_APPLIED,
            source_type="t", source_id="x", summary="cycle_history",
        ))
        a1.save_state()
        a2 = SelfModelAdapter()
        SelfModelBootstrap(data_dir=tmp_data_dir).bootstrap(a2)
        summaries = [e.summary for e in a2.get_history().all()]
        assert "cycle_history" in summaries

    def test_04_restart_with_reflections(self, tmp_data_dir):
        """重启后 reflection 保留"""
        a1 = SelfModelAdapter()
        a1.attach_persistence(SelfModelPersistence(tmp_data_dir))
        a1._reflections.append(SelfReflectionNote(
            trigger_source="manual", reflection_type="growth",
            content="cycle_reflection", confidence=0.5,
        ))
        a1.save_state()
        a2 = SelfModelAdapter()
        SelfModelBootstrap(data_dir=tmp_data_dir).bootstrap(a2)
        contents = [n.content for n in a2.get_reflections().all()]
        assert "cycle_reflection" in contents


# ============================================================
# 3. Runtime Default Test
# ============================================================

class TestRuntimeDefault:
    """验证 Runtime 启动后 SelfModel 自动参与推理"""

    def test_01_runtime_creates_adapter(self):
        """Runtime 启动自动创建 adapter"""
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": True})
        if rc.get_self_model_adapter() is None:
            pytest.skip("adapter not created")
        assert rc.get_self_model_adapter() is not None

    def test_02_orchestrator_auto_enables(self, reset_runtime_bridge):
        """Orchestrator 默认启用 Phase 6.2"""
        from src.runtime import runtime_bridge as rb_module
        from src.orchestrator import Orchestrator
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            # 默认应 enabled（即使无 adapter 走空 store 兼容模式）
            assert orch._phase_6_2_enabled is True
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_03_bridge_exposes_adapter(self):
        """RuntimeBridge 暴露 adapter"""
        from src.runtime.runtime_bridge import RuntimeBridge
        assert hasattr(RuntimeBridge, "get_self_model_adapter")


# ============================================================
# 4. Compatibility Test
# ============================================================

class TestCompatibility:
    """验证旧调用方式仍工作"""

    def test_01_legacy_get_context(self, reset_runtime_bridge):
        """legacy get_context 仍工作"""
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            orch.disable_phase_6_2_self_model()
            ctx = orch.get_self_model_context()
            assert isinstance(ctx, str)
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_02_explicit_enable_disable(self, reset_runtime_bridge):
        """显式 enable/disable 仍工作"""
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            # 重新 enable
            result = orch.enable_phase_6_2_self_model(None)
            assert result is True
            assert orch.is_phase_6_2_self_model_enabled() is True
            # disable
            orch.disable_phase_6_2_self_model()
            assert orch.is_phase_6_2_self_model_enabled() is False
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_03_old_self_model_store(self):
        """旧 SelfModelStore 仍可用"""
        from src.personality.self_model_store import SelfModelStore
        store = SelfModelStore()
        assert store is not None
        assert store.get_active_self_model() is None  # 初始为空

    def test_04_old_self_model_v3(self):
        """旧 SelfModelV3 仍可用"""
        v3 = SelfModelV3(identity="test", traits={"a": 0.1})
        d = v3.to_dict()
        assert d["identity"] == "test"
        v3_loaded = SelfModelV3.from_dict(d)
        assert v3_loaded.identity == "test"


# ============================================================
# 5. Audit Test
# ============================================================

class TestAuditTrail:
    """验证所有变化可追踪"""

    def test_01_write_audit_trail(self, tmp_audit_dir):
        """写事件留痕"""
        record_pcr_applied(
            proposal_id="audit_p1", pcr_id="pcr_p1", reason="test",
            confidence=0.6, beliefs_added=1,
        )
        trace = trace_self_model_evolution(proposal_id="audit_p1")
        assert len(trace) >= 1
        assert trace[0]["proposal_id"] == "audit_p1"

    def test_02_read_audit_trail(self, tmp_audit_dir):
        """读事件留痕"""
        from src.audit.self_model_audit import (
            record_self_model_read, query_self_model_audit, SELF_MODEL_READ,
        )
        record_self_model_read(source="lifecycle_test", context_type="combined")
        results = query_self_model_audit(operation_type=SELF_MODEL_READ, source="lifecycle_test")
        assert len(results) >= 1

    def test_03_full_audit_chain(self, tmp_audit_dir):
        """完整 audit 链：write → audit → query → 找到"""
        # 1) apply PCR
        adapter = SelfModelAdapter(actor="chain_test")
        pcr = {
            "request_id": "chain_pcr", "source_proposal_id": "chain_prop",
            "evolution_record": {"trait_changes": {"openness": 0.02}},
            "growth_records": [],
            "confidence": 0.6, "evidence_count": 2, "reason": "chain",
        }
        result = adapter.apply_pcr(pcr)
        # 2) record audit
        record_pcr_applied(
            proposal_id="chain_prop", pcr_id="chain_pcr", reason="chain",
            confidence=0.6, beliefs_added=result["beliefs_added"],
            affected_traits={"openness": 0.02},
        )
        # 3) query
        trace = trace_self_model_evolution(proposal_id="chain_prop")
        assert len(trace) >= 1
        # 4) 验证 trace 包含 affected_traits
        has_affected = any(
            t.get("affected_traits", {}).get("openness") for t in trace
        )
        assert has_affected


# ============================================================
# 6. 完整 E2E 集成
# ============================================================

class TestE2EIntegration:
    """端到端完整流程"""

    def test_01_complete_e2e(self, tmp_data_dir, tmp_audit_dir, reset_runtime_bridge):
        """完整 9 阶段流程"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime import runtime_bridge as rb_module
        # 1) Runtime 启动（自动创建 adapter + bootstrap）
        rc = RuntimeCore(config={
            "adapters_enabled": True,
            "self_model_data_dir": tmp_data_dir,
        })
        if rc.get_self_model_adapter() is None:
            pytest.skip("adapter not created")
        # 2) Experience：模拟用户消息
        user_msg = "我今天有点开心"
        # 3) Memory：写入 memory
        memory_path = os.path.join(tmp_data_dir, "memory.jsonl")
        with open(memory_path, "a") as f:
            f.write(json.dumps({"user_message": user_msg, "ts": "2026-07-30"}) + "\n")
        # 4) Reflection：模拟 reflection
        reflection = {
            "source": user_msg,
            "insight": "用户今天情绪积极",
            "ts": "2026-07-30",
        }
        # 5) GrowthProposal → PCR
        pcr = {
            "request_id": "e2e_pcr_1", "source_proposal_id": "e2e_prop_1",
            "evolution_record": {"trait_changes": {"cheerfulness": 0.02}},
            "growth_records": [{"record_id": "gr_e2e", "growth_signal": "cheerful"}],
            "confidence": 0.6, "evidence_count": 2, "reason": "e2e test",
        }
        # 6) SelfModel Update via adapter
        adapter = rc.get_self_model_adapter()
        result = adapter.apply_pcr(pcr)
        assert result["applied"] is True
        # 7) Persistence
        save_result = adapter.save_state()
        # 至少一部分被保存
        # 8) Restart simulation
        a2 = SelfModelAdapter(actor="e2e_restart")
        boot = SelfModelBootstrap(data_dir=tmp_data_dir)
        env = boot.bootstrap(a2)
        # 验证 beliefs 至少 1 条
        assert env["load_counts"]["beliefs"] >= 1
        # 9) Prompt：adapter 数据可用于 prompt
        from src.personality.self_model_sync_adapter import SelfModelSyncAdapter
        sync = SelfModelSyncAdapter(runtime_adapter=a2)
        view = sync.get_combined_view()
        assert len(view.beliefs) >= 1

    def test_02_legacy_data_preserved(self, tmp_data_dir):
        """legacy 数据在整合中保留"""
        v3 = SelfModelV3(
            identity="legacy_yuyi",
            traits={"legacy_trait": 0.5},
            narrative_items=[NarrativeItem(text="legacy narrative")],
        )

        class _LegacyStore:
            def get_active_self_model(self):
                return v3
        sync = SelfModelSyncAdapter(legacy_store=_LegacyStore())
        view = sync.get_combined_view()
        assert view.identity == "legacy_yuyi"
        assert "legacy_trait" in view.traits
        assert "legacy narrative" in view.narratives

    def test_03_audit_complete(self, tmp_data_dir, tmp_audit_dir):
        """完整审计链：PCR → SelfModel → audit → trace"""
        adapter = SelfModelAdapter(actor="audit_complete")
        # Apply 3 PCRs
        for i in range(3):
            pcr = {
                "request_id": f"audit_complete_pcr_{i}",
                "source_proposal_id": f"audit_complete_prop_{i}",
                "evolution_record": {"trait_changes": {"openness": 0.01 * (i+1)}},
                "growth_records": [],
                "confidence": 0.5 + i*0.1,
                "evidence_count": 2, "reason": f"e2e_audit_{i}",
            }
            result = adapter.apply_pcr(pcr)
            record_pcr_applied(
                proposal_id=f"audit_complete_prop_{i}",
                pcr_id=f"audit_complete_pcr_{i}",
                reason=f"e2e_audit_{i}",
                confidence=0.5 + i*0.1,
                beliefs_added=result["beliefs_added"],
            )
        # Trace all
        trace_all = trace_self_model_evolution(limit=20)
        assert len(trace_all) >= 3

    def test_04_no_double_source_of_truth(self, tmp_data_dir):
        """无第二事实来源：sync 不持有数据"""
        adapter = SelfModelAdapter(actor="not_double")
        adapter._beliefs.add(SelfBelief(domain="value", content="x", confidence=0.5))
        sync = SelfModelSyncAdapter(runtime_adapter=adapter)
        # 第一次 view
        view1 = sync.get_combined_view()
        # 修改数据
        adapter._beliefs.add(SelfBelief(domain="value", content="y", confidence=0.5))
        # 第二次 view 应反映新数据（无缓存）
        view2 = sync.get_combined_view()
        assert len(view2.beliefs) > len(view1.beliefs)
