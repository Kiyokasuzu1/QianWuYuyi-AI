"""
Phase 10.4：关系事件验证器测试
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.relationship.relationship_evaluator import RelationshipEvaluator, EvaluationResult
from src.relationship.relationship_event import RelationshipEvent


def make_event(event_type="collaboration", signal_strength=0.7, evidence_ids=None,
               potential_dimensions=None, description=None):
    """构造测试事件。None 表示使用默认值，空列表/集合不会被覆盖。"""
    return RelationshipEvent(
        event_id="test_001",
        event_type=event_type,
        evidence_ids=evidence_ids if evidence_ids is not None else ["mem_001"],
        signal_strength=signal_strength,
        potential_dimensions=potential_dimensions if potential_dimensions is not None else {"collaboration"},
        description=description if description is not None else "我们一起开发羽依项目",
    )


def test_valid_event_passes():
    evaluator = RelationshipEvaluator()
    event = make_event()
    result = evaluator.evaluate(event)
    assert result.passed is True
    assert len(result.checks) == 6
    assert all(result.checks.values())


def test_none_event_rejected():
    evaluator = RelationshipEvaluator()
    result = evaluator.evaluate(None)
    assert result.passed is False
    assert result.rejected_by == "null_check"


def test_unknown_type_rejected():
    evaluator = RelationshipEvaluator()
    event = make_event(event_type="unknown_type")
    result = evaluator.evaluate(event)
    assert result.passed is False
    assert result.rejected_by == "type_check"


def test_low_signal_rejected():
    evaluator = RelationshipEvaluator()
    event = make_event(signal_strength=0.2)
    result = evaluator.evaluate(event)
    assert result.passed is False
    assert result.rejected_by == "signal_check"


def test_no_evidence_rejected():
    evaluator = RelationshipEvaluator()
    event = make_event(evidence_ids=[])  # 空列表不会再被覆盖
    result = evaluator.evaluate(event)
    assert result.passed is False
    assert result.rejected_by == "evidence_check"


def test_no_dimensions_rejected():
    evaluator = RelationshipEvaluator()
    event = make_event(potential_dimensions=set())  # 空集合不会再被覆盖
    result = evaluator.evaluate(event)
    assert result.passed is False
    assert result.rejected_by == "dimension_check"


def test_external_subject_rejected():
    """外部关系（不含羽依主体词）应被拒绝"""
    evaluator = RelationshipEvaluator()
    event = make_event(description="他和朋友一起开发项目")
    result = evaluator.evaluate(event)
    assert result.passed is False
    assert result.rejected_by == "subject_check"


def test_evaluator_does_not_modify_event():
    """验证器不应修改原始事件"""
    evaluator = RelationshipEvaluator()
    event = make_event()
    before = event.to_dict()
    evaluator.evaluate(event)
    after = event.to_dict()
    assert before == after


def test_evidence_strength_weak_for_single():
    evaluator = RelationshipEvaluator()
    event = make_event(evidence_ids=["mem_001"])
    result = evaluator.evaluate(event)
    assert result.passed is True
    assert result.evidence_strength == "weak"


def test_evidence_strength_strong_for_multiple():
    evaluator = RelationshipEvaluator()
    event = make_event(evidence_ids=["mem_001", "mem_002", "mem_003"])
    result = evaluator.evaluate(event)
    assert result.passed is True
    assert result.evidence_strength == "strong"


if __name__ == "__main__":
    test_valid_event_passes()
    print("✅ 1/10 有效事件通过")
    test_none_event_rejected()
    print("✅ 2/10 空事件拒绝")
    test_unknown_type_rejected()
    print("✅ 3/10 未知类型拒绝")
    test_low_signal_rejected()
    print("✅ 4/10 低信号拒绝")
    test_no_evidence_rejected()
    print("✅ 5/10 无证据拒绝")
    test_no_dimensions_rejected()
    print("✅ 6/10 无维度拒绝")
    test_external_subject_rejected()
    print("✅ 7/10 外部主体拒绝")
    test_evaluator_does_not_modify_event()
    print("✅ 8/10 不修改原始事件")
    test_evidence_strength_weak_for_single()
    print("✅ 9/10 单证据强度=weak")
    test_evidence_strength_strong_for_multiple()
    print("✅ 10/10 多证据强度=strong")
    print("\n🎉 Phase 10.4 全部通过")