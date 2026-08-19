"""
人格演化引擎单元测试 v1.3
修复：置信度测试对齐初始方向逻辑
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.personality_evolution import PersonalityEvolutionEngine
from src.personality.trait_state import create_trait_state
from src.personality.personality_history import PersonalityHistory


def test_single_growth_limited():
    """单次成长不会导致人格暴涨"""
    engine = PersonalityEvolutionEngine()
    state = create_trait_state("creativity", 0.5)
    updated = engine.update_trait(state, 0.3)
    change = updated["current_value"] - 0.5
    assert change < 0.1, f"单次变化不应过大: {change}"
    assert change > 0.0
    assert updated["direction"] == "increase"


def test_momentum_accumulates_with_same_direction():
    """连续同方向变化增强动量"""
    engine = PersonalityEvolutionEngine()
    state = create_trait_state("creativity", 0.5)
    for _ in range(5):
        state = engine.update_trait(state, 0.01)
    assert state["momentum"] >= 0.5, f"动量应增长: {state['momentum']}"
    assert state["direction"] == "increase"


def test_momentum_resets_on_reversal():
    """反向变化重置动量"""
    engine = PersonalityEvolutionEngine()
    state = create_trait_state("creativity", 0.5)
    for _ in range(5):
        state = engine.update_trait(state, 0.01)
    assert state["momentum"] >= 0.5
    state = engine.update_trait(state, -0.01)
    assert state["momentum"] == 0.1
    assert state["direction"] == "decrease"


def test_stability_increases_with_history():
    """稳定性随验证次数提高"""
    engine = PersonalityEvolutionEngine()
    history = PersonalityHistory()
    state = create_trait_state("creativity", 0.5)
    for i in range(10):
        before = {"creativity": state["current_value"]}
        state = engine.update_trait(state, 0.1, history)
        after = {"creativity": state["current_value"]}
        history.record_change(before, after, reason=f"test_{i}")
    assert state["stability"] >= 0.75, f"稳定性应提高: {state['stability']}"


def test_confidence_changes_correctly():
    """置信度随方向变化（首次从 stable 变 increase 保持 0.1）"""
    engine = PersonalityEvolutionEngine()
    state = create_trait_state("creativity", 0.5)
    # 初始 confidence = 0.1，方向 stable
    assert state["confidence"] == 0.1

    # 第一次：stable → increase，方向不同，保持 0.1
    state = engine.update_trait(state, 0.01)
    assert state["confidence"] == 0.1, f"首次方向变化应保持 0.1，实际 {state['confidence']}"

    # 第二次：increase → increase，同方向，+0.05 → 0.15
    state = engine.update_trait(state, 0.01)
    assert state["confidence"] == 0.15, f"第二次同方向应为 0.15，实际 {state['confidence']}"

    # 第三次：increase → increase，同方向，+0.05 → 0.2
    state = engine.update_trait(state, 0.01)
    assert state["confidence"] == 0.2, f"第三次同方向应为 0.2，实际 {state['confidence']}"

    # 第四次：increase → decrease，反向，-0.1 → 0.1
    state = engine.update_trait(state, -0.01)
    assert state["confidence"] == 0.1, f"反向应重置为 0.1，实际 {state['confidence']}"


def test_momentum_decays_when_stable():
    """无变化时动量缓慢衰减"""
    engine = PersonalityEvolutionEngine()
    state = create_trait_state("creativity", 0.5)
    state["momentum"] = 0.5
    state["direction"] = "increase"
    for _ in range(10):
        state = engine.update_trait(state, 0.0)
    assert state["momentum"] < 0.3, f"动量应衰减: {state['momentum']}"
    assert state["direction"] == "stable"


if __name__ == "__main__":
    test_single_growth_limited()
    print("✅ 测试1通过：单次变化幅度受控")
    test_momentum_accumulates_with_same_direction()
    print("✅ 测试2通过：连续同方向增强动量")
    test_momentum_resets_on_reversal()
    print("✅ 测试3通过：反向变化重置动量")
    test_stability_increases_with_history()
    print("✅ 测试4通过：稳定性随验证次数提高")
    test_confidence_changes_correctly()
    print("✅ 测试5通过：置信度变化正确")
    test_momentum_decays_when_stable()
    print("✅ 测试6通过：无变化时动量衰减")
    print("\n🎉 全部通过")