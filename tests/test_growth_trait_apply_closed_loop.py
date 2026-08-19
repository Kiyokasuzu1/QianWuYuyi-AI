# -*- coding: utf-8 -*-
"""P0-1 Growth Trait Apply 闭环测试（9 例）。

覆盖:
  1. approved proposal 由 Runtime drain 自动 apply（含落盘 + governance 标 applied）
  2. pending proposal 不修改人格
  3. rejected proposal 不修改人格
  4. identity 变更被拒绝（pipeline 白名单拒绝 + drain 类型过滤）
  5. 重启恢复（personality_state.json 恢复 trait 与 applied_proposal_ids；版本单调保护）
  6. 重复 apply 幂等（EP-2 去重，不二次增长）
  7. save 失败保持 approved（可重试，不误标 applied）
  8. admin review 只标记 status，不直接修改人格
  9. stage 13 契约保持（阶段定义未动，drain 已接入）+ 开关关闭时空转

隔离要求: 全部使用临时目录；不连接生产服务；不污染 data/。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.contracts.governance_schema import GrowthProposalReviewRequest
from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.growth.proposal import storage as st_mod
from src.growth.proposal.constants import (
    PROPOSAL_STATUS,
    PROPOSAL_TYPE,
)
from src.growth.proposal.proposal import GrowthProposal as LegacyProposal
from src.growth.proposal.storage import ProposalStorage
from src.personality import personality_state as ps_mod
from src.personality.personality_evolution_pipeline import (
    PersonalityEvolutionPipeline,
)
from src.runtime import runtime_core as rc_mod


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """隔离全局单例:PersonalityState + governance ProposalStorage + 落盘路径。"""
    ps_mod.reset_personality_state()
    monkeypatch.setattr(
        ps_mod, "DEFAULT_PERSONALITY_STATE_PATH",
        str(tmp_path / "personality_state.json"),
    )
    st_mod.ProposalStorage.reset_for_testing()
    _storage = ProposalStorage(str(tmp_path / "proposals"))
    monkeypatch.setattr(st_mod, "_global_storage", _storage)
    pipeline = PersonalityEvolutionPipeline(
        history_path=str(tmp_path / "pep_history.json"),
        auto_apply_self_model=False,
    )
    fake_core = SimpleNamespace(
        config={"growth_apply_drain_enabled": True, "growth_apply_drain_limit": 5},
        personality_evolution_pipeline=pipeline,
    )
    return {
        "tmp": tmp_path,
        "storage": _storage,
        "pipeline": pipeline,
        "core": fake_core,
        "ps_path": tmp_path / "personality_state.json",
    }


def _legacy_proposal(
    storage, pid, after_state, before_state=None,
    status=PROPOSAL_STATUS["APPROVED"],
    ptype=PROPOSAL_TYPE["PERSONALITY"], confidence=0.8,
):
    legacy = LegacyProposal(
        proposal_id=pid,
        status=status,
        proposal_type=ptype,
        before_state=before_state or {},
        after_state=after_state,
        confidence=confidence,
        reviewer_id="admin_test",
        reviewed_at="2026-08-19T12:00:00Z",
    )
    storage.save(legacy)
    return legacy


def _drain(ctx, limit=None):
    return rc_mod.RuntimeCore.drain_approved_growth_proposals(ctx["core"], limit=limit)


# ---------------------------------------------------------------- case 1
def test_1_approved_proposal_auto_applies(isolated):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t1-approve",
        after_state={"creativity": 0.63}, before_state={"creativity": 0.6},
    )
    result = _drain(ctx)
    assert result["applied"] == 1, result
    assert result["details"][0]["status"] == "applied"
    ps = ps_mod.get_personality_state()
    assert ps.has_proposal_been_applied("t1-approve")
    assert ps.traits["creativity"] == pytest.approx(0.63)
    assert ps.version == 1
    # 落盘文件存在且包含 applied_proposal_ids（重启恢复依据）
    data = json.loads(ctx["ps_path"].read_text(encoding="utf-8"))
    assert data["traits"]["creativity"] == pytest.approx(0.63)
    assert "t1-approve" in data.get("applied_proposal_ids", [])
    # governance proposal 状态 → applied
    applied = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPLIED"])
    assert any(p.proposal_id == "t1-approve" for p in applied)
    approved = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPROVED"])
    assert not any(p.proposal_id == "t1-approve" for p in approved)


# ---------------------------------------------------------------- case 2
def test_2_pending_proposal_not_applied(isolated):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t2-pending",
        after_state={"creativity": 0.99}, status=PROPOSAL_STATUS["PENDING"],
    )
    result = _drain(ctx)
    assert result["applied"] == 0 and result["failed"] == 0, result
    assert result["details"] == []
    ps = ps_mod.get_personality_state()
    assert ps.traits["creativity"] == pytest.approx(0.6)
    assert ps.version == 0
    assert not ctx["ps_path"].exists()


# ---------------------------------------------------------------- case 3
def test_3_rejected_proposal_not_applied(isolated):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t3-reject",
        after_state={"empathy": 0.9}, status=PROPOSAL_STATUS["REJECTED"],
    )
    result = _drain(ctx)
    assert result["applied"] == 0, result
    ps = ps_mod.get_personality_state()
    assert ps.traits["empathy"] == pytest.approx(0.65)
    assert ps.version == 0
    assert not ctx["ps_path"].exists()


# ---------------------------------------------------------------- case 4
def test_4_identity_change_rejected(isolated):
    ctx = isolated
    # a) 经 pipeline:identity.* 路径被白名单/安全链拒绝
    canonical = GrowthProposal(
        id="t4-identity",
        proposed_changes=[ChangeItem(
            path="identity.core.name", before="浅雾羽依", after="冒名者",
            reason="test",
        )],
        confidence=0.9,
    )
    canonical.status = "accepted"
    envelope = ctx["pipeline"].apply_approved_to_state(
        proposal=canonical, actor="test", approval_id="test:1",
    )
    assert envelope["applied"] is False, envelope
    assert envelope.get("reason"), envelope
    ps = ps_mod.get_personality_state()
    assert ps.version == 0
    assert not ctx["ps_path"].exists()

    # b) 经 drain:proposal_type=identity 被类型过滤跳过
    _legacy_proposal(
        ctx["storage"], "t4b-identity",
        after_state={"identity.core.name": "x"},
        ptype=PROPOSAL_TYPE["IDENTITY"],
    )
    result = _drain(ctx)
    assert result["applied"] == 0, result
    assert result["skipped"] == 1
    assert result["details"][0]["reason"].startswith("skip_type")
    assert ps_mod.get_personality_state().version == 0


# ---------------------------------------------------------------- case 5
def test_5_restart_recovery_from_state_file(isolated):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t5-restart",
        after_state={"curiosity": 0.73}, before_state={"curiosity": 0.7},
    )
    assert _drain(ctx)["applied"] == 1
    assert ps_mod.get_personality_state().traits["curiosity"] == pytest.approx(0.73)

    # 模拟重启:重置全局单例,再从文件恢复(与 runtime_core._load_state 相同调用)
    ps_mod.reset_personality_state()
    fresh = ps_mod.get_personality_state()
    assert fresh.version == 0
    ps_mod.load_personality_state(
        path=str(ctx["ps_path"]),
        into=fresh,
        current_version_ceiling=int(getattr(fresh, "version", 0) or 0),
    )
    assert fresh.version == 1
    assert fresh.traits["curiosity"] == pytest.approx(0.73)
    assert fresh.has_proposal_been_applied("t5-restart")

    # 版本单调保护:旧版本文件不能覆盖更高版本的内存态
    bogus = {"version": 0, "traits": {"curiosity": 0.1}, "applied_proposal_ids": []}
    old_path = ctx["tmp"] / "old_state.json"
    old_path.write_text(json.dumps(bogus), encoding="utf-8")
    ps_mod.load_personality_state(
        path=str(old_path), into=fresh,
        current_version_ceiling=int(getattr(fresh, "version", 0) or 0),
    )
    assert fresh.version == 1
    assert fresh.traits["curiosity"] == pytest.approx(0.73)


# ---------------------------------------------------------------- case 6
def test_6_duplicate_apply_is_idempotent(isolated):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t6-dup",
        after_state={"playfulness": 0.58}, before_state={"playfulness": 0.55},
    )
    r1 = _drain(ctx)
    assert r1["applied"] == 1, r1
    ps = ps_mod.get_personality_state()
    assert ps.traits["playfulness"] == pytest.approx(0.58)
    assert ps.version == 1

    # 人为把 governance proposal 退回 approved(模拟标记失败后的重试场景)
    applied = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPLIED"])
    legacy = next(p for p in applied if p.proposal_id == "t6-dup")
    legacy.status = PROPOSAL_STATUS["APPROVED"]
    ctx["storage"].save(legacy)

    r2 = _drain(ctx)
    assert r2["applied"] == 0, r2
    assert r2["skipped"] == 1
    assert r2["details"][0]["reason"] == "already_applied"
    # 无二次增长
    assert ps.traits["playfulness"] == pytest.approx(0.58)
    assert ps.version == 1


# ---------------------------------------------------------------- case 7
def test_7_save_failure_keeps_proposal_approved(isolated, monkeypatch):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t7-savefail",
        after_state={"independence": 0.63}, before_state={"independence": 0.6},
    )
    monkeypatch.setattr(
        ps_mod, "save_personality_state",
        lambda state=None, path=None: False,
    )
    result = _drain(ctx)
    assert result["applied"] == 0, result
    assert result["failed"] == 1
    assert result["details"][0]["reason"] == "save_failed"
    # 保持 approved:可下轮/重启后重试;未误标 applied
    approved = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPROVED"])
    assert any(p.proposal_id == "t7-savefail" for p in approved)
    applied = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPLIED"])
    assert not any(p.proposal_id == "t7-savefail" for p in applied)
    assert not ctx["ps_path"].exists()


# ---------------------------------------------------------------- case 8
def test_8_admin_review_only_marks_status(isolated):
    ctx = isolated
    _legacy_proposal(
        ctx["storage"], "t8-admin",
        after_state={"empathy": 0.68}, before_state={"empathy": 0.65},
        status=PROPOSAL_STATUS["PENDING"],
    )
    from src.admin.governance_provider import GovernanceProvider

    provider = GovernanceProvider(
        runtime_provider=object(),  # 哑对象:避免连接生产 RuntimeProvider 单例
        proposal_storage=ctx["storage"],
    )
    req = GrowthProposalReviewRequest(
        proposal_id="t8-admin", action="approve", reason="test",
    )
    res = provider.review_proposal(req, actor="admin_test")
    assert res.get("success") is True, res
    # 只标记 approved:人格未变、无落盘、proposal 未标 applied
    ps = ps_mod.get_personality_state()
    assert ps.traits["empathy"] == pytest.approx(0.65)
    assert ps.version == 0
    assert not ctx["ps_path"].exists()
    approved = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPROVED"])
    assert any(p.proposal_id == "t8-admin" for p in approved)
    applied = ctx["storage"].list_by_status(PROPOSAL_STATUS["APPLIED"])
    assert not any(p.proposal_id == "t8-admin" for p in applied)
    # 随后 Runtime drain 才真正 apply
    result = _drain(ctx)
    assert result["applied"] == 1
    assert ps_mod.get_personality_state().traits["empathy"] == pytest.approx(0.68)


# ---------------------------------------------------------------- case 9
def test_9_stage13_contract_and_disabled_noop(isolated):
    ctx = isolated
    # stage 13 定义仍在,drain 已接入其函数体;阶段表未被改动
    src = Path(rc_mod.__file__).read_text(encoding="utf-8")
    assert "def _stage_13_self_model_persistence" in src
    stage13_body = src.split("def _stage_13_self_model_persistence", 1)[1]
    stage13_body = stage13_body.split("    def _sm_chain_mark", 1)[0]
    assert "drain_approved_growth_proposals()" in stage13_body
    # 开关关闭 → 空转,不触碰存储与人格
    ctx["core"].config = {
        "growth_apply_drain_enabled": False, "growth_apply_drain_limit": 5,
    }
    result = _drain(ctx)
    assert result["applied"] == 0
    assert result["reason"] == "disabled_by_config"
    assert ps_mod.get_personality_state().version == 0
    assert not ctx["ps_path"].exists()
