# -*- coding: utf-8 -*-
"""
tests/test_mutation_gateway_contract.py

P2.3-B.3 —— Mutation Gateway 基础设施单元测试。

覆盖（B.3 任务书 Phase 6 十项）：
1. MutationRequest 序列化（to_dict/to_json/from_dict 往返）
2. MutationDecision 四状态（ACCEPT/REJECT/NEED_REVIEW/DEFER）
3. frozen 防修改（request / decision 均不可变）
4. 禁止 manager/repository/client 字段（JSON 安全校验拒绝对象实例）
5. 五阶段检查顺序（identity→boundary→evidence→conflict→audit，首失败即停）
6. identity 红线拒绝（沙盒身份 → REJECT）
7. boundary 拒绝（identity.core.* 锚点路径 → REJECT）
8. evidence 不足 → NEED_REVIEW
9. conflict 检测（倒转 → NEED_REVIEW；重复提案 → DEFER）
10. audit hook 调用（record_request / record_decision / audit_reference 回填）

注意：本文件不实例化任何真实存储/管理器（治理层测试只用协作件替身），
避免污染 data/ 目录与生产单例。
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.governance import (  # noqa: E402
    AuditWriter,
    DecisionVerdict,
    InMemoryAuditWriter,
    MutationDecision,
    MutationGateway,
    MutationRequest,
)
from src.governance.checks import (  # noqa: E402
    BaseCheck,
    ConflictCheck,
)
from src.governance.mutation_contract import CheckResult  # noqa: E402


# ============================================================
# 构造辅助
# ============================================================
def _accepted_request(**overrides) -> MutationRequest:
    """能通过标准五道检查的合法请求（ACCEPT 基线）。"""
    base = dict(
        mutation_id="mut_test_1",
        source_event={"event_id": "evt_1", "occurrence_count": 3, "confidence": 0.9},
        actor_identity="user",
        target_domain="personality",
        target_path="personality.traits.warmth",
        proposed_change={"before": 0.50, "after": 0.51, "delta": 0.01, "confidence": 0.9},
        evidence=[
            {"type": "user_behavior", "ref": "mem_1", "summary": "行为证据 1"},
            {"type": "user_statement", "ref": "evt_1", "summary": "陈述证据"},
            {"type": "user_behavior", "ref": "mem_2", "summary": "行为证据 2"},
        ],
        context_snapshot={"request_id": "req_1", "trace_id": "trace_1"},
        risk_level="low",
    )
    base.update(overrides)
    return MutationRequest(**base)


class RecordingCheck(BaseCheck):
    """记录调用顺序的替身检查器。"""

    def __init__(self, tag: str, log: list, passed: bool = True, reason: str = "") -> None:
        self.tag = tag
        self.log = log
        self.passed = passed
        self.reason = reason

    def check(self, request):
        self.log.append(self.tag)
        if self.passed:
            return CheckResult(passed=True, reason=f"{self.tag}_ok", metadata={})
        return CheckResult(
            passed=False, reason=self.reason or f"{self.tag}_fail",
            metadata={"verdict": "REJECT"},
        )


class FakeDedupeStore:
    """proposal 存储协作件替身（exists_similar 接口）。"""

    def __init__(self, existing: str | None = None) -> None:
        self.existing = existing

    def exists_similar(self, source_event_id: str, fingerprint: str):
        return self.existing


class FakeJournal:
    """mutation journal 协作件替身（has_mutation 接口）。"""

    def __init__(self, known: set | None = None) -> None:
        self.known = known or set()

    def has_mutation(self, mutation_id: str) -> bool:
        return mutation_id in self.known


# ============================================================
# 1. MutationRequest 序列化
# ============================================================
class TestMutationRequestSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        req = _accepted_request()
        data = req.to_dict()
        assert sorted(data.keys()) == sorted([
            "mutation_id", "source_event", "actor_identity", "target_domain",
            "target_path", "proposed_change", "evidence",
            "context_snapshot", "risk_level",
        ])
        restored = MutationRequest.from_dict(data)
        assert restored == req

    def test_to_json_is_standard_json(self):
        req = _accepted_request()
        parsed = json.loads(req.to_json())
        assert parsed["mutation_id"] == "mut_test_1"
        assert parsed["target_domain"] == "personality"

    def test_from_dict_generates_missing_id(self):
        req = MutationRequest.from_dict(
            {"actor_identity": "user", "target_domain": "memory",
             "target_path": "memory.item.1"}
        )
        assert req.mutation_id.startswith("mut_")

    def test_target_domain_must_be_in_six_domains(self):
        with pytest.raises(ValueError, match="target_domain"):
            _accepted_request(target_domain="personality_evil")

    def test_risk_level_must_be_valid(self):
        with pytest.raises(ValueError, match="risk_level"):
            _accepted_request(risk_level="extreme")


# ============================================================
# 2. MutationDecision 四状态
# ============================================================
class TestMutationDecisionVerdicts:
    @pytest.mark.parametrize("verdict", [
        DecisionVerdict.ACCEPT,
        DecisionVerdict.REJECT,
        DecisionVerdict.NEED_REVIEW,
        DecisionVerdict.DEFER,
    ])
    def test_verdict_serializes_and_roundtrips(self, verdict):
        decision = MutationDecision(mutation_id="mut_1", decision=verdict, reason="r")
        data = decision.to_dict()
        assert data["decision"] == verdict.value
        assert json.loads(decision.to_json())["decision"] == verdict.value
        restored = MutationDecision.from_dict(data)
        assert restored.decision == verdict

    def test_verdict_values_match_contract(self):
        assert {v.value for v in DecisionVerdict} == {
            "ACCEPT", "REJECT", "NEED_REVIEW", "DEFER",
        }

    def test_non_enum_decision_rejected(self):
        with pytest.raises(TypeError):
            MutationDecision(mutation_id="mut_1", decision="APPROVED")  # type: ignore[arg-type]


# ============================================================
# 3. frozen 防修改
# ============================================================
class TestFrozenImmutability:
    def test_request_is_frozen(self):
        req = _accepted_request()
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.target_path = "personality.traits.coldness"  # type: ignore[misc]

    def test_decision_is_frozen(self):
        decision = MutationDecision(
            mutation_id="mut_1", decision=DecisionVerdict.ACCEPT, reason="r",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            decision.reason = "hijack"  # type: ignore[misc]

    def test_request_containers_are_copied(self):
        evidence = [{"type": "user_behavior", "ref": "mem_1"}]
        req = _accepted_request(evidence=evidence)
        evidence.append({"type": "evil", "ref": "mem_2"})
        assert len(req.evidence) == 1


# ============================================================
# 4. 禁止 manager/repository/client 字段
# ============================================================
class TestForbiddenInstanceFields:
    class FakeRepo:
        pass

    def test_evidence_cannot_hold_instances(self):
        with pytest.raises(TypeError, match="JSON"):
            _accepted_request(evidence=[
                {"type": "user_behavior", "ref": "r", "payload": self.FakeRepo()},
            ])

    def test_proposed_change_cannot_hold_instances(self):
        with pytest.raises(TypeError, match="JSON"):
            _accepted_request(proposed_change={"repo": self.FakeRepo()})

    def test_context_snapshot_cannot_hold_instances(self):
        with pytest.raises(TypeError, match="JSON"):
            _accepted_request(context_snapshot={"manager": self.FakeRepo()})

    def test_source_event_cannot_hold_instances(self):
        with pytest.raises(TypeError, match="JSON"):
            _accepted_request(source_event={"payload": self.FakeRepo()})

    def test_check_result_metadata_cannot_hold_instances(self):
        with pytest.raises(TypeError, match="JSON"):
            CheckResult(passed=True, reason="x", metadata={"obj": self.FakeRepo()})


# ============================================================
# 5. 五阶段检查顺序 + 首失败即停
# ============================================================
class TestCheckOrder:
    def test_checks_run_in_fixed_order(self):
        log: list = []
        gateway = MutationGateway(
            identity_check=RecordingCheck("identity", log),
            boundary_check=RecordingCheck("boundary", log),
            evidence_check=RecordingCheck("evidence", log),
            conflict_check=RecordingCheck("conflict", log),
            audit_check=RecordingCheck("audit", log),
        )
        decision = gateway.evaluate(_accepted_request())
        assert log == ["identity", "boundary", "evidence", "conflict", "audit"]
        assert decision.decision == DecisionVerdict.ACCEPT
        assert decision.reason == "all_checks_passed"
        assert list(decision.checks.keys()) == log

    def test_first_failure_stops_pipeline(self):
        log: list = []
        gateway = MutationGateway(
            identity_check=RecordingCheck("identity", log, passed=False, reason="denied"),
            boundary_check=RecordingCheck("boundary", log),
            evidence_check=RecordingCheck("evidence", log),
            conflict_check=RecordingCheck("conflict", log),
            audit_check=RecordingCheck("audit", log),
        )
        decision = gateway.evaluate(_accepted_request())
        assert log == ["identity"]
        assert decision.decision == DecisionVerdict.REJECT
        assert decision.reason == "denied"

    def test_evaluate_requires_mutation_request(self):
        with pytest.raises(TypeError):
            MutationGateway().evaluate({"mutation_id": "mut_x"})  # type: ignore[arg-type]


# ============================================================
# 6. identity 红线拒绝
# ============================================================
class TestIdentityRedLine:
    def test_sandbox_actor_rejected_for_personality(self):
        decision = MutationGateway().evaluate(
            _accepted_request(actor_identity="sandbox")
        )
        assert decision.decision == DecisionVerdict.REJECT
        assert "unauthorized_actor" in decision.reason
        assert decision.checks["identity"]["passed"] is False

    def test_unknown_actor_fail_closed(self):
        decision = MutationGateway().evaluate(
            _accepted_request(actor_identity="mystery_component")
        )
        assert decision.decision == DecisionVerdict.REJECT

    def test_privileged_actor_requires_audit_marker(self):
        decision = MutationGateway().evaluate(
            _accepted_request(actor_identity="admin")
        )
        # admin 通过身份门但必须携带审计标记；链路完整时整体 ACCEPT
        assert decision.checks["identity"]["metadata"]["requires_audit"] is True
        assert decision.decision == DecisionVerdict.ACCEPT

    def test_privileged_actor_without_linkage_defers(self):
        decision = MutationGateway().evaluate(
            _accepted_request(actor_identity="admin", context_snapshot={})
        )
        assert decision.checks["identity"]["metadata"]["requires_audit"] is True
        assert decision.decision == DecisionVerdict.DEFER
        assert "audit_linkage_missing" in decision.reason


# ============================================================
# 7. boundary 拒绝
# ============================================================
class TestBoundaryRedLines:
    def test_identity_anchor_path_rejected(self):
        decision = MutationGateway().evaluate(
            _accepted_request(target_path="identity.core.name")
        )
        assert decision.decision == DecisionVerdict.REJECT
        assert "identity_violation_rejected" in decision.reason

    def test_manifesto_path_rejected(self):
        decision = MutationGateway().evaluate(
            _accepted_request(target_path="manifesto.redline.1")
        )
        assert decision.decision == DecisionVerdict.REJECT

    def test_cross_domain_path_rejected(self):
        decision = MutationGateway().evaluate(
            _accepted_request(
                target_domain="memory", target_path="personality.traits.warmth",
            )
        )
        assert decision.decision == DecisionVerdict.REJECT
        assert "cross_domain_target_forbidden" in decision.reason

    def test_core_memory_delete_rejected(self):
        decision = MutationGateway().evaluate(
            _accepted_request(
                target_domain="memory",
                target_path="memory.core.anchor_1",
                proposed_change={"action": "delete", "delta": 0.0, "confidence": 0.9},
            )
        )
        assert decision.decision == DecisionVerdict.REJECT
        assert "core_memory_delete_forbidden" in decision.reason


# ============================================================
# 8. evidence 不足 → NEED_REVIEW
# ============================================================
class TestEvidenceInsufficient:
    def test_too_few_distinct_refs_needs_review(self):
        decision = MutationGateway().evaluate(
            _accepted_request(evidence=[
                {"type": "user_behavior", "ref": "mem_1"},
                {"type": "user_statement", "ref": "evt_1"},
            ])
        )
        assert decision.decision == DecisionVerdict.NEED_REVIEW
        assert "insufficient_evidence_needs_review" in decision.reason
        assert decision.checks["evidence"]["passed"] is False

    def test_llm_only_evidence_needs_review(self):
        decision = MutationGateway().evaluate(
            _accepted_request(evidence=[
                {"type": "llm_inference", "ref": "llm_1"},
                {"type": "llm_inference", "ref": "llm_2"},
                {"type": "context_guess", "ref": "llm_3"},
            ])
        )
        assert decision.decision == DecisionVerdict.NEED_REVIEW
        assert "llm_evidence_cannot_stand_alone" in decision.reason


# ============================================================
# 9. conflict 检测
# ============================================================
class TestConflictDetection:
    def test_reversal_needs_review_at_check_level(self):
        # 单事件幅度上限（0.01）先于倒转阈值（0.15）生效，
        # 倒转规则在 check 层直接验证（见 implementation_report §3 说明）。
        req = _accepted_request(
            proposed_change={"before": 0.2, "after": 0.8, "delta": 0.005},
        )
        result = ConflictCheck().check(req)
        assert result.passed is False
        assert result.metadata["verdict"] == "NEED_REVIEW"
        assert "conflict_with_existing_trait_reversal" in result.reason

    def test_duplicate_proposal_defers_through_gateway(self):
        gateway = MutationGateway(
            conflict_check=ConflictCheck(proposal_store=FakeDedupeStore(existing="prop_x")),
        )
        decision = gateway.evaluate(_accepted_request())
        assert decision.decision == DecisionVerdict.DEFER
        assert "duplicate_proposal" in decision.reason
        assert decision.checks["conflict"]["metadata"]["existing_id"] == "prop_x"

    def test_annual_limit_defers(self):
        req = _accepted_request(
            proposed_change={"before": 0.5, "after": 0.51, "delta": 0.01},
            context_snapshot={
                "request_id": "req_1", "trace_id": "trace_1", "annual_delta": 0.15,
            },
        )
        result = ConflictCheck().check(req)
        assert result.passed is False
        assert result.metadata["verdict"] == "DEFER"
        assert "annual_limit_exceeded_deferred" in result.reason


# ============================================================
# 10. audit hook 调用
# ============================================================
class TestAuditHook:
    def test_accept_flow_records_request_and_decision(self):
        writer = InMemoryAuditWriter()
        gateway = MutationGateway(audit_writer=writer)
        decision = gateway.evaluate(_accepted_request())

        assert len(writer.requests) == 1
        assert writer.requests[0]["mutation_id"] == "mut_test_1"
        assert len(writer.decisions) == 1
        assert writer.decisions[0]["decision"] == "ACCEPT"
        assert decision.audit_reference
        assert decision.audit_reference.startswith("audit_")

    def test_reject_flow_still_records_decision(self):
        writer = InMemoryAuditWriter()
        gateway = MutationGateway(audit_writer=writer)
        decision = gateway.evaluate(_accepted_request(actor_identity="sandbox"))

        assert decision.decision == DecisionVerdict.REJECT
        assert len(writer.requests) == 1
        assert len(writer.decisions) == 1
        assert writer.decisions[0]["decision"] == "REJECT"

    def test_broken_writer_is_isolated(self):
        class BrokenWriter(AuditWriter):
            def record_request(self, request) -> None:
                raise RuntimeError("boom")

            def record_decision(self, decision):
                raise RuntimeError("boom")

        decision = MutationGateway(audit_writer=BrokenWriter()).evaluate(
            _accepted_request()
        )
        assert decision.decision == DecisionVerdict.ACCEPT
        assert decision.audit_reference == ""

    def test_audit_linkage_missing_defers(self):
        decision = MutationGateway().evaluate(
            _accepted_request(context_snapshot={})
        )
        assert decision.decision == DecisionVerdict.DEFER
        assert "audit_linkage_missing" in decision.reason

    def test_duplicate_mutation_id_rejected_via_journal(self):
        from src.governance.checks import AuditCheck
        gateway = MutationGateway(
            audit_check=AuditCheck(journal=FakeJournal(known={"mut_test_1"})),
        )
        decision = gateway.evaluate(_accepted_request())
        assert decision.decision == DecisionVerdict.REJECT
        assert "duplicate_mutation_id" in decision.reason


# ============================================================
# 附：标准 Gateway 端到端 ACCEPT 基线
# ============================================================
class TestStandardGatewayAcceptBaseline:
    def test_all_checks_pass_yields_accept(self):
        decision = MutationGateway().evaluate(_accepted_request())
        assert decision.decision == DecisionVerdict.ACCEPT
        assert decision.reason == "all_checks_passed"
        assert all(c["passed"] for c in decision.checks.values())
