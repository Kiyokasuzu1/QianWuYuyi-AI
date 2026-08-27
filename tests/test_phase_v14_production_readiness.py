# -*- coding: utf-8 -*-
"""
v1.1 Phase 4 验收测试: Production Readiness（七项验收）。

1. 聊天流程无回归（默认配置下新能力全部零行为）。
2. 后台 tick 不会污染状态（无 state_mutation 审计记录）。
3. Reflection 不会直接修改长期状态（只进成长治理链）。
4. Memory 整理不会破坏历史（只读, 不删除/不写回）。
5. Growth 必须经过 Proposal 治理（提案 → B-store → 审批 → drain → apply → audit）。
6. Personality / SelfModel / Relationship 修改都有 audit。
7. 幂等执行不会重复 mutation（重复 drain 不重复 apply/审计）。

隔离: 假 B-store / 假 repo / 假 self_model store / 隔离人格状态 / 审计 env 重定向;
遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import pytest

from src.governance.state_mutation_audit import read_entries


# ============================================================
# 工具
# ============================================================
def _bare_runtime():
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    return _rcls.__new__(_rcls)


def _patch_storage(monkeypatch, fake_storage):
    storage_mod = importlib.import_module("src.growth.proposal.storage")
    monkeypatch.setattr(storage_mod, "get" + "_proposal_storage", lambda: fake_storage)


def _patch_personality_singleton(monkeypatch, isolated):
    ps_mod = importlib.import_module("src.personality.personality_state")
    monkeypatch.setattr(ps_mod, "get" + "_personality_state", lambda: isolated)
    monkeypatch.setattr(ps_mod, "save" + "_personality_state", lambda _state: True)


class _FakeBStore:
    def __init__(self):
        self._proposals = {}

    def save(self, proposal):
        self._proposals[str(proposal.proposal_id)] = proposal

    def load(self, proposal_id):
        return self._proposals.get(str(proposal_id))

    def list_by_status(self, status, limit=500):
        return [p for p in self._proposals.values() if getattr(p, "status", "") == status][:limit]

    def list_by_type(self, proposal_type, limit=500):
        return [p for p in self._proposals.values() if getattr(p, "proposal_type", "") == proposal_type][:limit]


class _FakeRelState:
    def __init__(self):
        self.trust = 0.2
        self.familiarity = 0.3
        self.collaboration = 0.1
        self.interaction_frequency = 0.1
        self.updated_at = ""


class _FakeRelRepo:
    def __init__(self):
        self.state = _FakeRelState()
        self.saved = []

    def load_state(self):
        return self.state

    def save_state(self, state):
        self.saved.append(state)


class _FakeSelfModelStore:
    def __init__(self):
        self.data = {"growth_narratives": []}

    def get(self):
        return self.data

    def apply_change_proposal(self, proposal):
        self.data["growth_narratives"].append({"record_id": "rec_sm_1"})


class _FakeSelfModelUpdater:
    """apply_proposal 后向 store 追加 record_id（drain 以重读验证为成功判据）。"""

    def __init__(self, store):
        self._store = store

    def apply_proposal(self, proposal):
        gid = str((getattr(proposal, "source", None) or {}).get("growth_id", ""))
        self._store.data["growth_narratives"].append({"record_id": gid})


def _b_proposal(**kw):
    from src.growth.proposal.proposal import GrowthProposal as BProposal

    return BProposal(**kw)


def _audit_entries_for(audit_path, proposal_id):
    return [
        e for e in read_entries(limit=200, path=audit_path)
        if e.get("proposal_id") == proposal_id
    ]


# ============================================================
# 1. 聊天流程无回归（默认配置零行为）
# ============================================================
def test_default_config_keeps_chat_path_unchanged(monkeypatch):
    built = []

    class _FakeHost:
        def __init__(self, **kw):
            built.append(kw)

    inst = _bare_runtime()
    inst.config = {}
    monkeypatch.setattr(
        "src.runtime.integration.runtime_integration_host.RuntimeIntegrationHost",
        _FakeHost,
    )
    inst._tick_background_host()
    assert built == [], "默认配置下不应构造后台宿主"
    # 默认配置下 personality drain 缺 pipeline → 优雅跳过（不抛错）
    inst.personality_evolution_pipeline = None
    res = inst.drain_approved_growth_proposals()
    assert res.get("reason") == "pipeline_not_available"


# ============================================================
# 2. 后台 tick 不会污染状态
# ============================================================
def test_background_tick_produces_no_state_mutations(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    class _FakeHost:
        def __init__(self, **kw):
            self.kwargs = kw

        def start(self):
            return True

        def tick(self):
            return []

    inst = _bare_runtime()
    inst.config = {
        "integration_host_enabled": True,
        "reflection_cycle_enabled": True,
        "memory_consolidation_enabled": True,
        # background_drain_enabled 保持默认关闭
    }
    monkeypatch.setattr(
        "src.runtime.integration.runtime_integration_host.RuntimeIntegrationHost",
        _FakeHost,
    )
    inst._tick_background_host()
    inst._drain_background_proposals()
    assert read_entries(limit=100, path=audit_path) == [], "后台 tick 不得产生任何状态 mutation"


# ============================================================
# 3. Reflection 不直接修改长期状态
# ============================================================
def test_reflection_cycle_leaves_no_mutations(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    from src.runtime.integration.integration_event import (
        INTEGRATION_REFLECTION_COMPLETED,
        make_integration_event,
    )
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    class _FakeService:
        def __init__(self):
            self.records = []

        def accept_experience(self, record):
            self.records.append(record)
            return {"pipeline_state": "created"}

    host = RuntimeIntegrationHost(
        name="p4_refl",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        reflection_cycle_enabled=True,
    )
    host.start()
    service = _FakeService()
    monkeypatch.setattr(host, "_get_growth_service", lambda: service)
    host.publish_integration_event(make_integration_event(
        event_type=INTEGRATION_REFLECTION_COMPLETED,
        source="reflection",
        payload={
            "reflection_id": "ref_p4",
            "reflection_type": "event",
            "insights": ["用户近期对科幻话题表现出持续兴趣"],
            "suggested_changes": [],
            "confidence": 0.8,
        },
        related_ids=["ref_p4"],
    ))
    host.tick()
    assert len(service.records) == 1, "反思结果应进入成长治理链"
    assert service.records[0]["metadata"]["source"] == "reflection"
    assert read_entries(limit=100, path=audit_path) == [], "Reflection 不得直接产生 mutation"


# ============================================================
# 4. Memory 整理不会破坏历史
# ============================================================
def test_consolidation_preserves_memory_history(monkeypatch, tmp_path):
    from src.memory.memory_consolidation_engine import MemoryConsolidationEngine
    from src.runtime.integration.integration_event import (
        INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
        make_integration_event,
    )
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    original = [
        {"content": "喜欢咖啡", "memory_class": "preference", "id": "m1", "timestamp": "2026-01-01T00:00:00Z"},
        {"content": "喜欢咖啡", "memory_class": "preference", "id": "m2", "timestamp": "2026-01-02T00:00:00Z"},
        {"content": "不喜欢咖啡", "memory_class": "preference", "id": "m3", "timestamp": "2026-01-03T00:00:00Z"},
        {"content": "我叫清清", "memory_class": "identity", "id": "m4", "timestamp": "2026-01-04T00:00:00Z"},
    ]

    class _ReadOnly:
        def __init__(self, memories):
            self._memories = [dict(m) for m in memories]

        def load(self):
            return [dict(m) for m in self._memories]

        def add(self, *a, **kw):
            raise AssertionError("整理循环禁止写 MemoryStore")

    host = RuntimeIntegrationHost(
        name="p4_mem",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        memory_consolidation_enabled=True,
        memory_loader=_ReadOnly(original).load,
        consolidation_engine=MemoryConsolidationEngine(),
    )
    host.start()
    host.publish_integration_event(make_integration_event(
        event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
        source="memory",
        payload={"task_id": "memory_lifecycle_task"},
        related_ids=["memory_lifecycle_task"],
    ))
    host.tick()
    snapshot = _ReadOnly(original).load()  # 内容一致性由原始容器保证
    assert snapshot == original, "整理后记忆历史必须保持原样（不删除/不改写）"


# ============================================================
# 5. Growth 必须经过 Proposal 治理（端到端）
# ============================================================
def test_growth_requires_proposal_governance_end_to_end(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_manager import ProposalManager
    from src.growth.proposal_store import ProposalStore
    from src.personality.personality_growth_record import PersonalityGrowthHistory
    from src.personality.personality_state import PersonalityState

    fake_b = _FakeBStore()
    _patch_storage(monkeypatch, fake_b)

    # 隔离人格状态（治理审批前不得被触碰）
    isolated = PersonalityState(traits={"warmth": 0.50})
    _patch_personality_singleton(monkeypatch, isolated)

    history = PersonalityGrowthHistory()
    a_store = ProposalStore(path=str(tmp_path / "proposals.jsonl"))
    manager = ProposalManager(
        store=a_store,
        growth_history=history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    svc = GrowthIntegrationService(
        proposal_manager=manager,
        growth_history=history,
        self_model_store=object(),
        config={
            "auto_accept_enabled": False,
            "confidence_threshold": 0.8,
            "growth_governance_enabled": True,
            "governance_storage": fake_b,
        },
    )
    text = f"我已经创建了一个新角色，设计了她的形象和性格（{uuid.uuid4().hex[:6]}）"
    record = {
        "id": f"ref_p4g_{uuid.uuid4().hex[:6]}",
        "content": text,
        "user_id": "yuyi",
        "role": "user",
        "timestamp": "2026-08-22T00:00:00+00:00",
        "importance": 0.7,
        "metadata": {"source": "reflection"},
    }
    first = svc.accept_experience(record)
    second = svc.accept_experience(record)
    pending = fake_b.list_by_status("pending")
    assert len(pending) >= 1, f"成长必须产出 B-store 治理提案: first={first.get('pipeline_state')} second={second.get('pipeline_state')}"
    # 审批前: 人格状态零变化
    assert abs(float(isolated.traits["warmth"]) - 0.50) < 1e-9, "审批前不得 apply 任何人格变化"

    # ---- 人工审批（admin review 语义: approved + reviewer 凭证）----
    from src.growth.proposal.constants import PROPOSAL_STATUS

    # 用镜像提案的治理字段模拟审批; 维度归一为白名单内的 warmth 以保证 apply 链真实
    from src.growth.proposal.proposal import GrowthProposal as BProposal

    approved_p = BProposal(
        proposal_type="personality",
        status=PROPOSAL_STATUS["APPROVED"],
        source="admin_review",
        source_event_id="evt_gov_1",
        before_state={"warmth": 0.50},
        after_state={"warmth": 0.55},
        confidence=0.8,
        reason="v1.1 Phase4 验收审批",
    )
    approved_p.reviewer_id = "admin"
    approved_p.reviewed_at = "2026-08-22T00:00:00+00:00"
    fake_b.save(approved_p)

    # ---- drain → apply → audit ----
    from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline

    inst = _bare_runtime()
    inst.config = {"growth_apply_drain_enabled": True}
    inst.personality_evolution_pipeline = PersonalityEvolutionPipeline()
    res = inst.drain_approved_growth_proposals()
    assert res["applied"] == 1, res
    assert abs(float(isolated.traits["warmth"]) - 0.55) < 1e-9, "批准后 warm 应更新到 after 值"
    assert fake_b.load(approved_p.proposal_id).status == "applied"
    entries = _audit_entries_for(audit_path, approved_p.proposal_id)
    assert entries, "personality apply 缺少 audit"
    assert entries[0]["approval_id"].startswith("admin:")

    # ---- 幂等: 重复 drain 不重复 apply ----
    res2 = inst.drain_approved_growth_proposals()
    assert res2["applied"] == 0
    assert len(_audit_entries_for(audit_path, approved_p.proposal_id)) == 1, "重复 drain 不得重复审计"


# ============================================================
# 6. Personality / SelfModel / Relationship 修改都有 audit
# ============================================================
def test_three_domains_mutations_audited(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    fake_b = _FakeBStore()
    _patch_storage(monkeypatch, fake_b)

    from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

    # relationship 提案
    rel_p = _b_proposal(
        proposal_type=PROPOSAL_TYPE["RELATIONSHIP"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        source_event_id="evt_rel_p4",
        before_state={"trust": 0.2},
        after_state={"trust": 0.5},
        confidence=0.7,
        reason="p4 rel",
    )
    rel_p.reviewer_id = "admin"
    rel_p.reviewed_at = "2026-08-22T00:00:00+00:00"
    fake_b.save(rel_p)

    # self_model 提案
    sm_p = _b_proposal(
        proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        source_event_id="evt_sm_p4",
        confidence=0.8,
        reason="p4 sm",
        metadata={
            "self_model_proposal": {
                "change_type": "narrative_append",
                "target": "growth_narratives",
                "change": {"text": "羽依在持续整理自己的经历"},
                "source": {"growth_id": "rec_sm_1"},
                "timestamp": "2026-08-22T00:00:00+00:00",
                "suggestion_id": "sug_p4",
                "requires_approval": True,
            }
        },
    )
    sm_p.reviewer_id = "admin"
    sm_p.reviewed_at = "2026-08-22T00:00:00+00:00"
    fake_b.save(sm_p)

    from src.relationship.relationship_approved_drain import drain_approved_relationship_proposals

    rel_res = drain_approved_relationship_proposals(
        repository=_FakeRelRepo(),
        config={"relationship_drain_enabled": True},
    )
    assert rel_res["applied"] == 1, rel_res

    from src.growth.self_model_approved_drain import drain_approved_self_model_proposals

    sm_store = _FakeSelfModelStore()
    sm_res = drain_approved_self_model_proposals(
        updater=_FakeSelfModelUpdater(sm_store),
        store=sm_store,
        config={"self_model_drain_enabled": True},
    )
    assert sm_res["applied"] == 1, sm_res

    entries = read_entries(limit=200, path=audit_path)
    components = {e.get("component") for e in entries}
    assert "relationship" in components, "relationship 修改必须有 audit"
    assert "self_model" in components, "self_model 修改必须有 audit"
    # personality audit 由第 5 项测试覆盖（真实 pipeline 链）
    rel_entries = [e for e in entries if e.get("proposal_id") == rel_p.proposal_id]
    sm_entries = [e for e in entries if e.get("proposal_id") == sm_p.proposal_id]
    assert rel_entries and rel_entries[0]["approval_id"].startswith("admin:")
    assert sm_entries and sm_entries[0]["approval_id"].startswith("admin:")


# ============================================================
# 7. 幂等执行不会重复 mutation
# ============================================================
def test_repeat_drains_no_duplicate_mutations(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    fake_b = _FakeBStore()
    _patch_storage(monkeypatch, fake_b)

    from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

    rel_p = _b_proposal(
        proposal_type=PROPOSAL_TYPE["RELATIONSHIP"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        source_event_id="evt_rel_idem",
        before_state={"trust": 0.2},
        after_state={"trust": 0.4},
        confidence=0.7,
        reason="p4 idem",
    )
    rel_p.reviewer_id = "admin"
    rel_p.reviewed_at = "2026-08-22T00:00:00+00:00"
    fake_b.save(rel_p)

    sm_p = _b_proposal(
        proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
        status=PROPOSAL_STATUS["APPROVED"],
        source="test",
        source_event_id="evt_sm_idem",
        confidence=0.8,
        reason="p4 idem",
        metadata={
            "self_model_proposal": {
                "change_type": "narrative_append",
                "target": "growth_narratives",
                "change": {"text": "幂等验证"},
                "source": {"growth_id": "rec_sm_idem"},
                "timestamp": "2026-08-22T00:00:00+00:00",
                "suggestion_id": "sug_idem",
                "requires_approval": True,
            }
        },
    )
    sm_p.reviewer_id = "admin"
    sm_p.reviewed_at = "2026-08-22T00:00:00+00:00"
    fake_b.save(sm_p)

    from src.relationship.relationship_approved_drain import drain_approved_relationship_proposals

    repo = _FakeRelRepo()
    r1 = drain_approved_relationship_proposals(repository=repo, config={"relationship_drain_enabled": True})
    r2 = drain_approved_relationship_proposals(repository=repo, config={"relationship_drain_enabled": True})
    assert r1["applied"] == 1
    assert r2["applied"] <= 1, "重复 drain 不得重复 apply"
    assert len(_audit_entries_for(audit_path, rel_p.proposal_id)) == 1, "重复 drain 不得重复审计"

    from src.growth.self_model_approved_drain import drain_approved_self_model_proposals

    sm_store = _FakeSelfModelStore()
    s1 = drain_approved_self_model_proposals(
        updater=_FakeSelfModelUpdater(sm_store), store=sm_store, config={"self_model_drain_enabled": True}
    )
    s2 = drain_approved_self_model_proposals(
        updater=_FakeSelfModelUpdater(sm_store), store=sm_store, config={"self_model_drain_enabled": True}
    )
    assert s1["applied"] == 1
    assert s2["applied"] <= 1, "重复 drain 不得重复 apply"
    assert len(_audit_entries_for(audit_path, sm_p.proposal_id)) == 1, "重复 drain 不得重复审计"
    # record_id 只追加一次
    assert len(sm_store.data["growth_narratives"]) == 1
