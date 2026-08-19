"""
冲突协调器测试 v1.2
新增：多冲突优先级测试
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.identity_resolver import IdentityResolver
from src.personality.value_system import ValueSystem
from src.personality.behavior_engine import BehaviorEngine
from src.personality.conflict_resolver import ConflictResolver


def test_no_conflict_returns_original():
    resolver = IdentityResolver(ValueSystem())
    snapshot = resolver.resolve()
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)

    cr = ConflictResolver()
    result = cr.resolve(profile)

    assert result.conflict_detected is False
    assert result.chosen_expression == profile.expression_style
    assert result.chosen_directness == profile.directness


def test_truth_vs_warmth_detected():
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", 0.2, "test", "system_init")
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)

    cr = ConflictResolver()
    result = cr.resolve(profile)

    assert result.conflict_detected is True
    assert result.conflict_type == "truth_vs_warmth"
    assert result.chosen_expression == "真诚但温和"
    assert result.chosen_directness == "moderate"


def test_growth_vs_shyness_detected():
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", -0.4, "test", "system_init")  # 降至 0.3
    system.adjust_weight("growth_over_stagnation", 0.2, "test", "system_init")   # 升至 0.9
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "shyness": create_trait_state("shyness", 0.8),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)

    cr = ConflictResolver()
    result = cr.resolve(profile)

    assert result.conflict_detected is True
    assert result.conflict_type == "growth_vs_shyness"
    assert "安全" in result.resolution_reason


def test_result_contains_reason():
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", 0.2, "test", "system_init")
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)

    cr = ConflictResolver()
    result = cr.resolve(profile)

    assert result.resolution_reason != ""
    assert "真实" in result.resolution_reason or "温和" in result.resolution_reason


def test_to_dict_works():
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", 0.2, "test", "system_init")
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)
    cr = ConflictResolver()
    result = cr.resolve(profile)
    d = result.to_dict()
    assert "conflict_type" in d
    assert d["conflict_detected"] is True


def test_multiple_conflicts_priority():
    """多个冲突同时存在时，按优先级选择首要冲突"""
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", 0.2, "test", "system_init")     # 0.9
    system.adjust_weight("growth_over_stagnation", 0.2, "test", "system_init")     # 0.9
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)

    cr = ConflictResolver()
    result = cr.resolve(profile)

    assert result.conflict_detected is True
    assert result.conflict_type == "truth_vs_warmth"  # 优先级最高
    assert len(result.all_conflicts) >= 2
    assert "growth_vs_shyness" in result.all_conflicts


if __name__ == "__main__":
    test_no_conflict_returns_original()
    print("✅ 测试1通过：无冲突时保持原始倾向")
    test_truth_vs_warmth_detected()
    print("✅ 测试2通过：真实优先 + 羞怯温和 → 真诚但温和")
    test_growth_vs_shyness_detected()
    print("✅ 测试3通过：成长驱动 + 表达谨慎 → 安全探索")
    test_result_contains_reason()
    print("✅ 测试4通过：解决结果包含可解释的原因")
    test_to_dict_works()
    print("✅ 测试5通过：to_dict 正常序列化")
    test_multiple_conflicts_priority()
    print("✅ 测试6通过：多冲突按优先级仲裁")
    print("\n🎉 全部通过")