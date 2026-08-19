"""
自我模型存储测试 v1.3
修正：test_growth_changes_identity 同时验证 identity_summary
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_store import SelfModelStore
from src.personality.personality_growth_record import (
    create_personality_growth_record,
    PersonalityGrowthHistory,
)


def test_first_update_always_true():
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    assert store.should_update(history) is True


def test_update_returns_model():
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    model = store.update(history, {})
    assert model is not None
    assert "identity_summary" in model
    assert model["identity_summary"] != ""
    assert "喜欢探索" in model["identity_summary"]


def test_no_update_when_no_new_records():
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    store.update(history, {})
    assert store.should_update(history) is False


def test_update_after_new_growth():
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    store.update(history, {})
    history.add(create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"],
        meaning="创造倾向增强",
        confidence=0.9,
        validation_count=5,
        growth_level="trait",
    ))
    assert store.should_update(history) is True


def test_growth_changes_identity():
    """成长记录应同时影响 stable_traits 和 identity_summary"""
    store = SelfModelStore()
    history = PersonalityGrowthHistory()
    history.add(create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={"creativity": {"before": 0.5, "after": 0.8, "delta": 0.3}},
        affected_dimensions=["creativity"],
        meaning="创造倾向增强",
        narrative="我越来越喜欢创造新的表达方式",
        confidence=0.9,
        validation_count=5,
        growth_level="trait",
    ))
    model = store.update(history, {})
    assert "创造倾向增强" in model["stable_traits"]
    assert "创造倾向增强" in model["identity_summary"]


if __name__ == "__main__":
    test_first_update_always_true()
    print("✅ 测试1通过：首次更新返回 True")
    test_update_returns_model()
    print("✅ 测试2通过：更新返回 SelfModel 且内容非空")
    test_no_update_when_no_new_records()
    print("✅ 测试3通过：无新记录不触发更新")
    test_update_after_new_growth()
    print("✅ 测试4通过：新记录触发更新")
    test_growth_changes_identity()
    print("✅ 测试5通过：成长记录同时影响 stable_traits 和 identity_summary")
    print("\n🎉 全部通过")