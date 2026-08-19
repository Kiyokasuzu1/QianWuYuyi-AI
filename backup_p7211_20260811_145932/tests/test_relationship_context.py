"""
Phase 10.7：关系上下文提供器测试
覆盖：空状态、熟悉度、信任度、协作度、沟通风格、关系阶段、模式展示、边界安全、数值不泄露
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.relationship.relationship_context_provider import RelationshipContextProvider
from src.relationship.relationship_state import RelationshipState
from src.relationship.relationship_cognitive_profile import RelationshipCognitiveProfile


def test_empty_state_returns_empty():
    provider = RelationshipContextProvider()
    assert provider.get_context(None) == ""
    assert provider.get_context(RelationshipState()) == ""


def test_high_familiarity_description():
    provider = RelationshipContextProvider()
    state = RelationshipState(familiarity=0.85)
    ctx = provider.get_context(state)
    assert "非常熟悉" in ctx


def test_moderate_trust_description():
    provider = RelationshipContextProvider()
    state = RelationshipState(trust=0.65)
    ctx = provider.get_context(state)
    assert "较高的信任感" in ctx


def test_deep_collaboration_description():
    provider = RelationshipContextProvider()
    state = RelationshipState(collaboration=0.9)
    ctx = provider.get_context(state)
    assert "多次深度合作" in ctx


def test_communication_style_included():
    provider = RelationshipContextProvider()
    state = RelationshipState(familiarity=0.5, communication_style=["技术讨论", "架构设计"])
    ctx = provider.get_context(state)
    assert "技术讨论" in ctx
    assert "架构设计" in ctx


def test_confirmed_patterns_included():
    provider = RelationshipContextProvider()
    state = RelationshipState(familiarity=0.5)
    profile = RelationshipCognitiveProfile(
        confirmed_patterns=["偏好深度架构分析", "长期项目合作"]
    )
    ctx = provider.get_context(state, profile)
    assert "偏好深度架构分析" in ctx
    assert "长期项目合作" in ctx


def test_stage_description():
    provider = RelationshipContextProvider()
    state = RelationshipState(familiarity=0.5, relationship_stage="deep_collaboration")
    ctx = provider.get_context(state)
    assert "深度协作" in ctx


def test_boundary_blocks_unsafe_context():
    """
    当 communication_style 中包含禁止词时，生成的上下文会被边界检查拒绝，
    返回空字符串。
    """
    provider = RelationshipContextProvider()
    # 构造一个包含禁止词的 communication_style
    state = RelationshipState(
        familiarity=0.6,
        communication_style=["你是我的"]  # 触发 possession 禁止词
    )
    ctx = provider.get_context(state)
    # 应该被 boundary 拦截，返回空字符串
    assert ctx == ""


def test_no_numerical_values_in_output():
    """Prompt 中不应暴露任何内部数值"""
    provider = RelationshipContextProvider()
    state = RelationshipState(familiarity=0.75, trust=0.68, collaboration=0.55)
    profile = RelationshipCognitiveProfile(confirmed_patterns=["偏好迭代反馈"])
    ctx = provider.get_context(state, profile)
    assert "0." not in ctx
    assert "0.75" not in ctx
    assert "0.68" not in ctx


if __name__ == "__main__":
    test_empty_state_returns_empty()
    print("✅ 1/9 空状态返回空")
    test_high_familiarity_description()
    print("✅ 2/9 高熟悉度描述")
    test_moderate_trust_description()
    print("✅ 3/9 信任度描述")
    test_deep_collaboration_description()
    print("✅ 4/9 深度协作描述")
    test_communication_style_included()
    print("✅ 5/9 沟通风格展示")
    test_confirmed_patterns_included()
    print("✅ 6/9 已确认模式展示")
    test_stage_description()
    print("✅ 7/9 阶段描述")
    test_boundary_blocks_unsafe_context()
    print("✅ 8/9 边界安全拦截")
    test_no_numerical_values_in_output()
    print("✅ 9/9 数值不泄露")
    print("\n🎉 Phase 10.7 全部通过")