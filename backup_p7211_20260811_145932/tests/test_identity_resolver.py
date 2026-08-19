"""
身份解析器测试 v1.2
适配结构化人格信号
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.identity_resolver import IdentityResolver
from src.personality.value_system import ValueSystem
from src.personality.self_model_builder import SelfModelBuilder
from src.personality.personality_growth_record import PersonalityGrowthHistory
from src.personality.trait_state import create_trait_state


def test_resolve_empty_state():
    """空状态解析不崩溃，且身份锚点正确"""
    resolver = IdentityResolver(ValueSystem())
    snapshot = resolver.resolve()

    assert snapshot.identity_name == "浅雾羽依"
    assert snapshot.dominant_values is not None
    assert snapshot.current_traits == {}
    assert snapshot.personality_signals is not None
    # 结构化信号验证
    for sig in snapshot.personality_signals:
        assert "id" in sig
        assert "label" in sig
        assert "strength" in sig


def test_resolve_with_traits():
    """传入特质后，快照应包含当前特质值"""
    resolver = IdentityResolver(ValueSystem())
    traits = {
        "creativity": create_trait_state("creativity", 0.82),
        "shyness": create_trait_state("shyness", 0.75),
    }
    snapshot = resolver.resolve(trait_states=traits)
    assert snapshot.current_traits["creativity"] == 0.82
    assert snapshot.current_traits["shyness"] == 0.75


def test_resolve_with_self_model():
    """传入 SelfModel 后，快照应包含自我认知"""
    resolver = IdentityResolver(ValueSystem())
    builder = SelfModelBuilder()
    self_model = builder.build(PersonalityGrowthHistory(), {})
    snapshot = resolver.resolve(self_model=self_model)

    assert snapshot.self_description is not None
    assert "self_understanding" in snapshot.to_dict()


def test_custom_value_system_reflected():
    """自定义 ValueSystem 的调整应在快照中反映"""
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", 0.15, "长期反思", "reflection_engine")
    resolver = IdentityResolver(system)
    snapshot = resolver.resolve()
    assert "truth_over_perfection" in snapshot.dominant_value_ids
    assert "真实比完美更重要" in snapshot.dominant_values


def test_value_state_persistence():
    """验证 ValueSystem 状态不会丢失"""
    system = ValueSystem()
    system.adjust_weight("truth_over_perfection", 0.15, "长期反思", "reflection_engine")
    resolver = IdentityResolver(system)
    snapshot = resolver.resolve()
    assert "truth_over_perfection" in snapshot.dominant_value_ids
    assert len(snapshot.value_profiles) == 5


def test_personality_signals_use_value_id():
    """人格信号应基于 value_id 推断，且为结构化信号"""
    system = ValueSystem()
    system.adjust_weight("creation_over_consumption", 0.1, "test", "system_init")
    resolver = IdentityResolver(system)

    traits = {
        "creativity": create_trait_state("creativity", 0.85),
        "shyness": create_trait_state("shyness", 0.8),
        "warmth": create_trait_state("warmth", 0.7),
    }
    snapshot = resolver.resolve(trait_states=traits)

    # 提取所有信号的 label 列表用于断言
    signal_labels = [s["label"] for s in snapshot.personality_signals]
    assert "创造倾向高" in signal_labels
    assert "羞怯但温和" in signal_labels


def test_short_description():
    """简短描述应包含身份和关键信息"""
    resolver = IdentityResolver(ValueSystem())
    snapshot = resolver.resolve()
    desc = resolver.get_short_description(snapshot)
    assert "浅雾羽依" in desc
    assert "%" in desc


def test_snapshot_to_dict():
    """快照应能正确序列化为字典"""
    resolver = IdentityResolver(ValueSystem())
    snapshot = resolver.resolve()
    d = snapshot.to_dict()
    assert d["schema_version"] == "identity_snapshot_v1"
    assert "personality_signals" in d
    assert "value_profiles" in d


def test_resolver_is_read_only():
    """Resolver 不应修改 ValueSystem 的状态"""
    system = ValueSystem()
    before = system.get_weight("truth_over_perfection")

    resolver = IdentityResolver(system)
    resolver.resolve()

    after = system.get_weight("truth_over_perfection")
    assert before == after


if __name__ == "__main__":
    tests = [
        test_resolve_empty_state,
        test_resolve_with_traits,
        test_resolve_with_self_model,
        test_custom_value_system_reflected,
        test_value_state_persistence,
        test_personality_signals_use_value_id,
        test_short_description,
        test_snapshot_to_dict,
        test_resolver_is_read_only,
    ]
    for t in tests:
        t()
        print(f"✅ {t.__name__} 通过")
    print("\n🎉 全部通过")