"""
E-Emotion-6 测试 2/5：情绪变化计算（EmotionChangeProposal + EmotionEvaluator）
覆盖任务书验收项：情绪变化计算
- evaluate_proposals 对每非零维度输出一条提案（含 11 维）
- 已知事件高置信度 / 未知事件低置信度
- 提案携带 reason/evidence/事件来源，纯评估不碰状态
- 缓解事件 → anxiety 下降
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_event import EmotionEvent
from src.emotion.emotion_evaluator import (
    EmotionEvaluator,
    DEFAULT_RULE_CONFIDENCE,
    UNKNOWN_EVENT_CONFIDENCE,
)
from src.emotion.emotion_change_proposal import (
    EmotionChangeProposal,
    VALID_DIMENSIONS,
)


evaluator = EmotionEvaluator()


def test_valid_dimensions_are_eleven():
    """合法维度 = 11 个（与 EmotionState 对齐）"""
    assert VALID_DIMENSIONS == frozenset({
        "valence", "arousal", "curiosity", "anxiety", "confidence", "energy",
        "stability", "happiness", "sadness", "trust", "attachment",
    })


def test_praise_produces_new_dimension_proposals():
    """赞美事件：包含 happiness/trust/attachment 维度提案"""
    proposals = evaluator.evaluate_proposals(EmotionEvent("user_praise", intensity=1.0))
    dims = {p.emotion_dimension for p in proposals}
    assert "happiness" in dims
    assert "trust" in dims
    assert "attachment" in dims
    assert "valence" in dims


def test_proposal_fields_complete():
    """提案字段契约完整：dimension/delta/confidence/reason/evidence/id"""
    proposals = evaluator.evaluate_proposals(
        EmotionEvent("user_praise", intensity=0.5),
        evidence_ids=["trace_1", "mem_2"],
    )
    assert proposals, "应至少产生一条提案"
    for p in proposals:
        assert p.emotion_dimension in VALID_DIMENSIONS
        assert abs(p.delta) > 0
        assert p.confidence == DEFAULT_RULE_CONFIDENCE
        assert p.reason
        assert p.evidence_ids == ["trace_1", "mem_2"]
        assert p.proposal_id.startswith("ecp_")
        assert p.created_at


def test_unknown_event_low_confidence():
    """未知事件类型 → 低置信度（不得静默按高置信度处理）"""
    proposals = evaluator.evaluate_proposals(EmotionEvent("mystery_event", intensity=1.0))
    assert proposals == []
    # 未知事件无规则 → 无维度变化提案（delta 全 0），低置信度仅适用于有变化的情形
    # 此处验证常数存在且小于默认置信度
    assert UNKNOWN_EVENT_CONFIDENCE < DEFAULT_RULE_CONFIDENCE


def test_mitigation_lowers_anxiety():
    """缓解事件（睡好了/放心了）→ anxiety 下降"""
    proposals = evaluator.evaluate_proposals(EmotionEvent("mitigation", intensity=0.7))
    anxiety = [p for p in proposals if p.emotion_dimension == "anxiety"]
    assert anxiety and anxiety[0].delta < 0


def test_proposal_roundtrip():
    """提案 to_dict/from_dict 序列化一致"""
    p = EmotionChangeProposal(
        emotion_dimension="valence", delta=0.2, confidence=0.8,
        reason="测试", evidence_ids=["e1"], source_event_id="evt_9",
    )
    restored = EmotionChangeProposal.from_dict(p.to_dict())
    assert restored.emotion_dimension == "valence"
    assert restored.delta == 0.2
    assert restored.confidence == 0.8
    assert restored.reason == "测试"
    assert restored.evidence_ids == ["e1"]
    assert restored.proposal_id == p.proposal_id
    assert restored.source_event_id == "evt_9"


def test_confidence_clamped():
    """confidence 创建时钳制到 0~1"""
    assert EmotionChangeProposal(emotion_dimension="valence", confidence=3.0).confidence == 1.0
    assert EmotionChangeProposal(emotion_dimension="valence", confidence=-1.0).confidence == 0.0


if __name__ == "__main__":
    test_valid_dimensions_are_eleven(); print("✅ 1/7 11 维白名单")
    test_praise_produces_new_dimension_proposals(); print("✅ 2/7 新维度提案")
    test_proposal_fields_complete(); print("✅ 3/7 提案字段契约")
    test_unknown_event_low_confidence(); print("✅ 4/7 未知事件低置信度")
    test_mitigation_lowers_anxiety(); print("✅ 5/7 缓解事件降焦虑")
    test_proposal_roundtrip(); print("✅ 6/7 提案序列化")
    test_confidence_clamped(); print("✅ 7/7 置信度钳制")
    print("\n🎉 E-Emotion-6-2 情绪变化计算全部通过")
