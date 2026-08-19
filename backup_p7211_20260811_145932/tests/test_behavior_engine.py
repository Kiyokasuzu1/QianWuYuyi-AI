"""
行为引擎测试 v1.1
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.identity_resolver import IdentityResolver
from src.personality.value_system import ValueSystem
from src.personality.behavior_engine import BehaviorEngine


def test_engine_runs_with_snapshot():
    resolver = IdentityResolver(ValueSystem())
    snapshot = resolver.resolve()
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)
    assert profile.expression_style is not None
    assert len(profile.behavior_signals) > 0
    assert profile.confidence > 0


def test_shy_warm_profile():
    system = ValueSystem()
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "creativity": create_trait_state("creativity", 0.85),
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
        "curiosity": create_trait_state("curiosity", 0.75),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)
    assert profile.expression_style in ("谨慎但温暖", "温和而富有创造力")
    assert profile.warmth_level in ("medium", "high")


def test_sensitivity_notes_generated():
    system = ValueSystem()
    resolver = IdentityResolver(system)
    from src.personality.trait_state import create_trait_state
    traits = {
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
    }
    snapshot = resolver.resolve(trait_states=traits)
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)
    assert len(profile.sensitivity_notes) > 0


def test_structured_signals_have_ids():
    resolver = IdentityResolver(ValueSystem())
    snapshot = resolver.resolve()
    engine = BehaviorEngine()
    profile = engine.analyze(snapshot)
    for sig in profile.behavior_signals:
        assert "id" in sig
        assert "label" in sig
        assert "strength" in sig
        assert "source" in sig


def test_version_check_raises():
    engine = BehaviorEngine()
    class FakeSnapshot:
        schema_version = "old_version"
        personality_signals = []
        current_traits = {}
        active_tensions = []
        active_conflicts = []
    try:
        engine.analyze(FakeSnapshot())
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


if __name__ == "__main__":
    test_engine_runs_with_snapshot()
    print("✅ 测试1通过：引擎正常运转")
    test_shy_warm_profile()
    print("✅ 测试2通过：羞怯但温和画像正确")
    test_sensitivity_notes_generated()
    print("✅ 测试3通过：敏感度提示正确生成")
    test_structured_signals_have_ids()
    print("✅ 测试4通过：行为信号结构化")
    test_version_check_raises()
    print("✅ 测试5通过：版本检查正常拦截")
    print("\n🎉 全部通过")