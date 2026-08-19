# -*- coding: utf-8 -*-
"""
tests/test_proposal_review_workflow.py

Phase C.4.6.3 — Proposal Review Workflow 端到端测试

目标:
验证完整的 proposal review 工作流:
- pending proposal 创建与读取
- approve 流程
- reject 流程
- archive 流程
- 无 reviewer 时拒绝
- 重复 review 防护
- auto_accept 关闭
- 审计字段完整性
- 状态机约束

覆盖范围(根据 Phase C.4.6.3 要求):
1. pending proposal 可以正常创建与查询
2. approve 流程可正确执行并写入审计
3. reject 流程可正确执行并写入审计
4. archive 流程可正确执行并写入审计
5. 无 reviewer 时所有操作被拒绝
6. 已 review 的 proposal 不可重复 review(需 reset)
7. auto_accept_enabled=True 被强制禁止
8. 审计字段(proposal_id / reviewer_id / decision / timestamp / comment)完整
9. review_history 正确追加
10. reset_to_pending 允许重新 review

约束:
- 不修改 GrowthEvaluator
- 不修改 ProposalManager 核心算法
- 不修改 CoreIdentity
- 不自动生成 SelfModel 数据
- 只验证 review workflow / audit / tests
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("YUYI_LLM_MOCK", "1")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_pending_proposals():
    """3 个 pending proposal 的样本数据。"""
    return {
        "version": "1.0",
        "proposals": [
            {
                "proposal_id": "prop_wf_001",
                "timestamp": "2026-08-02T10:00:00",
                "proposal_type": "preference",
                "status": "pending",
                "source": "growth_evaluator",
                "source_event_id": "evt_wf_001",
                "user_id": "user_yuyi",
                "affected_dimensions": {"reading_habit": 0.7},
                "before_state": {"reading_habit": 0.0},
                "after_state": {"reading_habit": 0.7},
                "confidence": 0.85,
                "reason": "用户在3次对话中表达相同偏好",
                "evidence": [
                    {"text": "我喜欢晚上看书", "role": "user", "source_index": 0},
                    {"text": "晚上看书比较安静", "role": "user", "source_index": 1},
                ],
                "priority": "normal",
            },
            {
                "proposal_id": "prop_wf_002",
                "timestamp": "2026-08-02T11:00:00",
                "proposal_type": "memory_growth",
                "status": "pending",
                "source": "growth_evaluator",
                "source_event_id": "evt_wf_002",
                "user_id": "user_yuyi",
                "affected_dimensions": {"knowledge_topic": "文学"},
                "before_state": {},
                "after_state": {"knowledge_topic": "文学"},
                "confidence": 0.75,
                "reason": "用户多次提到文学相关话题",
                "evidence": [
                    {"text": "我最近在读村上春树", "role": "user", "source_index": 0},
                ],
                "priority": "normal",
            },
            {
                "proposal_id": "prop_wf_003",
                "timestamp": "2026-08-02T12:00:00",
                "proposal_type": "relationship",
                "status": "pending",
                "source": "growth_evaluator",
                "source_event_id": "evt_wf_003",
                "user_id": "user_yuyi",
                "affected_dimensions": {"trust": 0.05},
                "before_state": {"trust": 0.5},
                "after_state": {"trust": 0.55},
                "confidence": 0.55,
                "reason": "单次对话触发,信心偏低",
                "evidence": [
                    {"text": "谢谢你", "role": "user", "source_index": 0},
                ],
                "priority": "low",
            },
        ],
    }


@pytest.fixture
def proposals_file(tmp_path, sample_pending_proposals):
    """创建临时 proposals.json 文件。"""
    f = tmp_path / "proposals.json"
    f.write_text(
        json.dumps(sample_pending_proposals, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return f


@pytest.fixture
def reviewer(proposals_file):
    """创建 ProposalReviewer 实例。"""
    from src.growth.proposal_review import ProposalReviewer

    return ProposalReviewer(proposals_file)


# ============================================================
# 1. Pending Proposal 状态
# ============================================================

class TestPendingProposal:
    """验证 pending 状态的 proposal 可正常读取与查询。"""

    def test_list_pending_returns_all_pending(self, reviewer):
        """list_pending() 应返回全部 pending proposal。"""
        pending = reviewer.list_pending()
        assert len(pending) == 3
        for p in pending:
            assert reviewer._get_status(p) in ("pending", "proposed")

    def test_pending_proposal_has_initial_fields(self, reviewer):
        """pending proposal 应包含所有初始字段。"""
        pending = reviewer.list_pending()
        first = pending[0]
        # 必备字段
        assert reviewer._get_id(first) == "prop_wf_001"
        assert "proposal_type" in first
        assert "source_event_id" in first
        assert "user_id" in first
        assert "confidence" in first
        assert "evidence" in first
        # 不应有 review 字段(初始 pending)
        assert first.get("reviewer_id", "") in (None, "")
        assert first.get("review_comment", "") in (None, "")
        assert first.get("reviewed_at") in (None, "")

    def test_get_detail_pending(self, reviewer):
        """get_detail() 应返回 pending proposal 的完整信息。"""
        detail = reviewer.get_detail("prop_wf_001")
        assert detail is not None
        assert detail["id"] == "prop_wf_001"
        assert detail["status"] == "pending"
        assert detail["proposal_type"] == "preference"
        assert detail["confidence"] == 0.85
        assert len(detail["evidence"]) >= 1
        assert len(detail["change_items"]) >= 1

    def test_count_pending(self, reviewer):
        """summary 应正确统计 pending 数。"""
        summary = reviewer.summary()
        assert summary["pending_count"] == 3
        assert summary["total"] == 3


# ============================================================
# 2. Approve 流程
# ============================================================

class TestApproveWorkflow:
    """验证 approve 流程。"""

    def test_approve_changes_status_to_approved(self, reviewer):
        """approve 后 status 应变为 approved。"""
        result = reviewer.approve("prop_wf_001", reviewer="alice", comment="证据充分")
        assert result["success"] is True
        assert result["new_status"] == "approved"
        assert result["decision"] == "approved"
        assert result["proposal_id"] == "prop_wf_001"

        # 验证持久化
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "approved"
        assert p.get("decision") == "approved"

    def test_approve_records_audit_fields(self, reviewer):
        """approve 必须记录完整审计字段。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="证据充分")

        p = reviewer.get("prop_wf_001")
        # 必备审计字段
        assert p.get("reviewer_id") == "alice"
        assert p.get("review_comment") == "证据充分"
        assert p.get("reviewed_at") is not None
        assert p.get("decision") == "approved"

        # review_history 必须追加
        history = p.get("review_history", [])
        assert len(history) == 1
        assert history[0]["decision"] == "approved"
        assert history[0]["reviewer_id"] == "alice"
        assert history[0]["review_comment"] == "证据充分"
        assert "reviewed_at" in history[0]

    def test_approve_timestamp_is_iso(self, reviewer):
        """reviewed_at 必须是 ISO 8601 格式。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        p = reviewer.get("prop_wf_001")
        ts = p.get("reviewed_at")
        assert ts is not None
        # 必须包含 'T' 和 'Z' 或时区
        assert "T" in ts

    def test_approve_proposal_id_recorded(self, reviewer):
        """审计日志必须包含 proposal_id。"""
        result = reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        assert "proposal_id" in result
        assert result["proposal_id"] == "prop_wf_001"

        # review_history 中也应能反查 proposal_id(通过定位 proposal)
        p = reviewer.get(result["proposal_id"])
        assert p is not None


# ============================================================
# 3. Reject 流程
# ============================================================

class TestRejectWorkflow:
    """验证 reject 流程。"""

    def test_reject_changes_status_to_rejected(self, reviewer):
        """reject 后 status 应变为 rejected。"""
        result = reviewer.reject("prop_wf_002", reviewer="bob", comment="证据不足")
        assert result["success"] is True
        assert result["new_status"] == "rejected"
        assert result["decision"] == "rejected"

        p = reviewer.get("prop_wf_002")
        assert reviewer._get_status(p) == "rejected"
        assert p.get("decision") == "rejected"

    def test_reject_records_audit_fields(self, reviewer):
        """reject 必须记录完整审计字段(comment 强制)。"""
        reviewer.reject("prop_wf_002", reviewer="bob", comment="证据仅 1 条")

        p = reviewer.get("prop_wf_002")
        assert p.get("reviewer_id") == "bob"
        assert p.get("review_comment") == "证据仅 1 条"
        assert p.get("reviewed_at") is not None
        assert p.get("decision") == "rejected"

        # review_history
        history = p.get("review_history", [])
        assert len(history) == 1
        assert history[0]["decision"] == "rejected"
        assert history[0]["review_comment"] == "证据仅 1 条"

    def test_reject_with_reason_archived(self, reviewer):
        """reject 时 comment 可包含拒绝理由。"""
        long_comment = (
            "用户仅在单次对话中表达了观点,"
            "且与已有 preference 数据不一致,建议改为 trace 级别"
        )
        result = reviewer.reject(
            "prop_wf_003",
            reviewer="charlie",
            comment=long_comment,
        )
        assert result["success"] is True
        p = reviewer.get("prop_wf_003")
        assert p.get("review_comment") == long_comment


# ============================================================
# 4. Archive 流程
# ============================================================

class TestArchiveWorkflow:
    """验证 archive 流程。"""

    def test_archive_changes_status_to_archived(self, reviewer):
        """archive 后 status 应变为 archived。"""
        result = reviewer.archive("prop_wf_003", reviewer="dave", comment="过期")
        assert result["success"] is True
        assert result["new_status"] == "archived"
        assert result["decision"] == "archived"

        p = reviewer.get("prop_wf_003")
        assert reviewer._get_status(p) == "archived"

    def test_archive_records_audit_fields(self, reviewer):
        """archive 必须记录完整审计字段。"""
        reviewer.archive("prop_wf_003", reviewer="dave", comment="超过 30 天")

        p = reviewer.get("prop_wf_003")
        assert p.get("reviewer_id") == "dave"
        assert p.get("review_comment") == "超过 30 天"
        assert p.get("reviewed_at") is not None
        assert p.get("decision") == "archived"


# ============================================================
# 5. 无 Reviewer 拒绝
# ============================================================

class TestNoReviewerRejection:
    """验证无 reviewer 或非法 reviewer 时必须拒绝。"""

    def test_approve_without_reviewer_rejected(self, reviewer):
        """approve 不传 reviewer 必须失败。"""
        result = reviewer.approve("prop_wf_001", reviewer="", comment="ok")
        assert result["success"] is False
        # proposal 状态不应改变
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "pending"

    def test_reject_without_reviewer_rejected(self, reviewer):
        """reject 不传 reviewer 必须失败。"""
        result = reviewer.reject("prop_wf_002", reviewer="", comment="ok")
        assert result["success"] is False
        p = reviewer.get("prop_wf_002")
        assert reviewer._get_status(p) == "pending"

    def test_archive_without_reviewer_rejected(self, reviewer):
        """archive 不传 reviewer 必须失败。"""
        result = reviewer.archive("prop_wf_003", reviewer="", comment="ok")
        assert result["success"] is False
        p = reviewer.get("prop_wf_003")
        assert reviewer._get_status(p) == "pending"

    def test_script_reviewer_forbidden(self, reviewer):
        """'script' reviewer 名称被禁止。"""
        for forbidden in ["script", "auto", "agent", "bot", "system", "llm"]:
            result = reviewer.approve(
                "prop_wf_001",
                reviewer=forbidden,
                comment="ok",
            )
            assert result["success"] is False
            assert "error" in result
            # proposal 不应被修改
            p = reviewer.get("prop_wf_001")
            assert reviewer._get_status(p) == "pending"

    def test_nonexistent_proposal_rejected(self, reviewer):
        """不存在的 proposal_id 应返回 proposal_not_found。"""
        result = reviewer.approve("prop_nonexistent", reviewer="alice", comment="ok")
        assert result["success"] is False
        assert result.get("error") == "proposal_not_found"


# ============================================================
# 6. 重复 Review 防护
# ============================================================

class TestDuplicateReviewProtection:
    """验证已 review 的 proposal 不可重复 review(除非 reset)。"""

    def test_cannot_approve_already_approved(self, reviewer):
        """已 approved 的 proposal 不可再次 approve。"""
        # 第一次 approve
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        # 第二次 approve 应失败
        result = reviewer.approve("prop_wf_001", reviewer="bob", comment="again")
        assert result["success"] is False
        assert result.get("error") == "already_reviewed"
        assert result.get("current_status") == "approved"

    def test_cannot_reject_already_rejected(self, reviewer):
        """已 rejected 的 proposal 不可再次 reject。"""
        reviewer.reject("prop_wf_001", reviewer="alice", comment="证据不足")
        result = reviewer.reject("prop_wf_001", reviewer="bob", comment="again")
        assert result["success"] is False
        assert result.get("error") == "already_reviewed"

    def test_cannot_archive_already_archived(self, reviewer):
        """已 archived 的 proposal 不可再次 archive。"""
        reviewer.archive("prop_wf_001", reviewer="alice", comment="过期")
        result = reviewer.archive("prop_wf_001", reviewer="bob", comment="again")
        assert result["success"] is False
        assert result.get("error") == "already_reviewed"

    def test_cannot_mix_review_operations(self, reviewer):
        """已 approved 的 proposal 不可 reject 或 archive。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        # 尝试 reject
        result = reviewer.reject("prop_wf_001", reviewer="bob", comment="change mind")
        assert result["success"] is False
        assert result.get("error") == "already_reviewed"
        # 尝试 archive
        result = reviewer.archive("prop_wf_001", reviewer="bob", comment="change mind")
        assert result["success"] is False
        assert result.get("error") == "already_reviewed"

    def test_reset_allows_re_review(self, reviewer):
        """reset_to_pending 后可重新 review。"""
        # 1. 第一次 review
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        # 2. 再次 review 应失败
        result = reviewer.approve("prop_wf_001", reviewer="bob", comment="again")
        assert result["success"] is False
        # 3. reset
        reset_result = reviewer.reset_to_pending(
            "prop_wf_001", reviewer="supervisor", comment="误操作,需重审"
        )
        assert reset_result["success"] is True
        # 4. 重新 review 应成功
        result = reviewer.reject("prop_wf_001", reviewer="bob", comment="重审后拒绝")
        assert result["success"] is True
        # 5. 最终状态为 rejected
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "rejected"

    def test_reset_appends_to_history(self, reviewer):
        """reset_to_pending 必须追加到 review_history。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        reviewer.reset_to_pending("prop_wf_001", reviewer="supervisor", comment="reset")
        p = reviewer.get("prop_wf_001")
        history = p.get("review_history", [])
        # 应包含 approve + reset 两条记录
        assert len(history) == 2
        assert history[0]["decision"] == "approved"
        assert history[1]["decision"] == "reset"


# ============================================================
# 7. Auto Accept 关闭
# ============================================================

class TestAutoAcceptDisabled:
    """验证 auto_accept_enabled=False 强制。"""

    def test_auto_accept_disabled_assertion(self):
        """assert_auto_accept_disabled 在 auto_accept=True 时必须抛出。"""
        from src.growth import proposal_review as pr_mod

        # 保存原值
        original = pr_mod.AUTO_ACCEPT_ENABLED

        try:
            # 模拟危险配置
            pr_mod.AUTO_ACCEPT_ENABLED = True
            with pytest.raises(RuntimeError) as exc_info:
                pr_mod.assert_auto_accept_disabled()
            assert "auto_accept_enabled=True" in str(exc_info.value)
        finally:
            pr_mod.AUTO_ACCEPT_ENABLED = original

    def test_auto_accept_disabled_default(self):
        """默认 auto_accept_enabled 必须为 False。"""
        from src.growth import proposal_review as pr_mod

        # 重新加载以确保默认值
        assert pr_mod.AUTO_ACCEPT_ENABLED is False

    def test_review_operations_blocked_when_auto_accept_enabled(self, proposals_file):
        """auto_accept=True 时所有 review 操作应被拒绝。"""
        from src.growth import proposal_review as pr_mod
        from src.growth.proposal_review import ProposalReviewer

        original = pr_mod.AUTO_ACCEPT_ENABLED
        try:
            pr_mod.AUTO_ACCEPT_ENABLED = True
            reviewer = ProposalReviewer(proposals_file)
            # 任何 review 操作都应失败
            for action in ["approve", "reject", "archive"]:
                method = getattr(reviewer, action)
                result = method("prop_wf_001", reviewer="alice", comment="ok")
                assert result["success"] is False
                assert result.get("error") == "auto_accept_forbidden"
            # proposal 状态未改变
            p = reviewer.get("prop_wf_001")
            assert reviewer._get_status(p) == "pending"
        finally:
            pr_mod.AUTO_ACCEPT_ENABLED = original

    def test_reviewer_assertion_function(self):
        """assert_human_reviewer 应拒绝非法 reviewer。"""
        from src.growth.proposal_review import assert_human_reviewer

        # 合法 reviewer 应通过
        assert_human_reviewer("alice")
        assert_human_reviewer("human_operator_001")

        # 非法 reviewer 应抛出
        for forbidden in ["script", "auto", "agent", "bot", "system", "llm"]:
            with pytest.raises(ValueError) as exc_info:
                assert_human_reviewer(forbidden)
            assert forbidden in str(exc_info.value).lower()

        # 空 reviewer 应抛出
        with pytest.raises(ValueError):
            assert_human_reviewer("")


# ============================================================
# 8. 审计完整性
# ============================================================

class TestAuditCompleteness:
    """验证审计字段完整性(proposal_id / reviewer_id / decision / timestamp / comment)。"""

    def test_all_audit_fields_present_after_review(self, reviewer):
        """所有 review 后必须包含 5 个审计字段。"""
        for action, comment in [
            ("approve", "证据充分"),
            ("reject", "证据不足"),
            ("archive", "过期"),
        ]:
            pid = {"approve": "prop_wf_001", "reject": "prop_wf_002", "archive": "prop_wf_003"}[action]
            method = getattr(reviewer, action)
            result = method(pid, reviewer="alice", comment=comment)
            assert result["success"] is True

            p = reviewer.get(pid)
            # 1. proposal_id(通过 get 可访问)
            assert reviewer._get_id(p) == pid
            # 2. reviewer_id
            assert p.get("reviewer_id") == "alice"
            # 3. decision
            expected_decision_map = {
                "approve": "approved",
                "reject": "rejected",
                "archive": "archived",
            }
            assert p.get("decision") == expected_decision_map[action]
            # 4. timestamp
            assert p.get("reviewed_at") is not None
            # 5. comment
            assert p.get("review_comment") == comment

    def test_review_history_preserved_on_persistence(self, reviewer, proposals_file):
        """重新加载后 review_history 必须保留。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        reviewer.archive("prop_wf_002", reviewer="bob", comment="过期")

        # 重新加载
        from src.growth.proposal_review import ProposalReviewer

        reloaded = ProposalReviewer(proposals_file)

        p1 = reloaded.get("prop_wf_001")
        assert p1.get("reviewer_id") == "alice"
        assert p1.get("decision") == "approved"
        assert len(p1.get("review_history", [])) == 1

        p2 = reloaded.get("prop_wf_002")
        assert p2.get("reviewer_id") == "bob"
        assert p2.get("decision") == "archived"
        assert len(p2.get("review_history", [])) == 1

    def test_review_history_chronological(self, reviewer):
        """多次 review 的 history 必须按时间顺序追加。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        reviewer.reset_to_pending("prop_wf_001", reviewer="supervisor", comment="reset")
        reviewer.reject("prop_wf_001", reviewer="bob", comment="重审后拒绝")

        p = reviewer.get("prop_wf_001")
        history = p.get("review_history", [])
        assert len(history) == 3
        assert history[0]["decision"] == "approved"
        assert history[1]["decision"] == "reset"
        assert history[2]["decision"] == "rejected"

    def test_audit_log_includes_different_reviewers(self, reviewer):
        """不同 reviewer 的操作必须被独立记录。"""
        reviewer.approve("prop_wf_001", reviewer="alice", comment="alice 批准")
        reviewer.reject("prop_wf_002", reviewer="bob", comment="bob 拒绝")
        reviewer.archive("prop_wf_003", reviewer="charlie", comment="charlie 归档")

        p1 = reviewer.get("prop_wf_001")
        p2 = reviewer.get("prop_wf_002")
        p3 = reviewer.get("prop_wf_003")

        assert p1.get("reviewer_id") == "alice"
        assert p2.get("reviewer_id") == "bob"
        assert p3.get("reviewer_id") == "charlie"


# ============================================================
# 9. 状态机约束
# ============================================================

class TestStateMachine:
    """验证状态机的合法性。"""

    def test_valid_statuses(self):
        """VALID_STATUSES 必须包含 4 个固定状态。"""
        from src.growth.proposal_review import (
            PENDING_STATUS,
            APPROVED_STATUS,
            REJECTED_STATUS,
            ARCHIVED_STATUS,
            VALID_STATUSES,
        )

        assert PENDING_STATUS == "pending"
        assert APPROVED_STATUS == "approved"
        assert REJECTED_STATUS == "rejected"
        assert ARCHIVED_STATUS == "archived"
        assert VALID_STATUSES == {"pending", "approved", "rejected", "archived"}

    def test_only_four_statuses(self, reviewer):
        """summary 应能正确统计 4 个状态(含 0 计数的状态)。"""
        # 只 review 一条,验证状态计数
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")

        summary = reviewer.summary()
        # approved 状态必须出现
        assert "approved" in summary["by_status"]
        assert summary["by_status"]["approved"] == 1
        # approved_count / rejected_count / archived_count 字段必须存在
        assert summary["approved_count"] == 1
        assert summary["rejected_count"] == 0
        assert summary["archived_count"] == 0

    def test_initial_status_must_be_pending_or_legacy_proposed(self, tmp_path):
        """proposal 初始状态只能是 pending 或 legacy 'proposed'。"""
        from src.growth.proposal_review import ProposalReviewer

        data = {
            "version": "1.0",
            "proposals": [
                {
                    "proposal_id": "prop_init_001",
                    "status": "pending",
                    "proposal_type": "preference",
                    "confidence": 0.5,
                    "evidence": [],
                },
                {
                    "proposal_id": "prop_init_002",
                    "status": "proposed",  # legacy
                    "proposal_type": "preference",
                    "confidence": 0.5,
                    "evidence": [],
                },
            ],
        }
        f = tmp_path / "proposals.json"
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        reviewer = ProposalReviewer(f)
        pending = reviewer.list_pending()
        # pending + legacy proposed 都应被识别
        assert len(pending) == 2


# ============================================================
# 10. CLI 接口验证
# ============================================================

class TestCLI:
    """验证 CLI 接口可正常工作。"""

    def test_cli_main_list_pending(self, proposals_file, capsys):
        """CLI list --status pending 应输出 pending proposal。"""
        from src.growth.proposal_review import main

        argv = ["--proposals", str(proposals_file), "list", "--status", "pending"]
        rc = main(argv)
        assert rc == 0
        captured = capsys.readouterr()
        assert "prop_wf_001" in captured.out
        assert "prop_wf_002" in captured.out
        assert "prop_wf_003" in captured.out

    def test_cli_main_show(self, proposals_file, capsys):
        """CLI show 应输出 proposal 详情。"""
        from src.growth.proposal_review import main

        argv = ["--proposals", str(proposals_file), "show", "prop_wf_001"]
        rc = main(argv)
        assert rc == 0
        captured = capsys.readouterr()
        assert "prop_wf_001" in captured.out
        assert "preference" in captured.out

    def test_cli_main_approve(self, proposals_file, capsys):
        """CLI approve 应成功执行。"""
        from src.growth.proposal_review import main

        argv = [
            "--proposals", str(proposals_file),
            "approve",
            "prop_wf_001",
            "--reviewer", "alice",
            "--comment", "ok",
        ]
        rc = main(argv)
        assert rc == 0
        captured = capsys.readouterr()
        assert "approved" in captured.out

    def test_cli_main_reject(self, proposals_file, capsys):
        """CLI reject 应成功执行。"""
        from src.growth.proposal_review import main

        argv = [
            "--proposals", str(proposals_file),
            "reject",
            "prop_wf_002",
            "--reviewer", "bob",
            "--comment", "证据不足",
        ]
        rc = main(argv)
        assert rc == 0
        captured = capsys.readouterr()
        assert "rejected" in captured.out

    def test_cli_main_archive(self, proposals_file, capsys):
        """CLI archive 应成功执行。"""
        from src.growth.proposal_review import main

        argv = [
            "--proposals", str(proposals_file),
            "archive",
            "prop_wf_003",
            "--reviewer", "charlie",
            "--comment", "过期",
        ]
        rc = main(argv)
        assert rc == 0
        captured = capsys.readouterr()
        assert "archived" in captured.out

    def test_cli_main_summary(self, proposals_file, capsys):
        """CLI summary 应输出统计。"""
        from src.growth.proposal_review import main

        argv = ["--proposals", str(proposals_file), "summary"]
        rc = main(argv)
        assert rc == 0
        captured = capsys.readouterr()
        assert "pending_count" in captured.out
        assert "total" in captured.out


# ============================================================
# 11. 端到端 Workflow
# ============================================================

class TestEndToEndWorkflow:
    """完整工作流端到端测试。"""

    def test_full_lifecycle_pending_to_rejected(self, reviewer):
        """完整流程: pending → reject。"""
        # 1. 初始 pending
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "pending"

        # 2. reject
        result = reviewer.reject("prop_wf_001", reviewer="alice", comment="证据不足")
        assert result["success"] is True

        # 3. 验证最终状态
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "rejected"
        assert p.get("decision") == "rejected"
        assert p.get("reviewer_id") == "alice"
        assert p.get("review_comment") == "证据不足"
        assert len(p.get("review_history", [])) == 1

    def test_full_lifecycle_pending_to_approved_to_reset_to_rejected(self, reviewer):
        """完整流程: pending → approved → reset → rejected。"""
        # 1. approved
        reviewer.approve("prop_wf_001", reviewer="alice", comment="ok")
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "approved"

        # 2. reset
        reviewer.reset_to_pending("prop_wf_001", reviewer="supervisor", comment="误操作")
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "pending"
        assert p.get("decision") is None

        # 3. rejected
        reviewer.reject("prop_wf_001", reviewer="bob", comment="重审后拒绝")
        p = reviewer.get("prop_wf_001")
        assert reviewer._get_status(p) == "rejected"

        # 4. history 应有 3 条
        history = p.get("review_history", [])
        assert len(history) == 3
        assert history[0]["decision"] == "approved"
        assert history[1]["decision"] == "reset"
        assert history[2]["decision"] == "rejected"

    def test_batch_review_workflow(self, reviewer):
        """批量 review 工作流(全部人工逐条)。"""
        # 1. 列出所有 pending
        pending = reviewer.list_pending()
        assert len(pending) == 3

        # 2. 逐条 review
        reviewer.approve("prop_wf_001", reviewer="alice", comment="证据充分")
        reviewer.reject("prop_wf_002", reviewer="alice", comment="单次证据")
        reviewer.archive("prop_wf_003", reviewer="alice", comment="单次触发,信心低")

        # 3. 检查 summary
        summary = reviewer.summary()
        assert summary["pending_count"] == 0
        assert summary["approved_count"] == 1
        assert summary["rejected_count"] == 1
        assert summary["archived_count"] == 1

        # 4. 所有 review 的 reviewer 必须一致(同一审计员)
        for pid in ["prop_wf_001", "prop_wf_002", "prop_wf_003"]:
            p = reviewer.get(pid)
            assert p.get("reviewer_id") == "alice"
