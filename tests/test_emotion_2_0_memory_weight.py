"""
E-Emotion-6 测试 4/5：情绪-记忆权重桥（EmotionMemoryWeightBridge）
覆盖任务书验收项：memory 权重影响
- 普通情绪 → 权重 0（importance 不变化）
- 高情绪 → importance 加权（clamp 0~1、上限封顶）
- 只修改 importance/emotional_tags/retrieval_score，历史记忆内容永不动
- 不原地改写调用方持有的记忆对象
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_memory_weight import (
    EmotionMemoryWeightBridge,
    HIGH_EMOTION_THRESHOLD,
    MAX_EMOTION_WEIGHT,
    MUTABLE_FIELDS,
)


bridge = EmotionMemoryWeightBridge()


def test_low_emotion_weight_zero():
    """普通事件 importance += 0（情绪未达门槛）"""
    state = EmotionState(valence=0.1, arousal=0.3)  # intensity ≈ 0.18
    assert bridge.compute_weight(state) == 0.0
    memory = {"content": "历史消息", "importance": 0.5}
    updated = bridge.apply_to_memory(memory, state)
    assert updated["importance"] == 0.5
    assert "emotional_tags" not in updated


def test_high_emotion_adds_weight():
    """高情绪事件 importance += emotion_weight（0 < w <= 上限）"""
    state = EmotionState(valence=0.9, arousal=0.9)  # intensity ≈ 0.9
    weight = bridge.compute_weight(state)
    assert 0.0 < weight <= MAX_EMOTION_WEIGHT
    memory = {"content": "高情绪时刻", "importance": 0.4}
    updated = bridge.apply_to_memory(memory, state)
    assert updated["importance"] == round(0.4 + weight, 4)


def test_importance_clamped_to_one():
    """importance 上限钳制到 1.0"""
    state = EmotionState(valence=0.9, arousal=0.9)
    updated = bridge.apply_to_memory({"importance": 0.95}, state)
    assert updated["importance"] <= 1.0


def test_weight_capped_by_max():
    """权重不超过 MAX_EMOTION_WEIGHT 封顶"""
    state = EmotionState(valence=1.0, arousal=1.0)
    assert bridge.compute_weight(state) <= MAX_EMOTION_WEIGHT
    assert bridge.threshold == HIGH_EMOTION_THRESHOLD


def test_memory_content_never_modified():
    """历史记忆内容字段永不动（content/text/role）——E-4 红线"""
    memory = {
        "content": "用户说过的重要的事",
        "text": "原文",
        "role": "user",
        "timestamp": "2026-01-01T00:00:00",
        "importance": 0.4,
    }
    state = EmotionState(valence=0.9, arousal=0.9)
    updated = bridge.apply_to_memory(memory, state)
    assert updated["content"] == "用户说过的重要的事"
    assert updated["text"] == "原文"
    assert updated["role"] == "user"
    assert updated["timestamp"] == "2026-01-01T00:00:00"
    # 仅允许字段发生变化
    changed = bridge.diff_fields(memory, updated)
    assert set(changed) <= MUTABLE_FIELDS


def test_emotional_tags_appended_dedup():
    """主导情绪标签追加且去重"""
    state = EmotionState(valence=0.9, arousal=0.9)  # dominant=joyful → joy
    memory = {"content": "x", "emotional_tags": ["joy"]}
    updated = bridge.apply_to_memory(memory, state)
    assert updated["emotional_tags"] == ["joy"]  # 不重复
    fresh = bridge.apply_to_memory({"content": "y"}, state)
    assert "joy" in fresh.get("emotional_tags", [])


def test_original_memory_not_mutated():
    """不原地改写调用方持有的记忆对象"""
    memory = {"content": "c", "importance": 0.5}
    snapshot = dict(memory)
    bridge.apply_to_memory(memory, EmotionState(valence=0.9, arousal=0.9))
    assert memory == snapshot


def test_batch_apply():
    """批量应用：每条独立输出新 dict"""
    memories = [
        {"content": "a", "importance": 0.3},
        {"content": "b", "importance": 0.5},
    ]
    updated = bridge.apply_to_memories(memories, EmotionState(valence=0.9, arousal=0.9))
    assert len(updated) == 2
    assert updated[0]["importance"] > 0.3
    assert memories[0]["importance"] == 0.3


def test_none_state_safe():
    """None 状态安全：权重 0、原样返回"""
    memory = {"content": "c", "importance": 0.5}
    assert bridge.compute_weight(None) == 0.0
    assert bridge.apply_to_memory(memory, None)["importance"] == 0.5


if __name__ == "__main__":
    test_low_emotion_weight_zero(); print("✅ 1/9 普通情绪零权重")
    test_high_emotion_adds_weight(); print("✅ 2/9 高情绪加权")
    test_importance_clamped_to_one(); print("✅ 3/9 importance 钳制")
    test_weight_capped_by_max(); print("✅ 4/9 权重封顶")
    test_memory_content_never_modified(); print("✅ 5/9 内容永不动")
    test_emotional_tags_appended_dedup(); print("✅ 6/9 标签去重追加")
    test_original_memory_not_mutated(); print("✅ 7/9 不原地改写")
    test_batch_apply(); print("✅ 8/9 批量应用")
    test_none_state_safe(); print("✅ 9/9 None 安全")
    print("\n🎉 E-Emotion-6-4 Memory Weight 全部通过")
