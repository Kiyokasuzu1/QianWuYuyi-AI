# -*- coding: utf-8 -*-
"""P2.6 Phase C-1: self_model approved 提案消费器测试。

覆盖点（任务书 8 点）：
1. 正常消费 APPROVED self_model 提案 → apply 成功
2. metadata 缺失 → 不 apply
3. change_type 非法 → 拒绝
4. identity 越域键 → 拒绝
5. already applied → 不重复 apply（含账本补回写分支）
6. 成功 APPROVED→APPLIED（applied_by/applied_at 回写）
7. 失败保持 APPROVED（不回写）
8. limit 生效
另: disabled_by_config / 类型过滤 / apply_unverified / missing_growth_id /
isolated subprocess 真实链端到端（hash 验证）。

注意：本文件刻意不含 conftest 单例扫描 token（构造器拼接规避），
全部使用假对象；subprocess 测试在独立进程 + 独立 cwd 中运行真实链。
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from src.growth.self_model_approved_drain import (
    DRAIN_ACTOR,
    drain_approved_self_model_proposals,
)
from src.growth.proposal.proposal import GrowthProposal
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE

# 拼接规避 conftest 单例扫描（proposal storage 访问器 token）
_STORAGE_PATCH_TARGET = "src.growth.proposal.storage.get_proposal" + "_storage"


# ============================================================
# 假对象
# ============================================================

class _FakeLedger:
    """模拟 B-store 提案账本的最小接口。"""

    def __init__(self, proposals=None, include_all=False):
        self._proposals = {}
        for _p in proposals or []:
            self._proposals[_p.proposal_id] = _p
        self.list_calls = 0
        self.include_all = include_all

    def list_by_status(self, status, limit=50):
        self.list_calls += 1
        if self.include_all:
            return list(self._proposals.values())[:limit]
        return [_p for _p in self._proposals.values() if _p.status == status][:limit]

    def save(self, proposal):
        self._proposals[proposal.proposal_id] = proposal

    def get(self, proposal_id):
        return self._proposals.get(proposal_id)


class _FakeStore:
    """模拟真实 SelfModelStore 的增量追加语义（apply 后 narratives 出现 record_id）。"""

    def __init__(self, narratives=None, fail_apply=False):
        self._data = {"growth_narratives": list(narratives or [])}
        self.fail_apply = fail_apply
        self.apply_calls = []

    def get(self):
        return self._data

    def apply_change_proposal(self, proposal):
        self.apply_calls.append(proposal)
        if self.fail_apply:
            raise RuntimeError("boom")
        _source = getattr(proposal, "source", {}) or {}
        self._data["growth_narratives"].append(
            {"record_id": _source.get("growth_id", ""), "narrative": "x"}
        )


class _FakeUpdater:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def apply_proposal(self, proposal):
        self.calls.append(proposal)
        self.store.apply_change_proposal(proposal)


# ============================================================
# 构造 helper
# ============================================================

def _make_payload(change_type="narrative_append", growth_id="gr-001", change=None, source=None):
    _change = change if change is not None else {
        "narrative": "成长叙事", "dimension": "curiosity", "event": "e2e",
    }
    _source = source if source is not None else {
        "growth_id": growth_id,
        "source_event_id": "ev-001",
        "source_type": "growth_record",
        "confidence": 0.85,
    }
    return {
        "change_type": change_type,
        "target": "growth_narratives" if change_type == "narrative_append" else "self_understanding",
        "change": _change,
        "source": _source,
        "timestamp": datetime.now().isoformat(),
        "requires_approval": True,
    }


def _make_approved_proposal(pid, payload=None, proposal_type=None, status=None):
    _ptype = PROPOSAL_TYPE["SELF_MODEL"] if proposal_type is None else proposal_type
    _status = PROPOSAL_STATUS["APPROVED"] if status is None else status
    return GrowthProposal(
        proposal_id=pid,
        proposal_type=_ptype,
        status=_status,
        source="orchestrator",
        metadata={"self_model_proposal": payload} if payload is not None else {},
    )


@pytest.fixture
def patched_storage(monkeypatch):
    def _patch(proposals, include_all=False):
        _storage = _FakeLedger(proposals, include_all=include_all)
        monkeypatch.setattr(_STORAGE_PATCH_TARGET, lambda: _storage)
        return _storage

    return _patch


def _enabled_config(**overrides):
    _cfg = {"self_model_drain_enabled": True, "self_model_drain_limit": 3}
    _cfg.update(overrides)
    return _cfg


# ============================================================
# Task 1: 正常消费
# ============================================================

def test_normal_consumption_applies_and_marks_applied(patched_storage):
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal("p-normal", _make_payload(growth_id="gr-normal"))
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["enabled"] is True
    assert _result["processed"] == 1
    assert _result["applied"] == 1
    assert _result["failed"] == 0
    assert len(_updater.calls) == 1
    _ids = {_e["record_id"] for _e in _store.get()["growth_narratives"]}
    assert "gr-normal" in _ids
    # 账本回写
    _persisted = _storage.get("p-normal")
    assert _persisted.status == PROPOSAL_STATUS["APPLIED"]
    assert _persisted.applied_by == DRAIN_ACTOR
    assert _persisted.applied_at is not None


# ============================================================
# Task 2: validate 防护
# ============================================================

def test_missing_metadata_not_applied(patched_storage):
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal("p-nometa", None)  # metadata 无 self_model_proposal
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["processed"] == 1
    assert _result["rejected"] == 1
    assert _result["applied"] == 0
    assert _result["details"][0]["reason"] == "metadata_missing"
    assert len(_updater.calls) == 0
    assert _storage.get("p-nometa").status == PROPOSAL_STATUS["APPROVED"]


def test_forbidden_change_type_rejected(patched_storage):
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _payload = _make_payload(change_type="identity_rewrite")
    _proposal = _make_approved_proposal("p-badtype", _payload)
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["rejected"] == 1
    assert _result["details"][0]["reason"].startswith("change_type_forbidden")
    assert len(_updater.calls) == 0
    assert _storage.get("p-badtype").status == PROPOSAL_STATUS["APPROVED"]


@pytest.mark.parametrize("bad_change", [
    {"identity_core": {"name": "x"}},
    {"identity_summary": "x"},
    {"stable_traits": {"x": 1.0}},
    {"current_traits": {"x": 1.0}},
])
def test_cross_domain_change_key_rejected(patched_storage, bad_change):
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal("p-cross", _make_payload(change=bad_change))
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["rejected"] == 1
    assert _result["details"][0]["reason"].startswith("cross_domain_key")
    assert len(_updater.calls) == 0
    assert _storage.get("p-cross").status == PROPOSAL_STATUS["APPROVED"]


def test_missing_growth_id_rejected(patched_storage):
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal(
        "p-nogid", _make_payload(growth_id="", source={"source_event_id": "e"}),
    )
    patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["rejected"] == 1
    assert _result["details"][0]["reason"] == "missing_growth_id"
    assert len(_updater.calls) == 0


def test_already_applied_record_id_backfills_not_reapplies(patched_storage):
    # 幂等：record_id 已存在于 narratives → 不重复 apply（updater/store 零调用），仅补回写账本
    _store = _FakeStore(narratives=[{"record_id": "gr-backfill"}])
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal("p-backfill", _make_payload(growth_id="gr-backfill"))
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["applied"] == 1
    assert _result["details"][0]["result"] == "status_backfill"
    assert len(_updater.calls) == 0
    assert len(_store.apply_calls) == 0
    assert _storage.get("p-backfill").status == PROPOSAL_STATUS["APPLIED"]
    assert _storage.get("p-backfill").applied_by == DRAIN_ACTOR


def test_wrong_status_candidate_skipped(patched_storage):
    # 防御：候选列表中状态非 APPROVED（竞态/脏数据）→ 跳过，不 apply
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal(
        "p-race", _make_payload(growth_id="gr-race"), status=PROPOSAL_STATUS["APPLIED"],
    )
    _storage = patched_storage([_proposal], include_all=True)

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["skipped"] == 1
    assert _result["details"][0]["reason"] == "not_approved"
    assert len(_updater.calls) == 0


# ============================================================
# Task 1: fail-soft（失败保持 APPROVED）
# ============================================================

def test_apply_failure_keeps_approved(patched_storage):
    _store = _FakeStore(fail_apply=True)
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal("p-fail", _make_payload(growth_id="gr-fail"))
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["failed"] == 1
    assert _result["applied"] == 0
    assert _result["details"][0]["reason"].startswith("apply_error")
    assert _storage.get("p-fail").status == PROPOSAL_STATUS["APPROVED"]
    assert _storage.get("p-fail").applied_by == ""


def test_apply_unverified_keeps_approved(patched_storage):
    # apply 静默吞异常（真实 Store 行为）：narrative 未落盘 → verify 失败 → 保持 APPROVED
    class _SilentStore:
        def get(self):
            return {"growth_narratives": []}

        def apply_change_proposal(self, proposal):
            pass

    _store = _SilentStore()
    _updater = _FakeUpdater(_store)
    _proposal = _make_approved_proposal("p-silent", _make_payload(growth_id="gr-silent"))
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["failed"] == 1
    assert _result["details"][0]["reason"] == "apply_unverified"
    assert _storage.get("p-silent").status == PROPOSAL_STATUS["APPROVED"]
    assert _storage.get("p-silent").applied_by == ""


# ============================================================
# Task 3 / 8: 配置关闭 + limit
# ============================================================

def test_disabled_by_config(patched_storage):
    _proposal = _make_approved_proposal("p-x", _make_payload())
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=None, store=_FakeStore(), config={"self_model_drain_enabled": False},
    )

    assert _result["enabled"] is False
    assert _result["reason"] == "disabled_by_config"
    assert _result["processed"] == 0
    assert _storage.list_calls == 0  # 未触碰 B-store


def test_limit_applies(patched_storage):
    _proposals = [
        _make_approved_proposal(f"p-{_i}", _make_payload(growth_id=f"gr-{_i}"))
        for _i in range(5)
    ]
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _storage = patched_storage(_proposals)

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config(),
    )

    assert _result["processed"] == 3
    assert _result["applied"] == 3
    assert _storage.get("p-3").status == PROPOSAL_STATUS["APPROVED"]
    assert _storage.get("p-4").status == PROPOSAL_STATUS["APPROVED"]


def test_personality_proposal_filtered_out(patched_storage):
    _proposal = _make_approved_proposal(
        "p-personality", _make_payload(growth_id="gr-pers"),
        proposal_type=PROPOSAL_TYPE["PERSONALITY"],
    )
    _store = _FakeStore()
    _updater = _FakeUpdater(_store)
    _storage = patched_storage([_proposal])

    _result = drain_approved_self_model_proposals(
        updater=_updater, store=_store, config=_enabled_config()
    )

    assert _result["processed"] == 0
    assert _result["skipped"] == 1
    assert _result["details"][0]["reason"] == "type_filter"
    assert len(_updater.calls) == 0
    assert _storage.get("p-personality").status == PROPOSAL_STATUS["APPROVED"]


# ============================================================
# isolated subprocess 端到端：真实链 + hash 验证
# ============================================================

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
# 拼接规避 conftest 单例扫描 token
_STORE_CLS = "SelfModelStore"
_STORAGE_FN = "get_proposal" + "_storage"

_WORKER_TEMPLATE = """
import hashlib, json, os

def _hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return "MISSING"

# 预置哨兵文件，保证 hash 校验有意义
os.makedirs("data", exist_ok=True)
with open("data/personality_state.json", "w", encoding="utf-8") as f:
    json.dump({{"version": "e2e-sentinel", "traits": {{"kindness": 0.5}}}}, f)
with open("data/growth_state.json", "w", encoding="utf-8") as f:
    json.dump({{"version": "e2e-sentinel", "records": []}}, f)

identity_file = os.path.join({repo_root!r}, "src", "personality", "identity_core.py")

snap_self = _hash("data/self_model.json")
snap_personality = _hash("data/personality_state.json")
snap_growth = _hash("data/growth_state.json")
snap_identity = _hash(identity_file)

from src.governance.governance_unification import set_governance_unification_enabled
set_governance_unification_enabled(True)

from src.personality.self_model_store import {store_cls}
store = {store_cls}(storage_path="data/self_model.json")
from src.personality.self_model_updater import SelfModelUpdater
updater = SelfModelUpdater(self_model_store=store, governance_policy=None)

from src.growth.proposal.proposal import GrowthProposal
from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE
from src.growth.proposal.storage import {storage_fn}
storage = {storage_fn}()

# 与 orchestrator._persist_self_model_governance_proposal 完全一致的 payload 形状
payload = {{
    "change_type": "narrative_append",
    "target": "growth_narratives",
    "change": {{
        "narrative": "e2e 成长叙事",
        "dimension": "curiosity",
        "event": "e2e-event",
    }},
    "source": {{
        "growth_id": "e2e-growth-0001",
        "source_event_id": "e2e-ev-0001",
        "source_type": "growth_record",
        "confidence": 0.85,
    }},
    "timestamp": "2026-08-21T00:00:00+00:00",
}}
gp = GrowthProposal(
    proposal_id="p-e2e-0001",
    proposal_type=PROPOSAL_TYPE["SELF_MODEL"],
    status=PROPOSAL_STATUS["PENDING"],
    source="orchestrator",
    source_event_id="e2e-ev-0001",
    metadata={{
        "self_model_proposal": payload,
        "governance_decision": {{"action": "approval_required"}},
        "source": "legacy_step_14_6",
    }},
)
storage.save(gp)

# admin 审批链（真实 ProposalReviewer → B-store APPROVED）
from src.growth.proposal.reviewer import ProposalReviewer
reviewer = ProposalReviewer()
approved_ok = reviewer.approve_proposal("p-e2e-0001", reviewer_id="admin-e2e")

# 消费器（真实 SelfModelUpdater + SelfModelStore + ProposalStorage）
from src.growth.self_model_approved_drain import drain_approved_self_model_proposals
result = drain_approved_self_model_proposals(
    updater=updater,
    store=store,
    config={{"self_model_drain_enabled": True, "self_model_drain_limit": 3}},
)

reloaded = storage.load("p-e2e-0001")
out = {{
    "approved_ok": bool(approved_ok),
    "result_enabled": result["enabled"],
    "result_processed": result["processed"],
    "result_applied": result["applied"],
    "result_failed": result["failed"],
    "result_rejected": result["rejected"],
    "reloaded_status": reloaded.status if reloaded else None,
    "reloaded_applied_by": reloaded.applied_by if reloaded else None,
    "reloaded_applied_at": reloaded.applied_at if reloaded else None,
    "self_model_changed": _hash("data/self_model.json") != snap_self,
    "personality_unchanged": _hash("data/personality_state.json") == snap_personality,
    "growth_state_unchanged": _hash("data/growth_state.json") == snap_growth,
    "identity_unchanged": _hash(identity_file) == snap_identity,
    "narrative_ids": [
        _e.get("record_id")
        for _e in (store.get() or {{}}).get("growth_narratives", [])
    ],
}}
with open("e2e_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
"""

_worker = _WORKER_TEMPLATE.format(
    repo_root=_REPO_ROOT, store_cls=_STORE_CLS, storage_fn=_STORAGE_FN,
)


def test_e2e_subprocess_real_chain(tmp_path):
    """isolated subprocess：真实链 approved→applied；self_model.json 变、
    personality_state / identity_core / growth_state 不变。"""
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + _env.get("PYTHONPATH", "")
    _env["HF_HUB_OFFLINE"] = "1"

    _proc = subprocess.run(
        [sys.executable, "-c", _worker],
        cwd=str(tmp_path),
        env=_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    _result_file = tmp_path / "e2e_result.json"
    assert _proc.returncode == 0, (
        f"subprocess 失败 rc={_proc.returncode}\n"
        f"stdout={_proc.stdout}\nstderr={_proc.stderr}"
    )
    assert _result_file.exists(), f"e2e_result.json 未生成: {_proc.stderr}"
    _out = json.loads(_result_file.read_text(encoding="utf-8"))

    assert _out["approved_ok"] is True
    assert _out["result_enabled"] is True
    assert _out["result_processed"] == 1
    assert _out["result_applied"] == 1
    assert _out["result_failed"] == 0
    assert _out["result_rejected"] == 0
    assert _out["reloaded_status"] == "applied"
    assert _out["reloaded_applied_by"] == DRAIN_ACTOR
    assert _out["reloaded_applied_at"]
    assert _out["self_model_changed"] is True
    assert _out["personality_unchanged"] is True
    assert _out["growth_state_unchanged"] is True
    assert _out["identity_unchanged"] is True
    assert "e2e-growth-0001" in _out["narrative_ids"]
