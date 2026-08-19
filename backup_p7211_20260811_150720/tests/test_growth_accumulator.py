"""
成长累积器单元测试 v1.1
验证：单次事件成长幅度受限、重复累积有上限、关系事件不影响人格、旧记录活跃度衰减
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from datetime import datetime, timedelta
from src.personality.growth_accumulator import GrowthAccumulator


def make_record(source_type, affected_dimensions, confidence=0.8, days_ago=0):
    """辅助函数：创建测试用的 GrowthRecord"""
    created_at = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return {
        "source_type": source_type,
        "affected_dimensions": affected_dimensions,
        "confidence": confidence,
        "created_at": created_at,
        "growth_signal": "test",
        "growth_level": "preference",
        "record_id": "rec_test",
        "source_event_id": "evt_test",
        "reason": "test",
        "schema_version": 1,
    }


def test_single_event_growth_limited():
    """单次事件成长幅度受 GROWTH_SCALE 限制，不会暴涨"""
    acc = GrowthAccumulator()
    base = {"creativity": 0.5}  # 显式定义包含 creativity 的基础字典
    records = [make_record("creation", {"creativity": 0.5})]
    result = acc.compute(records, base)
    growth = result["creativity"] - base["creativity"]
    assert growth < 0.3, f"Growth too large: {growth}"
    assert growth > 0.0, f"Expected positive growth, got {growth}"
    assert result["creativity"] <= 1.0


def test_repeated_events_saturate():
    """100次重复事件累积后有 tanh 上限"""
    acc = GrowthAccumulator()
    base = {"creativity": 0.5}
    records = [make_record("creation", {"creativity": 0.01}) for _ in range(100)]
    result = acc.compute(records, base)
    assert result["creativity"] <= 1.0
    growth = result["creativity"] - base["creativity"]
    assert growth > 0.05, f"Growth too small for 100 records: {growth}"


def test_relationship_events_no_trait_impact():
    """关系事件权重为0，不产生人格影响"""
    acc = GrowthAccumulator()
    base = {"warmth": 0.5}
    records = [make_record("relationship", {"warmth": 0.5}) for _ in range(10)]
    result = acc.compute(records, base)
    assert result["warmth"] == 0.5, f"Relationship event should not affect traits, got {result['warmth']}"


def test_old_records_have_lower_activity():
    """旧记录活跃度降低，影响减弱"""
    acc = GrowthAccumulator()
    base = {"creativity": 0.5}
    new_record = make_record("creation", {"creativity": 0.5}, days_ago=0)
    old_record = make_record("creation", {"creativity": 0.5}, days_ago=365)
    result_new = acc.compute([new_record], base)
    result_old = acc.compute([old_record], base)
    assert result_new["creativity"] > result_old["creativity"], \
        f"New record should have higher impact: new={result_new['creativity']}, old={result_old['creativity']}"


def test_mixed_event_types():
    """不同事件类型权重不同，creation 影响大于 preference"""
    acc = GrowthAccumulator()
    base = {"creativity": 0.5}
    record_creation = make_record("creation", {"creativity": 0.1})
    record_preference = make_record("preference", {"creativity": 0.1})
    result_c = acc.compute([record_creation], base)
    result_p = acc.compute([record_preference], base)
    assert result_c["creativity"] > result_p["creativity"], \
        f"Creation should have higher weight: creation={result_c['creativity']}, preference={result_p['creativity']}"


if __name__ == "__main__":
    test_single_event_growth_limited()
    print("✅ 测试1通过：单次事件成长幅度受限（<0.3）")
    test_repeated_events_saturate()
    print("✅ 测试2通过：重复事件累积但有 tanh 上限")
    test_relationship_events_no_trait_impact()
    print("✅ 测试3通过：关系事件不影响人格 trait")
    test_old_records_have_lower_activity()
    print("✅ 测试4通过：旧记录活跃度自然降低")
    test_mixed_event_types()
    print("✅ 测试5通过：creation 权重大于 preference")
    print("\n🎉 全部通过")