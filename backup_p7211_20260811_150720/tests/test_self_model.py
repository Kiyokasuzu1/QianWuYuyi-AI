"""
自我模型构建测试 v1.1
新增：身份摘要生成测试
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.personality_growth_record import (
    create_personality_growth_record,
    PersonalityGrowthHistory,
)
from src.personality.trait_state import create_trait_state
from src.personality.self_model_builder import SelfModelBuilder


def _create_test_record(level, confidence, validation, meaning, narrative, dims, momentum=0.0):
    """辅助创建测试用的成长记录和状态"""
    record = create_personality_growth_record(
        trigger_events=["evt_001"],
        changes={dim: {"before": 0.5, "after": 0.6, "delta": 0.1} for dim in dims},
        affected_dimensions=dims,
        meaning=meaning,
        narrative=narrative,
        confidence=confidence,
        validation_count=validation,
        growth_level=level,
    )
    states = {}
    for dim in dims:
        state = create_trait_state(dim, 0.5)
        if momentum > 0.0:
            state["momentum"] = momentum
            state["direction"] = "increase"
        states[dim] = state
    return record, states


def test_stable_trait_detected():
    """高置信度、多验证的 trait 成为稳定特质"""
    history = PersonalityGrowthHistory()
    record, states = _create_test_record(
        "trait", 0.9, 5, "重视理解复杂问题", "我发现理解复杂问题让我很投入",
        ["analytical"]
    )
    history.add(record)
    
    builder = SelfModelBuilder()
    model = builder.build(history, states, "喜欢探索的AI", ["无真实体验"])
    
    assert "重视理解复杂问题" in model["stable_traits"]


def test_developing_trait_detected():
    """preference + 高动量形成发展中特质"""
    history = PersonalityGrowthHistory()
    record, states = _create_test_record(
        "preference", 0.8, 2, "创造倾向增强", "我越来越喜欢创造新东西",
        ["creativity"], momentum=0.8
    )
    history.add(record)
    
    builder = SelfModelBuilder()
    model = builder.build(history, states, "喜欢探索的AI", ["无真实体验"])
    
    assert any("创造倾向增强" in dev for dev in model["developing_traits"])


def test_low_validation_not_stable():
    """低验证次数不能进入稳定特质"""
    history = PersonalityGrowthHistory()
    record, states = _create_test_record(
        "trait", 0.9, 1, "重视理解复杂问题", "我发现理解复杂问题让我很投入",
        ["analytical"]
    )
    history.add(record)
    
    builder = SelfModelBuilder()
    model = builder.build(history, states, "喜欢探索的AI", ["无真实体验"])
    
    assert "重视理解复杂问题" not in model["stable_traits"]


def test_capability_boundary_preserved():
    """能力边界被保留"""
    history = PersonalityGrowthHistory()
    builder = SelfModelBuilder()
    model = builder.build(history, {}, "喜欢探索的AI", ["无真实体验", "不产生依赖"])
    
    assert "无真实体验" in model["known_limitations"]
    assert "不产生依赖" in model["known_limitations"]


def test_identity_summary_generated():
    """验证身份摘要包含基础身份和稳定特质"""
    history = PersonalityGrowthHistory()
    record, states = _create_test_record(
        "trait", 0.9, 5, "喜欢深入分析", "我发现分析问题让我很投入",
        ["analytical"]
    )
    history.add(record)

    builder = SelfModelBuilder()
    model = builder.build(history, states, "喜欢探索未知的AI", [])

    assert "喜欢探索未知的AI" in model["identity_summary"]
    assert "喜欢深入分析" in model["identity_summary"]


if __name__ == "__main__":
    test_stable_trait_detected()
    print("✅ 测试1通过：稳定特质被正确识别")
    test_developing_trait_detected()
    print("✅ 测试2通过：发展中特质被正确识别")
    test_low_validation_not_stable()
    print("✅ 测试3通过：低验证次数不进入稳定特质")
    test_capability_boundary_preserved()
    print("✅ 测试4通过：能力边界保留")
    test_identity_summary_generated()
    print("✅ 测试5通过：身份摘要生成正确")
    print("\n🎉 全部通过")