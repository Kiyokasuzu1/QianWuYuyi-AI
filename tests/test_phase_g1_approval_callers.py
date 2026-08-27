# -*- coding: utf-8 -*-
"""
Phase G-1.3.3: 三个审批调用方迁移验收测试。

覆盖:
1. proposal_manager: accept → apply 传递真实 approval_record（mark_approved 不再是主路径）。
2. proposal_manager: 真实凭证 → pipeline apply → approval_id == record_id。
3. runtime_growth_pipeline: metadata="approved" 但无 provider →
   enforcement 开启拒绝 / legacy fallback 应用。
4. runtime_growth_pipeline: provider 返回真实凭证 → 成功 apply。
5. runtime_core preview: record_id="preview" 凭证, 无真实 mutation audit。
6. 全仓扫描: 无新增 approved_by:{actor} 生成逻辑（文件白名单）。

隔离: 假 store / spy adapter / 人格状态单例 monkeypatch / 审计 env 重定向;
遵守 conftest 陷阱 token 规则（runtime_core 经 importlib+getattr 构造）。
"""

from __future__ import annotations

import importlib
import copy
from pathlib import Path

import pytest

from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.growth.proposal_manager import ProposalManager
from src.governance.state_mutation_audit import read_entries
from src.personality.personality_adapter import (
    PersonalityAdapter,
    set_approval_record_enforced,
)
from src.personality.personality_evolution_pipeline import PersonalityEvolutionPipeline
from src.runtime.pipeline.runtime_growth_pipeline import (
    PipelineRun,
    RuntimeGrowthPipeline,
)


@pytest.fixture(autouse=True)
def _reset_enforced():
    set_approval_record_enforced(False)
    yield
    set_approval_record_enforced(False)


def _proposal(pid: str = "P001") -> GrowthProposal:
    return GrowthProposal(
        id=pid,
        proposed_changes=[
            ChangeItem(path="personality.traits.warmth", before=0.5, after=0.55),
        ],
        confidence=0.8,
    )


def _patch_personality_singleton(monkeypatch, isolated):
    ps_mod = importlib.import_module("src.personality.personality_state")
    monkeypatch.setattr(ps_mod, "get" + "_personality_state", lambda: isolated)
    monkeypatch.setattr(ps_mod, "save" + "_personality_state", lambda _state: True)


class _FakeStore:
    """proposal_manager 的最小存储替身。"""

    def __init__(self, proposal=None):
        self._proposal = proposal

    def load(self, pid):
        if self._proposal is not None and self._proposal.id == pid:
            return self._proposal
        return None

    def update(self, proposal):
        self._proposal = proposal

    def list(self, status=None, limit=50, offset=0):
        return [self._proposal] if self._proposal is not None else []


class _SpyAdapter:
    """包装真实 adapter, 记录 apply 调用参数。"""

    def __init__(self):
        self._inner = PersonalityAdapter()
        self.calls = []

    def apply_proposal(self, proposal, actor="system", mark_approved=False, **kw):
        self.calls.append({
            "mark_approved": mark_approved,
            "approval_record": kw.get("approval_record"),
        })
        return self._inner.apply_proposal(
            proposal, actor=actor, mark_approved=mark_approved, **kw,
        )

    def build_change_request(self, proposal, **kw):
        return self._inner.build_change_request(proposal, **kw)


class _FakeGrowth:
    def __init__(self, proposal):
        self._proposal = proposal

    def list_proposals(self, limit=1000):
        return [self._proposal]


# ------------------------------------------------------------
# 1. proposal_manager: approval_record 被传递
# ------------------------------------------------------------
def test_proposal_manager_passes_approval_record():
    proposal = _proposal()
    spy = _SpyAdapter()
    manager = ProposalManager(
        store=_FakeStore(proposal),
        personality_adapter=spy,
        config={},
    )
    accepted = manager.accept_proposal("P001", actor="admin")
    assert accepted["status"] == "accepted"
    result = manager.apply_proposal("P001", actor="admin")
    assert result["status"] == "applied", result

    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["mark_approved"] is False, "主路径不得再用 mark_approved 自证"
    record = call["approval_record"]
    assert record is not None
    assert record["record_id"].startswith("apr_")
    assert record["proposal_id"] == "P001"
    assert record["reviewer_id"] == "admin"
    assert record["decision"] == "approve"
    # 凭证随提案持久化
    stored = (result["proposal"].evaluator_meta or {}).get("approval_record")
    assert stored and stored["record_id"] == record["record_id"]


# ------------------------------------------------------------
# 2. proposal_manager 真实凭证 → pipeline apply → approval_id == record_id
# ------------------------------------------------------------
def test_proposal_manager_record_flows_into_pipeline(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))

    proposal = _proposal()
    manager = ProposalManager(
        store=_FakeStore(proposal),
        personality_adapter=PersonalityAdapter(),
        config={},
    )
    manager.accept_proposal("P001", actor="admin")
    result = manager.apply_proposal("P001", actor="admin")
    record = (result["proposal"].evaluator_meta or {}).get("approval_record")
    assert record is not None

    # 隔离人格状态 + 经 pipeline 应用同一凭证
    from src.personality.personality_state import PersonalityState

    isolated = PersonalityState(traits={"warmth": 0.50})
    _patch_personality_singleton(monkeypatch, isolated)
    canonical = copy.deepcopy(result["proposal"])
    canonical.status = "accepted"  # pipeline 契约: apply 前须 accepted
    envelope = PersonalityEvolutionPipeline().apply_approved_to_state(
        proposal=canonical,
        actor="runtime",
        approval_record=record,
    )
    assert envelope.get("applied") is True, envelope
    assert isolated.has_proposal_been_applied("P001") is True

    entries = read_entries(limit=50, path=audit_path)
    applied = [e for e in entries if e.get("proposal_id") == "P001"]
    assert applied, "缺少 apply 审计"
    assert applied[0]["approval_id"] == record["record_id"]
    assert not str(applied[0]["approval_id"]).startswith("approved_by:")


# ------------------------------------------------------------
# 3. runtime_growth_pipeline 无 provider
# ------------------------------------------------------------
def test_pipeline_without_provider_enforced_vs_legacy():
    proposal = _proposal()
    pipeline_obj = RuntimeGrowthPipeline(
        personality_adapter=PersonalityAdapter(),
        growth_adapter=_FakeGrowth(proposal),
    )
    run = PipelineRun(proposal_id="P001")
    run.metadata["lifecycle_state"] = "approved"  # 自设状态不作为审批证明

    set_approval_record_enforced(True)
    try:
        stage = pipeline_obj.step_personality(run)
    finally:
        set_approval_record_enforced(False)
    envelope = stage.outputs["envelope"]
    assert envelope.get("applied") is False
    assert envelope.get("note") == "approval_record_required"

    # legacy fallback（enforcement 关闭）→ 旧行为保留
    stage2 = pipeline_obj.step_personality(run)
    assert stage2.outputs["envelope"].get("applied") is True


# ------------------------------------------------------------
# 4. runtime_growth_pipeline provider 返回真实凭证
# ------------------------------------------------------------
def test_pipeline_with_provider_applies():
    proposal = _proposal()
    real_record = {
        "record_id": "apr_real_1",
        "proposal_id": "P001",
        "reviewer_id": "admin",
        "reviewed_at": "2026-08-21T00:00:00Z",
        "decision": "approved",
    }
    pipeline_obj = RuntimeGrowthPipeline(
        personality_adapter=PersonalityAdapter(),
        growth_adapter=_FakeGrowth(proposal),
        approval_record_provider=lambda pid: dict(real_record) if pid == "P001" else None,
    )
    run = PipelineRun(proposal_id="P001")
    run.metadata["lifecycle_state"] = "approved"
    stage = pipeline_obj.step_personality(run)
    assert stage.outputs["envelope"].get("applied") is True


# ------------------------------------------------------------
# 5. runtime_core preview
# ------------------------------------------------------------
def test_runtime_core_preview_credential(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH",
        str(tmp_path / "state_mutations.jsonl"),
    )
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _runtime_cls = getattr(_rmod, "RuntimeCore")
    inst = _runtime_cls.__new__(_runtime_cls)
    spy = _SpyAdapter()
    inst.personality_adapter = spy

    change_request = {
        "evolution_record": {
            "trait_changes": {"warmth": {"before": 0.5, "delta": 0.05}},
        },
        "confidence": 0.8,
        "request_id": "req_preview_1",
    }
    result = inst.apply_personality_change_request_in_memory(change_request)
    assert result.get("evolution_applied") is True, result
    assert spy.calls, "preview 应经 adapter apply"
    preview_record = spy.calls[0]["approval_record"]
    assert preview_record is not None
    assert preview_record["record_id"] == "preview"
    assert preview_record["decision"] == "preview"
    # 预览不生成真实 mutation audit
    assert not (tmp_path / "state_mutations.jsonl").exists()


# ------------------------------------------------------------
# 6. 全仓扫描: approved_by: 生成点文件白名单（无新增伪造源）
# ------------------------------------------------------------
def test_no_new_forged_approval_id_sources():
    allowed_files = {
        "src/contracts/cognitive_event_types.py",   # dataclass 字段名（非生成）
        "src/growth/growth_loop.py",                # dataclass 字段名（非生成）
        "src/personality/personality_evolution_pipeline.py",  # legacy 兜底（已 deprecated）
        "src/personality/personality_adapter.py",   # 反伪造校验 + legacy 标记
    }
    offenders = []
    for py_file in Path("src").rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "approved_by:" in text:
            rel = str(py_file.as_posix())
            if rel not in allowed_files:
                offenders.append(rel)
    assert not offenders, "新增 approved_by: 生成点: " + "; ".join(sorted(set(offenders)))
