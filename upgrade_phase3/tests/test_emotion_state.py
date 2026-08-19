"""
Phase 9.0A：EmotionState 测试
覆盖默认创建、边界保护、delta 应用、序列化、空数据防御
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest
from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_delta import EmotionDelta


def test_default_state():
    """默认状态为中性"""
    state = EmotionState()
    assert state.valence == 0.0
    assert state.arousal == 0.5
    assert state.anxiety == 0.0
    assert state.updated_at is not None and state.updated_at != ""


def test_boundary_clamp_on_creation():
    """创建时自动限制边界"""
    state = EmotionState(valence=2.0, anxiety=-0.5, confidence=3.0)
    assert state.valence == 1.0
    assert state.anxiety == 0.0
    assert state.confidence == 1.0


def test_apply_delta():
    """delta 应用后状态正确变化，包括负向情绪降低"""
    state = EmotionState(valence=0.3, confidence=0.5, anxiety=0.4)
    delta = EmotionDelta(valence=0.2, confidence=0.1, anxiety=-0.1)
    new_state = state.apply_delta(delta)

    assert new_state.valence == 0.5
    assert new_state.confidence == 0.6
    # 焦虑应从 0.4 降低到 0.3，浮点比较使用近似值
    assert new_state.anxiety == pytest.approx(0.3, abs=1e-6)
    # 原状态不变
    assert state.valence == 0.3
    assert state.anxiety == 0.4


def test_apply_delta_clamp():
    """delta 应用时边界保护"""
    state = EmotionState(valence=0.9)
    delta = EmotionDelta(valence=0.5)
    new_state = state.apply_delta(delta)
    assert new_state.valence == 1.0


def test_from_empty_dict():
    """空字典应返回安全的默认状态"""
    state = EmotionState.from_dict({})
    assert state.valence == 0.0
    assert state.arousal == 0.5
    assert state.energy == 0.5
    assert state.anxiety == 0.0
    # from_dict 传入空 updated_at 应保留空字符串
    assert state.updated_at == ""


def test_serialization_roundtrip():
    """序列化/反序列化一致性"""
    original = EmotionState(
        valence=0.4,
        arousal=0.6,
        curiosity=0.7,
        anxiety=0.1,
        confidence=0.8,
        energy=0.5,
        updated_at="2026-08-01T00:00:00"
    )
    data = original.to_dict()
    restored = EmotionState.from_dict(data)

    assert restored.valence == original.valence
    assert restored.arousal == original.arousal
    assert restored.curiosity == original.curiosity
    assert restored.anxiety == original.anxiety
    assert restored.confidence == original.confidence
    assert restored.energy == original.energy
    assert restored.updated_at == original.updated_at


def test_delta_does_not_mutate_original():
    """apply_delta 不修改原对象"""
    state = EmotionState(valence=0.5)
    delta = EmotionDelta(valence=0.3)
    new_state = state.apply_delta(delta)

    assert state.valence == 0.5
    assert new_state.valence == 0.8
    assert state is not new_state


if __name__ == "__main__":
    test_default_state()
    print("✅ 1/7 默认状态正常")
    test_boundary_clamp_on_creation()
    print("✅ 2/7 边界保护（创建时）")
    test_apply_delta()
    print("✅ 3/7 Delta 应用正确（含负向情绪降低）")
    test_apply_delta_clamp()
    print("✅ 4/7 Delta 应用边界保护")
    test_from_empty_dict()
    print("✅ 5/7 空字典防御")
    test_serialization_roundtrip()
    print("✅ 6/7 序列化一致性")
    test_delta_does_not_mutate_original()
    print("✅ 7/7 Delta 不修改原对象")
    print("\n🎉 Phase 9.0A 全部通过")