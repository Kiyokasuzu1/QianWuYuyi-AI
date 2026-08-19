"""
人格网络与矛盾测试 v1.1
新增：联动不改变数值验证、高稳定性抗联动验证
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.trait_relations import get_relations_for
from src.personality.personality_tension import detect_tensions
from src.personality.personality_evolution import PersonalityEvolutionEngine
from src.personality.trait_state import create_trait_state


def test_creativity_relations_exist():
    relations = get_relations_for("creativity")
    assert "curiosity" in relations
    assert relations["curiosity"]["type"] == "positive"


def test_shyness_negative_relation():
    relations = get_relations_for("shyness")
    assert "initiative" in relations
    assert relations["initiative"]["type"] == "negative"


def test_tension_detected():
    traits = {"shyness": 0.85, "desire_connection": 0.9}
    tensions = detect_tensions(traits)
    assert len(tensions) > 0
    assert tensions[0]["name"] == "social_approach_avoidance"
    assert "dimension_values" in tensions[0]


def test_tension_not_active_when_low():
    traits = {"shyness": 0.2, "desire_connection": 0.8}
    assert len(detect_tensions(traits)) == 0


def test_relation_only_changes_momentum():
    """验证联动不影响 current_value，只影响 momentum"""
    engine = PersonalityEvolutionEngine()
    states = {
        "creativity": create_trait_state("creativity", 0.5),
        "curiosity": create_trait_state("curiosity", 0.5),
    }
    before_value = states["curiosity"]["current_value"]
    engine.apply_relations("creativity", 0.3, states)
    after_value = states["curiosity"]["current_value"]
    assert before_value == after_value, f"current_value 不应改变: {before_value} → {after_value}"


def test_high_stability_reduces_relation_effect():
    """验证高稳定性减弱联动影响"""
    engine = PersonalityEvolutionEngine()

    # 低稳定性目标
    low_stability_state = create_trait_state("curiosity", 0.5)
    low_stability_state["stability"] = 0.2
    # 高稳定性目标
    high_stability_state = create_trait_state("curiosity", 0.5)
    high_stability_state["stability"] = 0.9

    states_low = {"creativity": create_trait_state("creativity", 0.5), "curiosity": low_stability_state}
    states_high = {"creativity": create_trait_state("creativity", 0.5), "curiosity": high_stability_state}

    engine.apply_relations("creativity", 0.3, states_low)
    engine.apply_relations("creativity", 0.3, states_high)

    low_effect = states_low["curiosity"]["momentum"]
    high_effect = states_high["curiosity"]["momentum"]
    assert low_effect > high_effect, f"低稳定性应更易受影响: low={low_effect}, high={high_effect}"


if __name__ == "__main__":
    test_creativity_relations_exist()
    print("✅ 测试1通过：创造力联动关系存在")
    test_shyness_negative_relation()
    print("✅ 测试2通过：羞怯反向联动存在")
    test_tension_detected()
    print("✅ 测试3通过：高冲突值激活矛盾")
    test_tension_not_active_when_low()
    print("✅ 测试4通过：低值不激活矛盾")
    test_relation_only_changes_momentum()
    print("✅ 测试5通过：联动不影响 current_value")
    test_high_stability_reduces_relation_effect()
    print("✅ 测试6通过：高稳定性减弱联动影响")
    print("\n🎉 全部通过")