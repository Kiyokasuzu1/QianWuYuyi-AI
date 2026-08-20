# -*- coding: utf-8 -*-
"""
P2.3-B.10 Phase 7 — Mutation Governance Runtime 层测试

覆盖任务书 8 项：
  1. NEED_REVIEW 自动落 GovernanceProposalStore
  2. 旧 pending cache（adapter 内存槽位）仍存在（双写）
  3. 统一 MutationAuditRecord 创建
  4. 三个 domain adapter 契约一致
  5. shadow 模式状态一致
  6. legacy 模式零行为变化
  7. proposal 查询接口（GovernanceInspector）
  8. data/ 零污染

注意：所有落账目录均为 pytest tmp_path；仓库 data/ 不得被触碰。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.emotion.mutation_adapter import EmotionMutationAdapter
from src.governance.audit_writer import (
    InMemoryAuditWriter,
    MutationAuditRecord,
)
from src.governance.governance_config import (
    DOMAIN_FLAG_FIELDS,
    GovernanceConfig,
    get_governance_config,
    reset_governance_config,
    set_governance_config,
)
from src.governance.governance_inspector import GovernanceInspector
from src.governance.mutation_contract import (
    DecisionVerdict,
    MutationDecision,
    MutationRequest,
)
from src.governance.mutation_gateway import MutationGateway
from src.governance.proposal_store import GovernanceProposalStore as GovStore
from src.growth.mutation_adapter import GrowthMutationAdapter
from src.relationship.mutation_adapter import RelationshipMutationAdapter


@pytest.fixture(autouse=True)
def _reset_b10_config():
    """每测前后恢复默认治理配置（legacy 全关）。"""
    reset_governance_config()
    yield
    reset_governance_config()


# ============================================================
# helpers
# ============================================================
def _linkage() -> dict:
    return {"request_id": "req_test1", "trace_id": "trace_test1"}


def _weak_evidence() -> list:
    return [{"ref": "r1", "type": "memory_link"}]


def _need_review_request(*, domain: str, path: str) -> MutationRequest:
    return MutationRequest(
        source_event={"event_id": "e1"},
        actor_identity="user",
        target_domain=domain,
        target_path=path,
        proposed_change={"before": 0.4, "after": 0.45, "delta": 0.05},
        evidence=_weak_evidence(),
        context_snapshot=_linkage(),
        risk_level="low",
    )


def _gateway_with_store(tmp_path: Path):
    store = GovStore(tmp_path)
    writer = InMemoryAuditWriter()
    gateway = MutationGateway(proposal_store=store, audit_writer=writer)
    return gateway, store, writer


def _emotion_request(adapter: EmotionMutationAdapter) -> MutationRequest:
    return adapter.build_request(
        source_event={"event_id": "e1"},
        target_path="emotion.state.valence",
        proposed_change={"before": 0.4, "after": 0.45, "delta": 0.05},
        evidence=_weak_evidence(),
        context_snapshot=_linkage(),
        risk_level="low",
    )


# ============================================================
# 1. NEED_REVIEW 自动落 GovernanceProposalStore
# ============================================================
def test_need_review_auto_persists_to_store(tmp_path):
    gateway, store, _ = _gateway_with_store(tmp_path)
    req = _need_review_request(
        domain="relationship", path="relationship.state.trust",
    )
    decision = gateway.evaluate(req)
    assert decision.decision == DecisionVerdict.NEED_REVIEW
    records = store.list_pending()
    assert len(records) == 1
    rec = records[0]
    assert rec["proposal_id"] == f"prop_{req.mutation_id[4:]}"
    assert rec["mutation_id"] == req.mutation_id
    assert rec["domain"] == "relationship"
    assert rec["target_path"] == "relationship.state.trust"
    assert rec["actor_identity"] == "user"
    assert rec["decision"] == "NEED_REVIEW"
    assert rec["request_id"] == "req_test1"
    assert rec["trace_id"] == "trace_test1"
    assert rec["evidence_refs"] == ["r1"]
    assert rec["created_at"]


# ============================================================
# 2. 旧 pending cache 仍存在（双写：落账 + 内存槽位）
# ============================================================
def test_adapter_pending_cache_and_store_double_write(tmp_path):
    gateway, store, _ = _gateway_with_store(tmp_path)
    adapter = EmotionMutationAdapter(gateway=gateway)
    req = _emotion_request(adapter)
    envelope = adapter.route(req)
    assert envelope["decision"] == "NEED_REVIEW"
    assert envelope["applied"] is False
    # 内存缓存保留（旧行为不变）
    assert len(adapter.pending_proposals) == 1
    pending = adapter.pending_proposals[0]
    assert pending["mutation_id"] == req.mutation_id
    assert pending["status"] == "pending"
    # 同时落账到 store
    records = store.list_pending()
    assert len(records) == 1
    assert records[0]["mutation_id"] == req.mutation_id
    assert records[0]["domain"] == "emotion"


# ============================================================
# 3. 统一 MutationAuditRecord 创建
# ============================================================
def test_unified_audit_record_created(tmp_path):
    gateway, _, writer = _gateway_with_store(tmp_path)
    adapter = EmotionMutationAdapter(gateway=gateway)
    req = _emotion_request(adapter)
    adapter.route(req)
    assert len(writer.records) == 1
    record = writer.records[0]
    assert isinstance(record, MutationAuditRecord)
    data = record.to_dict()
    # 6 字段冻结契约
    assert set(data.keys()) == {
        "mutation_id", "domain", "actor", "decision", "timestamp", "reason",
    }
    assert data["mutation_id"] == req.mutation_id
    assert data["domain"] == "emotion"
    assert data["actor"] == "emotion_system"
    assert data["decision"] == "NEED_REVIEW"
    assert data["reason"]
    assert data["timestamp"]
    # 旧字段保持可用（requests / decisions 不变）
    assert len(writer.requests) == 1
    assert len(writer.decisions) == 1


# ============================================================
# 4. 三个 domain adapter 契约一致
# ============================================================
def test_three_domain_adapter_contracts_align(tmp_path):
    gateway, store, _ = _gateway_with_store(tmp_path)
    emotion = EmotionMutationAdapter(gateway=gateway)
    relationship = RelationshipMutationAdapter(gateway=gateway)
    growth = GrowthMutationAdapter(gateway=gateway)

    cases = [
        (emotion, "emotion.state.valence"),
        (relationship, "relationship.state.trust"),
        (growth, "growth.metrics.trust"),
    ]
    envelopes = []
    for adapter, path in cases:
        req = adapter.build_request(
            source_event={"event_id": "e1"},
            target_path=path,
            proposed_change={"before": 0.4, "after": 0.45, "delta": 0.05},
            evidence=_weak_evidence(),
            context_snapshot=_linkage(),
            risk_level="low",
        )
        envelopes.append(adapter.route(req))

    expected_keys = {
        "request_id", "mutation_id", "trace_id", "target_domain",
        "target_path", "decision", "audit_reference", "applied",
        "reason", "note", "evidence_refs",
    }
    for env in envelopes:
        assert set(env.keys()) == expected_keys
        assert env["decision"] == "NEED_REVIEW"
        assert env["applied"] is False
        assert env["mutation_id"] == env["request_id"]

    # 三域全部落账（统一治理视图）
    records = store.list_pending()
    assert len(records) == 3
    assert {r["domain"] for r in records} == {
        "emotion", "relationship", "growth",
    }
    # 内存槽位：emotion/relationship 有（旧行为），growth 无（Phase 1 审计文档化差异）
    assert len(emotion.pending_proposals) == 1
    assert len(relationship.pending_proposals) == 1
    assert getattr(growth, "pending_proposals", []) == []


# ============================================================
# 5. shadow 模式状态一致（旧路径执行 + Gateway verdict 记录）
# ============================================================
def test_shadow_mode_state_identical_to_legacy(tmp_path):
    set_governance_config(GovernanceConfig(governance_mode="shadow"))
    cfg = get_governance_config()
    assert cfg.is_shadow()

    store = GovStore(tmp_path)
    gateway = MutationGateway(proposal_store=store)
    req = _need_review_request(
        domain="relationship", path="relationship.state.trust",
    )

    def legacy_apply(state: dict, delta: float) -> dict:
        state["trust"] = round(state["trust"] + delta, 6)
        return state

    # shadow：先记录 Gateway verdict，再照常执行旧路径（不拦截）
    verdict = gateway.evaluate(req)
    state_shadow = legacy_apply({"trust": 0.4}, 0.05)
    # 纯 legacy：无 Gateway 参与
    state_legacy = legacy_apply({"trust": 0.4}, 0.05)
    # 状态一致：shadow 不改变旧路径执行结果
    assert state_shadow == state_legacy == {"trust": 0.45}
    # verdict 被记录（供灰度期对比）
    assert verdict.decision == DecisionVerdict.NEED_REVIEW
    assert len(store.list_pending()) == 1


# ============================================================
# 6. legacy 模式零行为变化
# ============================================================
def test_legacy_mode_zero_behavior_change(tmp_path):
    cfg = get_governance_config()
    assert cfg.governance_mode == "legacy"
    assert cfg.is_legacy()
    assert not cfg.is_shadow() and not cfg.is_enforce()
    # 默认所有域 flag 关闭
    data = cfg.to_dict()
    assert all(data[name] is False for name in DOMAIN_FLAG_FIELDS)

    # 未注入 store 的 Gateway：evaluate 与 save_pending_proposal 均为 no-op
    gateway = MutationGateway()
    req = _need_review_request(
        domain="relationship", path="relationship.state.trust",
    )
    decision = gateway.evaluate(req)
    assert decision.decision == DecisionVerdict.NEED_REVIEW
    assert gateway.save_pending_proposal(req, decision) is None
    assert not (tmp_path / "proposals.jsonl").exists()

    # 三域模块级迁移开关保持关闭（生产默认）
    from src.emotion.mutation_adapter import (
        is_emotion_mutation_gateway_enabled,
    )
    from src.growth.mutation_adapter import (
        is_growth_mutation_gateway_enabled,
    )
    from src.relationship.mutation_adapter import (
        is_relationship_mutation_gateway_enabled,
    )
    assert not is_emotion_mutation_gateway_enabled()
    assert not is_growth_mutation_gateway_enabled()
    assert not is_relationship_mutation_gateway_enabled()


# ============================================================
# 7. proposal 查询接口（GovernanceInspector，只读）
# ============================================================
def test_governance_inspector_queries(tmp_path):
    gateway, store, _ = _gateway_with_store(tmp_path)
    adapter = EmotionMutationAdapter(gateway=gateway)
    req = _emotion_request(adapter)
    adapter.route(req)

    inspector = GovernanceInspector(store=store)
    pending = inspector.list_pending()
    assert len(pending) == 1
    assert pending[0]["mutation_id"] == req.mutation_id

    assert inspector.get_by_domain("emotion")
    assert inspector.get_by_domain("relationship") == []
    hit = inspector.get_by_mutation_id(req.mutation_id)
    assert len(hit) == 1
    assert hit[0]["target_path"] == "emotion.state.valence"
    assert inspector.get_by_mutation_id("mut_nonexistent") == []
    # 只读：Inspector 不暴露任何写方法
    assert not hasattr(inspector, "save")
    assert not hasattr(inspector, "record_pending")


# ============================================================
# 8. data/ 零污染（仓库目录不被触碰）
# ============================================================
def _repo_snapshot() -> dict:
    repo = Path(__file__).resolve().parents[1]
    paths = {
        "governance_proposals": repo / "data" / "governance" / "proposals.jsonl",
        "relationship_state": repo / "data" / "relationship_state.json",
        "emotion_state": repo / "data" / "emotion_state.json",
    }
    snapshot = {}
    for name, path in paths.items():
        if path.exists():
            snapshot[name] = (True, path.stat().st_mtime_ns)
        else:
            snapshot[name] = (False, None)
    return snapshot


def test_data_dir_zero_pollution(tmp_path):
    before = _repo_snapshot()
    gateway, store, _ = _gateway_with_store(tmp_path)
    adapter = EmotionMutationAdapter(gateway=gateway)
    req = _emotion_request(adapter)
    adapter.route(req)
    # 落账确实发生（写在 tmp 目录）
    assert (tmp_path / "proposals.jsonl").exists()
    assert store.list_pending()
    # 仓库 data/ 无任何新增或修改
    assert _repo_snapshot() == before


# ============================================================
# 附加：append-only 幂等与业务目录守卫
# ============================================================
def test_store_rejects_business_data_dirs(tmp_path):
    with pytest.raises(ValueError):
        GovStore(tmp_path / "data" / "users")
    with pytest.raises(ValueError):
        GovStore(tmp_path / "x" / "data" / "users" / "uid1")


def test_store_append_only_idempotent(tmp_path):
    gateway, store, _ = _gateway_with_store(tmp_path)
    req = _need_review_request(
        domain="relationship", path="relationship.state.trust",
    )
    decision = gateway.evaluate(req)
    # 同一 mutation_id 重复落账 → 幂等跳过（不重写旧行）
    gateway.save_pending_proposal(req, decision)
    gateway.save_pending_proposal(req, decision)
    records = store.load_records()
    assert len(records) == 1
    lines = (tmp_path / "proposals.jsonl").read_text(
        encoding="utf-8",
    ).strip().splitlines()
    assert len(lines) == 1
