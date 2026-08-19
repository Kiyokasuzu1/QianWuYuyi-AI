"""
Phase 9.5 v2.1 最终测试：EmotionBelief
覆盖：提取阈值、证据链、低次数过滤、旧数据兼容、未知事件安全、updated_at 字段
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_pattern import EmotionPattern
from src.emotion.emotion_belief import EmotionBelief
from src.emotion.emotion_belief_extractor import EmotionBeliefExtractor


def make_high_confidence_pattern():
    return EmotionPattern(
        pattern_type="trigger_event",
        event_type="user_praise",
        emotion="joy",
        confidence=0.85,
        stability=0.9,
        evidence_trace_ids=["t1", "t2", "t3", "t4", "t5"],
        occurrence_count=5,
    )


def make_low_confidence_pattern():
    return EmotionPattern(
        pattern_type="trigger_event",
        event_type="user_conflict",
        emotion="anxiety",
        confidence=0.4,
        stability=0.6,
        evidence_trace_ids=["t10", "t11"],
        occurrence_count=2,
    )


def make_low_stability_pattern():
    return EmotionPattern(
        pattern_type="trigger_event",
        event_type="new_topic",
        emotion="curiosity",
        confidence=0.8,
        stability=0.3,
        evidence_trace_ids=["t20", "t21", "t22"],
        occurrence_count=3,
    )


def make_low_occurrence_pattern():
    return EmotionPattern(
        pattern_type="trigger_event",
        event_type="achievement",
        emotion="joy",
        confidence=0.9,
        stability=0.9,
        evidence_trace_ids=["t30", "t31"],
        occurrence_count=2,
    )


def make_unknown_event_pattern():
    return EmotionPattern(
        pattern_type="trigger_event",
        event_type="unknown_event",
        emotion="unknown",
        confidence=0.85,
        stability=0.9,
        evidence_trace_ids=["t40", "t41", "t42"],
        occurrence_count=5,
    )


def test_extract_high_confidence_belief():
    extractor = EmotionBeliefExtractor()
    beliefs = extractor.extract([make_high_confidence_pattern()])
    assert len(beliefs) == 1
    b = beliefs[0]
    assert "被认可" in b.content
    assert "产生积极情绪" in b.content
    assert b.confidence == 0.85
    assert len(b.evidence_trace_ids) == 5


def test_filter_low_confidence():
    extractor = EmotionBeliefExtractor(min_confidence=0.6)
    beliefs = extractor.extract([make_low_confidence_pattern()])
    assert len(beliefs) == 0


def test_filter_low_stability():
    extractor = EmotionBeliefExtractor(min_stability=0.5)
    beliefs = extractor.extract([make_low_stability_pattern()])
    assert len(beliefs) == 0


def test_filter_low_occurrence():
    extractor = EmotionBeliefExtractor(min_occurrences=3)
    beliefs = extractor.extract([make_low_occurrence_pattern()])
    assert len(beliefs) == 0


def test_empty_patterns_returns_empty():
    assert EmotionBeliefExtractor().extract([]) == []


def test_belief_serialization():
    b = EmotionBelief(content="测试", emotion="joy", event_type="user_praise",
                      confidence=0.8, stability=0.7, source_pattern_id="p1",
                      evidence_trace_ids=["t1"], occurrence_count=5)
    data = b.to_dict()
    assert data["content"] == "测试"


def test_belief_deserialization():
    data = {"content": "测试信念", "confidence": 0.8}
    b = EmotionBelief.from_dict(data)
    assert b.content == "测试信念"
    assert b.belief_version == "1.0"


def test_belief_version_default():
    b = EmotionBelief()
    assert b.belief_version == "1.0"


def test_belief_content_does_not_contain_internal_ids():
    extractor = EmotionBeliefExtractor()
    beliefs = extractor.extract([make_high_confidence_pattern()])
    b = beliefs[0]
    assert "trace_" not in b.content
    assert "pattern_" not in b.content
    assert "user_praise" not in b.content


def test_evidence_ids_are_copied():
    pattern = make_high_confidence_pattern()
    extractor = EmotionBeliefExtractor()
    belief = extractor.extract([pattern])[0]
    belief.evidence_trace_ids.append("injected")
    assert "injected" not in pattern.evidence_trace_ids


def test_from_dict_missing_belief_id():
    data = {"content": "旧数据", "confidence": 0.8}
    b = EmotionBelief.from_dict(data)
    assert b.belief_id.startswith("eb_")
    assert len(b.belief_id) > 3


def test_unknown_event_fallback():
    extractor = EmotionBeliefExtractor()
    beliefs = extractor.extract([make_unknown_event_pattern()])
    assert len(beliefs) == 1
    b = beliefs[0]
    assert "unknown_event" not in b.content
    assert "unknown" not in b.content
    assert "某些情况下" in b.content
    assert "产生相应的情绪变化" in b.content


def test_belief_has_updated_at():
    b = EmotionBelief()
    assert b.updated_at
    data = b.to_dict()
    assert "updated_at" in data
    restored = EmotionBelief.from_dict(data)
    assert restored.updated_at == b.updated_at


if __name__ == "__main__":
    test_extract_high_confidence_belief()
    print("✅ 1/13 高置信度提取")
    test_filter_low_confidence()
    print("✅ 2/13 低置信度过滤")
    test_filter_low_stability()
    print("✅ 3/13 低稳定性过滤")
    test_filter_low_occurrence()
    print("✅ 4/13 低次数过滤")
    test_empty_patterns_returns_empty()
    print("✅ 5/13 空数据安全")
    test_belief_serialization()
    print("✅ 6/13 序列化")
    test_belief_deserialization()
    print("✅ 7/13 反序列化")
    test_belief_version_default()
    print("✅ 8/13 版本字段默认值")
    test_belief_content_does_not_contain_internal_ids()
    print("✅ 9/13 内容不含内部ID")
    test_evidence_ids_are_copied()
    print("✅ 10/13 证据链隔离")
    test_from_dict_missing_belief_id()
    print("✅ 11/13 旧数据自动生成ID")
    test_unknown_event_fallback()
    print("✅ 12/13 未知事件安全兜底")
    test_belief_has_updated_at()
    print("✅ 13/13 updated_at 字段")
    print("\n🎉 Phase 9.5 v2.1 最终修正版全部通过")