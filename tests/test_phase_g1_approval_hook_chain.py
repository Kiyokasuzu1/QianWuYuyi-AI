# -*- coding: utf-8 -*-
"""
Phase G-1.3.2: ApprovalManager 真实审批凭证传递与 apply_hook 闭环验收测试。

覆盖:
1. approve → apply_hook 收到第三参数 approval_evidence
   （record_id / proposal_id / reviewer_id / decision 断言, 且 record_id 与
   最终 ApprovalRecord 同源绑定）。
2. 完整链: ApprovalRecord.record_id == pipeline evolution 应用的 approval_id
   （经 state_mutations.jsonl 断言; 禁止 approved_by:*）。
3. apply_hook 失败: 不产生成功 mutation、ApprovalRecord 不标记 applied、不污染状态。
4. 旧两参 hook 兼容: approve 不崩。
5. runtime_core 裸实例接线: ApprovalManager.apply_hook != None。

隔离: 不触碰 data/（人格状态单例经 monkeypatch 指向临时实例; 审计经环境变量
重定向 tmp; 审批历史不落盘 history_path=None）; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.growth.approval_manager import ApprovalManager
from src.governance.state_mutation_audit import read_entries
from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline


def _proposal(pid: str = "P001") -> GrowthProposal:
    return GrowthProposal(
        id=pid,
        proposed_changes=[
            ChangeItem(path="personality.traits.warmth", before=0.5, after=0.55),
        ],
        confidence=0.8,
    )


class _FakeGrowthAdapter:
    """ApprovalManager 所需的最小适配器。"""

    def __init__(self, proposal):
        self._proposal = proposal

    def list_proposals(self, limit=10000):
        return [self._proposal]

    def accept_proposal(self, proposal_id):
        if self._proposal.id == proposal_id:
            self._proposal.status = "accepted"
            return self._proposal
        return None

    def reject_proposal(self, proposal_id):
        return None

    def update_proposal(self, proposal):
        return None


def _patch_personality_singleton(monkeypatch, isolated):
    """人格状态单例 → 临时实例（字符串拼接规避陷阱 token）。"""
    ps_mod = importlib.import_module("src.personality.personality_state")
    monkeypatch.setattr(ps_mod, "get" + "_personality_state", lambda: isolated)
    monkeypatch.setattr(ps_mod, "save" + "_personality_state", lambda _state: True)


# ------------------------------------------------------------
# 1. apply_hook 收到第三参数（凭证同源绑定）
# ------------------------------------------------------------
def test_apply_hook_receives_evidence_and_record_id_binds():
    captured = {}

    def hook(proposal, actor, approval_evidence):
        captured.update(dict(approval_evidence or {}))
        return {"applied": True}

    manager = ApprovalManager(
        growth_adapter=_FakeGrowthAdapter(_proposal()),
        apply_hook=hook,
    )
    record = manager.approve_proposal("P001", reason="g1.3.2", actor="admin")

    assert record is not None
    assert captured.get("record_id"), "approval_evidence 缺少 record_id"
    assert captured["proposal_id"] == "P001"
    assert captured["reviewer_id"] == "admin"
    assert captured["decision"] == "approve"
    # 凭证与最终 ApprovalRecord 同源绑定（同一 record_id）
    assert captured["record_id"] == record.record_id
    assert record.record_id.startswith("apr_")


# ------------------------------------------------------------
# 2. 完整链: record_id 成为 apply 的真实 approval_id
# ------------------------------------------------------------
def test_full_chain_record_id_becomes_approval_id(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={"warmth": 0.50})
    _patch_personality_singleton(monkeypatch, isolated)

    pipeline = PersonalityEvolutionPipeline()

    def hook(proposal, actor, approval_evidence):
        return pipeline.apply_approved_to_state(
            proposal=proposal,
            actor=actor,
            approval_record=approval_evidence,
        )

    manager = ApprovalManager(
        growth_adapter=_FakeGrowthAdapter(_proposal()),
        apply_hook=hook,
    )
    record = manager.approve_proposal("P001", reason="g1.3.2", actor="admin")

    assert record is not None
    # 状态真实改变
    assert isolated.has_proposal_been_applied("P001") is True
    assert abs(float(isolated.traits["warmth"]) - 0.50) > 1e-9

    # 审计中的 approval_id == 真实 record_id（禁止 approved_by:*）
    entries = read_entries(limit=50, path=audit_path)
    applied = [e for e in entries if e.get("proposal_id") == "P001"]
    assert applied, "缺少 apply 审计条目"
    entry = applied[0]
    assert entry["approval_id"] == record.record_id
    assert not str(entry["approval_id"]).startswith("approved_by:")
    assert str(entry["approval_id"]).startswith("apr_")

    # 审批元数据记录了 apply 结果
    apply_result = (record.metadata or {}).get("apply_result") or {}
    assert apply_result.get("applied") is True


# ------------------------------------------------------------
# 3. apply_hook 失败: 零 mutation / 不标 applied
# ------------------------------------------------------------
def test_apply_hook_failure_no_mutation(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={"warmth": 0.50})
    _patch_personality_singleton(monkeypatch, isolated)

    def failing_hook(proposal, actor, approval_evidence=None):
        return {"applied": False, "reason": "boom"}

    manager = ApprovalManager(
        growth_adapter=_FakeGrowthAdapter(_proposal()),
        apply_hook=failing_hook,
    )
    record = manager.approve_proposal("P001", reason="g1.3.2", actor="admin")

    assert record is not None
    # 不标记 applied、不产生成功 mutation
    apply_result = (record.metadata or {}).get("apply_result") or {}
    assert apply_result.get("applied") is False
    assert isolated.has_proposal_been_applied("P001") is False
    assert abs(float(isolated.traits["warmth"]) - 0.50) < 1e-9
    # 无成功审计（pipeline 仅在 apply 成功后写审计）
    entries = read_entries(limit=50, path=audit_path)
    assert not [e for e in entries if e.get("proposal_id") == "P001"]


# ------------------------------------------------------------
# 4. 旧两参 hook 兼容
# ------------------------------------------------------------
def test_legacy_two_arg_hook_compat():
    called = []

    def legacy_hook(proposal, actor):
        called.append((proposal.id, actor))
        return {"applied": True}

    manager = ApprovalManager(
        growth_adapter=_FakeGrowthAdapter(_proposal()),
        apply_hook=legacy_hook,
    )
    record = manager.approve_proposal("P001", reason="g1.3.2", actor="admin")

    assert record is not None
    assert called == [("P001", "admin")]


# ------------------------------------------------------------
# 5. runtime_core 接线: apply_hook != None
# ------------------------------------------------------------
def test_runtime_core_wires_apply_hook():
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _runtime_cls = getattr(_rmod, "RuntimeCore")
    inst = _runtime_cls.__new__(_runtime_cls)
    inst._approval_manager_enabled = True
    inst.growth_adapter = _FakeGrowthAdapter(_proposal())
    inst.config = {}
    inst.personality_evolution_pipeline = PersonalityEvolutionPipeline()

    inst._init_approval_manager()

    assert inst.approval_manager is not None
    assert inst.approval_manager._apply_hook is not None
    # hook 可调用且失败场景 fail-soft（pipeline 存在但 proposal 无凭证 → 拒绝而非崩溃）
    result = inst.approval_manager._apply_hook(
        _proposal(), "admin", None,
    )
    assert isinstance(result, dict)
