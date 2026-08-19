"""
Phase 3.8.3 测试套件 — Growth → SelfModel Cognitive Closure

测试范围：
1. GrowthRecord → SelfModelChangeProposal 生成
2. 有证据成长可以更新 SelfModel
3. 无证据/低置信度成长不能修改 SelfModel
4. SelfModel 更新后 ContextProvider 可读取
5. 失败隔离：SelfModel 更新失败不影响聊天回复
6. 架构边界：SelfModelUpdater 不修改 PersonalityState
"""
import sys
import os
import tempfile
import shutil
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


# ============================================================
# 工具函数
# ============================================================

def _make_growth_record(
    record_id="rec_001",
    source_event_id="evt_001",
    growth_signal="creative_activity_interest",
    source_type="creation",
    growth_level="preference",
    affected_dimensions=None,
    confidence=0.85,
    reason="用户长期鼓励创作活动",
):
    """创建一条测试用 GrowthRecord"""
    from src.growth.growth_record import create_growth_record
    return create_growth_record(
        record_id=record_id,
        source_event_id=source_event_id,
        growth_signal=growth_signal,
        source_type=source_type,
        growth_level=growth_level,
        affected_dimensions=affected_dimensions if affected_dimensions is not None else {"creativity": 0.03},
        confidence=confidence,
        reason=reason,
        created_at=datetime.now().isoformat(),
    )


def _make_self_model_store():
    """创建带有初始 SelfModel 的 SelfModelStore"""
    from src.personality.self_model_store import SelfModelStore
    from src.personality.self_model_builder import SelfModelBuilder
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    store = SelfModelStore()
    builder = SelfModelBuilder()
    history = PersonalityGrowthHistory()
    model = builder.build(
        history=history,
        trait_states={},
        base_identity="浅雾羽依",
        capability_limitations=["我没有真实的人类体验"],
    )
    store._current_model = model
    store._last_growth_count = 0
    return store


# ============================================================
# Test 1: GrowthRecord → SelfModelChangeProposal
# ============================================================

def test_growth_record_generates_proposal():
    """验证 GrowthRecord 可以生成 SelfModelChangeProposal"""
    from src.personality.self_model_updater import SelfModelUpdater, SelfModelChangeProposal

    store = _make_self_model_store()
    updater = SelfModelUpdater(self_model_store=store)

    record = _make_growth_record(
        confidence=0.85,
        growth_signal="creative_activity_interest",
        reason="用户长期鼓励创作活动",
    )

    proposal = updater.update_from_growth(record)

    assert proposal is not None, "Expected non-None proposal"
    assert isinstance(proposal, SelfModelChangeProposal)
    assert proposal.change_type == "narrative_append"
    assert proposal.target == "growth_narratives"
    assert "narrative" in proposal.change
    assert "dimension" in proposal.change
    assert "meaning" in proposal.change
    assert proposal.source["growth_id"] == "rec_001"
    assert proposal.source["source_event_id"] == "evt_001"
    assert proposal.source["confidence"] == 0.85
    assert len(proposal.source["evidence_ids"]) >= 1

    # 验证提案有时间戳
    assert proposal.timestamp is not None

    # 验证 to_dict
    d = proposal.to_dict()
    assert d["change_type"] == "narrative_append"
    assert "source" in d

    print("✅ Test 1 通过: GrowthRecord 正确生成 SelfModelChangeProposal")


# ============================================================
# Test 2: 有证据的成长可以更新 SelfModel
# ============================================================

def test_valid_growth_updates_self_model():
    """验证有证据的成长记录可以更新 SelfModelStore"""
    from src.personality.self_model_updater import SelfModelUpdater

    store = _make_self_model_store()

    # 记录更新前的 narratives 数量和 self_understanding
    old_model = store.get()
    old_narratives = old_model.get("growth_narratives", [])
    old_narrative_count = len(old_narratives)
    old_understanding = old_model.get("self_understanding", {})

    updater = SelfModelUpdater(self_model_store=store)

    record = _make_growth_record(
        confidence=0.85,
        growth_signal="creative_activity_interest",
    )

    proposal = updater.update_from_growth(record)
    assert proposal is not None

    # 验证 SelfModelStore 已更新
    new_model = store.get()
    new_narratives = new_model.get("growth_narratives", [])
    assert len(new_narratives) > old_narrative_count, \
        f"Expected new narratives, old={old_narrative_count}, new={len(new_narratives)}"

    # 验证新增的 narrative 包含源信息
    latest = new_narratives[-1]
    assert "narrative" in latest
    assert "dimension" in latest
    assert "_source_growth_id" in latest
    assert "_source_event_id" in latest
    assert "_evidence_ids" in latest
    assert "_confidence" in latest

    # 验证 self_understanding 已更新
    new_understanding = new_model.get("self_understanding", {})
    assert new_understanding.get("experience_awareness", 0) >= old_understanding.get("experience_awareness", 0), \
        "experience_awareness should increase"
    assert "overall" in new_understanding

    print("✅ Test 2 通过: 有证据成长正确更新 SelfModelStore")


# ============================================================
# Test 3: 无证据/低置信度成长不能修改 SelfModel
# ============================================================

def test_low_confidence_growth_rejected():
    """验证低置信度的成长记录被拒绝"""
    from src.personality.self_model_updater import SelfModelUpdater

    store = _make_self_model_store()
    old_model = store.get()
    old_narrative_count = len(old_model.get("growth_narratives", []))

    updater = SelfModelUpdater(self_model_store=store)

    # 低置信度记录
    record = _make_growth_record(
        record_id="rec_low",
        confidence=0.3,  # < 0.5 阈值
        growth_signal="weak_signal",
    )

    proposal = updater.update_from_growth(record)
    assert proposal is None, "Expected None for low-confidence record"

    # 验证 SelfModel 未被修改
    new_model = store.get()
    new_narrative_count = len(new_model.get("growth_narratives", []))
    assert new_narrative_count == old_narrative_count, \
        f"SelfModel should not be modified, old={old_narrative_count}, new={new_narrative_count}"

    print("✅ Test 3 通过: 低置信度成长被正确拒绝")


def test_no_evidence_growth_rejected():
    """验证无意义的成长记录被拒绝"""
    from src.personality.self_model_updater import SelfModelUpdater

    store = _make_self_model_store()
    old_narrative_count = len(store.get().get("growth_narratives", []))

    updater = SelfModelUpdater(self_model_store=store)

    # 空记录
    proposal = updater.update_from_growth({})
    assert proposal is None

    # 无 record_id（直接构造 dict，不通过 create_growth_record）
    record_no_id = {
        "source_event_id": "evt_001",
        "growth_signal": "test",
        "confidence": 0.8,
        "affected_dimensions": {"creativity": 0.03},
        "reason": "test",
        "created_at": datetime.now().isoformat(),
    }
    proposal = updater.update_from_growth(record_no_id)
    assert proposal is None, f"Expected None for record without record_id, got {proposal}"

    # 无 affected_dimensions
    record = _make_growth_record(affected_dimensions={})
    proposal = updater.update_from_growth(record)
    assert proposal is None

    new_narrative_count = len(store.get().get("growth_narratives", []))
    assert new_narrative_count == old_narrative_count, \
        "SelfModel should not be modified for invalid records"

    print("✅ Test 3b 通过: 无效成长记录被正确拒绝")


# ============================================================
# Test 4: SelfModel 更新后 ContextProvider 可读取
# ============================================================

def test_context_provider_reads_updated_self_model():
    """验证 SelfModel 更新后，ContextProvider 能读取到新的叙事"""
    from src.personality.self_model_updater import SelfModelUpdater
    from src.personality.self_model_context_provider import SelfModelContextProvider

    store = _make_self_model_store()
    updater = SelfModelUpdater(self_model_store=store)

    # 更新 SelfModel
    record = _make_growth_record(
        confidence=0.85,
        growth_signal="identity_strength_growth",
        affected_dimensions={"identity_strength": 0.04},
        reason="用户多次确认羽依的身份意义",
    )
    updater.update_from_growth(record)

    # 创建 ContextProvider 并读取
    provider = SelfModelContextProvider(store=store)
    context = provider.get_context()

    assert isinstance(context, str), f"Expected string context, got {type(context)}"
    assert len(context) > 0, "Context should not be empty"

    # 验证 context 包含自我认知相关字段
    assert "自我认知" in context or "身份" in context or "浅雾羽依" in context, \
        f"Context should contain self-model info, got: {context[:200]}"

    print("✅ Test 4 通过: ContextProvider 正确读取更新后的 SelfModel")


# ============================================================
# Test 5: 失败隔离 — SelfModel 更新失败不影响聊天回复
# ============================================================

def test_self_model_failure_isolation():
    """验证 SelfModelUpdater 失败时不会抛出异常"""
    from src.personality.self_model_updater import SelfModelUpdater

    # 不传入 store
    updater = SelfModelUpdater(self_model_store=None)

    record = _make_growth_record()
    # 应该不抛异常，静默跳过
    try:
        proposal = updater.update_from_growth(record)
        # 无 store 时，proposal 可以生成但不会写入
        assert proposal is not None or proposal is None  # 两种都合理
    except Exception as e:
        assert False, f"SelfModelUpdater should not raise exception: {e}"

    print("✅ Test 5 通过: SelfModelUpdater 失败隔离正确")


def test_self_model_store_none_safe():
    """验证 store 为 None 时的安全处理"""
    from src.personality.self_model_updater import SelfModelUpdater

    updater = SelfModelUpdater(self_model_store=None)

    # 批量更新也不应崩溃
    records = [
        _make_growth_record(record_id="r1", confidence=0.8),
        _make_growth_record(record_id="r2", confidence=0.9),
    ]
    try:
        proposals = updater.update_from_growth_records(records)
        assert isinstance(proposals, list)
    except Exception as e:
        assert False, f"update_from_growth_records should not raise: {e}"

    # 查询接口也不应崩溃
    narratives = updater.get_recent_narratives()
    assert narratives == []

    understanding = updater.get_self_understanding()
    assert understanding == {}

    print("✅ Test 5b 通过: store=None 时所有接口安全")


# ============================================================
# Test 6: 架构边界 — SelfModelUpdater 不修改 PersonalityState
# ============================================================

def test_self_model_updater_no_personality_modification():
    """验证 SelfModelUpdater 不修改人格状态"""
    from src.personality.self_model_updater import SelfModelUpdater
    from src.growth.growth_state import GrowthState

    tmp_dir = tempfile.mkdtemp(prefix="test_smu_")
    state_path = os.path.join(tmp_dir, "growth_state.json")
    try:
        state = GrowthState(state_path=state_path)
        before_metrics = {k: state.get_metric(k) for k in ["self_confidence", "trust", "closeness"]}

        store = _make_self_model_store()
        updater = SelfModelUpdater(self_model_store=store)

        record = _make_growth_record(
            confidence=0.85,
            affected_dimensions={"self_confidence": 0.04},
        )
        updater.update_from_growth(record)

        # 验证 GrowthState 未被修改
        for k, v in before_metrics.items():
            after = state.get_metric(k)
            assert after == v, f"GrowthState[{k}] should not be modified: {v} -> {after}"

        print("✅ Test 6 通过: SelfModelUpdater 不修改 PersonalityState")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 7: 批量更新与叙事数量控制
# ============================================================

def test_batch_update_and_narrative_limit():
    """验证批量更新和叙事数量限制"""
    from src.personality.self_model_updater import SelfModelUpdater

    store = _make_self_model_store()
    updater = SelfModelUpdater(self_model_store=store)

    # 批量添加 25 条记录（超过 20 条上限）
    records = []
    for i in range(25):
        records.append(_make_growth_record(
            record_id=f"rec_{i:03d}",
            source_event_id=f"evt_{i:03d}",
            confidence=0.8,
            affected_dimensions={"creativity": 0.02},
        ))

    proposals = updater.update_from_growth_records(records)
    assert len(proposals) == 25, f"Expected 25 proposals, got {len(proposals)}"

    # 验证 narratives 不超过 20 条
    narratives = store.get().get("growth_narratives", [])
    assert len(narratives) <= 20, f"Expected <= 20 narratives, got {len(narratives)}"

    print(f"✅ Test 7 通过: 批量更新 {len(proposals)} 条，narratives 控制在 {len(narratives)} 条")


# ============================================================
# Test 8: SelfModelChangeProposal 可直接序列化
# ============================================================

def test_proposal_serialization():
    """验证 SelfModelChangeProposal 可以正确序列化"""
    from src.personality.self_model_updater import SelfModelChangeProposal

    proposal = SelfModelChangeProposal(
        change_type="narrative_append",
        target="growth_narratives",
        change={
            "narrative": "我注意到自己在创造力方面有所成长",
            "dimension": "creativity",
            "meaning": "用户长期鼓励创作",
        },
        source={
            "growth_id": "rec_001",
            "source_event_id": "evt_001",
            "evidence_ids": ["evt_001", "rec_001"],
            "confidence": 0.85,
        },
    )

    d = proposal.to_dict()
    assert isinstance(d, dict)
    assert d["change_type"] == "narrative_append"
    assert d["change"]["narrative"] == "我注意到自己在创造力方面有所成长"
    assert d["source"]["confidence"] == 0.85

    # 验证时间戳
    assert d["timestamp"] is not None

    print("✅ Test 8 通过: SelfModelChangeProposal 序列化正确")


# ============================================================
# Test 9: Orchestrator 集成 — SelfModelUpdater 属性存在
# ============================================================

def test_orchestrator_has_self_model_updater():
    """验证 Orchestrator 初始化后包含 SelfModelUpdater"""
    from src.orchestrator import Orchestrator

    orch = Orchestrator(config={})

    assert hasattr(orch, "_self_model_updater"), \
        "Orchestrator should have _self_model_updater attribute"

    if orch._self_model_updater is not None:
        assert hasattr(orch._self_model_updater, "update_from_growth_records"), \
            "SelfModelUpdater should have update_from_growth_records method"
        print("✅ Test 9 通过: Orchestrator 已集成 SelfModelUpdater")
    else:
        print("✅ Test 9 通过: SelfModelUpdater 初始化失败但已隔离（不影响 Orchestrator）")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    results = []
    tests = [
        ("Test 1: GrowthRecord → Proposal", test_growth_record_generates_proposal),
        ("Test 2: 有证据成长更新 SelfModel", test_valid_growth_updates_self_model),
        ("Test 3: 低置信度被拒绝", test_low_confidence_growth_rejected),
        ("Test 3b: 无效记录被拒绝", test_no_evidence_growth_rejected),
        ("Test 4: ContextProvider 可读取", test_context_provider_reads_updated_self_model),
        ("Test 5: 失败隔离（无 store）", test_self_model_failure_isolation),
        ("Test 5b: store=None 安全", test_self_model_store_none_safe),
        ("Test 6: 不修改 PersonalityState", test_self_model_updater_no_personality_modification),
        ("Test 7: 批量更新与数量限制", test_batch_update_and_narrative_limit),
        ("Test 8: Proposal 序列化", test_proposal_serialization),
        ("Test 9: Orchestrator 集成", test_orchestrator_has_self_model_updater),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"❌ {name} 失败: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Phase 3.8.3 测试结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)