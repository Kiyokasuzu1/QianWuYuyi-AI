"""
E-Emotion-5：Response Integration 测试
覆盖：
- 响应策略随情绪状态变化（表达方式/主动程度/语气倾向）
- 策略派生只读（不修改 EmotionState / 不触碰 personality）
- EmotionContext / Provider 携带策略字段
- engine 主链渲染【表达策略】行（有策略追加 / 无策略零变化）
- RuntimeContext 装配时注入 response_strategy（fail-soft）
- 策略文本不泄露数值、None 安全
"""
import sys, os
from types import SimpleNamespace
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_response_strategy import (
    ResponseStrategy,
    EmotionResponseStrategyBuilder,
    response_strategy_prompt_line,
)
from src.emotion.emotion_context import EmotionContext
from src.emotion.emotion_context_provider import EmotionContextProvider


builder = EmotionResponseStrategyBuilder()


def test_strategy_varies_with_state():
    """不同情绪状态应产出不同的响应策略"""
    upbeat = builder.build(EmotionState(valence=0.8, arousal=0.9, energy=0.8))
    down = builder.build(EmotionState(valence=-0.7, arousal=0.2, energy=0.2, sadness=0.6))

    assert upbeat.proactivity == "提高"
    assert down.proactivity == "降低"
    assert upbeat.expression_style != down.expression_style
    assert upbeat.tone_style != down.tone_style


def test_strategy_prompt_text_contains_three_axes():
    """策略文本应覆盖表达方式/主动程度/语气倾向三要素"""
    text = builder.build(EmotionState(valence=0.5)).to_prompt_text()
    assert "表达方式" in text
    assert "主动程度" in text
    assert "语气倾向" in text


def test_builder_does_not_modify_state():
    """策略派生是纯只读映射，不改状态"""
    state = EmotionState(valence=0.6, arousal=0.8, anxiety=0.7)
    before = state.to_dict()
    builder.build(state)
    assert state.to_dict() == before


def test_builder_does_not_modify_personality():
    """策略派生不得触碰人格对象（Personality = who I am，Emotion = how I feel now）"""
    personality = {
        "identity_core": "羽依",
        "values": ["真诚", "陪伴"],
        "temperature": 0.5,
    }
    snapshot = dict(personality)
    builder.build(EmotionState(valence=-0.9, anxiety=0.9))
    assert personality == snapshot


def test_context_provider_carries_strategy():
    """EmotionContextProvider 应在 EmotionContext 上携带策略三要素"""
    ctx = EmotionContextProvider().build(EmotionState(valence=0.8, arousal=0.9))
    assert ctx.expression_style
    assert ctx.proactivity
    assert ctx.tone_style
    text = ctx.to_prompt_text()
    assert "主动程度" in text
    assert "语气倾向" in text


def test_context_serialization_includes_strategy():
    """EmotionContext.to_dict 应包含新策略字段"""
    data = EmotionContext(
        summary="s", mood="calm", expression_style="自然表达",
        proactivity="自然", tone_style="平稳自然",
    ).to_dict()
    assert data["expression_style"] == "自然表达"
    assert data["proactivity"] == "自然"
    assert data["tone_style"] == "平稳自然"


def test_engine_renders_strategy_bullet():
    """engine 主链在提供 response_strategy 时追加【表达策略】行"""
    from src.engine import ResponseEngine
    messages = ResponseEngine()._build_messages_original(
        user_message="你好",
        history=[],
        chat_memories=[],
        life_events=[],
        personality_context="",
        self_model_context={},
        emotion_context={
            "dominant": "开心",
            "intensity": 0.8,
            "response_strategy": "表达方式：偏开朗活跃；主动程度：提高；语气倾向：轻快温暖。",
        },
        relationship_context={},
        context_prompt_blocks=[],
    )
    system_content = messages[0]["content"]
    assert "当前情绪" in system_content
    assert "表达策略" in system_content
    assert "偏开朗活跃" in system_content


def test_engine_without_strategy_unchanged():
    """未提供 response_strategy 时情绪块保持原样（零新增）"""
    from src.engine import ResponseEngine
    messages = ResponseEngine()._build_messages_original(
        user_message="你好",
        history=[],
        chat_memories=[],
        life_events=[],
        personality_context="",
        self_model_context={},
        emotion_context={"dominant": "开心", "intensity": 0.8},
        relationship_context={},
        context_prompt_blocks=[],
    )
    system_content = messages[0]["content"]
    assert "当前情绪" in system_content
    assert "表达策略" not in system_content


def test_runtime_context_injects_response_strategy():
    """RuntimeContext 装配时应从 EmotionState 派生 response_strategy 注入情绪上下文"""
    from src.runtime.runtime_context import RuntimeContext
    fake_manager = SimpleNamespace(state=EmotionState(valence=0.8, arousal=0.9))
    assembled = RuntimeContext().assemble_context(
        user_id="default",
        conversation={"recent_turns": []},
        emotion_manager=fake_manager,
    )
    assert assembled["emotion_context"]["dominant"]
    assert assembled["emotion_context"].get("response_strategy")
    assert "主动程度" in assembled["emotion_context"]["response_strategy"]


def test_runtime_context_strategy_fail_soft():
    """emotion_manager 缺失时装配不受影响（无 response_strategy 键）"""
    from src.runtime.runtime_context import RuntimeContext
    assembled = RuntimeContext().assemble_context(
        user_id="default",
        conversation={"recent_turns": []},
        emotion_manager=None,
    )
    assert assembled["emotion_context"] == {}


def test_strategy_text_has_no_numerical_leak():
    """策略文本不泄露数值（与既有「数值不泄露」约定一致）"""
    state = EmotionState(valence=0.9, anxiety=0.8, energy=0.9)
    text = builder.build(state).to_prompt_text()
    assert "0." not in text
    assert "9" not in text


def test_none_state_safe():
    """None 状态：builder 返回中性默认，prompt_line 返回空串"""
    assert builder.build(None).proactivity == "自然"
    assert response_strategy_prompt_line(None) == ""


if __name__ == "__main__":
    test_strategy_varies_with_state()
    print("✅ 1/12 策略随状态变化")
    test_strategy_prompt_text_contains_three_axes()
    print("✅ 2/12 三要素文本")
    test_builder_does_not_modify_state()
    print("✅ 3/12 只读映射")
    test_builder_does_not_modify_personality()
    print("✅ 4/12 不触碰人格")
    test_context_provider_carries_strategy()
    print("✅ 5/12 Provider 携带策略")
    test_context_serialization_includes_strategy()
    print("✅ 6/12 序列化含策略")
    test_engine_renders_strategy_bullet()
    print("✅ 7/12 engine 渲染表达策略")
    test_engine_without_strategy_unchanged()
    print("✅ 8/12 engine 无策略零变化")
    test_runtime_context_injects_response_strategy()
    print("✅ 9/12 RuntimeContext 注入策略")
    test_runtime_context_strategy_fail_soft()
    print("✅ 10/12 RuntimeContext fail-soft")
    test_strategy_text_has_no_numerical_leak()
    print("✅ 11/12 数值不泄露")
    test_none_state_safe()
    print("✅ 12/12 None 安全")
    print("\n🎉 E-Emotion-5 全部通过")
