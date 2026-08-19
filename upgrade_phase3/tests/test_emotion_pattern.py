"""
Phase 9.4 v2.1：EmotionPattern 测试（修正版）
修复阈值过高及证据 ID 比对错误。
"""
import sys, os, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotional_trace import EmotionalTrace, EmotionCause
from src.emotion.emotion_pattern_analyzer import EmotionPatternAnalyzer
from src.emotion.emotion_pattern_repository import EmotionPatternRepository
from src.emotion.emotion_pattern import EmotionPattern


def make_traces():
    """构造包含不同事件类型的轨迹"""
    return [
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.8,
                       event_type="user_praise", memory_id="m1"),
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.7,
                       event_type="user_praise", memory_id="m2"),
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.9,
                       event_type="user_praise", memory_id="m3"),
        EmotionalTrace(emotion="anxiety", cause=EmotionCause.USER_INTERACTION, intensity=0.6,
                       event_type="user_conflict", memory_id="m4"),
        EmotionalTrace(emotion="anxiety", cause=EmotionCause.USER_INTERACTION, intensity=0.5,
                       event_type="user_conflict", memory_id="m5"),
        EmotionalTrace(emotion="curiosity", cause=EmotionCause.USER_INTERACTION, intensity=0.7,
                       event_type="new_topic", memory_id="m6"),
    ]


def test_discover_trigger_event_pattern():
    """应发现 user_praise → joy 的模式（阈值调低以适应低样本）"""
    analyzer = EmotionPatternAnalyzer(min_occurrences=3, confidence_threshold=0.2)  # 降低阈值
    patterns = analyzer.analyze(make_traces())
    praise_patterns = [p for p in patterns if p.event_type == "user_praise"]
    assert len(praise_patterns) == 1
    p = praise_patterns[0]
    assert p.emotion == "joy"
    assert p.occurrence_count == 3
    assert len(p.evidence_trace_ids) == 3


def test_confidence_increases_with_more_samples():
    """样本量增大时，confidence 应随 sample_weight 提升"""
    analyzer = EmotionPatternAnalyzer(min_occurrences=3, confidence_threshold=0.2,
                                      sample_weight_factor=10.0)
    patterns_small = analyzer.analyze(make_traces())
    praise_small = [p for p in patterns_small if p.event_type == "user_praise"][0]

    many_traces = make_traces() + [
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.8,
                       event_type="user_praise", memory_id=f"m{i}") for i in range(7, 14)
    ]
    patterns_large = analyzer.analyze(many_traces)
    praise_large = [p for p in patterns_large if p.event_type == "user_praise"][0]

    assert praise_large.confidence > praise_small.confidence


def test_emotion_distribution_present():
    """模式应包含情绪分布"""
    analyzer = EmotionPatternAnalyzer(min_occurrences=3, confidence_threshold=0.2)
    patterns = analyzer.analyze(make_traces())
    p = patterns[0]
    assert "joy" in p.emotion_distribution
    assert p.emotion_distribution["joy"] == 1.0


def test_stability_value():
    """稳定性应在 0~1 之间"""
    analyzer = EmotionPatternAnalyzer(min_occurrences=2, confidence_threshold=0.2)
    patterns = analyzer.analyze(make_traces())
    for p in patterns:
        assert 0.0 <= p.stability <= 1.0


def test_analyzer_does_not_modify_traces():
    """分析器不应修改原始 traces"""
    traces = make_traces()
    before = [t.to_dict() for t in traces]
    analyzer = EmotionPatternAnalyzer(min_occurrences=2, confidence_threshold=0.2)
    analyzer.analyze(traces)
    after = [t.to_dict() for t in traces]
    assert before == after


def test_evidence_ids_match():
    """证据 trace ID 应精确匹配（使用同一批 traces）"""
    analyzer = EmotionPatternAnalyzer(min_occurrences=2, confidence_threshold=0.2)
    traces = make_traces()          # 生成一次
    patterns = analyzer.analyze(traces)
    for p in patterns:
        # 从当前 traces 中提取预期 ID，而不是重新调用 make_traces()
        expected_ids = {t.trace_id for t in traces if t.event_type == p.event_type}
        assert set(p.evidence_trace_ids) == expected_ids


def test_pattern_repository_append():
    """仓库支持增量添加"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = f"{tmpdir}/patterns.json"
        repo = EmotionPatternRepository(path)
        pattern = EmotionPattern(
            pattern_type="trigger_event",
            event_type="user_praise",
            emotion="joy",
            description="测试",
            confidence=0.8,
        )
        repo.append(pattern)
        loaded = repo.load_all()
        assert len(loaded) == 1
        assert loaded[0].event_type == "user_praise"


def test_old_trace_without_event_type():
    """旧版本 trace（无 event_type）反序列化后 event_type 应为空字符串，不崩溃"""
    old_data = {
        "emotion": "joy",
        "cause": "user_interaction",
        "intensity": 0.8,
        "memory_id": "old_mem"
    }
    trace = EmotionalTrace.from_dict(old_data)
    assert trace.event_type == ""
    assert trace.emotion == "joy"


def test_empty_event_type_safe():
    """event_type 为空字符串时，分析器应能正常处理，不会崩溃"""
    traces = [
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.6,
                       event_type=""),
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.7,
                       event_type=""),
        EmotionalTrace(emotion="joy", cause=EmotionCause.USER_INTERACTION, intensity=0.8,
                       event_type=""),
    ]
    analyzer = EmotionPatternAnalyzer(min_occurrences=3, confidence_threshold=0.3)
    patterns = analyzer.analyze(traces)
    assert len(patterns) == 1
    assert patterns[0].event_type == ""


def test_pattern_version_default():
    """pattern_version 应为 1.0"""
    pattern = EmotionPattern()
    assert pattern.pattern_version == "1.0"


if __name__ == "__main__":
    test_discover_trigger_event_pattern()
    print("✅ 1/10 发现事件类型模式")
    test_confidence_increases_with_more_samples()
    print("✅ 2/10 样本量影响置信度")
    test_emotion_distribution_present()
    print("✅ 3/10 情绪分布完整")
    test_stability_value()
    print("✅ 4/10 稳定性范围")
    test_analyzer_does_not_modify_traces()
    print("✅ 5/10 分析器不修改原始数据")
    test_evidence_ids_match()
    print("✅ 6/10 证据 ID 精确匹配")
    test_pattern_repository_append()
    print("✅ 7/10 模式仓库增量添加")
    test_old_trace_without_event_type()
    print("✅ 8/10 旧 trace 兼容（无 event_type）")
    test_empty_event_type_safe()
    print("✅ 9/10 空 event_type 安全")
    test_pattern_version_default()
    print("✅ 10/10 pattern_version 默认值正确")
    print("\n🎉 Phase 9.4 v2.1 全部通过")