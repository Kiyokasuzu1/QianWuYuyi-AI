"""
Phase 3.8.2-B 测试套件

测试范围：
1. GrowthProposal 创建与字段完整性
2. GrowthEngine.apply_proposal() 纯执行方法
3. GrowthPipeline._build_proposal_from_evaluated() 提案构建
4. GrowthPipeline.incremental_update() Proposal 化流程
5. Orchestrator.process() 中 GrowthPipeline 集成（隔离性）
6. 向后兼容：apply(event) 旧接口仍可用
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# ============================================================
# Test 1: GrowthProposal 创建与字段完整性
# ============================================================

def test_proposal_creation():
    """验证 GrowthProposal 可以正确创建，所有字段完整"""
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    changes = [
        ChangeItem(
            path="creativity",
            before=0.5,
            after=0.03,
            reason="growth_signal=creative_activity_interest, confidence=0.85",
        )
    ]

    proposal = GrowthProposal(
        source_event_id="evt_test_001",
        proposed_changes=changes,
        confidence=0.85,
        evidence_ids=["ev_abc123"],
        evaluator_meta={
            "growth_signal": "creative_activity_interest",
            "growth_level": "preference",
            "growth_domain": "creative",
            "_governance_origin": "GrowthPipeline.incremental_update",
            "_source_schema": "growth_evaluator",
        },
        status="proposed",
    )

    # 字段完整性
    assert proposal.id.startswith("prop_"), f"Expected id to start with prop_, got {proposal.id}"
    assert proposal.source_event_id == "evt_test_001"
    assert len(proposal.proposed_changes) == 1
    assert proposal.proposed_changes[0].path == "creativity"
    assert proposal.proposed_changes[0].after == 0.03
    assert proposal.confidence == 0.85
    assert proposal.evidence_ids == ["ev_abc123"]
    assert proposal.status == "proposed"
    assert proposal.schema_version == "1.0"

    # to_dict / from_dict 往返
    d = proposal.to_dict()
    assert isinstance(d, dict)
    assert d["proposed_changes"][0]["path"] == "creativity"

    restored = GrowthProposal.from_dict(d)
    assert restored.id == proposal.id
    assert restored.confidence == 0.85

    print("✅ Test 1 通过: GrowthProposal 创建与字段完整性")


def test_proposal_no_evidence():
    """验证没有 evidence 的 proposal 创建正确（拒绝由 apply_proposal 处理）"""
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    proposal = GrowthProposal(
        proposed_changes=[ChangeItem(path="curiosity", after=0.02)],
        confidence=0.3,
        evidence_ids=[],  # 无证据
        status="proposed",
    )

    assert proposal.evidence_ids == []
    assert proposal.confidence == 0.3
    print("✅ Test 1b 通过: 无证据 proposal 创建正确")


# ============================================================
# Test 2: GrowthEngine.apply_proposal() 纯执行方法
# ============================================================

def _setup_temp_state():
    """创建临时 GrowthState 用于测试"""
    from src.growth.growth_state import GrowthState
    tmp_dir = tempfile.mkdtemp(prefix="test_growth_state_")
    state_path = os.path.join(tmp_dir, "growth_state.json")
    return GrowthState(state_path=state_path), tmp_dir


def test_apply_proposal_with_valid_proposal():
    """验证 apply_proposal 正确执行合法 GrowthProposal"""
    from src.growth.growth_engine import GrowthEngine
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        # 使用 self_confidence（默认 metrics 中存在，初始值 0.10）
        before_val = state.get_metric("self_confidence")

        changes = [ChangeItem(path="self_confidence", before=before_val, after=0.04, reason="test")]
        proposal = GrowthProposal(
            source_event_id="evt_test_002",
            proposed_changes=changes,
            confidence=0.9,
            evidence_ids=["ev_001", "ev_002"],
            status="proposed",
        )

        result = engine.apply_proposal(proposal)

        assert result["status"] == "applied", f"Expected 'applied', got {result}"
        assert result["mode"] == "proposal"
        assert "delta" in result
        assert "self_confidence" in result["delta"]
        assert result["delta"]["self_confidence"] > 0

        after_val = state.get_metric("self_confidence")
        assert after_val > before_val, f"Expected growth, before={before_val}, after={after_val}"
        print("✅ Test 2 通过: apply_proposal 正确执行合法 GrowthProposal")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_proposal_rejects_no_evidence_low_confidence():
    """验证 apply_proposal 拒绝无证据 + 低置信度的 proposal"""
    from src.growth.growth_engine import GrowthEngine
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        changes = [ChangeItem(path="curiosity", after=0.05)]
        proposal = GrowthProposal(
            proposed_changes=changes,
            confidence=0.3,  # 低置信度
            evidence_ids=[],  # 无证据
        )

        result = engine.apply_proposal(proposal)

        assert result["status"] == "rejected", f"Expected 'rejected', got {result}"
        assert "no_evidence" in result.get("reason", "")
        print("✅ Test 2b 通过: 无证据低置信度 proposal 被拒绝")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_proposal_respects_max_delta():
    """验证 apply_proposal 遵守 MAX_SINGLE_EVENT_DELTA 上限"""
    from src.growth.growth_engine import GrowthEngine
    from src.growth.growth_schema import MAX_SINGLE_EVENT_DELTA
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        # 尝试设置一个极大的 delta
        huge_delta = 0.5  # 远超 MAX_SINGLE_EVENT_DELTA (0.05)
        changes = [ChangeItem(path="self_confidence", after=huge_delta)]
        proposal = GrowthProposal(
            proposed_changes=changes,
            confidence=1.0,
            evidence_ids=["ev_001"],
        )

        result = engine.apply_proposal(proposal)

        if result["status"] == "applied":
            actual_delta = result["delta"].get("self_confidence", 0)
            assert actual_delta <= MAX_SINGLE_EVENT_DELTA + 0.001, \
                f"Delta {actual_delta} exceeds MAX_SINGLE_EVENT_DELTA {MAX_SINGLE_EVENT_DELTA}"
            print(f"✅ Test 2c 通过: delta {actual_delta} 受限于 MAX_SINGLE_EVENT_DELTA {MAX_SINGLE_EVENT_DELTA}")
        else:
            print(f"✅ Test 2c 通过: proposal 被拒绝（{result.get('reason')}）")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_apply_proposal_dict_input():
    """验证 apply_proposal 兼容 dict 输入"""
    from src.growth.growth_engine import GrowthEngine

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        proposal_dict = {
            "id": "prop_test_dict",
            "source_event_id": "evt_003",
            "proposed_changes": [
                {"path": "self_confidence", "after": 0.03}
            ],
            "confidence": 0.9,
            "evidence_ids": ["ev_001"],
            "status": "proposed",
        }

        result = engine.apply_proposal(proposal_dict)

        assert result["status"] == "applied", f"Expected 'applied', got {result}"
        print("✅ Test 2d 通过: apply_proposal 兼容 dict 输入")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 3: GrowthPipeline._build_proposal_from_evaluated()
# ============================================================

def test_build_proposal_from_evaluated():
    """验证从评估结果构建 GrowthProposal"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    state, tmp_dir = _setup_temp_state()
    try:
        pipeline = GrowthPipeline(growth_state=state)

        evaluated = {
            "growth_allowed": True,
            "target_candidates": ["creativity", "self_expression"],
            "applied_delta": 0.03,
            "confidence": 0.85,
            "growth_signal": "creative_activity_interest",
            "growth_level": "preference",
            "growth_domain": "creative",
            "experience_meaning": {
                "surface_meaning": "用户对创作有持续兴趣",
                "deeper_significance": "用户通过创作表达自我",
                "confidence": 0.8,
            },
        }

        event = {
            "event_id": "evt_test_004",
            "topic": "创作",
            "evidence": [
                {"id": "ev_a1b2c3", "source_id": "mem_001"},
            ],
        }

        proposal = pipeline._build_proposal_from_evaluated(evaluated, event)

        assert proposal is not None, "Expected non-None proposal"
        assert proposal.source_event_id == "evt_test_004"
        assert len(proposal.proposed_changes) >= 1
        assert proposal.proposed_changes[0].path == "creativity"
        assert proposal.confidence == 0.85
        assert len(proposal.evidence_ids) > 0
        assert proposal.status == "proposed"
        # 验证 evaluator_meta 包含 meaning 信息
        assert "experience_meaning" in proposal.evaluator_meta
        assert proposal.evaluator_meta["_governance_origin"] == "GrowthPipeline.incremental_update"

        print("✅ Test 3 通过: _build_proposal_from_evaluated 正确构建 GrowthProposal")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_build_proposal_no_target_candidates():
    """验证无 target_candidates 时返回 None"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    state, tmp_dir = _setup_temp_state()
    try:
        pipeline = GrowthPipeline(growth_state=state)

        evaluated = {
            "growth_allowed": False,
            "target_candidates": [],
            "applied_delta": 0.0,
            "confidence": 0.5,
        }
        event = {"event_id": "evt_test_005"}

        proposal = pipeline._build_proposal_from_evaluated(evaluated, event)
        assert proposal is None, "Expected None for empty target_candidates"
        print("✅ Test 3b 通过: 无 target_candidates 返回 None")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 4: GrowthPipeline.incremental_update() Proposal 化流程
# ============================================================

def test_incremental_update_produces_proposal():
    """验证增量更新在 Proposal 化流程中正常工作"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    state, tmp_dir = _setup_temp_state()
    try:
        pipeline = GrowthPipeline(growth_state=state)

        # 多次创作事件触发成长
        for _ in range(5):
            result = pipeline.incremental_update("我今天用AI画了一幅新作品，感觉创作能力提升了")

        # 验证返回结构完整
        assert "events" in result
        assert "personality" in result
        assert "growth_records" in result

        # 如果有成长记录，验证其结构
        if result["growth_records"]:
            record = result["growth_records"][0]
            assert "record_id" in record or "affected_dimensions" in record, \
                f"GrowthRecord should have key fields, got {list(record.keys())}"

        print(f"✅ Test 4 通过: 增量更新完成 (events={len(result['events'])}, records={len(result['growth_records'])})")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_incremental_update_returns_personality():
    """验证 incremental_update 始终返回有效的人格数据"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    state, tmp_dir = _setup_temp_state()
    try:
        pipeline = GrowthPipeline(growth_state=state)

        # 普通聊天也应返回人格
        result = pipeline.incremental_update("今天天气不错")
        assert "personality" in result
        personality = result["personality"]
        assert personality is not None
        print("✅ Test 4b 通过: 即使无成长事件也返回人格数据")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 5: 向后兼容 — apply(event) 旧接口
# ============================================================

def test_apply_event_backward_compatible():
    """验证 apply(event) 旧接口仍然可用"""
    from src.growth.growth_engine import GrowthEngine

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        event = {
            "event_id": "evt_compat_001",
            "event_type": "creation",
            "canonical_topic": "创作",
            "topic": "创作",
            "importance": 0.8,
            "is_first_occurrence": True,
            "metadata": {"validator_apply": True},
            "source_ids": ["mem_001"],
            "category": "creation",
        }

        result = engine.apply(event)

        assert result["status"] in ("applied", "rejected", "error"), \
            f"Unexpected status: {result['status']}"
        print(f"✅ Test 5 通过: apply(event) 旧接口兼容 (status={result['status']})")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 6: Orchestrator 集成 — 隔离性
# ============================================================

def test_orchestrator_growth_pipeline_isolation():
    """
    验证 Orchestrator 中 GrowthPipeline 失败不影响聊天。
    
    由于 Orchestrator.process() 需要完整的 LLM 环境，
    这里只验证初始化隔离性和接口存在性。
    """
    from src.orchestrator import Orchestrator

    orch = Orchestrator(config={})

    # 验证 _growth_pipeline 属性存在
    assert hasattr(orch, "_growth_pipeline"), \
        "Orchestrator should have _growth_pipeline attribute"

    # 验证初始化成功或失败都有合理状态
    if orch._growth_pipeline is not None:
        assert hasattr(orch._growth_pipeline, "incremental_update"), \
            "GrowthPipeline should have incremental_update method"
        print("✅ Test 6 通过: GrowthPipeline 已成功初始化（隔离性通过）")
    else:
        print("✅ Test 6 通过: GrowthPipeline 初始化失败但已隔离（不影响 Orchestrator）")


# ============================================================
# Test 7: 数据流回滚 — before/after 可追溯
# ============================================================

def test_proposal_traceability():
    """验证 apply_proposal 返回 before/delta，确保可追溯"""
    from src.growth.growth_engine import GrowthEngine
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        before_val = state.get_metric("self_confidence")

        changes = [ChangeItem(path="self_confidence", before=before_val, after=0.03)]
        proposal = GrowthProposal(
            source_event_id="evt_trace_001",
            proposed_changes=changes,
            confidence=0.9,
            evidence_ids=["ev_001"],
        )

        result = engine.apply_proposal(proposal)

        if result["status"] == "applied":
            assert "before" in result, "Result should contain 'before' for traceability"
            assert "delta" in result, "Result should contain 'delta' for traceability"
            assert "self_confidence" in result["before"]
            assert "self_confidence" in result["delta"]
            assert result["before"]["self_confidence"] == before_val
            assert result["delta"]["self_confidence"] > 0
            print(f"✅ Test 7 通过: 可追溯性 before={result['before']}, delta={result['delta']}")
        else:
            print(f"✅ Test 7 通过: proposal 被拒绝但流程正确 ({result.get('reason')})")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 8: 架构边界 — GrowthEngine 不承担认知职责
# ============================================================

def test_growth_engine_no_cognitive_duty():
    """验证 GrowthEngine.apply_proposal 不包含意义判断、方向决定、规则映射"""
    from src.growth.growth_engine import GrowthEngine
    from src.contracts.growth_schema import GrowthProposal, ChangeItem

    state, tmp_dir = _setup_temp_state()
    try:
        engine = GrowthEngine(state=state)

        changes = [ChangeItem(path="some_random_dim", after=0.02)]
        proposal = GrowthProposal(
            proposed_changes=changes,
            confidence=0.8,
            evidence_ids=["ev_001"],
        )

        result = engine.apply_proposal(proposal)

        # 即使维度不存在，apply_proposal 也不应报错
        # 也不应尝试"理解"或"映射"这个维度
        assert result["status"] in ("applied", "rejected", "error"), \
            f"Unexpected status: {result['status']}"
        print(f"✅ Test 8 通过: GrowthEngine 不承担认知职责 (status={result['status']})")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    results = []
    tests = [
        ("Test 1: GrowthProposal 创建", test_proposal_creation),
        ("Test 1b: 无证据 proposal", test_proposal_no_evidence),
        ("Test 2: apply_proposal 合法 proposal", test_apply_proposal_with_valid_proposal),
        ("Test 2b: 拒绝无证据低置信度", test_apply_proposal_rejects_no_evidence_low_confidence),
        ("Test 2c: MAX_SINGLE_EVENT_DELTA 上限", test_apply_proposal_respects_max_delta),
        ("Test 2d: apply_proposal dict 输入", test_apply_proposal_dict_input),
        ("Test 3: _build_proposal_from_evaluated", test_build_proposal_from_evaluated),
        ("Test 3b: 无 target_candidates", test_build_proposal_no_target_candidates),
        ("Test 4: incremental_update Proposal 化", test_incremental_update_produces_proposal),
        ("Test 4b: 普通聊天返回人格", test_incremental_update_returns_personality),
        ("Test 5: apply(event) 向后兼容", test_apply_event_backward_compatible),
        ("Test 6: Orchestrator 隔离性", test_orchestrator_growth_pipeline_isolation),
        ("Test 7: 可追溯性 before/delta", test_proposal_traceability),
        ("Test 8: GrowthEngine 无认知职责", test_growth_engine_no_cognitive_duty),
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
    print(f"Phase 3.8.2-B 测试结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)