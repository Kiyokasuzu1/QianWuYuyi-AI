"""
Phase 3.8.3-C 测试套件 — Growth 来源追溯与语义稳定性

测试范围：
1. source_growth_record_id 存在
2. evidence_ids 证据链保持
3. 旧数据兼容（缺失新字段）
4. Pipeline 不再修改 GrowthRecord
5. 回归测试
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
    record_id="growth_001",
    source_event_id="memory_001",
    growth_signal="creative_activity_interest",
    growth_level="trait",
    affected_dimensions=None,
    confidence=0.82,
    reason="长期创作行为",
):
    """创建一条测试用 GrowthRecord"""
    from src.growth.growth_record import create_growth_record
    return create_growth_record(
        record_id=record_id,
        source_event_id=source_event_id,
        growth_signal=growth_signal,
        source_type="creation",
        growth_level=growth_level,
        affected_dimensions=affected_dimensions if affected_dimensions is not None else {"creativity": 0.03},
        confidence=confidence,
        reason=reason,
        created_at=datetime.now().isoformat(),
    )


# ============================================================
# Test 1: source_growth_record_id 存在
# ============================================================

def test_source_growth_record_id_present():
    """验证转换后 PersonalityGrowthRecord 包含 source_growth_record_id"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(record_id="growth_001")

    result = adapter.convert(record)

    assert "source_growth_record_id" in result, \
        "PersonalityGrowthRecord should have source_growth_record_id"
    assert result["source_growth_record_id"] == "growth_001", \
        f"source_growth_record_id should be 'growth_001', got {result['source_growth_record_id']}"

    print("✅ Test 1 通过: source_growth_record_id 正确追溯来源 GrowthRecord")


# ============================================================
# Test 2: evidence_ids 证据链保持
# ============================================================

def test_evidence_ids_preserved():
    """验证证据链正确传递"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()

    # Case 1: source_event_id → evidence_ids fallback
    record = _make_growth_record(
        record_id="growth_001",
        source_event_id="memory_001",
    )
    result = adapter.convert(record)
    assert "evidence_ids" in result
    assert isinstance(result["evidence_ids"], list)
    assert "memory_001" in result["evidence_ids"], \
        f"evidence_ids should contain source_event_id, got {result['evidence_ids']}"

    print("✅ Test 2a 通过: source_event_id → evidence_ids fallback")

    # Case 2: GrowthRecord 已有 evidence_ids（优先使用）
    record_with_evidence = _make_growth_record(
        record_id="growth_002",
        source_event_id="memory_002",
    )
    record_with_evidence["evidence_ids"] = ["memory_100", "memory_200"]
    result = adapter.convert(record_with_evidence)
    assert "memory_100" in result["evidence_ids"]
    assert "memory_200" in result["evidence_ids"]
    # 已有 evidence_ids 时不应再添加 source_event_id
    assert result["evidence_ids"] == ["memory_100", "memory_200"], \
        f"Should preserve existing evidence_ids, got {result['evidence_ids']}"

    print("✅ Test 2b 通过: 已有 evidence_ids 优先保留")


# ============================================================
# Test 3: 旧数据兼容
# ============================================================

def test_legacy_data_compatibility():
    """验证没有新增字段的 PersonalityGrowthRecord 可以正常加载"""
    from src.personality.personality_growth_record import (
        PersonalityGrowthHistory, validate_record
    )

    # 模拟旧版本数据（无 source_growth_record_id 和 evidence_ids）
    legacy_record = {
        "record_id": "pgr_old001",
        "timestamp": "2024-01-01T00:00:00",
        "trigger_events": ["creative_activity_interest"],
        "changes": {"creativity": {"delta": 0.03}},
        "affected_dimensions": ["creativity"],
        "meaning": "我逐渐发现创造和表达成为我成长的一部分",
        "narrative": "我逐渐意识到创造对我来说具有越来越重要的意义",
        "confidence": 0.82,
        "validation_count": 1,
        "growth_level": "trait",
    }

    # validate_record 应通过（旧数据兼容）
    assert validate_record(legacy_record), \
        "validate_record should accept legacy records without new fields"

    # PersonalityGrowthHistory.add() 应接受
    history = PersonalityGrowthHistory()
    assert history.add(legacy_record), \
        "PersonalityGrowthHistory should accept legacy records"

    # 读取时新字段应有默认值
    loaded = history.latest()
    assert loaded is not None
    # 新字段缺失时应为 None / []
    assert loaded.get("source_growth_record_id") is None, \
        f"Legacy record source_growth_record_id should be None, got {loaded.get('source_growth_record_id')}"

    print("✅ Test 3 通过: 旧数据兼容（新字段缺失时使用默认值）")


# ============================================================
# Test 4: Pipeline 不再修改 GrowthRecord
# ============================================================

def test_pipeline_no_longer_mutates_growth_record():
    """验证 Pipeline 转换后 GrowthRecord 不包含 meaning/narrative/changes"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    tmp_dir = tempfile.mkdtemp(prefix="test_trace_pipeline_")
    try:
        state = GrowthState(state_path=os.path.join(tmp_dir, "growth_state.json"))
        pipeline = GrowthPipeline(growth_state=state)

        # 验证 _inject_compat_fields 已删除
        assert not hasattr(pipeline, "_inject_compat_fields"), \
            "_inject_compat_fields should be removed from GrowthPipeline"

        # 验证 adapter 存在
        assert hasattr(pipeline, "narrative_adapter"), \
            "GrowthPipeline should have narrative_adapter"

        # 手动创建一条 GrowthRecord 并转换
        from src.growth.growth_record import create_growth_record
        record = create_growth_record(
            record_id="growth_test",
            source_event_id="evt_test",
            growth_signal="creative_activity_interest",
            source_type="creation",
            growth_level="trait",
            affected_dimensions={"creativity": 0.03},
            confidence=0.82,
            reason="测试",
            created_at=datetime.now().isoformat(),
        )

        # 保存原始 keys
        original_keys = set(record.keys())

        # 通过 adapter 转换
        personality_record = pipeline.narrative_adapter.convert(record)

        # 验证 GrowthRecord 未被修改
        assert set(record.keys()) == original_keys, \
            f"GrowthRecord should not gain keys: {set(record.keys()) - original_keys}"

        # 验证 GrowthRecord 不包含 meaning/narrative/changes
        assert "meaning" not in record, "GrowthRecord should NOT have meaning"
        assert "narrative" not in record, "GrowthRecord should NOT have narrative"
        assert "changes" not in record, "GrowthRecord should NOT have changes"

        # 验证 PersonalityGrowthRecord 包含这些字段
        assert "meaning" in personality_record
        assert "narrative" in personality_record
        assert "changes" in personality_record

        print("✅ Test 4 通过: Pipeline 不再修改 GrowthRecord（事实层保持纯净）")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 5: 回归测试 — Phase 3.8.2-B
# ============================================================

def test_phase_382b_regression():
    """验证 Phase 3.8.2-B 测试全部通过"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_phase_382b.py", "-v", "--tb=short"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    if result.returncode != 0:
        print("Phase 3.8.2-B regression output:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    assert result.returncode == 0, f"Phase 3.8.2-B regression failed: {result.stderr[:200]}"
    print("✅ Test 5 通过: Phase 3.8.2-B 回归测试全部通过")


# ============================================================
# Test 6: 回归测试 — Phase 3.8.3
# ============================================================

def test_phase_383_regression():
    """验证 Phase 3.8.3 测试全部通过"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase383_self_model_growth.py", "-v", "--tb=short"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    if result.returncode != 0:
        print("Phase 3.8.3 regression output:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    assert result.returncode == 0, f"Phase 3.8.3 regression failed: {result.stderr[:200]}"
    print("✅ Test 6 通过: Phase 3.8.3 回归测试全部通过")


# ============================================================
# Test 7: 回归测试 — Phase 3.8.3-B Adapter
# ============================================================

def test_phase_383b_adapter_regression():
    """验证 Phase 3.8.3-B Adapter 测试全部通过"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_narrative_adapter.py", "-v", "--tb=short",
         "-k", "not regression"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    if result.returncode != 0:
        print("Phase 3.8.3-B adapter regression output:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    assert result.returncode == 0, f"Phase 3.8.3-B adapter regression failed: {result.stderr[:200]}"
    print("✅ Test 7 通过: Phase 3.8.3-B Adapter 回归测试全部通过")


# ============================================================
# Test 8: growth_pipeline 回归
# ============================================================

def test_growth_pipeline_regression():
    """验证 growth_pipeline 测试全部通过"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_pipeline.py", "-v", "--tb=short"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    if result.returncode != 0:
        print("Growth pipeline regression output:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    assert result.returncode == 0, f"Growth pipeline regression failed: {result.stderr[:200]}"
    print("✅ Test 8 通过: Growth Pipeline 回归测试全部通过")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    results = []
    tests = [
        ("Test 1: source_growth_record_id 存在", test_source_growth_record_id_present),
        ("Test 2: evidence_ids 证据链", test_evidence_ids_preserved),
        ("Test 3: 旧数据兼容", test_legacy_data_compatibility),
        ("Test 4: Pipeline 不修改 GrowthRecord", test_pipeline_no_longer_mutates_growth_record),
        ("Test 5: Phase 3.8.2-B 回归", test_phase_382b_regression),
        ("Test 6: Phase 3.8.3 回归", test_phase_383_regression),
        ("Test 7: Phase 3.8.3-B Adapter 回归", test_phase_383b_adapter_regression),
        ("Test 8: Growth Pipeline 回归", test_growth_pipeline_regression),
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
    print(f"Phase 3.8.3-C 测试结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)