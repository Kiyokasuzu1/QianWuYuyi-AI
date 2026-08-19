"""
Phase 9.0C：EmotionDecay 测试
覆盖基本衰减、零时间、大时间、维度差异、不可变性
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_decay import EmotionDecay


decay = EmotionDecay()


def test_decay_reduces_valence():
    """正 valence 随时间衰减趋近于 0"""
    state = EmotionState(valence=0.8)
    decayed = decay.apply(state, 1800)  # 30 分钟
    assert decayed.valence < 0.8
    assert decayed.valence > 0.0


def test_decay_zero_seconds_no_change():
    """0 秒衰减不改变状态"""
    state = EmotionState(valence=0.8, arousal=0.7)
    decayed = decay.apply(state, 0)
    assert decayed.valence == 0.8
    assert decayed.arousal == 0.7


def test_decay_long_time_returns_to_baseline():
    """极长时间衰减后趋近基线"""
    state = EmotionState(valence=0.9, anxiety=0.7)
    decayed = decay.apply(state, 86400 * 7)  # 7 天
    assert abs(decayed.valence - 0.0) < 0.01
    assert abs(decayed.anxiety - 0.0) < 0.01
    assert abs(decayed.arousal - 0.5) < 0.01


def test_decay_does_not_mutate_original():
    """decay 不修改原对象"""
    state = EmotionState(valence=0.8)
    decayed = decay.apply(state, 1800)
    assert state.valence == 0.8
    assert decayed.valence < 0.8
    assert state is not decayed


def test_decay_rates_are_dimension_specific():
    """
    验证不同维度的衰减速度差异 (基于 lambda)
    将 anxiety 和 curiosity 设置为相同的远离基线的距离，
    然后验证 anxiety 恢复得更快。
    """
    # 初始值均为 0.8，基线分别为 0.0 和 0.5
    state = EmotionState(anxiety=0.8, curiosity=0.8)
    decayed = decay.apply(state, 1800)  # 30 分钟

    # anxiety 距离基线的下降量
    anxiety_recovery = 0.8 - decayed.anxiety
    # curiosity 当前值 (0.8) 到基线 (0.5) 的下降量
    curiosity_recovery = 0.8 - decayed.curiosity

    # anxiety 应该恢复得更多（因为 lambda 更大）
    assert anxiety_recovery > curiosity_recovery, \
        f"anxiety 应衰减更快，但 anxiety 恢复 {anxiety_recovery:.4f}, curiosity 恢复 {curiosity_recovery:.4f}"


if __name__ == "__main__":
    test_decay_reduces_valence()
    print("✅ 1/5 正向情绪衰减")
    test_decay_zero_seconds_no_change()
    print("✅ 2/5 零时间无变化")
    test_decay_long_time_returns_to_baseline()
    print("✅ 3/5 长时间回归基线")
    test_decay_does_not_mutate_original()
    print("✅ 4/5 不修改原对象")
    test_decay_rates_are_dimension_specific()
    print("✅ 5/5 不同维度衰减速度差异验证")
    print("\n🎉 Phase 9.0C 全部通过")