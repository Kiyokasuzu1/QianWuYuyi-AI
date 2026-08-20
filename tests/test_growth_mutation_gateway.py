# -*- coding: utf-8 -*-
"""
P2.3-B.5 Phase 6 — Growth Mutation Gateway 测试

覆盖任务书 9 项：
1. GrowthEvent 可以生成 MutationRequest
2. MutationRequest target_domain="growth"
3. Gateway reject 不产生状态变化
4. Gateway accept 才进入 apply
5. proposal 未批准不能执行
6. duplicate proposal 会被 conflict check 拦截
7. audit 正常记录
8. feature flag false 保持旧行为
9. feature flag true 走治理路径

附 Phase 4 转换层测试：
10. attach_governance_linkage 写入治理链接键
11. to_mutation_proposal legacy → canonical 转换

注意：本文件不直接引用 GState / GE / PSStore / PersonalityResolver 等
单例敏感构造器（conftest 扫描约束），一律使用别名 + 显式临时路径，
保证不触碰 data/ 与运行时单例。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from src.contracts import growth_schema
from src.contracts.growth_schema import GrowthProposal as GP, ChangeItem as CI
from src.governance.mutation_contract import MutationRequest
from src.growth.mutation_adapter import (
    GrowthMutationAdapter as GAdapter,
    is_growth_mutation_gateway_enabled as is_flag,
    set_growth_mutation_gateway_enabled as set_flag,
    attach_governance_linkage,
    to_mutation_proposal,
)
from src.growth.growth_engine import GrowthEngine as GE
from src.growth.growth_state import GrowthState as GState
from src.growth.proposal_store import ProposalStore as PSStore
from src.growth.pipeline import GrowthPipeline as GPipe
from src.personality.relationship_state import RelationshipState as RS
from src.personality.personality_growth_record import PersonalityGrowthHistory


@pytest.fixture(autouse=True)
def _reset_flag():
    """每个用例结束后恢复 feature flag（默认关闭）。"""
    yield
    set_flag(False)


def _tmp_path(prefix="p23b5_"):
    return os.path.join(tempfile.mkdtemp(prefix=prefix), "state.json")


def _accepted_request(adapter, **overrides):
    """五道检查全过的请求配方（对齐 B.4 accepted-request 语义）。"""
    base = dict(
        source_event={
            "event_id": "ev_1",
            "type": "creation",
            "canonical_topic": "创作",
            "occurrence_count": 3,
            "confidence": 0.9,
        },
        target_path="growth.metrics.trust",
        proposed_change={
            "path": "growth.metrics.trust",
            "before": 0.30,
            "after": 0.305,
            "delta": 0.005,
            "confidence": 0.9,
        },
        evidence=[
            {"ref": "e1", "type": "user_behavior"},
            {"ref": "e2", "type": "user_behavior"},
            {"ref": "e3", "type": "user_statement"},
        ],
        context_snapshot={"request_id": "req_1", "trace_id": "trace_1"},
        risk_level="low",
    )
    base.update(overrides)
    return adapter.build_request(**base)


def _apply_engine(engine):
    """apply_route 执行件：包装 GE.apply_proposal 单变更执行。"""
    calls = []

    def _route(request, decision):
        calls.append(True)
        dim = request.target_path.rsplit(".", 1)[-1]
        single = GP(
            source_event_id=request.source_event.get("event_id", ""),
            proposed_changes=[
                CI(path=dim, before=request.proposed_change.get("before"),
                   after=request.proposed_change.get("after"))
            ],
            confidence=request.proposed_change.get("confidence", 0.9),
            evidence_ids=[e.get("ref") for e in request.evidence],
        )
        result = engine.apply_proposal(single)
        return result.get("status") == "applied"

    return _route, calls


def _crafted_event(evidence_count=3):
    """确定性事件（绕过 LLM 提取器，供 pipeline 级测试）。"""
    evidence = [
        {"id": f"e{i}", "text": f"用户完成了第{i}张新作品", "role": "user"}
        for i in range(1, evidence_count + 1)
    ]
    return {
        "event_id": "ev_craft_1",
        "event": "用户完成新作品",
        "topic": "AI画作创作",
        "canonical_topic": "AI画作创作",
        "event_type": "creation",
        "importance": 1.0,
        "evidence": evidence,
        "source_ids": [f"s{i}" for i in range(1, evidence_count + 1)],
        "metadata": {"validator_apply": True},
    }


def _crafted_evaluated(event, confidence=0.9):
    return {
        **event,
        "growth_allowed": True,
        "growth_signal": "creative_activity_interest",
        "target_candidates": ["trust"],
        "applied_delta": 0.01,
        "confidence": confidence,
        "growth_level": "context",
        "growth_domain": "capability",
    }


def _make_pipeline(evidence_count=3, confidence=0.9):
    """构造隔离的 pipeline：注入临时状态/历史/proposal store，
    并 monkeypatch 掉 LLM 提取与评估，保证确定性。"""
    growth_state = GState(state_path=_tmp_path("p23b5_gs_"))
    pipeline = GPipe(
        growth_state=growth_state,
        relationship_state=RS(state_path=_tmp_path("p23b5_rs_")),
        growth_history=PersonalityGrowthHistory(
            storage_path=_tmp_path("p23b5_gh_")
        ),
        proposal_store=PSStore(path=_tmp_path("p23b5_ps_")),
    )
    pipeline.extractor.extract_from_text = (
        lambda msg: [_crafted_event(evidence_count=evidence_count)]
    )
    pipeline.normalizer.normalize = lambda evs: evs
    pipeline.validator.validate = lambda evs: evs
    pipeline.evaluator.evaluate = (
        lambda event, history, experience_meaning=None: _crafted_evaluated(
            event, confidence=confidence
        )
    )
    pipeline._resolve_experience_meaning = lambda event: None
    return pipeline, growth_state


# ============================================================
# 1. GrowthEvent 可以生成 MutationRequest
# ============================================================
def test_growth_event_builds_mutation_request():
    adapter = GAdapter()
    request = adapter.from_growth_event(
        {
            "event_id": "ev_g1",
            "event_type": "creation",
            "canonical_topic": "AI画作创作",
            "source_ids": ["s1", "s2", "s3"],
        },
        primary_dimension="trust",
        delta=0.005,
        confidence=0.9,
    )
    assert isinstance(request, MutationRequest)
    assert request.actor_identity == "growth_system"
    assert request.target_path == "growth.metrics.trust"
    assert request.proposed_change["delta"] == 0.005
    assert len(request.evidence) == 3
    assert request.context_snapshot.get("request_id")
    assert request.context_snapshot.get("trace_id")


# ============================================================
# 2. MutationRequest target_domain="growth"
# ============================================================
def test_request_target_domain_is_growth():
    adapter = GAdapter()
    request = _accepted_request(adapter)
    assert request.target_domain == "growth"
    assert request.mutation_id.startswith("mut_")
    # 跨域路径在 Adapter 前置守卫处即拒（与 BoundaryCheck 同源）
    with pytest.raises(ValueError):
        adapter.build_request(
            source_event={"event_id": "ev_x"},
            target_path="personality.traits.warmth",
            proposed_change={},
            evidence=[],
        )


# ============================================================
# 3. Gateway reject 不产生状态变化
# ============================================================
def test_gateway_reject_no_state_change():
    state = GState(state_path=_tmp_path("p23b5_rj_"))
    engine = GE(state=state)
    before_metric = state.get_metric("trust")
    adapter = GAdapter()
    apply_route, calls = _apply_engine(engine)

    # 沙盒身份 → IdentityCheck REJECT
    request = _accepted_request(adapter, actor_identity="sandbox")
    envelope = adapter.route(request, apply_route=apply_route)

    assert envelope["decision"] == "REJECT"
    assert envelope["applied"] is False
    assert calls == []  # apply 执行件从未被调用
    assert state.get_metric("trust") == before_metric  # 状态零变化


# ============================================================
# 4. Gateway accept 才进入 apply
# ============================================================
def test_accept_required_for_apply():
    state = GState(state_path=_tmp_path("p23b5_ac_"))
    engine = GE(state=state)
    adapter = GAdapter()

    # 证据充分 → ACCEPT → 执行件被调用且状态变化
    request = _accepted_request(adapter)
    apply_route, calls = _apply_engine(engine)
    envelope = adapter.route(request, apply_route=apply_route)
    assert envelope["decision"] == "ACCEPT"
    assert envelope["applied"] is True
    assert calls
    assert state.get_metric("trust") > 0.30

    # 证据不足（1 条 ref）→ NEED_REVIEW → 执行件不被调用、状态不变
    state2 = GState(state_path=_tmp_path("p23b5_nr_"))
    engine2 = GE(state=state2)
    adapter2 = GAdapter()
    weak = _accepted_request(adapter2, evidence=[{"ref": "e1", "type": "user_behavior"}])
    apply_route2, calls2 = _apply_engine(engine2)
    envelope2 = adapter2.route(weak, apply_route=apply_route2)
    assert envelope2["decision"] == "NEED_REVIEW"
    assert envelope2["applied"] is False
    assert calls2 == []
    assert state2.get_metric("trust") == 0.30


# ============================================================
# 5. proposal 未批准不能执行
# ============================================================
def test_unapproved_proposal_cannot_execute():
    state = GState(state_path=_tmp_path("p23b5_un_"))
    engine = GE(state=state)
    adapter = GAdapter()
    apply_route, calls = _apply_engine(engine)

    # status="proposed"（未批准）+ 无证据 → 治理链必须拦截
    proposal = GP(
        source_event_id="ev_un1",
        proposed_changes=[CI(path="trust", before=0.30, after=0.305)],
        confidence=0.9,
        evidence_ids=[],
        status="proposed",
    )
    requests = adapter.from_proposal(proposal)
    assert len(requests) == 1
    envelope = adapter.route(requests[0], apply_route=apply_route)

    assert envelope["decision"] != "ACCEPT"
    assert envelope["applied"] is False
    assert calls == []  # 未批准提案没有执行任何状态变化
    assert state.get_metric("trust") == 0.30


# ============================================================
# 6. duplicate proposal 会被 conflict check 拦截
# ============================================================
def test_duplicate_proposal_conflict_check_intercepts():
    class _FakeDedupe:
        def __init__(self):
            self.calls = 0

        def exists_similar(self, source_event_id, fingerprint):
            self.calls += 1
            return "prop_existing" if self.calls > 1 else None

    adapter = GAdapter(conflict_store=_FakeDedupe())
    first = adapter.route(_accepted_request(adapter))
    second = adapter.route(_accepted_request(adapter))

    assert first["decision"] == "ACCEPT"
    assert second["decision"] == "DEFER"
    assert "duplicate_proposal" in second["reason"]
    assert len(adapter.deferred) == 1  # 重复提案进入延迟队列


# ============================================================
# 7. audit 正常记录
# ============================================================
def test_audit_records_requests_and_decisions():
    adapter = GAdapter()
    ok_env = adapter.route(_accepted_request(adapter))
    reject_env = adapter.route(_accepted_request(adapter, actor_identity="sandbox"))

    trace = adapter.audit_trace()
    assert len(trace["requests"]) == 2
    assert len(trace["decisions"]) == 2
    # 接受与拒绝都有审计引用（可追踪）
    assert ok_env["audit_reference"]
    assert reject_env["audit_reference"]
    for env in (ok_env, reject_env):
        assert env["request_id"]
        assert env["mutation_id"]
        assert env["target_path"] == "growth.metrics.trust"
        assert env["decision"] in ("ACCEPT", "REJECT")
        assert env["evidence_refs"]
    assert reject_env["applied"] is False  # 拒绝同样留痕但不执行


# ============================================================
# 8. feature flag false 保持旧行为
# ============================================================
def test_flag_false_keeps_legacy_behavior():
    assert not is_flag()
    pipeline, state = _make_pipeline()
    result = pipeline.incremental_update("dummy")

    assert pipeline.mutation_outcomes == []  # 旧模式无治理留痕
    assert pipeline.deferred_requests == []
    # 旧行为：proposal 直接经现有执行件应用（trust 0.30 → 0.309）
    assert state.get_metric("trust") > 0.30
    assert result["events"]  # 旧路径仍然产出 applied 事件


# ============================================================
# 9. feature flag true 走治理路径
# ============================================================
def test_flag_true_goes_through_governance():
    set_flag(True)
    pipeline, state = _make_pipeline(evidence_count=3, confidence=0.9)
    result = pipeline.incremental_update("dummy")

    outcomes = pipeline.mutation_outcomes
    assert outcomes  # 治理路径有裁决留痕
    assert all(o["decision"] == "ACCEPT" for o in outcomes)
    assert all(o["audit_reference"] for o in outcomes)
    assert state.get_metric("trust") > 0.30  # ACCEPT 才进入 apply
    assert result["events"]

    # 证据不足（唯一 ref 为 proposal 回退证据）→ NEED_REVIEW →
    # 状态不变 + 提案落盘待审
    pipeline2, state2 = _make_pipeline(evidence_count=0, confidence=0.9)
    result2 = pipeline2.incremental_update("dummy")
    outcomes2 = pipeline2.mutation_outcomes
    assert outcomes2
    assert any(o["decision"] == "NEED_REVIEW" for o in outcomes2)
    assert state2.get_metric("trust") == 0.30  # 未批准：零状态变化

    pending = pipeline2._proposal_store.list(status="pending")
    assert len(pending) == 1
    parked = pending[0]
    governance = (parked.evaluator_meta or {}).get("_governance") or {}
    assert governance.get("mutation_id")
    assert governance.get("request_id")
    assert governance.get("trace_id")
    assert governance.get("decision") == "NEED_REVIEW"
    assert governance.get("evidence")


# ============================================================
# Phase 4 转换层
# ============================================================
def test_attach_governance_linkage_writes_keys():
    proposal = GP(proposed_changes=[CI(path="trust", before=0.30, after=0.305)])
    attach_governance_linkage(
        proposal,
        mutation_id="mut_1",
        request_id="req_1",
        trace_id="trace_1",
        evidence=[{"ref": "e1", "type": "user_behavior"}],
        decision="NEED_REVIEW",
    )
    governance = proposal.evaluator_meta["_governance"]
    assert governance["mutation_id"] == "mut_1"
    assert governance["request_id"] == "req_1"
    assert governance["trace_id"] == "trace_1"
    assert governance["decision"] == "NEED_REVIEW"


def test_to_mutation_proposal_converts_legacy():
    legacy = {
        "proposal_id": "prop_legacy_1",
        "source_event_id": "ev_l1",
        "status": "cancelled",
        "confidence": 0.85,
        "reason": "legacy reason",
        "evidence": ["e1", "e2"],
        "affected_dimensions": {"trust": 0.01},
        "before_state": {"trust": 0.30},
        "after_state": {"trust": 0.31},
        "metadata": {"priority": "low"},
    }
    converted = to_mutation_proposal(
        legacy,
        mutation_id="mut_l1",
        request_id="req_l1",
        trace_id="trace_l1",
        evidence=[{"ref": "e1", "type": "user_behavior"}],
    )
    assert converted["id"] == "prop_legacy_1"
    assert converted["status"] == "rejected"  # cancelled → rejected 映射
    assert converted["proposed_changes"][0]["path"] == "trust"
    assert converted["mutation_id"] == "mut_l1"  # Phase 4 顶层治理链接键
    assert converted["request_id"] == "req_l1"
    assert converted["trace_id"] == "trace_l1"
    assert converted["evidence"]
    assert converted["evaluator_meta"]["_governance"]["mutation_id"] == "mut_l1"
    # canonical schema 往返无损（未知顶层键被忽略）
    restored = growth_schema.GrowthProposal.from_dict(converted)
    assert restored.id == "prop_legacy_1"
    assert restored.status == "rejected"
