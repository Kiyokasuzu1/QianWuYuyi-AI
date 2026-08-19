"""
Phase 9.1：EmotionContextProvider 测试
覆盖正向/负向/中性/空状态/数值不泄露/只读/维度差异/序列化
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_context_provider import EmotionContextProvider


provider = EmotionContextProvider()


def test_positive_high_arousal():
    """正向高激活：应体现外向倾向"""
    state = EmotionState(valence=0.8, arousal=0.9)
    ctx = provider.build(state)
    assert ctx.mood == "joyful"
    assert any("外向" in t for t in ctx.expression_tendencies)


def test_negative_low_arousal():
    """负向低激活：应体现活跃度降低"""
    state = EmotionState(valence=-0.7, arousal=0.2)
    ctx = provider.build(state)
    assert ctx.mood == "flat"
    assert any("活跃度可能降低" in t for t in ctx.expression_tendencies)


def test_neutral_state():
    """中性状态：应返回平稳描述"""
    state = EmotionState(valence=0.0, arousal=0.5)
    ctx = provider.build(state)
    assert ctx.mood == "neutral"
    assert any("自然表达" in t for t in ctx.expression_tendencies)


def test_empty_state_returns_empty_context():
    """空状态应返回空的 EmotionContext"""
    ctx = provider.build(None)
    assert ctx.summary == ""
    assert ctx.mood == "neutral"
    assert ctx.expression_tendencies == []


def test_no_numerical_values_in_output():
    """输出中不应包含数值"""
    state = EmotionState(valence=0.9, anxiety=0.8)
    ctx = provider.build(state)
    assert "0.9" not in ctx.summary
    assert "0.8" not in ctx.summary
    assert "0." not in ctx.summary


def test_provider_does_not_modify_state():
    """Provider 不应修改原始的 EmotionState"""
    state = EmotionState(valence=0.5, anxiety=0.7)
    original_valence = state.valence
    original_anxiety = state.anxiety
    provider.build(state)
    assert state.valence == original_valence
    assert state.anxiety == original_anxiety


def test_anxiety_influences_tendencies():
    """高焦虑应产生谨慎倾向"""
    state = EmotionState(anxiety=0.8)
    ctx = provider.build(state)
    assert any("谨慎" in t for t in ctx.expression_tendencies)


def test_context_serialization():
    """EmotionContext 应支持序列化"""
    from src.emotion.emotion_context import EmotionContext
    ctx = EmotionContext(
        summary="测试摘要。",
        mood="joyful",
        expression_tendencies=["倾向A", "倾向B"]
    )
    data = ctx.to_dict()
    assert data["summary"] == "测试摘要。"
    assert data["mood"] == "joyful"
    assert data["expression_tendencies"] == ["倾向A", "倾向B"]


if __name__ == "__main__":
    test_positive_high_arousal()
    print("✅ 1/8 正向高激活状态")
    test_negative_low_arousal()
    print("✅ 2/8 负向低激活状态")
    test_neutral_state()
    print("✅ 3/8 中性状态")
    test_empty_state_returns_empty_context()
    print("✅ 4/8 空状态安全")
    test_no_numerical_values_in_output()
    print("✅ 5/8 数值不泄露")
    test_provider_does_not_modify_state()
    print("✅ 6/8 Provider 不修改原状态")
    test_anxiety_influences_tendencies()
    print("✅ 7/8 焦虑影响表达倾向")
    test_context_serialization()
    print("✅ 8/8 序列化支持")
    print("\n🎉 Phase 9.1 全部通过")