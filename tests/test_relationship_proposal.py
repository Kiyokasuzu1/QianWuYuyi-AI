# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段三契约测试:RelationshipProposal 状态机 + 审计 + 候选评估。

覆盖:
- 状态流:candidate → evaluating → pending_review → accepted/rejected → activated
- 没有人工审批(approve 且提供审核人)不能 accepted
- rejected 终态:不可激活、不可再审批
- 每次状态流转写审计(actor/reason/before/after/timestamp)
- RelationshipCoreEvaluator 四维度评分:约定类记忆入候选,瞬时记忆排除
"""
from src.relationship.relationship_proposal import (
    PROPOSAL_STATUS,
    RelationshipProposal,
)
from src.relationship.relationship_core_evaluator import RelationshipCoreEvaluator

CREATOR = "366648462"
REVIEWER = "366648462"  # 唯一治理者 = 清清


def _make_proposal(**overrides) -> RelationshipProposal:
    kwargs = dict(
        source_memory_ids=["mem_agreement"],
        source_user_id=CREATOR,
        category="relationship_core_candidate",
        score=0.82,
        reason="稳定约定,影响未来互动",
    )
    kwargs.update(overrides)
    return RelationshipProposal(**kwargs)


# ---------------- 状态机 ----------------

def test_initial_status_is_candidate():
    assert _make_proposal().status == PROPOSAL_STATUS["CANDIDATE"]


def test_system_flow_candidate_to_pending_review():
    p = _make_proposal()
    assert p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    assert p.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator")
    assert p.status == PROPOSAL_STATUS["PENDING_REVIEW"]


def test_cannot_accept_without_human_review():
    """没有人工审批不能 accepted——这是 2.5-C 的核心安全约束。"""
    p = _make_proposal()
    # 直接 transition 到 accepted(绕过审批)必须失败
    assert not p.transition(PROPOSAL_STATUS["ACCEPTED"])
    assert not p.transition(PROPOSAL_STATUS["ACCEPTED"], actor="evaluator")
    # approve() 缺少审核人必须失败
    assert not p.approve("")
    assert not p.approve("")
    # 未到 pending_review 时 approve 也失败
    assert p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    assert not p.approve(REVIEWER, reason="过早审批")
    assert p.status == PROPOSAL_STATUS["EVALUATING"]


def test_approve_from_pending_review():
    p = _make_proposal()
    p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    p.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator")
    assert p.approve(REVIEWER, reason="证据充分,同意进入关系核心")
    assert p.status == PROPOSAL_STATUS["ACCEPTED"]


def test_accepted_then_activated():
    p = _make_proposal()
    p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    p.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator")
    p.approve(REVIEWER, reason="ok")
    assert p.activate(actor="anchor_registry", reason="锚点+核心已写入")
    assert p.status == PROPOSAL_STATUS["ACTIVATED"]


def test_activate_only_from_accepted():
    p = _make_proposal()
    assert not p.activate()  # candidate 不可激活
    p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    assert not p.activate()  # evaluating 不可激活
    p.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator")
    assert not p.activate()  # pending_review 不可激活


def test_rejected_is_terminal():
    p = _make_proposal()
    p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    p.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator")
    assert p.reject(REVIEWER, reason="证据不足")
    assert p.status == PROPOSAL_STATUS["REJECTED"]
    # reject 后不可激活、不可再审批
    assert not p.activate()
    assert not p.approve(REVIEWER, reason="反悔")
    assert not p.reject(REVIEWER, reason="重复")
    assert not p.transition(PROPOSAL_STATUS["ACTIVATED"])


def test_illegal_transitions_rejected():
    p = _make_proposal()
    assert not p.transition(PROPOSAL_STATUS["ACTIVATED"])
    assert not p.transition(PROPOSAL_STATUS["REJECTED"], actor="evaluator")
    assert not p.transition("nonsense")
    # candidate 不能直接跳到 pending_review/accepted
    assert not p.transition(PROPOSAL_STATUS["PENDING_REVIEW"])


# ---------------- 审计 ----------------

def test_audit_records_every_transition():
    p = _make_proposal()
    p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator", reason="评估完成")
    p.transition(PROPOSAL_STATUS["PENDING_REVIEW"], actor="evaluator", reason="提交审核")
    p.approve(REVIEWER, reason="同意")
    p.activate(actor="anchor_registry", reason="写入完成")
    assert len(p.audit) == 4
    for entry in p.audit:
        for key in ("action", "actor", "reason", "timestamp", "before", "after"):
            assert key in entry, key
    assert p.audit[0]["before"] == PROPOSAL_STATUS["CANDIDATE"]
    assert p.audit[0]["after"] == PROPOSAL_STATUS["EVALUATING"]
    assert p.audit[-1]["after"] == PROPOSAL_STATUS["ACTIVATED"]
    assert p.audit[2]["action"] == "approve"
    assert p.audit[2]["actor"] == REVIEWER


def test_failed_transition_does_not_audit():
    p = _make_proposal()
    assert not p.transition(PROPOSAL_STATUS["ACCEPTED"])
    assert not p.activate()
    assert p.audit == []


def test_proposal_serialization_roundtrip():
    p = _make_proposal()
    p.transition(PROPOSAL_STATUS["EVALUATING"], actor="evaluator")
    data = p.to_dict()
    restored = RelationshipProposal.from_dict(data)
    assert restored.proposal_id == p.proposal_id
    assert restored.status == p.status
    assert restored.source_memory_ids == ["mem_agreement"]
    assert len(restored.audit) == len(p.audit)


# ---------------- 候选评估器 ----------------

def _memory(content, user_id=CREATOR, **extra):
    record = {
        "id": "mem_x", "user_id": user_id, "role": "user",
        "content": content, "timestamp": "2026-08-16T09:00:00",
    }
    record.update(extra)
    return record


def test_evaluator_agreement_memory_is_candidate():
    ev = RelationshipCoreEvaluator()
    result = ev.evaluate(_memory("羽依,你永远不要随便和别人抱抱,这是我们的约定"))
    assert result["score"] >= 0.5
    assert result["category"] == "relationship_core_candidate"
    assert result["reason"]


def test_evaluator_transient_memory_not_candidate():
    ev = RelationshipCoreEvaluator()
    result = ev.evaluate(_memory("我今天中午吃了牛肉面"))
    assert result["category"] == "not_candidate"
    assert result["score"] < 0.5


def test_evaluator_evidence_missing_not_candidate():
    ev = RelationshipCoreEvaluator()
    result = ev.evaluate({"id": "mem_x", "content": "永远不要抱抱"})
    assert result["category"] == "not_candidate"
    assert result["score"] < 0.5


def test_evaluator_score_bounds():
    ev = RelationshipCoreEvaluator()
    for content in ("约定 永远 我们", "今天 刚才 暂时", "关系 以后 记得 我们"):
        result = ev.evaluate(_memory(content))
        assert 0.0 <= result["score"] <= 1.0
        assert result["category"] in ("relationship_core_candidate", "not_candidate")
