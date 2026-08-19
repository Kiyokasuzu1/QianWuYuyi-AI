"""
自我模型 v2.1.1 构建测试
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_builder import SelfModelBuilder
from src.personality.personality_growth_record import (
    create_personality_growth_record, PersonalityGrowthHistory,
)
from src.personality.trait_state import create_trait_state


def test_identity_core_bound():
    """验证 Identity Core 正确绑定"""
    builder = SelfModelBuilder()
    model = builder.build(PersonalityGrowthHistory(), {})
    assert model["identity_id"] == "qianwu_yuyi_core_v1"
    assert model["identity_name"] == "浅雾羽依"


def test_self_description_has_sources():
    """验证自我描述包含来源和置信度"""
    builder = SelfModelBuilder()
    states = {"creativity": create_trait_state("creativity", 0.75)}
    model = builder.build(PersonalityGrowthHistory(), states)
    desc = model["self_description"]
    assert "text" in desc and "sources" in desc and "confidence" in desc
    assert desc["text"] != ""


def test_growth_narrative_has_event_and_meaning():
    """验证成长叙事包含正确的 event 和 meaning"""
    builder = SelfModelBuilder()
    history = PersonalityGrowthHistory()
    record = create_personality_growth_record(
        trigger_events=["连续一个月练习绘画"],
        changes={"creativity": {"before": 0.5, "after": 0.6, "delta": 0.1}},
        affected_dimensions=["creativity"], meaning="创造倾向增强",
        narrative="我发现创造让我很开心", confidence=0.9, validation_count=5, growth_level="trait",
    )
    record["event"] = "连续一个月练习绘画"
    history.add(record)
    model = builder.build(history, {})
    assert len(model["growth_narratives"]) >= 1
    assert model["growth_narratives"][0]["event"] == "连续一个月练习绘画"
    assert "meaning" in model["growth_narratives"][0]


def test_tension_has_trait_values():
    """验证人格矛盾包含正确的 trait_values"""
    builder = SelfModelBuilder()
    traits = {
        "shyness": create_trait_state("shyness", 0.85),
        "desire_connection": create_trait_state("desire_connection", 0.9),
        "warmth": create_trait_state("warmth", 0.7),
    }
    model = builder.build(PersonalityGrowthHistory(), traits)
    if model["personality_tensions"]:
        tension = model["personality_tensions"][0]
        assert "trait_values" in tension
        assert tension["trait_values"]["shyness"] == 0.85


def test_self_understanding_three_dimensions():
    """验证 SelfUnderstanding 三维输出"""
    builder = SelfModelBuilder()
    su = builder.build(PersonalityGrowthHistory(), {})["self_understanding"]
    for key in ["experience_awareness", "trait_awareness", "identity_continuity", "overall"]:
        assert key in su
    assert 0.0 <= su["overall"] <= 1.0


def test_empty_data_does_not_crash():
    """验证空数据不会崩溃"""
    builder = SelfModelBuilder()
    model = builder.build(PersonalityGrowthHistory(), {})
    assert model is not None and model["current_traits"] == {}
    assert model["growth_narratives"] == []
    assert model["personality_tensions"] == []


if __name__ == "__main__":
    tests = [
        test_identity_core_bound,
        test_self_description_has_sources,
        test_growth_narrative_has_event_and_meaning,
        test_tension_has_trait_values,
        test_self_understanding_three_dimensions,
        test_empty_data_does_not_crash,
    ]
    for i, t in enumerate(tests, 1):
        t()
        print(f"✅ 测试{i}通过：{t.__doc__}")
    print("\n🎉 全部通过")