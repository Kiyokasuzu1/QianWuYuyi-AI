# -*- coding: utf-8 -*-
"""
P2.3-B.4 — 人格 mutation 治理迁移层测试

覆盖 B.4 Phase 6 任务书 7 项：
1. Resolver 不直接修改 trait（迁移模式）
2. Resolver 可以生成 MutationRequest
3. Gateway reject 不改变状态
4. Gateway accept 才允许进入 apply adapter
5. 伪 approval ID 被拒绝
6. identity path 永远拒绝
7. audit 正常记录（拒绝也可追踪）

附加：
8. 迁移开关默认关闭 → 旧路径行为不变（渐进迁移保证）
9. RuntimeController 迁移模式下不再伪造 p_rt_*/a_rt_* 直写

注：本文件刻意使用 import 别名（PR / PS / RC），避免触发 conftest
单例陷阱（_SINGLETON_PATTERN 扫描的是裸构造字面量，如 PersonalityResolver(）。
"""
from __future__ import annotations

import pytest

from src.governance.mutation_contract import MutationRequest
from src.personality.mutation_adapter import (
    PersonalityMutationAdapter,
    is_forged_approval_id,
    is_personality_mutation_gateway_enabled,
    set_personality_mutation_gateway_enabled,
)
from src.personality.personality_resolver import PersonalityResolver as PR
from src.personality.personality_state import PersonalityState as PS
from src.runtime.runtime_controller import RuntimeController as RC


@pytest.fixture(autouse=True)
def _gateway_flag_off():
    """每个测试后恢复迁移开关默认关闭，避免状态泄漏。"""
    yield
    set_personality_mutation_gateway_enabled(False)


def _drift_record() -> dict:
    """一条足以让 warmth 产生 >0.005 漂移的成长记录。"""
    return {
        "affected_dimensions": {"warmth": 0.5},
        "confidence": 0.5,
        "source_type": "preference",
        "created_at": "",
    }


def _accepted_request(**overrides) -> MutationRequest:
    """五道检查全过的标准请求（与 B.3 契约测试同构）。"""
    fields = dict(
        mutation_id="mut_b4_acc_001",
        source_event={
            "type": "user_behavior",
            "event_id": "ev_b4_001",
            "occurrence_count": 3,
        },
        actor_identity="user",
        target_domain="personality",
        target_path="personality.traits.warmth",
        proposed_change={
            "path": "personality.traits.warmth",
            "before": 0.50,
            "after": 0.51,
            "delta": 0.01,
            "confidence": 0.9,
        },
        evidence=[
            {"ref": "user_behavior_1", "type": "user_behavior"},
            {"ref": "user_behavior_2", "type": "user_behavior"},
            {"ref": "user_statement_1", "type": "user_statement"},
        ],
        context_snapshot={"request_id": "req_1", "trace_id": "trace_1"},
        risk_level="low",
    )
    fields.update(overrides)
    return MutationRequest(**fields)


# ============================================================
# 1. Resolver 迁移模式：不直接修改 trait
# ============================================================
def test_resolver_does_not_mutate_traits_when_gateway_enabled():
    set_personality_mutation_gateway_enabled(True)
    resolver = PR(growth_records=[_drift_record()])
    resolver.resolve()
    resolver.resolve()  # 第二次 resolve 同样不得累积漂移
    warmth_state = resolver.get_trait_states()["warmth"]
    assert warmth_state["current_value"] == pytest.approx(0.7)
    assert resolver.mutation_requests, "迁移模式应发出 MutationRequest 而非静默直写"


# ============================================================
# 2. Resolver 可以生成 MutationRequest
# ============================================================
def test_resolver_emits_mutation_request_when_gateway_enabled():
    set_personality_mutation_gateway_enabled(True)
    resolver = PR(growth_records=[_drift_record()])
    resolver.resolve()

    trait_requests = [
        r for r in resolver.mutation_requests
        if r.target_domain == "personality"
    ]
    assert trait_requests, "应生成 personality 域 MutationRequest"
    req = trait_requests[0]
    assert isinstance(req, MutationRequest)
    assert req.target_path == "personality.traits.warmth"
    assert req.proposed_change["path"] == "personality.traits.warmth"
    assert "before" in req.proposed_change and "after" in req.proposed_change

    # 裁决已留痕且未 apply（证据不足 → 未落状态）
    outcomes = resolver._mutation_adapter.outcomes
    assert outcomes
    assert outcomes[-1]["applied"] is False


# ============================================================
# 3. Gateway reject 不改变状态
# ============================================================
def test_gateway_reject_does_not_change_state():
    adapter = PersonalityMutationAdapter()
    calls = []

    def apply_route(req, dec):
        calls.append(dec)
        return True

    outcome = adapter.route(
        _accepted_request(actor_identity="sandbox"),
        apply_route=apply_route,
    )
    assert outcome["decision"] == "REJECT"
    assert outcome["applied"] is False
    assert calls == []


# ============================================================
# 4. Gateway accept 才允许进入 apply adapter
# ============================================================
def test_gateway_accept_allows_apply_adapter_only_on_accept():
    adapter = PersonalityMutationAdapter()
    calls = []

    def apply_route(req, dec):
        calls.append((req.mutation_id, dec.decision.value))
        return True

    # ACCEPT → apply adapter 被调用
    outcome = adapter.route(_accepted_request(), apply_route=apply_route)
    assert outcome["decision"] == "ACCEPT"
    assert outcome["applied"] is True
    assert len(calls) == 1

    # 证据不足 → NEED_REVIEW → 不进入 apply adapter
    weak = _accepted_request(
        evidence=[{"ref": "only_one", "type": "user_behavior"}],
    )
    outcome2 = adapter.route(weak, apply_route=apply_route)
    assert outcome2["decision"] == "NEED_REVIEW"
    assert outcome2["applied"] is False
    assert len(calls) == 1

    # ACCEPT 但未注入 apply_route → 不自动 apply
    outcome3 = adapter.route(_accepted_request())
    assert outcome3["decision"] == "ACCEPT"
    assert outcome3["applied"] is False
    assert outcome3["note"] == "accepted_no_apply_route（禁止自动 apply）"


# ============================================================
# 5. 伪 approval ID 被拒绝
# ============================================================
def test_forged_approval_id_rejected():
    assert is_forged_approval_id("p_rt_abc") is True
    assert is_forged_approval_id("a_rt_abc") is True
    assert is_forged_approval_id("p_real_1") is False
    assert is_forged_approval_id("") is False
    assert is_forged_approval_id(None) is False

    adapter = PersonalityMutationAdapter()

    # build_request 直接拒绝伪造 ID
    with pytest.raises(ValueError):
        adapter.build_request(
            source_event={"type": "chat_turn_delta", "approval_id": "a_rt_x"},
            actor_identity="user",
            target_path="personality.traits",
            proposed_change={"path": "personality.traits"},
            evidence=[],
        )
    with pytest.raises(ValueError):
        adapter.build_request(
            source_event={"type": "chat_turn_delta"},
            actor_identity="user",
            target_path="personality.traits",
            proposed_change={"path": "personality.traits"},
            evidence=[],
            context_snapshot={"proposal_id": "p_rt_y"},
        )

    # 绕过 build_request 手工构造的请求 → route 直接 REJECT，不进入放行逻辑
    forged_request = _accepted_request(
        context_snapshot={
            "request_id": "req_1",
            "trace_id": "trace_1",
            "approval_id": "a_rt_x",
        },
    )
    calls = []
    outcome = adapter.route(
        forged_request,
        apply_route=lambda req, dec: calls.append(dec) or True,
    )
    assert outcome["decision"] == "REJECT"
    assert outcome["applied"] is False
    assert calls == []
    assert "forged" in outcome["reason"]


# ============================================================
# 6. identity path 永远拒绝
# ============================================================
def test_identity_path_forever_rejected():
    adapter = PersonalityMutationAdapter()
    request = _accepted_request(
        target_path="identity.core.name",
        proposed_change={
            "path": "identity.core.name",
            "before": "羽依",
            "after": "别的名字",
            "delta": 1,
            "confidence": 0.9,
        },
    )
    outcome = adapter.route(request)
    assert outcome["decision"] == "REJECT"
    assert outcome["applied"] is False


# ============================================================
# 7. audit 正常记录（拒绝也可追踪）
# ============================================================
def test_audit_records_requests_and_decisions_including_rejections():
    adapter = PersonalityMutationAdapter()
    adapter.route(_accepted_request())
    rej_outcome = adapter.route(
        _accepted_request(mutation_id="mut_b4_rej_001", actor_identity="sandbox"),
    )

    trace = adapter.audit_trace()
    assert len(trace["requests"]) == 2
    assert len(trace["decisions"]) == 2

    assert rej_outcome["decision"] == "REJECT"
    assert rej_outcome["audit_reference"], "拒绝请求也必须有审计引用"
    assert trace["decisions"][1]["decision"] == "REJECT"
    assert trace["decisions"][1]["mutation_id"] == "mut_b4_rej_001"

    acc = adapter.outcomes[0]
    assert acc["request_id"] == "mut_b4_acc_001"
    assert acc["decision"] == "ACCEPT"
    assert acc["audit_reference"], "ACCEPT 也必须有审计引用"


# ============================================================
# 8. 迁移开关默认关闭 → 旧路径行为不变
# ============================================================
def test_resolver_legacy_behavior_unchanged_when_gateway_disabled():
    assert is_personality_mutation_gateway_enabled() is False
    resolver = PR(growth_records=[_drift_record()])
    resolver.resolve()
    warmth_state = resolver.get_trait_states()["warmth"]
    # 旧路径：漂移被直接提交（约 0.7 → 0.7108）
    assert warmth_state["current_value"] == pytest.approx(0.7108, abs=1e-3)
    assert resolver.mutation_requests == []


# ============================================================
# 9. RuntimeController 迁移模式：不再伪造 p_rt_*/a_rt_* 直写
# ============================================================
def test_runtime_controller_gateway_mode_no_forged_apply():
    set_personality_mutation_gateway_enabled(True)
    ctrl = RC(config={"runtime": {"growth_enabled": True}})
    ps = PS()
    version_before = ps.version

    ctrl._apply_delta_safely(ps, {"creativity": 0.01}, turn_uuid="turn_b4_001")

    assert ps.version == version_before, "迁移模式不得直写 PersonalityState"
    outcome = ctrl._mutation_adapter.outcomes[-1]
    assert outcome["request_id"]
    assert outcome["decision"] == "NEED_REVIEW"
    assert outcome["applied"] is False
    assert outcome["audit_reference"]

    # 旧路径（开关关闭）依然可用且直写（渐进迁移，不删除旧行为）
    set_personality_mutation_gateway_enabled(False)
    ctrl2 = RC(config={"runtime": {"growth_enabled": True}})
    ps2 = PS()
    ctrl2._apply_delta_safely(ps2, {"creativity": 0.01}, turn_uuid="turn_b4_002")
    assert ps2.version == 1
