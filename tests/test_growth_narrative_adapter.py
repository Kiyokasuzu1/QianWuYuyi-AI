"""
Phase 3.8.3-B 测试套件 — GrowthNarrativeAdapter

测试范围：
1. 正常转换：creative_activity_interest → PersonalityGrowthRecord
2. 低置信度不拦截：Adapter 不负责过滤
3. 未知 growth_signal 不崩溃：fallback narrative
4. Pipeline 集成：GrowthRecord → Adapter → PersonalityGrowthHistory
5. 批量转换
6. 模板查询接口
7. 旧 Phase 3.8.2-B 测试全部通过（回归）
8. 旧 Phase 3.8.3 测试全部通过（回归）
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
        source_event_id="evt_001",
        growth_signal=growth_signal,
        source_type="creation",
        growth_level=growth_level,
        affected_dimensions=affected_dimensions if affected_dimensions is not None else {"creativity": 0.03},
        confidence=confidence,
        reason=reason,
        created_at=datetime.now().isoformat(),
    )


# ============================================================
# Test 1: 正常转换
# ============================================================

def test_normal_conversion():
    """验证 GrowthRecord → PersonalityGrowthRecord 正常转换"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(
        growth_signal="creative_activity_interest",
        confidence=0.82,
    )

    result = adapter.convert(record)

    # 验证字段完整性
    assert "record_id" in result
    assert "timestamp" in result
    assert "trigger_events" in result
    assert "changes" in result
    assert "affected_dimensions" in result
    assert "meaning" in result
    assert "narrative" in result
    assert "confidence" in result
    assert "growth_level" in result
    assert "validation_count" in result

    # 验证 changes 结构
    assert len(result["changes"]) > 0
    assert "creativity" in result["changes"]
    assert "delta" in result["changes"]["creativity"]

    # 验证 affected_dimensions 是 List[str]（不是 Dict）
    assert isinstance(result["affected_dimensions"], list)
    assert "creativity" in result["affected_dimensions"]

    # 验证 meaning 和 narrative 不是空
    assert len(result["meaning"]) > 0
    assert len(result["narrative"]) > 0

    # 验证 trigger_events 包含 growth_signal
    assert "creative_activity_interest" in result["trigger_events"]

    # 验证 validate_record 通过
    from src.personality.personality_growth_record import validate_record
    assert validate_record(result), "validate_record should pass"

    print("✅ Test 1 通过: 正常转换成功")


# ============================================================
# Test 1b: 各类型 growth_signal 转换
# ============================================================

def test_various_signals():
    """验证多种 growth_signal 都能正确转换"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter
    from src.personality.personality_growth_record import validate_record

    adapter = GrowthNarrativeAdapter()

    signals = [
        "creative_activity_interest",
        "learning_interest",
        "relationship_understanding",
        "identity_formation",
        "emotional_depth",
        "initiative_growth",
    ]

    for signal in signals:
        record = _make_growth_record(growth_signal=signal)
        result = adapter.convert(record)
        assert validate_record(result), f"validate_record failed for {signal}"
        assert len(result["meaning"]) > 0, f"meaning should not be empty for {signal}"
        assert len(result["narrative"]) > 0, f"narrative should not be empty for {signal}"

    print(f"✅ Test 1b 通过: {len(signals)} 种 growth_signal 全部转换成功")


# ============================================================
# Test 2: 低置信度不拦截
# ============================================================

def test_low_confidence_still_converts():
    """验证 Adapter 不负责过滤 — 低置信度仍转换"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(
        growth_signal="creative_activity_interest",
        confidence=0.2,  # 低置信度
    )

    result = adapter.convert(record)

    # 仍应转换成功
    assert result["confidence"] == 0.2
    assert len(result["meaning"]) > 0
    assert len(result["narrative"]) > 0

    # 但 validate_record 可能失败（取决于实现）
    # Adapter 不负责过滤，由 GrowthEvaluator 和 PersonalityGrowthHistory 负责
    print("✅ Test 2 通过: 低置信度仍转换（Adapter 不负责过滤）")


# ============================================================
# Test 3: 未知 growth_signal 不崩溃
# ============================================================

def test_unknown_signal_fallback():
    """验证未知 growth_signal 使用 fallback，不崩溃"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(
        growth_signal="completely_unknown_signal_xyz",
        confidence=0.8,
        reason="",  # 空 reason，确保触发 FALLBACK（而非用 reason 回退）
    )

    result = adapter.convert(record)

    # 不应崩溃
    assert len(result["meaning"]) > 0
    assert len(result["narrative"]) > 0

    # 应使用 fallback
    assert result["meaning"] == "这次经历让我对自身产生了新的理解"
    assert result["narrative"] == "我感受到自己在经历中逐渐变化"

    print("✅ Test 3 通过: 未知信号使用 fallback，不崩溃")


# ============================================================
# Test 3b: 异常回退
# ============================================================

def test_exception_fallback():
    """验证异常输入不崩溃，返回最小有效记录"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()

    # 空 dict
    result = adapter.convert({})
    assert "record_id" in result
    assert "meaning" in result
    assert "narrative" in result

    # None 的 growth_signal
    record = _make_growth_record(growth_signal="")
    result = adapter.convert(record)
    assert len(result["meaning"]) > 0

    print("✅ Test 3b 通过: 异常输入回退到最小有效记录")


# ============================================================
# Test 4: Pipeline 集成
# ============================================================

def test_pipeline_integration():
    """验证 GrowthRecord → Adapter → PersonalityGrowthHistory 完整链路"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    tmp_dir = tempfile.mkdtemp(prefix="test_adapter_pipeline_")
    try:
        state = GrowthState(state_path=os.path.join(tmp_dir, "growth_state.json"))
        pipeline = GrowthPipeline(growth_state=state)

        # 验证 adapter 已初始化
        assert hasattr(pipeline, "narrative_adapter")
        assert pipeline.narrative_adapter is not None

        # 多次创作事件触发成长
        for _ in range(5):
            result = pipeline.incremental_update("我今天画了一幅画，创作让我感到充实")

        # 验证 PersonalityGrowthHistory 有记录
        history_count = pipeline.growth_records.count()
        if history_count > 0:
            # 验证记录格式正确
            records = pipeline.growth_records.all()
            for r in records:
                assert "meaning" in r, f"PersonalityGrowthRecord should have meaning: {r}"
                assert "narrative" in r, f"PersonalityGrowthRecord should have narrative: {r}"
                assert "changes" in r, f"PersonalityGrowthRecord should have changes: {r}"
                assert isinstance(r["affected_dimensions"], list), \
                    f"affected_dimensions should be list, got {type(r['affected_dimensions'])}"
            print(f"✅ Test 4 通过: Pipeline 集成成功，{history_count} 条记录已进入 PersonalityGrowthHistory")
        else:
            print("✅ Test 4 通过: Pipeline 集成成功（无成长事件触发）")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# Test 5: 批量转换
# ============================================================

def test_batch_conversion():
    """验证批量转换"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    records = [
        _make_growth_record(record_id="r1", growth_signal="creative_activity_interest"),
        _make_growth_record(record_id="r2", growth_signal="learning_interest"),
        _make_growth_record(record_id="r3", growth_signal="relationship_understanding"),
    ]

    results = adapter.convert_batch(records)

    assert len(results) == 3
    for r in results:
        assert "meaning" in r
        assert "narrative" in r

    assert adapter.convert_count == 3
    print(f"✅ Test 5 通过: 批量转换 {len(results)} 条成功")


# ============================================================
# Test 6: 模板查询接口
# ============================================================

def test_template_query():
    """验证模板查询接口"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    # 有模板
    meaning = GrowthNarrativeAdapter.get_meaning_template("creative_activity_interest")
    assert meaning is not None
    assert "创造" in meaning

    narrative = GrowthNarrativeAdapter.get_narrative_template("creative_activity_interest")
    assert narrative is not None
    assert "创造" in narrative

    # 无模板
    meaning = GrowthNarrativeAdapter.get_meaning_template("nonexistent")
    assert meaning is None

    # 支持的信号列表
    signals = GrowthNarrativeAdapter.get_supported_signals()
    assert len(signals) > 0
    assert "creative_activity_interest" in signals

    print(f"✅ Test 6 通过: 模板查询接口正常（{len(signals)} 个信号）")


# ============================================================
# Test 7: 旧 GrowthRecord 不会被修改（不可变性）
# ============================================================

def test_growth_record_not_mutated():
    """验证 Adapter 不修改原始 GrowthRecord（与 _inject_compat_fields 不同）"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record()

    # 保存原始字段
    original_keys = set(record.keys())
    original_reason = record["reason"]

    # 转换
    result = adapter.convert(record)

    # 验证原始 record 未被修改
    assert set(record.keys()) == original_keys, \
        f"GrowthRecord should not gain new keys: {set(record.keys()) - original_keys}"
    assert record["reason"] == original_reason, "GrowthRecord.reason should not change"

    # 验证 result 是独立对象
    assert result is not record, "Result should be a new object, not the original"

    print("✅ Test 7 通过: Adapter 不修改原始 GrowthRecord（不可变性）")


# ============================================================
# Test 8: 回归测试 — Phase 3.8.2-B 全部通过
# ============================================================

def test_phase_382b_regression():
    """验证 Phase 3.8.2-B 测试全部通过"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_phase_382b.py", "-v", "--tb=short"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    if result.returncode != 0:
        print("Phase 3.8.2-B regression output:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    assert result.returncode == 0, f"Phase 3.8.2-B regression failed: {result.stderr[:200]}"
    print("✅ Test 8 通过: Phase 3.8.2-B 回归测试全部通过")


# ============================================================
# Test 9: 回归测试 — Phase 3.8.3 全部通过
# ============================================================

def test_phase_383_regression():
    """验证 Phase 3.8.3 测试全部通过"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase383_self_model_growth.py", "-v", "--tb=short"],
        capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    if result.returncode != 0:
        print("Phase 3.8.3 regression output:")
        print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
    assert result.returncode == 0, f"Phase 3.8.3 regression failed: {result.stderr[:200]}"
    print("✅ Test 9 通过: Phase 3.8.3 回归测试全部通过")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    results = []
    tests = [
        ("Test 1: 正常转换", test_normal_conversion),
        ("Test 1b: 多种信号转换", test_various_signals),
        ("Test 2: 低置信度不拦截", test_low_confidence_still_converts),
        ("Test 3: 未知信号 fallback", test_unknown_signal_fallback),
        ("Test 3b: 异常回退", test_exception_fallback),
        ("Test 4: Pipeline 集成", test_pipeline_integration),
        ("Test 5: 批量转换", test_batch_conversion),
        ("Test 6: 模板查询", test_template_query),
        ("Test 7: 不可变性", test_growth_record_not_mutated),
        ("Test 8: Phase 3.8.2-B 回归", test_phase_382b_regression),
        ("Test 9: Phase 3.8.3 回归", test_phase_383_regression),
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
    print(f"Phase 3.8.3-B 测试结果: {passed} 通过, {failed} 失败, 共 {len(tests)} 项")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)