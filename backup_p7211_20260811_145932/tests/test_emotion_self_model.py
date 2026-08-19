"""
Phase 9.6：EmotionSelfModelBridge + SelfModelV3 情绪认知接入测试
覆盖：接入、去重、更新、证据合并、普通信念隔离、序列化、Prompt 输出、数量限制
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.personality.self_model_v3 import SelfModelV3
from src.emotion.emotion_belief import EmotionBelief
from src.emotion.emotion_self_model_bridge import EmotionSelfModelBridge


def make_belief(event_type="user_praise", emotion="joy", confidence=0.8, stability=0.9,
                evidence_ids=None, occurrence_count=5):
    """构造测试用的情绪信念"""
    return EmotionBelief(
        content=f"我发现自己在{'被认可' if event_type == 'user_praise' else event_type}时，通常更容易产生积极情绪",
        emotion=emotion,
        event_type=event_type,
        confidence=confidence,
        stability=stability,
        evidence_trace_ids=evidence_ids or ["t1", "t2", "t3"],
        occurrence_count=occurrence_count,
    )


def test_merge_new_belief():
    """首次接入：空 SelfModel 接入 1 条信念"""
    model = SelfModelV3()
    bridge = EmotionSelfModelBridge()
    bridge.merge(model, [make_belief()])
    assert len(model.emotional_self_understanding) == 1


def test_merge_updates_existing():
    """同 event_type+emotion 再次接入 → 更新置信度和证据，不新增条目"""
    model = SelfModelV3()
    bridge = EmotionSelfModelBridge(confidence_history_weight=0.7)

    # 第一次
    bridge.merge(model, [make_belief(confidence=0.7, evidence_ids=["t1", "t2"])])
    # 第二次
    bridge.merge(model, [make_belief(confidence=0.9, evidence_ids=["t2", "t3"])])

    assert len(model.emotional_self_understanding) == 1
    b = model.emotional_self_understanding[0]
    # 置信度应为加权平均：0.7*0.7 + 0.3*0.9 = 0.76
    assert abs(b.confidence - 0.76) < 0.01
    # 证据链应合并去重
    assert set(b.evidence_trace_ids) == {"t1", "t2", "t3"}


def test_merge_preserves_existing_beliefs():
    """普通 beliefs 不受影响"""
    model = SelfModelV3(beliefs=["我重视探索"])
    bridge = EmotionSelfModelBridge()
    bridge.merge(model, [make_belief()])
    assert "我重视探索" in model.beliefs
    assert len(model.emotional_self_understanding) == 1


def test_empty_beliefs_safe():
    """空列表不崩溃"""
    model = SelfModelV3()
    bridge = EmotionSelfModelBridge()
    bridge.merge(model, [])
    assert model.emotional_self_understanding == []


def test_to_prompt_includes_emotional_understanding():
    """to_prompt_context 输出包含情绪自我认知"""
    model = SelfModelV3(identity="浅雾羽依")
    bridge = EmotionSelfModelBridge()
    bridge.merge(model, [make_belief()])

    ctx = model.to_prompt_context()
    assert "我发现自己通常" in ctx
    assert "被认可" in ctx


def test_prompt_limits_emotion_beliefs():
    """情绪信念数量过多时，输出应限制条数"""
    model = SelfModelV3()
    bridge = EmotionSelfModelBridge()
    # 合并多条不同信念
    for i in range(10):
        bridge.merge(model, [
            make_belief(event_type=f"event_{i}", emotion=f"emotion_{i}",
                        confidence=0.5 + i * 0.05, stability=0.9,
                        occurrence_count=i + 3)
        ])
    ctx = model.to_prompt_context(max_emotion_beliefs=5)
    # 应该只有 5 条被输出
    assert ctx.count("- ") <= 5


def test_serialization_roundtrip_with_emotional():
    """含情绪认知的模型序列化/反序列化正确"""
    model = SelfModelV3(identity="浅雾羽依")
    bridge = EmotionSelfModelBridge()
    bridge.merge(model, [make_belief()])

    data = model.to_dict()
    restored = SelfModelV3.from_dict(data)

    assert len(restored.emotional_self_understanding) == 1
    assert restored.emotional_self_understanding[0].event_type == "user_praise"


def test_emotional_belief_not_in_regular_beliefs():
    """情绪信念不应混入普通 beliefs 字段"""
    model = SelfModelV3(beliefs=["原有信念"])
    bridge = EmotionSelfModelBridge()
    bridge.merge(model, [make_belief()])

    # 序列化后，普通 beliefs 不应包含情绪信念的内容
    data = model.to_dict()
    assert "被认可" not in str(data["beliefs"])


def test_bridge_does_not_filter_again():
    """Bridge 层不做额外置信度过滤（由 Extractor 负责）"""
    # 即使是低置信度信念，如果被传入，也会被合并
    model = SelfModelV3()
    bridge = EmotionSelfModelBridge()
    low_confidence = make_belief(confidence=0.3, stability=0.3, occurrence_count=2)
    bridge.merge(model, [low_confidence])
    assert len(model.emotional_self_understanding) == 1  # 不会被桥接层拒绝


if __name__ == "__main__":
    test_merge_new_belief()
    print("✅ 1/9 首次接入信念")
    test_merge_updates_existing()
    print("✅ 2/9 更新已有信念（置信度+证据）")
    test_merge_preserves_existing_beliefs()
    print("✅ 3/9 普通信念不受影响")
    test_empty_beliefs_safe()
    print("✅ 4/9 空信念安全")
    test_to_prompt_includes_emotional_understanding()
    print("✅ 5/9 Prompt 包含情绪认知")
    test_prompt_limits_emotion_beliefs()
    print("✅ 6/9 Prompt 数量限制")
    test_serialization_roundtrip_with_emotional()
    print("✅ 7/9 序列化兼容")
    test_emotional_belief_not_in_regular_beliefs()
    print("✅ 8/9 情绪信念不混入普通 beliefs")
    test_bridge_does_not_filter_again()
    print("✅ 9/9 Bridge 不做额外过滤")
    print("\n🎉 Phase 9.6 全部通过")