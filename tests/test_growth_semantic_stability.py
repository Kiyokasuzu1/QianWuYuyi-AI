"""
Phase 3.8.4 测试套件 — Growth 系统语义稳定性

验证：
1. record_id 与 source_growth_record_id 完全解耦
2. 同一 GrowthRecord 多次转换产生不同 record_id
3. Bridge 传递来源追溯字段
4. 旧数据兼容
5. GrowthRecord 不可变
6. 回归测试
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
    affected_dimensions=None,
    confidence=0.82,
):
    from src.growth.growth_record import create_growth_record
    return create_growth_record(
        record_id=record_id,
        source_event_id=source_event_id,
        growth_signal=growth_signal,
        source_type="creation",
        growth_level="trait",
        affected_dimensions=affected_dimensions or {"creativity": 0.03},
        confidence=confidence,
        reason="长期创作行为",
        created_at=datetime.now().isoformat(),
    )


# ============================================================
# Test 1: record_id 与 source_growth_record_id 完全解耦
# ============================================================

def test_record_id_decoupled_from_source():
    """验证 Adapter 不复用 GrowthRecord 的 record_id"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(record_id="growth_001")

    result = adapter.convert(record)

    # record_id 应该是独立生成的 pgr_xxx 格式
    assert result["record_id"].startswith("pgr_"), \
        f"record_id should start with 'pgr_', got {result['record_id']}"

    # source_growth_record_id 应该指向来源
    assert result["source_growth_record_id"] == "growth_001", \
        f"source_growth_record_id should be 'growth_001', got {result['source_growth_record_id']}"

    # 两者不能相同
    assert result["record_id"] != result["source_growth_record_id"], \
        f"record_id should NOT equal source_growth_record_id: {result['record_id']}"

    print("Test 1: record_id 与 source_growth_record_id 完全解耦")


# ============================================================
# Test 2: 同一 GrowthRecord 转换两次 → 不同 record_id
# ============================================================

def test_double_conversion_unique_ids():
    """验证同一 GrowthRecord 转换两次产生不同 record_id"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(record_id="growth_001")

    result_a = adapter.convert(record)
    result_b = adapter.convert(record)

    # record_id 不同
    assert result_a["record_id"] != result_b["record_id"], \
        f"Two conversions should produce different record_id: {result_a['record_id']} vs {result_b['record_id']}"

    # source_growth_record_id 相同
    assert result_a["source_growth_record_id"] == result_b["source_growth_record_id"] == "growth_001", \
        "source_growth_record_id should be same for same GrowthRecord"

    # 两个 record_id 都是 pgr_ 格式
    assert result_a["record_id"].startswith("pgr_")
    assert result_b["record_id"].startswith("pgr_")

    print("Test 2: 同一 GrowthRecord 两次转换产生不同 record_id")


# ============================================================
# Test 3: Bridge 保留 source_growth_record_id 和 evidence_ids
# ============================================================

def test_bridge_preserves_traceability():
    """验证 GrowthHistoryBridge._normalize() 传递来源追溯字段"""
    from src.personality.growth_history_bridge import GrowthHistoryBridge

    bridge = GrowthHistoryBridge()

    # 模拟一条带来源追溯的 PersonalityGrowthRecord
    pgr = {
        "record_id": "pgr_a1b2c3d4e5f6",
        "timestamp": "2024-01-01T00:00:00",
        "source_growth_record_id": "growth_001",
        "evidence_ids": ["memory_001", "memory_005"],
        "trigger_events": ["creative_activity_interest"],
        "changes": {"creativity": {"delta": 0.03}},
        "affected_dimensions": ["creativity"],
        "meaning": "我逐渐发现创造和表达成为我成长的一部分",
        "narrative": "我逐渐意识到创造对我来说具有越来越重要的意义",
        "confidence": 0.82,
        "growth_level": "trait",
    }

    normalized = bridge._normalize(pgr)

    assert normalized["source_growth_record_id"] == "growth_001", \
        f"source_growth_record_id should be preserved, got {normalized.get('source_growth_record_id')}"

    assert normalized["evidence_ids"] == ["memory_001", "memory_005"], \
        f"evidence_ids should be preserved, got {normalized.get('evidence_ids')}"

    print("Test 3: Bridge 保留 source_growth_record_id 和 evidence_ids")


# ============================================================
# Test 4: 旧 PersonalityGrowthRecord 兼容
# ============================================================

def test_legacy_record_compatibility():
    """验证没有新字段的旧数据仍然可以通过验证和加载"""
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

    # validate_record 应通过
    assert validate_record(legacy_record), \
        "validate_record should accept legacy records"

    # PersonalityGrowthHistory 应接受
    history = PersonalityGrowthHistory()
    assert history.add(legacy_record), \
        "PersonalityGrowthHistory should accept legacy records"

    # 读取时新字段缺失 → 旧数据自然为 None
    loaded = history.latest()
    assert loaded is not None
    assert loaded.get("source_growth_record_id") is None, \
        "Legacy record should have source_growth_record_id=None"
    assert loaded.get("evidence_ids") is None, \
        "Legacy record should have evidence_ids=None"

    # Bridge 也能处理旧数据
    from src.personality.growth_history_bridge import GrowthHistoryBridge
    bridge = GrowthHistoryBridge()
    normalized = bridge._normalize(legacy_record)
    assert normalized["source_growth_record_id"] == ""
    assert normalized["evidence_ids"] == []

    print("Test 4: 旧数据兼容（缺失 source_growth_record_id 和 evidence_ids）")


# ============================================================
# Test 5: GrowthRecord 不可变
# ============================================================

def test_growth_record_immutability():
    """验证 Adapter 转换前后 GrowthRecord 字段完全一致"""
    from src.growth.growth_narrative_adapter import GrowthNarrativeAdapter

    adapter = GrowthNarrativeAdapter()
    record = _make_growth_record(record_id="growth_001")

    # 保存原始字段
    original_keys = set(record.keys())
    original_values = {k: record[k] for k in record}

    # 转换
    _ = adapter.convert(record)

    # 验证字段未增加
    assert set(record.keys()) == original_keys, \
        f"GrowthRecord should not gain keys: {set(record.keys()) - original_keys}"

    # 验证字段值未修改
    for key in original_keys:
        assert record[key] == original_values[key], \
            f"GrowthRecord field '{key}' was mutated: {original_values[key]} → {record[key]}"

    # 验证不包含 meaning/narrative/changes
    assert "meaning" not in record
    assert "narrative" not in record
    assert "changes" not in record

    print("Test 5: GrowthRecord 不可变（转换前后字段完全一致）")


# ============================================================
# Test 6: add() 失败时记录日志
# ============================================================

def test_add_failure_logs_warning():
    """验证 PersonalityGrowthHistory.add() 失败时内部记录日志"""
    import logging
    from io import StringIO
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    # 捕获日志
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.WARNING)

    logger = logging.getLogger("src.personality.personality_growth_record")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)

    try:
        history = PersonalityGrowthHistory()

        # 无效记录（缺少 meaning）
        invalid_record = {
            "record_id": "pgr_bad",
            "timestamp": "2024-01-01T00:00:00",
            "trigger_events": ["test"],
            "changes": {"creativity": {"delta": 0.03}},
            "affected_dimensions": ["creativity"],
            "meaning": "",  # 空，验证失败
            "confidence": 0.5,
            "growth_level": "trait",
        }

        result = history.add(invalid_record)
        assert result is False, "add() should return False for invalid record"

        # 验证日志已输出
        log_output = log_stream.getvalue()
        assert "验证失败" in log_output, \
            f"add() should log warning on validation failure, got: {log_output[:200]}"

        print("Test 6: add() 失败时内部记录日志")
    finally:
        logger.removeHandler(handler)


# ============================================================
# Test 7: Pipeline 不检查 add() 返回值
# ============================================================

def test_pipeline_add_no_return_check():
    """验证 Pipeline 中 add() 调用不检查返回值（日志在 History 内部）"""
    from src.growth.pipeline import GrowthPipeline
    from src.growth.growth_state import GrowthState

    tmp_dir = tempfile.mkdtemp(prefix="test_sem_stable_")
    try:
        state = GrowthState(state_path=os.path.join(tmp_dir, "growth_state.json"))
        pipeline = GrowthPipeline(growth_state=state)

        # 验证 narrative_adapter 存在
        assert hasattr(pipeline, "narrative_adapter")
        # 验证 _inject_compat_fields 已删除
        assert not hasattr(pipeline, "_inject_compat_fields")

        # 验证 growth_records 是 PersonalityGrowthHistory 实例
        from src.personality.personality_growth_record import PersonalityGrowthHistory
        assert isinstance(pipeline.growth_records, PersonalityGrowthHistory)

        print("Test 7: Pipeline 不检查 add() 返回值（职责在 History 内部）")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ============================================================
# 回归测试
# ============================================================

def test_regression_382b():
    """Phase 3.8.2-B 回归"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_phase_382b.py", "-v", "--tb=short"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    assert result.returncode == 0, f"3.8.2-B regression: {result.stderr[:200]}"
    print("Regression: Phase 3.8.2-B")


def test_regression_383():
    """Phase 3.8.3 回归"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase383_self_model_growth.py", "-v", "--tb=short"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    assert result.returncode == 0, f"3.8.3 regression: {result.stderr[:200]}"
    print("Regression: Phase 3.8.3")


def test_regression_383b():
    """Phase 3.8.3-B Adapter 回归"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_narrative_adapter.py", "-v", "--tb=short",
         "-k", "not regression"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    assert result.returncode == 0, f"3.8.3-B regression: {result.stderr[:200]}"
    print("Regression: Phase 3.8.3-B Adapter")


def test_regression_383c():
    """Phase 3.8.3-C Traceability 回归"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_narrative_traceability.py", "-v", "--tb=short",
         "-k", "not regression"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    assert result.returncode == 0, f"3.8.3-C regression: {result.stderr[:200]}"
    print("Regression: Phase 3.8.3-C Traceability")


def test_regression_pipeline():
    """Growth Pipeline 回归"""
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_growth_pipeline.py", "-v", "--tb=short"],
        capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        timeout=300,
    )
    assert result.returncode == 0, f"Pipeline regression: {result.stderr[:200]}"
    print("Regression: Growth Pipeline")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    tests = [
        ("Test 1: record_id 解耦", test_record_id_decoupled_from_source),
        ("Test 2: 双重转换唯一 ID", test_double_conversion_unique_ids),
        ("Test 3: Bridge 追溯", test_bridge_preserves_traceability),
        ("Test 4: 旧数据兼容", test_legacy_record_compatibility),
        ("Test 5: GrowthRecord 不可变", test_growth_record_immutability),
        ("Test 6: add() 日志", test_add_failure_logs_warning),
        ("Test 7: Pipeline 职责", test_pipeline_add_no_return_check),
        ("Test 8: 3.8.2-B 回归", test_regression_382b),
        ("Test 9: 3.8.3 回归", test_regression_383),
        ("Test 10: 3.8.3-B 回归", test_regression_383b),
        ("Test 11: 3.8.3-C 回归", test_regression_383c),
        ("Test 12: Pipeline 回归", test_regression_pipeline),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"FAIL {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Phase 3.8.4 测试结果: {passed}/{len(tests)} 通过, {failed} 失败")
    print(f"{'='*60}")

    if failed > 0:
        sys.exit(1)