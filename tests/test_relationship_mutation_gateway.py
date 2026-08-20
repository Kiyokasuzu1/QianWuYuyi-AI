# -*- coding: utf-8 -*-
"""
P2.3-B.9 — Relationship Mutation Boundary Migration 测试

覆盖任务书 Phase 6 八项：
  1. adapter 不直接写状态
  2. Gateway ACCEPT 应用
  3. REJECT 不变化
  4. NEED_REVIEW pending
  5. trust delta 超阈值
  6. identity deny（适配层 sandbox + 运行时身份门）
  7. self_model cross-domain block（adapter 强制复核 + SelfModelManager 开关）
  8. duplicate mutation_id 去重

另加回归锚点：
  - engine allowed_dimensions：None=旧行为字节一致 / 白名单 / 空白名单全拦
  - 运行时治理路径（flag=True）与 legacy 路径（flag=False）对比
  - Phase 5：legacy mutation sources 登记 + mutation_source 元数据

conftest 陷阱规避：本文件不含任何单例扫描正则（不写 RuntimeCore( /
get_runtime_core / RelationshipRepository( 等字面量）；runtime 方法以
未绑定调用方式测试（fake self），不实例化运行时单例。
"""
from __future__ import annotations

import json
import types

import pytest

from src.contracts.relationship_snapshot import (
    LongTermRelationshipState,
    RelationshipSnapshot,
)
from src.personality.self_model_manager import SelfModelManager
from src.relationship.mutation_adapter import (
    LEGACY_MUTATION_SOURCES,
    RelationshipMutationAdapter,
    is_relationship_mutation_gateway_enabled,
    is_relationship_self_model_gateway_enabled,
    set_relationship_mutation_gateway_enabled,
    set_relationship_self_model_gateway_enabled,
)
from src.relationship.relationship_model import (
    RelationshipIntelligenceEngine,
    RelationshipModel,
)
from src.relationship.relationship_state import RelationshipState

# 未绑定运行时方法：避免实例化 RuntimeCore 单例（导入路径文本不含陷阱正则）
from src.runtime.runtime_core import RuntimeCore as RCoreAlias  # noqa: E402


# ============================================================
# 开关复位（每个用例前后强制回到默认 False = 旧行为）
# ============================================================
@pytest.fixture(autouse=True)
def _reset_b9_flags():
    set_relationship_mutation_gateway_enabled(False)
    set_relationship_self_model_gateway_enabled(False)
    yield
    set_relationship_mutation_gateway_enabled(False)
    set_relationship_self_model_gateway_enabled(False)


# ============================================================
# 工具
# ============================================================
def _strong_evidence():
    """3 条去重 memory_link 证据：通过 EvidenceCheck 数量关。"""
    return [
        {"ref": "mem_a", "type": "memory_link"},
        {"ref": "mem_b", "type": "memory_link"},
        {"ref": "mem_c", "type": "memory_link"},
    ]


def _accept_request(adapter: RelationshipMutationAdapter):
    """构造可通过全部五道检查的 relationship 域请求（familiarity，微小增量）。

    ACCEPT 配方（B.9 治理规则推导）：
      - 维度 familiarity（不触 trust/bond 红线）
      - |delta|=0.005 ≤ 0.01（不触单次事件上限）
      - 3 条去重 memory_link 证据 + confidence 0.9 + risk low
      - before=0.4 / after=0.405（不跨越 0.5 中点）
    """
    return adapter.from_relationship_event(
        {
            "id": "rel_evt_accept01",
            "type": "trust_building",
            "content": "我相信你",
            "confidence": 0.9,
            "evidence_ids": ["mem_1"],
        },
        dimension="familiarity",
        delta=0.005,
        before=0.4,
        confidence=0.9,
        evidence=_strong_evidence(),
    )


def _fresh_state_model():
    return RelationshipState(), RelationshipModel()


def _relation_snapshot() -> RelationshipSnapshot:
    return RelationshipSnapshot(
        user_id="366648462",
        preferred_name="清清",
        long_term=LongTermRelationshipState(
            trust=0.6, familiarity=0.5, bond_strength=0.55,
        ),
    )


class _FakeRepo:
    """把运行时持久化重定向到 tmp_path（绝不允许写入 data/）。"""

    def __init__(self, tmp_path):
        self.tmp = tmp_path

    def save_state(self, state) -> None:
        (self.tmp / "rel_state.json").write_text(
            json.dumps(state.to_dict(), ensure_ascii=False), encoding="utf-8"
        )

    def save_relationship_model(self, model) -> None:
        (self.tmp / "rel_model.json").write_text(
            json.dumps(model.to_dict(), ensure_ascii=False), encoding="utf-8"
        )


class _FakeRuntime:
    """承载未绑定 runtime 方法所需的最小表面（不含任何单例）。"""

    def __init__(self, tmp_path):
        self.relationship_intelligence_engine = RelationshipIntelligenceEngine()
        self.relationship_state_runtime = RelationshipState()
        self.relationship_model_runtime = RelationshipModel()
        self.relationship_repository = _FakeRepo(tmp_path)
        self._relationship_mutation_adapter = RelationshipMutationAdapter()
        self.notified = []

    def _get_relationship_mutation_adapter(self):
        return self._relationship_mutation_adapter

    def _forward_relationship_event_to_growth(self, rel_event):
        return "fake_evidence_state"

    def notify_relationship_changed(self, payload) -> None:
        self.notified.append(payload)


# ============================================================
# 1. adapter 不直接写状态
# ============================================================
def test_adapter_does_not_write_state_directly():
    adapter = RelationshipMutationAdapter()
    state = RelationshipState()
    before = state.to_dict()

    request = _accept_request(adapter)
    envelope = adapter.route(request)  # 未注入 apply_route

    assert envelope["decision"] == "ACCEPT"
    assert envelope["applied"] is False
    assert envelope["note"] == "accepted_no_apply_route（禁止自动 apply）"
    # 状态未被 adapter 触碰
    assert state.to_dict() == before
    # adapter 不持有任何持久化写句柄
    assert not hasattr(adapter, "save_state")
    assert not hasattr(adapter, "save_relationship_model")
    assert not hasattr(adapter, "repository")


def test_build_request_rejects_non_relationship_path():
    adapter = RelationshipMutationAdapter()
    with pytest.raises(ValueError):
        adapter.build_request(
            source_event={"event_id": "ev_x", "type": "trust_building",
                          "occurrence_count": 1, "confidence": 0.9},
            target_path="self_model.preferences.relationship.trust_level",
            proposed_change={"path": "self_model.preferences.relationship.trust_level",
                             "dimension": "trust_level", "before": 0.4, "after": 0.5,
                             "delta": 0.1, "confidence": 0.9},
            evidence=_strong_evidence(),
            context_snapshot={"request_id": "r1", "trace_id": "t1"},
            risk_level="low",
        )


# ============================================================
# 2. Gateway ACCEPT 应用
# ============================================================
def test_gateway_accept_applies_via_apply_route():
    adapter = RelationshipMutationAdapter()
    fake_state = {"familiarity": 0.4}
    calls = []

    def apply_route(req, decision):
        calls.append(req.mutation_id)
        fake_state["familiarity"] = req.proposed_change["after"]
        return True

    envelope = adapter.route(_accept_request(adapter), apply_route=apply_route)

    assert envelope["decision"] == "ACCEPT"
    assert envelope["applied"] is True
    assert fake_state["familiarity"] == pytest.approx(0.405)
    assert len(calls) == 1
    assert envelope["request_id"] in adapter.applied_mutation_ids


# ============================================================
# 3. REJECT 不变化
# ============================================================
def test_reject_does_not_change_state():
    adapter = RelationshipMutationAdapter()
    request = adapter.build_request(
        source_event={"event_id": "ev_sandbox", "type": "trust_building",
                      "occurrence_count": 1, "confidence": 0.9},
        target_path="relationship.state.familiarity",
        proposed_change={"path": "relationship.state.familiarity",
                         "dimension": "familiarity", "before": 0.4, "after": 0.405,
                         "delta": 0.005, "confidence": 0.9},
        evidence=_strong_evidence(),
        context_snapshot={"request_id": "req_sandbox", "trace_id": "trace_sandbox"},
        risk_level="low",
        actor_identity="sandbox",
    )
    envelope = adapter.route(request)

    assert envelope["decision"] == "REJECT"
    assert "unauthorized_actor" in envelope["reason"]
    assert envelope["applied"] is False
    # 拒绝同样留审计痕（可追踪）
    assert envelope["audit_reference"]
    assert adapter.pending_proposals == []
    assert adapter.deferred == []


# ============================================================
# 4. NEED_REVIEW pending
# ============================================================
def test_need_review_pends_proposal_with_linkage():
    adapter = RelationshipMutationAdapter()
    request = adapter.from_relationship_event(
        {
            "id": "rel_evt_trust01",
            "type": "trust_building",
            "content": "我相信你",
            "confidence": 0.9,
            "evidence_ids": ["mem_1"],
        },
        dimension="trust",
        delta=0.005,
        before=0.4,
        confidence=0.9,
        evidence=_strong_evidence(),
    )
    envelope = adapter.route(request)

    assert envelope["decision"] == "NEED_REVIEW"
    assert "relationship_trust_bond_requires_review" in envelope["reason"]
    assert envelope["applied"] is False
    assert len(adapter.pending_proposals) == 1
    proposal = adapter.pending_proposals[0]
    assert proposal["status"] == "pending"
    assert proposal["target_domain"] == "relationship"
    assert proposal["target_path"] == "relationship.state.trust"
    for key in ("mutation_id", "request_id", "trace_id", "evidence"):
        assert key in proposal
    assert proposal["proposed_change"]["dimension"] == "trust"


# ============================================================
# 5. trust delta 超阈值
# ============================================================
def test_oversized_delta_needs_review():
    adapter = RelationshipMutationAdapter()
    # familiarity 增量 0.02 > 单次事件上限 0.01 → 证据关 NEED_REVIEW
    request = adapter.from_relationship_event(
        {
            "id": "rel_evt_big01",
            "type": "trust_building",
            "content": "我相信你",
            "confidence": 0.9,
            "evidence_ids": ["mem_1"],
        },
        dimension="familiarity",
        delta=0.02,
        before=0.4,
        confidence=0.9,
        evidence=_strong_evidence(),
    )
    envelope = adapter.route(request)
    assert envelope["decision"] == "NEED_REVIEW"
    assert "delta_oversized" in envelope["reason"]

    # bond 属于红线维度：即使微小增量也必须复核
    bond_request = adapter.from_relationship_event(
        {
            "id": "rel_evt_bond01",
            "type": "trust_building",
            "content": "我相信你",
            "confidence": 0.9,
            "evidence_ids": ["mem_1"],
        },
        dimension="bond",
        delta=0.005,
        before=0.4,
        confidence=0.9,
        evidence=_strong_evidence(),
    )
    bond_envelope = adapter.route(bond_request)
    assert bond_envelope["decision"] == "NEED_REVIEW"
    assert "relationship_trust_bond_requires_review" in bond_envelope["reason"]


# ============================================================
# 6. identity deny（运行时 Stage_03 身份门 + 适配层）
# ============================================================
def test_runtime_identity_gate_denies_unknown_uid():
    gate = RCoreAlias._relationship_update_allowed
    ctx = types.SimpleNamespace(user_id="unknown")
    fake_self = types.SimpleNamespace(config={"memory": {"target_user_id": ""}})
    assert gate(fake_self, ctx) is False


def test_runtime_identity_gate_allows_valid_qq_uid():
    gate = RCoreAlias._relationship_update_allowed
    ctx = types.SimpleNamespace(user_id="366648462")
    fake_self = types.SimpleNamespace(config={"memory": {"target_user_id": ""}})
    assert gate(fake_self, ctx) is True


def test_runtime_identity_gate_legacy_fallback_on_empty_uid():
    gate = RCoreAlias._relationship_update_allowed
    ctx = types.SimpleNamespace(user_id="")
    fake_self = types.SimpleNamespace(config={"memory": {"target_user_id": ""}})
    # 空 uid 保持旧行为（放行，仅日志）
    assert gate(fake_self, ctx) is True


def test_runtime_identity_gate_uses_config_target_uid():
    gate = RCoreAlias._relationship_update_allowed
    ctx = types.SimpleNamespace(user_id="")
    fake_self = types.SimpleNamespace(config={"memory": {"target_user_id": "366648462"}})
    assert gate(fake_self, ctx) is True


# ============================================================
# 7. self_model cross-domain block
# ============================================================
def test_adapter_self_model_influence_forced_review():
    adapter = RelationshipMutationAdapter()
    envelope = adapter.route_self_model_influence("trust_level", 0.8, confidence=0.9)

    assert envelope["decision"] == "NEED_REVIEW"
    assert envelope["applied"] is False
    assert envelope["target_domain"] == "self_model"
    assert envelope["target_path"] == "self_model.preferences.relationship.trust_level"
    assert any(
        p["target_domain"] == "self_model" for p in adapter.pending_proposals
    )


def test_adapter_self_model_influence_accept_is_force_rewritten():
    """跨域强制复核：即使 Gateway 五道全过（ACCEPT），也不得自动写入 self_model。"""
    from src.governance.mutation_contract import DecisionVerdict, MutationDecision

    adapter = RelationshipMutationAdapter()
    adapter.gateway = types.SimpleNamespace(
        evaluate=lambda req: MutationDecision(
            mutation_id=req.mutation_id,
            decision=DecisionVerdict.ACCEPT,
            reason="gateway_ok_stub",
            audit_reference="audit_stub_1",
        )
    )
    envelope = adapter.route_self_model_influence("trust_level", 0.8, confidence=0.9)

    assert envelope["decision"] == "NEED_REVIEW"
    assert envelope["applied"] is False
    assert "cross_domain" in envelope["reason"]
    assert envelope["note"] == "verdict_needs_review_cross_domain_no_state_change"
    cross_proposals = [
        p for p in adapter.pending_proposals if p["target_domain"] == "self_model"
    ]
    assert len(cross_proposals) == 1
    assert cross_proposals[0]["reason"] == "cross_domain_relationship_to_self_model_requires_review"


def test_self_model_flag_off_keeps_legacy_direct_write():
    assert is_relationship_self_model_gateway_enabled() is False
    manager = SelfModelManager()
    manager.refresh(snapshot=_relation_snapshot())
    rel_prefs = [p for p in manager.identity.preferences if p.domain == "relationship"]
    assert {p.key for p in rel_prefs} == {
        "bond_strength", "familiarity_level", "trust_level",
    }


def test_self_model_flag_on_blocks_direct_write():
    set_relationship_self_model_gateway_enabled(True)
    manager = SelfModelManager()
    manager.refresh(snapshot=_relation_snapshot())
    rel_prefs = [p for p in manager.identity.preferences if p.domain == "relationship"]
    # 跨域写入全部进入复核队列，不再直写 SelfIdentity.preferences
    assert rel_prefs == []


# ============================================================
# 8. duplicate mutation_id 去重
# ============================================================
def test_duplicate_mutation_id_defers_and_skips_apply():
    adapter = RelationshipMutationAdapter()
    request = _accept_request(adapter)
    calls = []

    def apply_route(req, decision):
        calls.append(req.mutation_id)
        return True

    first = adapter.route(request, apply_route=apply_route)
    assert first["decision"] == "ACCEPT"
    assert first["applied"] is True

    second = adapter.route(request, apply_route=apply_route)
    assert second["decision"] == "DEFER"
    assert "duplicate_mutation_id" in second["reason"]
    assert second["applied"] is False
    # apply 仅执行一次：同一 mutation_id 不重复应用
    assert len(calls) == 1


# ============================================================
# Phase 5：legacy 登记 + mutation_source 元数据
# ============================================================
def test_phase5_legacy_registry_and_mutation_source_metadata():
    adapter = RelationshipMutationAdapter()
    request = adapter.build_request(
        source_event={"event_id": "ev_p5", "type": "trust_building",
                      "occurrence_count": 1, "confidence": 0.9},
        target_path="relationship.state.familiarity",
        proposed_change={"path": "relationship.state.familiarity",
                         "dimension": "familiarity", "before": 0.4, "after": 0.42,
                         "delta": 0.02, "confidence": 0.9},
        evidence=_strong_evidence(),
        context_snapshot={"request_id": "r_p5", "trace_id": "t_p5"},
        risk_level="low",
        mutation_source="runtime_stage03",
    )
    assert request.context_snapshot["mutation_source"] == "runtime_stage03"

    assert set(LEGACY_MUTATION_SOURCES.keys()) == {"R-04", "R-05"}
    for rid in ("R-04", "R-05"):
        entry = LEGACY_MUTATION_SOURCES[rid]
        assert entry["governance"] == "legacy_fallback_ungoverned"
        assert entry["file"] and entry["function"] and entry["target"]


# ============================================================
# 引擎 allowed_dimensions 回归锚点
# ============================================================
def test_engine_none_whitelist_identical_to_legacy():
    msg, ev = "我相信你", "ev_legacy01"
    state_a, model_a = _fresh_state_model()
    state_b, model_b = _fresh_state_model()
    engine_a, engine_b = RelationshipIntelligenceEngine(), RelationshipIntelligenceEngine()

    engine_a.process_interaction(
        state=state_a, model=model_a, user_message=msg, evidence_id=ev,
    )
    engine_b.process_interaction(
        state=state_b, model=model_b, user_message=msg, evidence_id=ev,
        allowed_dimensions=None,
    )
    # 时间戳（last_interaction_at/updated_at）因两次运行不同，仅比较数值维度
    for attr in ("trust", "familiarity", "collaboration", "interaction_frequency"):
        assert getattr(state_a, attr) == pytest.approx(getattr(state_b, attr))
    assert state_a.relationship_stage == state_b.relationship_stage
    rec_a = model_a.interaction_history[-1]
    rec_b = model_b.interaction_history[-1]
    assert rec_a["trust_delta"] == pytest.approx(rec_b["trust_delta"])


def test_engine_whitelist_applies_only_accepted_dimensions():
    state, model = _fresh_state_model()
    engine = RelationshipIntelligenceEngine()
    result = engine.process_interaction(
        state=state, model=model, user_message="我相信你", evidence_id="ev_wl01",
        allowed_dimensions={"familiarity"},
    )
    # trust_building 计划 (trust 0.06, familiarity 0.02)；仅 familiarity 被放行
    assert state.trust == 0.0
    assert state.familiarity == pytest.approx(0.02)
    assert state.interaction_frequency == 0.0  # 未在白名单 → 不应用
    record = model.interaction_history[-1]
    assert record["trust_delta"] == 0.0
    assert record["familiarity_delta"] == pytest.approx(0.02)
    assert result["interaction"]["trust_delta"] == pytest.approx(0.0)


def test_engine_empty_whitelist_blocks_all_dimensions():
    state, model = _fresh_state_model()
    engine = RelationshipIntelligenceEngine()
    result = engine.process_interaction(
        state=state, model=model, user_message="我相信你", evidence_id="ev_none01",
        allowed_dimensions=set(),
    )
    assert state.trust == 0.0
    assert state.familiarity == 0.0
    assert state.collaboration == 0.0
    assert state.interaction_frequency == 0.0
    # 互动仍然被记录，但增量如实为 0（不允许声称已变更）
    assert len(model.interaction_history) == 1
    record = model.interaction_history[-1]
    assert record["trust_delta"] == 0.0
    assert record["familiarity_delta"] == 0.0
    assert result["interaction"]["trust_delta"] == pytest.approx(0.0)


# ============================================================
# 运行时治理路径（flag=True）vs legacy（flag=False）
# ============================================================
def test_runtime_governed_path_all_needs_review(tmp_path):
    set_relationship_mutation_gateway_enabled(True)
    fake = _FakeRuntime(tmp_path)
    result = RCoreAlias._record_relationship_interaction_governed(
        fake, user_message="我相信你", evidence_id="ev_gov01", emotion_tag="calm",
    )

    assert result is not None
    assert result["mutation"]["mode"] == "governed"
    decisions = result["mutation"]["decisions"]
    # trust → 边界红线；familiarity(0.02) / interaction_frequency(0.03) → 超单次上限
    assert {d["decision"] for d in decisions} == {"NEED_REVIEW"}
    assert result["mutation"]["applied"] == []
    # 状态未被改变（单入口治理：REVIEW 一律不应用）
    assert fake.relationship_state_runtime.trust == 0.0
    assert fake.relationship_state_runtime.familiarity == 0.0
    assert fake.relationship_state_runtime.interaction_frequency == 0.0
    # 记录如实为 0 增量
    record = fake.relationship_model_runtime.interaction_history[-1]
    assert record["trust_delta"] == 0.0
    # pending 提案与持久化 / 通知均正常
    assert len(fake._relationship_mutation_adapter.pending_proposals) == len(decisions)
    assert (tmp_path / "rel_state.json").exists()
    assert (tmp_path / "rel_model.json").exists()
    assert len(fake.notified) == 1


def test_runtime_flag_off_keeps_legacy_behavior(tmp_path):
    assert is_relationship_mutation_gateway_enabled() is False
    fake = _FakeRuntime(tmp_path)
    result = RCoreAlias.record_relationship_interaction(
        fake, user_message="我相信你", evidence_id="ev_legacy01",
    )

    assert result is not None
    assert "mutation" not in result
    # 旧行为：trust_building 直接应用 (trust 0.06, familiarity 0.02, freq 0.13)
    assert fake.relationship_state_runtime.trust == pytest.approx(0.06)
    assert fake.relationship_state_runtime.familiarity == pytest.approx(0.02)
    assert fake.relationship_state_runtime.interaction_frequency == pytest.approx(0.13)
    assert len(fake.notified) == 1
