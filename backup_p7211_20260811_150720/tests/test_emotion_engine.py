"""
Phase 9.0B：EmotionEngine 测试
覆盖：正向事件、负向事件、强度缩放、边界保护、闭环验证、未知事件、source 传递
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_engine import EmotionEngine
from src.emotion.emotion_state import EmotionState


def test_praise_increases_valence():
    """被夸奖 → valence + confidence 提升"""
    engine = EmotionEngine()
    event = EmotionEvent(event_type="user_praise", intensity=1.0)
    delta = engine.process(event)

    assert delta.valence > 0
    assert delta.confidence > 0


def test_conflict_increases_anxiety():
    """冲突事件 → anxiety 提升，valence 下降"""
    engine = EmotionEngine()
    event = EmotionEvent(event_type="user_conflict", intensity=1.0)
    delta = engine.process(event)

    assert delta.valence < 0
    assert delta.anxiety > 0


def test_intensity_scales_delta():
    """强度缩放：高强度事件产生更大的变化"""
    engine = EmotionEngine()
    low = engine.process(EmotionEvent("user_praise", intensity=0.2))
    high = engine.process(EmotionEvent("user_praise", intensity=1.0))

    assert abs(high.valence) > abs(low.valence)


def test_intensity_clamp():
    """intensity 自动限制在 0~1"""
    event = EmotionEvent("user_praise", intensity=5.0)
    assert event.intensity == 1.0

    event2 = EmotionEvent("user_praise", intensity=-0.5)
    assert event2.intensity == 0.0


def test_event_changes_state():
    """闭环验证：事件 → Delta → State 变化"""
    state = EmotionState(valence=0.3, confidence=0.5)
    engine = EmotionEngine()
    event = EmotionEvent("achievement", intensity=1.0)

    delta = engine.process(event)
    new_state = state.apply_delta(delta)

    assert new_state.valence > state.valence
    assert new_state.confidence > state.confidence
    # 原状态不变
    assert state.valence == 0.3


def test_unknown_event_returns_neutral():
    """未知事件类型返回空 Delta"""
    engine = EmotionEngine()
    event = EmotionEvent("unknown_event", intensity=1.0)
    delta = engine.process(event)

    assert delta.valence == 0.0
    assert delta.arousal == 0.0
    assert delta.confidence == 0.0


def test_source_is_preserved():
    """source 字段正确保存"""
    event = EmotionEvent("user_praise", source="user")
    assert event.source == "user"


if __name__ == "__main__":
    test_praise_increases_valence()
    print("✅ 1/7 夸奖提升正向情绪")
    test_conflict_increases_anxiety()
    print("✅ 2/7 冲突提升焦虑")
    test_intensity_scales_delta()
    print("✅ 3/7 强度缩放生效")
    test_intensity_clamp()
    print("✅ 4/7 intensity 自动限制")
    test_event_changes_state()
    print("✅ 5/7 闭环验证：事件→Delta→State")
    test_unknown_event_returns_neutral()
    print("✅ 6/7 未知事件安全")
    test_source_is_preserved()
    print("✅ 7/7 source 字段传递")
    print("\n🎉 Phase 9.0B 全部通过")