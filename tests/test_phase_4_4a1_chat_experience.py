"""
Phase 4.4-A1 — Chat Turn Experience Production 验收测试（E1~E5）

任务卡验收：
- E1: 多轮模拟 journal > 0（修复前 100 轮 = 0）
- E2: Experience 完整（user_input / assistant_response / timestamp / relationship 证据）
- E3: Growth 重新获得输入（至少 pending proposal）
- E4: 无人格污染（personality delta = 0，除非 approval）
- E5: 重启恢复（journal 数量一致）
附带 P2：builder 悬挂不再累积
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid

import pytest


def _make_rc(tmp: str):
    from src.runtime.runtime_core import RuntimeCore

    rc = RuntimeCore(config={
        "adapters_enabled": True,
        "relationship_enabled": True,
        "experience_enabled": True,
        "event_driven_enabled": True,
        "memory_store_path": os.path.join(tmp, "mem.json"),
        "experience_journal_path": os.path.join(tmp, "exp_journal.jsonl"),
        "growth_proposals_path": os.path.join(tmp, "gp.json"),
        "state_file": os.path.join(tmp, "runtime.json"),
    })
    # 测试侧确定性配置：模拟轮次耗时可能 <10ms，
    # 会被 ExperienceValidator.min_duration_ms(=10) 当作无效经历丢弃（生产规则保持不变）。
    # 本测试验收的是"接线是否落账"，不是耗时校验，故测试内关闭该门槛。
    if rc.experience_builder is not None:
        rc.experience_builder.validator.min_duration_ms = 0.0
    return rc


def _make_service(tmp: str):
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal_manager import ProposalManager
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    growth_history = PersonalityGrowthHistory()
    store = ProposalStore(path=os.path.join(tmp, "proposals.jsonl"))
    manager = ProposalManager(
        store=store, growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    return GrowthIntegrationService(
        proposal_manager=manager, growth_history=growth_history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )


def _ev(text: str):
    from src.runtime.events import Event

    return Event(type="user_input", source="user", payload={"text": text, "content": text})


def _journal_records(rc):
    return rc.memory_adapter._journal.load()


# ============================================================
# E1 + E2：对话轮经历真实落账且内容完整
# ============================================================

class TestE1E2ChatExperienceProduced:
    def test_chat_rounds_write_journal(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            for i in range(5):
                rc.process(_ev(f"第{i}轮：我们继续讨论羽依的架构"))

            records = _journal_records(rc)
            assert len(records) == 5, (
                f"5 轮对话应产生 5 条经历（修复前为 0），实际={len(records)}"
            )

    def test_experience_record_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            msg = "我们一起继续开发羽依这个项目"
            rc.process(_ev(msg))

            records = _journal_records(rc)
            assert len(records) == 1
            md = records[0]["metadata"]

            # E2 四项必须内容
            assert md.get("user_input") == msg, "user_input 必须是本轮用户真实输入"
            assert "assistant_response" in md, "必须记录 assistant_response 键"
            assert records[0].get("timestamp"), "必须有 timestamp"
            assert "relationship_stage" in md, "必须记录关系证据（stage）"

            # 既有契约不变：user_response = 用户真实输入（4.3 Stage 4 投影依赖）
            assert md.get("user_response") == msg
            assert md.get("type") == "runtime_experience"
            assert md.get("action_type") == "conversation"

    def test_tick_like_empty_turn_no_junk(self):
        """无用户输入且无回复的轮次 → cancel，不落垃圾记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            rc._stage_16_response(None, _empty_ctx())
            assert _journal_records(rc) == []


def _empty_ctx():
    from src.runtime.context import RuntimeContext

    return RuntimeContext()


# ============================================================
# P2：builder 悬挂不再累积
# ============================================================

class TestP2BuilderNoLeak:
    def test_building_cleared_each_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            for i in range(10):
                rc.process(_ev(f"第{i}轮普通聊天"))
                pending = len(rc.experience_builder._building)
                assert pending == 0, (
                    f"第{i}轮后 builder 仍有 {pending} 个悬挂（修复前线性累积）"
                )

    def test_building_cap_drops_oldest(self):
        from src.runtime.experience_builder import (
            ExperienceBuilder, ExperienceBuilderConfig,
        )

        builder = ExperienceBuilder(
            config=ExperienceBuilderConfig(max_building_size=5)
        )
        for i in range(10):
            builder.start_building(
                trigger_event={"event_type": "x", "data": {}},
                trigger_type="event",
                self_state_before={},
            )
        assert len(builder._building) == 5, "悬挂必须被上限截断"


# ============================================================
# E3：Growth 重新获得输入（pending proposal）
# ============================================================

class TestE3GrowthFedAgain:
    def test_chat_rounds_feed_growth_to_pending_proposal(self):
        """重复创造类真实表达 → journal → Stage 4 → GracePeriod → pending proposal。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            svc = _make_service(tmp)
            rc._growth_integration_service = svc

            tag = uuid.uuid4().hex[:6]
            for i in range(6):
                rc.process(_ev(f"我已经创建了一个新角色，设计了她的形象和性格（{tag}）"))

            proposals = svc.list_proposals(status="pending", limit=10)
            assert len(proposals) >= 1, (
                "对话经历应经 Stage 4 产生 pending proposal（E3）"
            )
            # trace 证明 Stage 4 真的在消费经历
            ctx = rc.get_last_process_ctx()
            trace = (getattr(ctx, "lifecycle_trace", None) or {}).get(
                "growth_activation", {}
            )
            assert trace.get("status") == "activated"
            assert trace.get("experience_count", 0) >= 1


# ============================================================
# E4：无人格污染
# ============================================================

class TestE4NoPersonalityPollution:
    def test_no_personality_change_without_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            svc = _make_service(tmp)
            rc._growth_integration_service = svc

            tag = uuid.uuid4().hex[:6]
            for i in range(10):
                rc.process(_ev(f"我已经创建了一个新角色，设计了她的形象（{tag}）"))

            # 提案存在但全部 pending
            pending = svc.list_proposals(status="pending", limit=20)
            assert len(pending) >= 1
            # 人格成长历史为零（未经 approval 不得有任何应用）
            assert svc.growth_count() == 0
            # personality resolver 快照与初始一致（无自动漂移）
            pr = rc.get_personality_resolver()
            if pr is not None:
                snap = pr.resolve()
                d = snap.to_dict() if hasattr(snap, "to_dict") else {}
                traits = d.get("traits", {}) or {}
                for k, v in traits.items():
                    assert abs(float(v) - 0.5) < 0.05, (
                        f"未经审批人格维度 {k} 不应偏离初始值，实际={v}"
                    )


# ============================================================
# E5：重启恢复
# ============================================================

class TestE5RestartRecovery:
    def test_journal_consistent_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc1 = _make_rc(tmp)
            for i in range(5):
                rc1.process(_ev(f"第{i}轮：继续讨论架构设计"))
            count_before = len(_journal_records(rc1))
            assert count_before == 5

            # 模拟重启：全新 RuntimeCore，同一数据目录
            rc2 = _make_rc(tmp)
            count_after = len(_journal_records(rc2))
            assert count_after == count_before, (
                f"重启后 journal 数量必须一致：{count_before} → {count_after}"
            )

            # 新实例可继续累积（不是只读恢复）
            rc2.process(_ev("重启后的第一轮对话"))
            assert len(_journal_records(rc2)) == count_before + 1


# ============================================================
# 不重复完成（action 轮防护）
# ============================================================

class TestNoDoubleFinish:
    def test_second_finish_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            rc.process(_ev("正常一轮对话"))
            assert len(_journal_records(rc)) == 1
            # 再次调用 finish（_building_experience_id 已清空）→ 不落第二条
            rc._finish_chat_turn_experience(_empty_ctx())
            assert len(_journal_records(rc)) == 1
