"""
人格成长记录单元测试 v1.1
验证创建、验证、查询、筛选和意义保存功能
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.personality_growth_record import (
    create_personality_growth_record,
    validate_record,
    PersonalityGrowthHistory,
)


def test_create_record():
    """测试正常创建记录"""
    record = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={
            "creativity": {"before": 0.65, "after": 0.72, "delta": 0.07},
            "curiosity": {"momentum_before": 0.3, "momentum_after": 0.5},
        },
        affected_dimensions=["creativity", "curiosity"],
        meaning="通过持续创造形成了探索倾向",
        narrative="我发现自己越来越喜欢创造新东西了",
        confidence=0.8,
        validation_count=5,
        growth_level="preference",
    )
    assert record["record_id"].startswith("pgr_")
    assert record["affected_dimensions"] == ["creativity", "curiosity"]
    assert record["confidence"] == 0.8


def test_validate_rejects_invalid_confidence():
    """非法 confidence 被拒绝"""
    record = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="test",
        confidence=1.5,
    )
    assert validate_record(record) is False


def test_validate_rejects_invalid_growth_level():
    """非法 growth_level 被拒绝"""
    record = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="test",
        growth_level="memory",
    )
    assert validate_record(record) is False


def test_validate_rejects_empty_changes():
    """空 changes 被拒绝"""
    record = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={},
        affected_dimensions=["creativity"],
        meaning="test",
    )
    assert validate_record(record) is False


def test_history_dimension_query():
    """按维度查询返回正确记录"""
    history = PersonalityGrowthHistory()
    record1 = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="创造倾向增强",
    )
    record2 = create_personality_growth_record(
        trigger_events=["evt_002"],
        changes={"curiosity": {"before": 0.4, "after": 0.5, "delta": 0.1}},
        affected_dimensions=["curiosity"],
        meaning="好奇心增强",
    )
    history.add(record1)
    history.add(record2)
    assert len(history.get_by_dimension("creativity")) == 1
    assert len(history.get_by_dimension("curiosity")) == 1
    assert len(history.get_by_dimension("warmth")) == 0


def test_history_high_confidence_filter():
    """高置信度筛选正确"""
    history = PersonalityGrowthHistory()
    history.add(create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="test",
        confidence=0.9,
    ))
    history.add(create_personality_growth_record(
        trigger_events=["evt_002"],
        changes={"curiosity": {"before": 0.4, "after": 0.5, "delta": 0.1}},
        affected_dimensions=["curiosity"],
        meaning="test",
        confidence=0.3,
    ))
    high = history.get_high_confidence(0.7)
    assert len(high) == 1
    assert high[0]["affected_dimensions"] == ["creativity"]


def test_growth_meaning_preserved():
    """验证人格成长意义不会丢失"""
    record = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={
            "creativity": {
                "before": 0.5,
                "after": 0.7,
                "delta": 0.2
            }
        },
        affected_dimensions=["creativity"],
        meaning="通过持续创造形成探索倾向",
        narrative="我发现创造让我更愿意探索未知"
    )
    assert record["meaning"] == "通过持续创造形成探索倾向"
    assert "创造" in record["narrative"]
    assert "探索" in record["narrative"]


if __name__ == "__main__":
    test_create_record()
    print("✅ 测试1通过：正常创建记录")
    test_validate_rejects_invalid_confidence()
    print("✅ 测试2通过：非法 confidence 被拒绝")
    test_validate_rejects_invalid_growth_level()
    print("✅ 测试3通过：非法 growth_level 被拒绝")
    test_validate_rejects_empty_changes()
    print("✅ 测试4通过：空 changes 被拒绝")
    test_history_dimension_query()
    print("✅ 测试5通过：按维度查询正确")
    test_history_high_confidence_filter()
    print("✅ 测试6通过：高置信度筛选正确")
    test_growth_meaning_preserved()
    print("✅ 测试7通过：成长意义保存正确")
    print("\n🎉 全部通过")