"""
信号衰减器测试 v1.0
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.signal_decay import SignalDecay
from datetime import datetime, timedelta


def test_decay_reduces_strength():
    """信号随时间衰减，强度降低"""
    decay = SignalDecay()
    old_date = (datetime.now() - timedelta(days=100)).isoformat()
    signals = [{
        "id": "trait.creativity.high", "label": "创造力高",
        "strength": 0.9, "source": "trait_state", "timestamp": old_date,
    }]
    result = decay.apply(signals)
    assert result[0]["strength"] < 0.9
    assert result[0]["strength"] > 0.1


def test_decay_preserves_original_strength():
    """衰减后保留原始强度记录"""
    decay = SignalDecay()
    old_date = (datetime.now() - timedelta(days=50)).isoformat()
    signals = [{
        "id": "trait.creativity.high", "label": "创造力高",
        "strength": 0.8, "source": "trait_state", "timestamp": old_date,
    }]
    result = decay.apply(signals)
    assert "original_strength" in result[0]
    assert result[0]["original_strength"] == 0.8


def test_fresh_signal_barely_decays():
    """新信号几乎不衰减"""
    decay = SignalDecay()
    fresh_date = datetime.now().isoformat()
    signals = [{
        "id": "trait.creativity.high", "label": "创造力高",
        "strength": 0.9, "source": "trait_state", "timestamp": fresh_date,
    }]
    result = decay.apply(signals)
    assert result[0]["strength"] >= 0.89


def test_different_sources_have_different_rates():
    """不同来源的信号衰减速率不同"""
    decay = SignalDecay()
    old_date = (datetime.now() - timedelta(days=100)).isoformat()
    trait_signal = [{
        "id": "test", "label": "test", "strength": 0.8,
        "source": "trait_state", "timestamp": old_date,
    }]
    tension_signal = [{
        "id": "test", "label": "test", "strength": 0.8,
        "source": "personality_tension", "timestamp": old_date,
    }]
    trait_result = decay.apply(trait_signal)
    tension_result = decay.apply(tension_signal)
    assert trait_result[0]["strength"] > tension_result[0]["strength"]


if __name__ == "__main__":
    test_decay_reduces_strength()
    print("✅ 测试1通过：信号随时间衰减")
    test_decay_preserves_original_strength()
    print("✅ 测试2通过：保留原始强度记录")
    test_fresh_signal_barely_decays()
    print("✅ 测试3通过：新信号几乎不衰减")
    test_different_sources_have_different_rates()
    print("✅ 测试4通过：不同来源衰减速率不同")
    print("\n🎉 全部通过")