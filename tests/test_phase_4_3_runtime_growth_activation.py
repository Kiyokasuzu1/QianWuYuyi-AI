"""
Phase 4.3 — Runtime Growth Activation 验收测试（GROWTH-R1 ~ R5）

任务卡红线：
- Stage 4 唯一入口 = GrowthIntegrationService.accept_experience
- 经历必须来自 ExperienceJournal（真实持久化经历）
- 不直接调 GrowthEngine / 不改 personality / 不 apply proposal
- lifecycle_trace["growth_activation"] 必须可见（activated/no_experience/failed）

设计说明（如实）：
- GrowthEligibilityFilter 的 GracePeriod 是既有时机门控：
  首次观察只记账不通过，第二次命中才放行。
  因此 R1 需要跑两次 Stage 4 才产生 proposal —— 这是设计语义，不是缺陷。
- 每个测试用 uuid 文本避免 EligibilityFilter 进程级 ledger 的跨测试污染。
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest


# ============================================================
# Helpers
# ============================================================

def _make_runtime(tmp: str):
    """构造 tmp 隔离的 RuntimeCore（journal 派生在 tmp 目录）。"""
    from src.runtime.runtime_core import RuntimeCore

    return RuntimeCore(config={
        "adapters_enabled": True,
        "memory_store_path": os.path.join(tmp, "mem.json"),
        "growth_proposals_path": os.path.join(tmp, "gp.json"),
        "state_file": os.path.join(tmp, "runtime.json"),
    })


def _make_service(tmp: str, name: str = "proposals.jsonl"):
    """tmp 隔离的 GrowthIntegrationService（安全配置不弱化）。"""
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal_manager import ProposalManager
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    growth_history = PersonalityGrowthHistory()
    store = ProposalStore(path=os.path.join(tmp, name))
    manager = ProposalManager(
        store=store,
        growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    return GrowthIntegrationService(
        proposal_manager=manager,
        growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )


def _journal_path(rc) -> str:
    return rc.memory_adapter._journal.path


def _seed_experience(rc, user_text: str, *, exp_id: str = "") -> str:
    """以真实持久化形态写入一条经历记录（含真实用户文本）。"""
    exp_id = exp_id or f"exp_{uuid.uuid4().hex[:12]}"
    record = {
        "id": f"jr_{uuid.uuid4().hex[:12]}",
        "user_id": "yuyi",
        "content": f"chat | trigger=user_input | success | duration=100ms",
        "role": "system",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "metadata": {
            "type": "runtime_experience",
            "experience_id": exp_id,
            "action_type": "chat",
            "trigger_type": "user_input",
            "success": True,
            "user_response": user_text,
        },
    }
    with open(_journal_path(rc), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return exp_id


def _run_stage4(rc, ctx=None):
    """直接调用 Stage 4 handler（生产由 LifecycleExecutor 分派）。"""
    from src.runtime.context import RuntimeContext

    ctx = ctx or RuntimeContext()
    rc._stage_04_growth_evaluation(None, ctx)
    return ctx


def _trace(ctx) -> Dict[str, Any]:
    return (getattr(ctx, "lifecycle_trace", None) or {}).get("growth_activation", {})


# 创造类文本：event_type=creation（TYPE_WEIGHTS 0.9）+ 行为标记（reliability 1.0）
# → confidence ≈ 0.945 ≥ 0.8 门槛，可走完真实管线产生 pending proposal
def _creation_text() -> str:
    return f"我已经创建了一个新角色，设计了她的形象和性格（{uuid.uuid4().hex[:6]}）"


# ============================================================
# GROWTH-R1：经历进入 Growth（ExperienceJournal → Stage4 → Proposal）
# ============================================================

class TestGrowthR1ExperienceEntersGrowth:
    def test_journal_experience_produces_pending_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_runtime(tmp)
            svc = _make_service(tmp)
            rc._growth_integration_service = svc

            _seed_experience(rc, _creation_text())

            # 第一次：GracePeriod 首次观察（只记账，设计语义）
            ctx1 = _run_stage4(rc)
            assert _trace(ctx1)["status"] == "activated"
            assert _trace(ctx1)["experience_count"] == 1

            # 第二次：GracePeriod 命中 → 完整管线 → pending proposal
            ctx2 = _run_stage4(rc)
            trace = _trace(ctx2)
            assert trace["status"] == "activated"
            assert trace["proposal_count"] >= 1, (
                f"第二次 Stage 4 应产生 proposal，trace={trace}"
            )
            assert len(ctx2.growth_proposals) >= 1
            proposal = ctx2.growth_proposals[0]
            assert proposal.status == "pending"  # auto_accept=False 冻结
            assert proposal.schema_version == "1.0"  # canonical

            # 证据锚定：evidence_ids 必须指向 journal 经历记录
            journal_records = json.loads(
                open(_journal_path(rc), encoding="utf-8").readline()
            )
            assert journal_records["id"] in proposal.evidence_ids, (
                f"proposal.evidence_ids 必须锚定 journal 记录，"
                f"实际={proposal.evidence_ids}"
            )
            # Phase 4.3-D 回归：source_event_id 必须存在且锚定 journal 记录
            # （修复前恒为 None —— source_event 缺 "id" 键的契约缝隙）
            assert proposal.source_event_id == f"evt_{journal_records['id']}"

    def test_no_fake_record_used(self):
        """journal 里没有的经历绝不参与（无 user_response 的经历被跳过）。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_runtime(tmp)
            rc._growth_integration_service = _make_service(tmp)

            # 写入一条无用户文本的经历
            record = {
                "id": f"jr_{uuid.uuid4().hex[:12]}",
                "user_id": "yuyi",
                "content": "chat | trigger=system_tick | success",
                "role": "system",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metadata": {
                    "type": "runtime_experience",
                    "experience_id": f"exp_{uuid.uuid4().hex[:8]}",
                    "user_response": None,
                },
            }
            with open(_journal_path(rc), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            ctx = _run_stage4(rc)
            assert _trace(ctx)["status"] == "no_experience"
            assert ctx.growth_proposals == []


# ============================================================
# GROWTH-R2：Growth 不直接改变人格（snapshot 不变 + proposal pending）
# ============================================================

class TestGrowthR2NoDirectPersonalityChange:
    def test_stage4_does_not_mutate_personality(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_runtime(tmp)
            svc = _make_service(tmp)
            rc._growth_integration_service = svc

            _seed_experience(rc, _creation_text())
            _run_stage4(rc)
            ctx = _run_stage4(rc)

            assert len(ctx.growth_proposals) >= 1, "前置：应已产生 pending proposal"

            # 人格未被直接改变：growth_history 无任何已应用记录
            assert svc.growth_count() == 0, (
                "Stage 4 不得写入任何 growth history（人格变更必须走审批）"
            )
            # 所有提案停留在 pending
            for p in ctx.growth_proposals:
                assert p.status == "pending"


# ============================================================
# GROWTH-R3：审批后才改变
# ============================================================

class TestGrowthR3ChangeOnlyAfterApproval:
    def test_personality_change_requires_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_runtime(tmp)
            svc = _make_service(tmp)
            rc._growth_integration_service = svc

            _seed_experience(rc, _creation_text())
            _run_stage4(rc)
            ctx = _run_stage4(rc)
            assert len(ctx.growth_proposals) >= 1
            pid = ctx.growth_proposals[0].id

            # 审批前：无 growth 记录
            assert svc.growth_count() == 0

            # 审批 + 应用
            result = svc.apply_proposal(pid, actor="user")
            assert result.get("status") == "applied", f"apply 失败: {result}"

            # 审批后：人格成长链路真实发生（growth history 有记录）
            assert svc.growth_count() >= 1


# ============================================================
# GROWTH-R4：无经历安全运行
# ============================================================

class TestGrowthR4NoExperienceSafe:
    def test_empty_journal_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_runtime(tmp)
            rc._growth_integration_service = _make_service(tmp)

            ctx = _run_stage4(rc)
            assert _trace(ctx)["status"] == "no_experience"
            assert ctx.growth_proposals == []
            # 无错误记录
            errors = getattr(ctx, "_phase_errors", {}) or {}
            assert "GROWTH_EVALUATION" not in errors

    def test_service_unavailable_traced_failed(self):
        """装配失败必须可见（4.3-B：断点不再隐形）。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_runtime(tmp)
            rc._get_growth_integration_service = lambda: None  # type: ignore[method-assign]

            ctx = _run_stage4(rc)
            trace = _trace(ctx)
            assert trace["status"] == "failed"
            assert trace["reason"] == "growth_integration_service_unavailable"


# ============================================================
# GROWTH-R5：跨重启（journal 持久 → 新 RuntimeCore → proposal 存在）
# ============================================================

class TestGrowthR5CrossRestart:
    def test_proposal_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            # 第一次"进程"：产生 proposal
            rc1 = _make_runtime(tmp)
            svc1 = _make_service(tmp)
            rc1._growth_integration_service = svc1

            _seed_experience(rc1, _creation_text())
            _run_stage4(rc1)
            ctx = _run_stage4(rc1)
            assert len(ctx.growth_proposals) >= 1
            pid = ctx.growth_proposals[0].id

            # 模拟重启：全新 RuntimeCore + 全新 service（同一 tmp 数据目录）
            rc2 = _make_runtime(tmp)
            svc2 = _make_service(tmp)
            rc2._growth_integration_service = svc2

            # journal 里的经历仍在（真实持久化）
            ctx2 = _run_stage4(rc2)
            assert _trace(ctx2)["status"] == "activated"

            # proposal 跨重启存在（ProposalStore 持久化）
            restored = svc2.get_proposal(pid)
            assert restored is not None, "proposal 必须跨重启存在"
            assert restored.status == "pending"
