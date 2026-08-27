"""
E-Emotion-6 测试 3/5：EmotionUpdater（最大变化限制 / decay / 审计）
覆盖任务书验收项：最大变化限制、decay 运行、audit 完整
- 合法提案应用（before/after 快照）
- 超限 delta 拒绝（trust/attachment 上限 0.10）
- 低置信度拒绝 / 非法维度拒绝
- 拒绝不改变状态、原状态对象不可变
- 审计完整：每条提案一条审计，component/actor/version/reason/evidence 齐全
- decay：短期快衰减 vs 长期慢衰减；不修改原对象
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.emotion.emotion_state import EmotionState
from src.emotion.emotion_change_proposal import EmotionChangeProposal
from src.emotion.emotion_updater import EmotionUpdater, DEFAULT_MAX_DELTA


def _prop(dim, delta, confidence=0.75, reason="测试提案", evidence=None):
    return EmotionChangeProposal(
        emotion_dimension=dim, delta=delta, confidence=confidence,
        reason=reason, evidence_ids=evidence or [],
    )


def test_apply_accepted_proposal():
    """合法提案应用：状态变化 + before/after 快照"""
    state = EmotionState(valence=0.2, trust=0.5)
    result = EmotionUpdater().apply(
        state, [_prop("valence", 0.2), _prop("trust", 0.05)]
    )

    assert result.applied and len(result.applied) == 2
    assert result.rejected == []
    assert result.state_before["valence"] == 0.2
    assert result.state_after["valence"] == 0.4
    assert result.state_after["trust"] == 0.55
    # 原状态对象不被修改（不可变契约）
    assert state.valence == 0.2
    assert state.trust == 0.5


def test_delta_exceeds_limit_rejected():
    """最大变化限制：超限 delta 拒绝，状态不变——验收项「最大变化限制」"""
    state = EmotionState(trust=0.5)
    result = EmotionUpdater().apply(state, [_prop("trust", 0.9)])
    assert result.rejected and result.applied == []
    assert result.state_after["trust"] == 0.5
    assert abs(0.9) > DEFAULT_MAX_DELTA["trust"]


def test_short_term_limit_looser_than_long_term():
    """短期维度上限 > 长期维度上限（信任/依恋更严格）"""
    assert DEFAULT_MAX_DELTA["valence"] > DEFAULT_MAX_DELTA["trust"]
    assert DEFAULT_MAX_DELTA["happiness"] > DEFAULT_MAX_DELTA["attachment"]


def test_low_confidence_rejected():
    """低置信度提案拒绝并审计（不静默）"""
    state = EmotionState(valence=0.0)
    result = EmotionUpdater().apply(state, [_prop("valence", 0.2, confidence=0.1)])
    assert result.rejected and result.applied == []
    assert result.state_after["valence"] == 0.0


def test_invalid_dimension_rejected():
    """非法维度拒绝（如人格域字段不得混入情绪提案）"""
    state = EmotionState()
    result = EmotionUpdater().apply(state, [_prop("personality_temperature", 0.5)])
    assert result.rejected and result.applied == []
    assert result.state_after == result.state_before


def test_audit_entries_complete():
    """审计完整：每条提案一条审计，字段齐全——验收项「audit完整」"""
    sink = []
    updater = EmotionUpdater(audit_sink=sink.append)
    proposals = [
        _prop("valence", 0.2, evidence=["mem_1"]),
        _prop("trust", 0.9),                       # 超限 → 拒绝
        _prop("bad_dim", 0.1),                     # 非法维度 → 拒绝
        _prop("anxiety", 0.2, confidence=0.05),    # 低置信度 → 拒绝
    ]
    result = updater.apply(
        EmotionState(),
        proposals,
        source_event_id="evt_42",
    )

    assert len(result.audit_entries) == len(proposals)
    assert len(sink) == len(proposals)
    for entry in result.audit_entries:
        assert entry["component"] == "emotion"
        assert entry["actor"] == "emotion_updater"
        assert entry["version"] == "emotion.2.0"
        assert entry["source_event_id"] == "evt_42"
        assert entry["reason"]
        assert "before" in entry and "after" in entry
        assert entry["timestamp"]
    # 拒绝条目的审计原因与 after.accepted 标记
    rejected_entries = [
        e for e in result.audit_entries if e["after"]["accepted"] is False
    ]
    assert len(rejected_entries) == 3
    for e in rejected_entries:
        assert e["reason"].startswith("rejected:")
    # 证据链进入审计
    first = result.audit_entries[0]
    assert first["evidence_memory_ids"] == ["mem_1"]


def test_decay_short_term_faster_than_long_term():
    """decay 运行：同样时长，短期维度回落远快于长期维度——验收项「decay运行」"""
    state = EmotionState(valence=0.9, happiness=0.9, trust=0.9, attachment=0.9)
    updater = EmotionUpdater()
    decayed = updater.decay(state, seconds=86400)  # 一天

    valence_drop = state.valence - decayed.valence
    trust_drop = state.trust - decayed.trust
    assert valence_drop > trust_drop * 10
    assert decayed.trust > 0.85     # 长期维度一天内几乎不动
    assert decayed.valence < 0.7    # 短期维度一天内明显回落


def test_decay_does_not_mutate_original():
    """decay 返回新状态，不修改原对象"""
    state = EmotionState(valence=0.8)
    before = state.to_dict()
    EmotionUpdater().decay(state, seconds=3600)
    assert state.to_dict() == before


def test_decay_dimension_categories():
    """维度分类暴露：短期 7 维 / 长期 4 维"""
    cats = EmotionUpdater().decay_dimension_categories()
    assert len(cats["short_term"]) == 7
    assert len(cats["long_term"]) == 4
    assert "trust" in cats["long_term"]
    assert "valence" in cats["short_term"]


if __name__ == "__main__":
    test_apply_accepted_proposal(); print("✅ 1/9 合法应用")
    test_delta_exceeds_limit_rejected(); print("✅ 2/9 最大变化限制")
    test_short_term_limit_looser_than_long_term(); print("✅ 3/9 长短期限差异")
    test_low_confidence_rejected(); print("✅ 4/9 低置信度拒绝")
    test_invalid_dimension_rejected(); print("✅ 5/9 非法维度拒绝")
    test_audit_entries_complete(); print("✅ 6/9 审计完整")
    test_decay_short_term_faster_than_long_term(); print("✅ 7/9 decay 运行")
    test_decay_does_not_mutate_original(); print("✅ 8/9 decay 不可变")
    test_decay_dimension_categories(); print("✅ 9/9 维度分类")
    print("\n🎉 E-Emotion-6-3 Updater 全部通过")
