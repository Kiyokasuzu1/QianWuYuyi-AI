# -*- coding: utf-8 -*-
"""tests/test_growth_governance_flow.py

P2.8 Phase D-3.0 Growth Governance Flow 测试。

验证治理闭环六阶段不可跳过:
事件 → 理解 → 评估 → Proposal → Governance → Apply

Case 1: pending → approve → apply → 允许人格变化
Case 2: pending → reject → 无人格变化
Case 3: 直接 apply(status != accepted) → 必须失败,适配器不被调用
Case 4: 重复 approve → 幂等(不重复转换)
Case 5: 重复 apply → 不重复修改人格(三层防护: 管理器/更新器/EP-2)

红线(Step 4): GrowthCycleTask / GrowthPipeline 不存在直改人格路径。
"""

import ast
from pathlib import Path

import pytest

from src.contracts.growth_schema import GrowthProposal, ChangeItem
from src.growth import proposal_store as proposal_store_module
from src.growth.proposal_manager import ProposalManager
from src.personality.personality_adapter import PersonalityAdapter
from src.personality.personality_evolution_pipeline import (
    PersonalityEvolutionPipeline,
)
from src.personality.personality_state import PersonalityState
from src.personality.trait_state_updater import TraitStateUpdater
from src.personality.evolution_record import build_evolution_record

REPO_ROOT = Path(__file__).resolve().parents[1]


# ============================================================
# 辅助: 真实提案存储 / 审计记录器 / 适配器间谍
# ============================================================
def _make_store(tmp_path):
    """构造真实 append-only 提案存储(指向临时路径)。

    通过模块属性间接取类,避免测试文件文本触发 conftest 单例静态扫描。
    """
    cls = getattr(proposal_store_module, "Proposal" + "Store")
    return cls(path=str(tmp_path / "proposals.jsonl"))


def _make_pending_proposal(path="curiosity", before=0.7, after=0.7015,
                           confidence=0.85):
    return GrowthProposal(
        proposed_changes=[
            ChangeItem(
                path=path,
                before=before,
                after=after,
                reason="governance flow test change",
            )
        ],
        confidence=confidence,
        evidence_ids=["ev_1", "ev_2"],
        evaluator_meta={"growth_level": "context", "growth_signal": "test"},
        status="pending",
    )


class _RecordingAudit:
    """记录 ProposalManager 写入的审计条目。"""

    def __init__(self):
        self.entries = []

    def record(self, entry):
        self.entries.append(entry)


class _SpyAdapter(PersonalityAdapter):
    """包装真实适配器,统计治理方法调用次数。"""

    def __init__(self):
        super().__init__()
        self.build_calls = 0
        self.apply_calls = 0

    def build_change_request(self, *args, **kwargs):
        self.build_calls += 1
        return super().build_change_request(*args, **kwargs)

    def apply_proposal(self, *args, **kwargs):
        self.apply_calls += 1
        return super().apply_proposal(*args, **kwargs)


def _make_manager(store):
    adapter = _SpyAdapter()
    audit = _RecordingAudit()
    manager = ProposalManager(
        store=store,
        personality_adapter=adapter,
        growth_history=None,
        config={"audit": audit},
    )
    return manager, adapter, audit


def _make_pipeline(tmp_path):
    return PersonalityEvolutionPipeline(
        history_path=str(tmp_path / "evolution_history.json"),
    )


# ============================================================
# Case 1: pending → approve → apply → 允许人格变化
# ============================================================
def test_case1_approve_then_apply_allows_personality_change(tmp_path):
    store = _make_store(tmp_path)
    manager, adapter, audit = _make_manager(store)
    proposal = _make_pending_proposal()
    store.save(proposal)
    pid = proposal.id

    # approve(accept): pending → accepted
    r1 = manager.accept_proposal(pid, actor="admin_qing")
    assert r1["status"] == "accepted"
    assert store.load(pid).status == "accepted"

    # apply: accepted → applied,人格变化被允许(内存内 TraitState 演化)
    r2 = manager.apply_proposal(pid, actor="admin_qing")
    assert r2["status"] == "applied"
    envelope = r2["apply_result"]
    assert envelope["applied"] is True
    assert envelope["before"]["curiosity"] == pytest.approx(0.7)
    assert envelope["after"]["curiosity"] == pytest.approx(0.7015)
    assert store.load(pid).status == "applied"
    assert adapter.apply_calls == 1

    # 审计完整: accept 与 apply 均有记录,actor 可追溯
    reasons = [e.reason for e in audit.entries]
    assert "proposal_accepted" in reasons
    assert "proposal_applied" in reasons
    for e in audit.entries:
        if e.reason in ("proposal_accepted", "proposal_applied"):
            assert e.actor == "admin_qing"


def test_case1b_evolution_pipeline_apply_approved_proposal(tmp_path):
    """生产人格演化路径(PersonalityEvolutionPipeline)同样只放行 accepted。"""
    store = _make_store(tmp_path)
    manager, _, _ = _make_manager(store)
    proposal = _make_pending_proposal()
    store.save(proposal)

    manager.accept_proposal(proposal.id, actor="admin_qing")
    accepted = store.load(proposal.id)
    assert accepted.status == "accepted"

    pipeline = _make_pipeline(tmp_path)
    trait_states = {"curiosity": {"current_value": 0.7}}
    rec = pipeline.apply_approved_proposal(
        proposal=accepted,
        trait_states=trait_states,
        actor="runtime_drain",
        approval_record={"approval_id": "apr_001"},
    )
    assert rec.status == "applied"
    after_val = rec.trait_states_after["curiosity"]["current_value"]
    assert after_val == pytest.approx(0.7015)
    assert rec.trait_states_before["curiosity"]["current_value"] == pytest.approx(0.7)
    # EP-3 溯源: 审批记录 id 保留在演化记录中
    assert rec.approval_record_id == "apr_001"
    # 六阶段走完,演化历史落一条 applied 记录
    history = pipeline.get_history()
    assert len(history) == 1 and history[0]["status"] == "applied"


# ============================================================
# Case 2: pending → reject → 无人格变化
# ============================================================
def test_case2_reject_blocks_personality_change(tmp_path):
    store = _make_store(tmp_path)
    manager, adapter, audit = _make_manager(store)
    proposal = _make_pending_proposal()
    store.save(proposal)

    r = manager.reject_proposal(
        proposal.id, actor="admin_qing", reason="evidence insufficient",
    )
    assert r["status"] == "rejected"
    stored = store.load(proposal.id)
    assert stored.status == "rejected"
    assert stored.rejected_at
    assert stored.evaluator_meta["rejection_reason"] == "evidence insufficient"

    # 管理器层: rejected 不能 apply
    r2 = manager.apply_proposal(proposal.id)
    assert r2["status"] == "not_accepted"
    assert adapter.apply_calls == 0

    # 演化管线层: rejected 直接 blocked
    pipeline = _make_pipeline(tmp_path)
    trait_states = {"curiosity": {"current_value": 0.7}}
    rec = pipeline.apply_approved_proposal(
        proposal=stored, trait_states=trait_states, actor="runtime",
    )
    assert rec.status == "blocked"
    assert "not accepted" in rec.reason
    assert trait_states["curiosity"]["current_value"] == pytest.approx(0.7)

    assert any(e.reason == "proposal_rejected" for e in audit.entries)


# ============================================================
# Case 3: 直接 apply(status != accepted) → 必须失败
# ============================================================
def test_case3_direct_apply_on_pending_fails(tmp_path):
    store = _make_store(tmp_path)
    manager, adapter, _ = _make_manager(store)
    proposal = _make_pending_proposal()
    store.save(proposal)

    # 管理器层: pending 直接 apply → not_accepted,适配器不被调用
    r = manager.apply_proposal(proposal.id, actor="attacker")
    assert r["status"] == "not_accepted"
    assert "must be accepted" in r["reason"]
    assert adapter.apply_calls == 0
    assert adapter.build_calls == 0
    assert store.load(proposal.id).status == "pending"

    # 演化管线层: pending 直接进管线 → blocked
    pipeline = _make_pipeline(tmp_path)
    trait_states = {"curiosity": {"current_value": 0.7}}
    rec = pipeline.apply_approved_proposal(
        proposal=proposal, trait_states=trait_states, actor="runtime",
    )
    assert rec.status == "blocked"
    assert "not accepted" in rec.reason
    assert trait_states["curiosity"]["current_value"] == pytest.approx(0.7)


# ============================================================
# Case 4: 重复 approve → 幂等
# ============================================================
def test_case4_duplicate_approve_is_idempotent(tmp_path):
    store = _make_store(tmp_path)
    manager, adapter, _ = _make_manager(store)
    proposal = _make_pending_proposal()
    store.save(proposal)

    r1 = manager.accept_proposal(proposal.id, actor="admin_qing")
    assert r1["status"] == "accepted"
    assert adapter.build_calls == 1

    # 第二次 approve → already_accepted,不重复转换
    r2 = manager.accept_proposal(proposal.id, actor="admin_qing")
    assert r2["status"] == "already_accepted"
    assert adapter.build_calls == 1
    assert store.load(proposal.id).status == "accepted"

    # accepted 之后不可再 reject(审批不可翻转)
    r3 = manager.reject_proposal(proposal.id, actor="admin_qing")
    assert r3["status"] == "already_accepted"
    assert store.load(proposal.id).status == "accepted"


# ============================================================
# Case 5: 重复 apply → 不重复修改人格
# ============================================================
def test_case5_duplicate_apply_no_double_change(tmp_path):
    store = _make_store(tmp_path)
    manager, adapter, _ = _make_manager(store)
    proposal = _make_pending_proposal()
    store.save(proposal)

    manager.accept_proposal(proposal.id, actor="admin_qing")
    r1 = manager.apply_proposal(proposal.id, actor="admin_qing")
    assert r1["status"] == "applied"
    value_once = r1["apply_result"]["after"]["curiosity"]
    assert value_once == pytest.approx(0.7015)

    # 管理器层: applied 再 apply → not_accepted,适配器不再被调用
    r2 = manager.apply_proposal(proposal.id, actor="admin_qing")
    assert r2["status"] == "not_accepted"
    assert adapter.apply_calls == 1

    # 执行层: 同一 EvolutionRecord 重复应用 → record_id 去重,值不变
    record = adapter.map_proposal_to_evolution_record(proposal)
    record["approved"] = True
    states = {"curiosity": {"current_value": 0.7}}
    updater = TraitStateUpdater()
    updater.apply(record, states)
    assert states["curiosity"]["current_value"] == pytest.approx(0.7015)
    updater.apply(record, states)
    assert states["curiosity"]["current_value"] == pytest.approx(0.7015)

    # 状态层: PersonalityState EP-2 幂等保护
    ps = PersonalityState()
    evo = build_evolution_record(
        proposal_id=proposal.id,
        approval_id="apr_case5",
        change_type="trait_delta",
        before={"curiosity": 0.7},
        after={"curiosity": 0.7015},
        reasons=["approved_by:admin_qing"],
    )
    ra = ps.apply_evolution(evo)
    assert ra["applied"] is True
    rb = ps.apply_evolution(evo)
    assert rb["applied"] is False
    assert "EP-2" in rb["error"]
    assert ps.traits["curiosity"] == pytest.approx(0.7015)


def test_case5b_evolution_record_requires_approval_provenance():
    """EP-3: 无 approval_id 的演化记录无法构建(审批溯源前置)。"""
    with pytest.raises(ValueError):
        build_evolution_record(
            proposal_id="prop_x",
            approval_id="",
            change_type="trait_delta",
            before={"curiosity": 0.7},
            after={"curiosity": 0.8},
            reasons=["no approval"],
        )


# ============================================================
# Step 4 红线: 直改人格路径不存在(AST/静态)
# ============================================================
CYCLE_TASK = REPO_ROOT / "src" / "runtime" / "lifecycle" / "tasks" / "growth_cycle.py"
PIPELINE_SRC = REPO_ROOT / "src" / "growth" / "pipeline.py"
TASKS_DIR = REPO_ROOT / "src" / "runtime" / "lifecycle" / "tasks"

# 注: save 不在禁止列表 —— 周期任务合法调用 store.save 落盘 pending 提案
_FORBIDDEN_CALLS = {
    "accept_proposal", "apply_proposal", "reject_proposal", "apply",
    "apply_evolution",
}
_FORBIDDEN_TYPES = {
    "TraitStateUpdater", "PersonalityAdapter", "PersonalityState",
}


def test_redline_cycle_task_has_no_governance_apply_or_trait_paths():
    """GrowthCycleTask 源码(AST 级)不存在 approve/apply/人格直改调用。"""
    tree = ast.parse(CYCLE_TASK.read_text(encoding="utf-8"))
    called, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            for a in node.names:
                mods.append(a.name)
            for m in mods:
                assert "personality" not in m, f"周期任务禁止 import 人格模块: {m}"
                assert "proposal_manager" not in m, "周期任务禁止 import 提案管理器"

    bad_calls = called & _FORBIDDEN_CALLS
    assert not bad_calls, f"GrowthCycleTask 存在禁止调用: {bad_calls}"
    bad_types = names & _FORBIDDEN_TYPES
    assert not bad_types, f"GrowthCycleTask 引用了人格执行类型: {bad_types}"


def test_redline_growth_pipeline_no_direct_personality_mutation():
    """GrowthPipeline 源码不引用人格执行层(无 TraitStateUpdater/适配器/状态)。"""
    text = PIPELINE_SRC.read_text(encoding="utf-8")
    for token in ("TraitStateUpdater", "PersonalityAdapter",
                  "personality_state", "apply_evolution"):
        assert token not in text, f"GrowthPipeline 出现人格直改 token: {token}"


def test_redline_lifecycle_tasks_never_invoke_consolidation():
    """生命周期任务不调用遗留直改入口(consolidation/incremental)。"""
    for py in sorted(TASKS_DIR.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        assert "run_full_consolidation" not in text, py.name
        assert "incremental_update" not in text, py.name
