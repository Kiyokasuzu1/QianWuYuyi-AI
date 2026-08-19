# -*- coding: utf-8 -*-
"""
tests/test_selfmodel_runtime_flow.py

Phase C.4.6.4.1 / C.4.6.4.2 — SelfModel 运行时流程测试

目标:
验证完整链路:
  memory event → GrowthEvaluator → GrowthProposal → Human Review → SelfModel

覆盖范围:
C.4.6.4.1 Runtime Data Capture:
- memory event 可以进入 Growth
- Growth 可以生成 proposal
- proposal 默认 pending
- pending 不会自动 apply

C.4.6.4.2 Proposal Review Integration Test:
- approve 后 status: pending → approved
- approve 必须记录 reviewer_id / review_comment / reviewed_at
- reject 必须不产生 SelfModel 数据
- archive 必须不产生 SelfModel 数据

约束(Phase C.4.6.4 严格):
- auto_accept_enabled 必须保持 False
- 不预填 SelfModel 数据
- 不修改 CoreIdentity / Memory / GrowthEvaluator / Pipeline
- 仅验证 review workflow 与审计
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("YUYI_LLM_MOCK", "1")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_data_root(tmp_path):
    """为每个测试创建独立的 data/ 目录。"""
    data_root = tmp_path / "data"
    data_root.mkdir()
    # 必要的子目录
    (data_root / "growth" / "proposals").mkdir(parents=True)
    (data_root / "self_model").mkdir(parents=True)
    (data_root / "proposals").mkdir(parents=True)
    return data_root


@pytest.fixture
def memory_event():
    """模拟一个 memory event(从 memory.json 产生)。"""
    return {
        "id": "mem_evt_001",
        "memory_id": "mem_evt_001",
        "role": "user",
        "user_id": "user_yuyi",
        "content": "我最近在读村上春树的书,感觉很喜欢",
        "memory_type": "user_preference",
        "importance": 0.7,
        "timestamp": "2026-08-02T10:00:00Z",
        "summary": "用户表达对村上春树的偏好",
    }


@pytest.fixture
def growth_evaluator_output():
    """模拟 GrowthEvaluator 评估输出(per-event proposal generation result)。"""
    return {
        "growth_level": "preference",
        "confidence": 0.85,
        "evidence_ids": ["mem_evt_001"],
        "proposed_changes": [
            {
                "path": "preference.literature",
                "before": None,
                "after": "村上春树",
                "reason": "用户明确表达偏好",
            }
        ],
        "reason": "用户表达对文学作品的稳定偏好",
    }


# ============================================================
# C.4.6.4.1 Runtime Data Capture
# ============================================================

class TestRuntimeDataCapture:
    """验证 memory event → growth → proposal 链路。"""

    def test_memory_event_can_enter_growth(self, memory_event):
        """memory event 必须可以进入 Growth 评估阶段。

        验证点: event 包含必要字段,符合 GrowthEvaluator 输入契约。
        """
        # 必备字段
        assert "id" in memory_event
        assert "memory_id" in memory_event
        assert "role" in memory_event
        assert "content" in memory_event
        # role=user 才会进入 Growth
        assert memory_event["role"] == "user"
        # importance > threshold 才会触发
        assert memory_event["importance"] > 0.5

    def test_growth_can_generate_proposal(self, tmp_data_root, memory_event, growth_evaluator_output):
        """Growth 可以从 evaluator 输出生成 proposal(默认 pending)。

        使用 ProposalManager.create_proposal(不实际持久化到生产数据)。
        """
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        # 构造 ChangeItem 列表
        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]

        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
            evaluator_meta={"reason": growth_evaluator_output["reason"]},
        )

        # 必须成功创建
        assert result["status"] == "created"
        assert result["proposal"] is not None

        # 验证 proposal 默认状态
        p = result["proposal"]
        assert p.status == "pending"
        assert p.confidence == 0.85
        assert p.source_event_id == "mem_evt_001"
        assert "mem_evt_001" in p.evidence_ids

    def test_proposal_default_status_is_pending(self, tmp_data_root, memory_event, growth_evaluator_output):
        """新建的 proposal 默认 status 必须是 pending。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]

        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )

        assert result["proposal"].status == "pending"

    def test_pending_proposal_does_not_auto_apply(self, tmp_data_root, memory_event, growth_evaluator_output):
        """pending proposal 不会自动 apply(必须人工 review)。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        # 显式设置 auto_accept_enabled=False
        mgr = ProposalManager(
            store=store,
            config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
        )

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]

        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )

        # 验证 proposal 未被自动 accept
        p = result["proposal"]
        assert p.status == "pending"
        assert p.accepted_at is None
        assert p.rejected_at is None

        # 验证 store 中 proposal 状态保持 pending
        loaded = mgr.get_proposal(p.id)
        assert loaded.status == "pending"

    def test_auto_accept_disabled_default(self, tmp_data_root):
        """默认 auto_accept_enabled 必须为 False。"""
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store)  # 无 config
        assert mgr.auto_accept_enabled is False

    def test_low_confidence_proposal_rejected(self, tmp_data_root, memory_event):
        """低置信度 proposal 应被拒绝,不进入 pending。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(
            store=store,
            config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
        )

        changes = [
            ChangeItem(
                path="preference.literature",
                before=None,
                after="X",
                reason="low confidence",
            )
        ]

        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=0.5,  # 低于 0.8
            evidence_ids=["mem_evt_001"],
        )

        # 必须被低置信度拒绝
        assert result["status"] == "rejected_low_confidence"
        assert result["proposal"] is None

    def test_proposal_without_evidence_rejected(self, tmp_data_root, memory_event):
        """无 evidence 的 proposal 必须被拒绝。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(
            store=store,
            config={"auto_accept_enabled": False},
        )

        changes = [
            ChangeItem(
                path="preference.literature",
                before=None,
                after="X",
                reason="no evidence",
            )
        ]

        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=0.85,
            evidence_ids=[],  # 空 evidence
        )

        # 必须被拒绝
        assert result["status"] == "rejected_no_evidence"
        assert result["proposal"] is None


# ============================================================
# C.4.6.4.2 Proposal Review Integration Test
# ============================================================

class TestProposalReviewIntegration:
    """验证 pending → review → approved 流程 + reject 不产生 SelfModel 数据。"""

    def test_proposal_review_pending_to_approved(self, tmp_data_root, memory_event, growth_evaluator_output):
        """通过 proposal_review.py approve 后,status 必须从 pending 变为 approved。

        验证: status / decision / reviewer_id / review_comment / reviewed_at 全部写入。
        """
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_review import ProposalReviewer

        # 1. 创建 proposal
        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        review_path = tmp_data_root / "growth" / "proposals" / "proposals.json"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]
        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )
        pid = result["proposal"].id

        # 2. 准备 review 文件(将 proposal 同步到 review store)
        review_data = {
            "version": "1.0",
            "proposals": [result["proposal"].to_dict()],
        }
        review_path.write_text(
            json.dumps(review_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 3. 通过 proposal_review.approve
        reviewer = ProposalReviewer(review_path)
        approve_result = reviewer.approve(
            proposal_id=pid,
            reviewer="alice",
            comment="证据充分,批准",
        )

        # 4. 验证
        assert approve_result["success"] is True
        assert approve_result["new_status"] == "approved"
        assert approve_result["decision"] == "approved"
        assert approve_result["reviewer"] == "alice"

        # 5. 验证 proposal 状态
        p = reviewer.get(pid)
        assert p["status"] == "approved"
        assert p["decision"] == "approved"
        assert p["reviewer_id"] == "alice"
        assert p["review_comment"] == "证据充分,批准"
        assert p["reviewed_at"] is not None
        # review_history 必须追加
        assert len(p.get("review_history", [])) == 1

    def test_reject_does_not_produce_selfmodel_data(self, tmp_data_root, memory_event, growth_evaluator_output):
        """reject 的 proposal 必须不产生 SelfModel 数据(测试前后 data/self_model/ 保持空)。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_review import ProposalReviewer

        # 0. 验证 SelfModel 目录初始为空
        self_model_dir = tmp_data_root / "self_model"
        assert self_model_dir.exists()
        initial_files = list(self_model_dir.iterdir())
        # 创建时无 jsonl 文件
        assert not any(f.suffix == ".jsonl" for f in initial_files)

        # 1. 创建 proposal
        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        review_path = tmp_data_root / "growth" / "proposals" / "proposals.json"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]
        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )
        pid = result["proposal"].id

        # 2. 同步到 review store
        review_data = {
            "version": "1.0",
            "proposals": [result["proposal"].to_dict()],
        }
        review_path.write_text(
            json.dumps(review_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 3. reject
        reviewer = ProposalReviewer(review_path)
        reject_result = reviewer.reject(
            proposal_id=pid,
            reviewer="bob",
            comment="证据不足,拒绝",
        )

        # 4. 验证
        assert reject_result["success"] is True
        assert reject_result["new_status"] == "rejected"

        # 5. 关键验证: SelfModel 目录**仍然**不包含任何 jsonl 数据
        after_files = list(self_model_dir.iterdir())
        jsonl_files = [f for f in after_files if f.suffix == ".jsonl"]
        assert len(jsonl_files) == 0, (
            f"reject 不应产生 SelfModel 数据,但发现: {jsonl_files}"
        )

    def test_archive_does_not_produce_selfmodel_data(self, tmp_data_root, memory_event, growth_evaluator_output):
        """archive 的 proposal 必须不产生 SelfModel 数据。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_review import ProposalReviewer

        self_model_dir = tmp_data_root / "self_model"
        assert self_model_dir.exists()

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        review_path = tmp_data_root / "growth" / "proposals" / "proposals.json"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]
        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )
        pid = result["proposal"].id

        review_data = {
            "version": "1.0",
            "proposals": [result["proposal"].to_dict()],
        }
        review_path.write_text(
            json.dumps(review_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        reviewer = ProposalReviewer(review_path)
        archive_result = reviewer.archive(
            proposal_id=pid,
            reviewer="charlie",
            comment="过期,归档",
        )

        assert archive_result["success"] is True
        assert archive_result["new_status"] == "archived"

        # 关键验证: SelfModel 数据未生成
        jsonl_files = [
            f for f in self_model_dir.iterdir() if f.suffix == ".jsonl"
        ]
        assert len(jsonl_files) == 0

    def test_review_records_all_audit_fields(self, tmp_data_root, memory_event, growth_evaluator_output):
        """approve 必须记录所有 5 个审计字段(proposal_id/reviewer_id/decision/timestamp/comment)。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_review import ProposalReviewer

        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        review_path = tmp_data_root / "growth" / "proposals" / "proposals.json"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]
        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )
        pid = result["proposal"].id

        review_data = {
            "version": "1.0",
            "proposals": [result["proposal"].to_dict()],
        }
        review_path.write_text(
            json.dumps(review_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        reviewer = ProposalReviewer(review_path)
        reviewer.approve(pid, reviewer="alice", comment="ok")

        # 验证 result 中 5 字段
        # (审计结果从 reviewer.get 获取)
        p = reviewer.get(pid)
        # 1. proposal_id
        assert p.get("proposal_id") == pid or p.get("id") == pid
        # 2. reviewer_id
        assert p.get("reviewer_id") == "alice"
        # 3. decision
        assert p.get("decision") == "approved"
        # 4. timestamp
        assert p.get("reviewed_at") is not None
        # 5. comment
        assert p.get("review_comment") == "ok"

    def test_pending_proposal_no_selfmodel_write(self, tmp_data_root, memory_event, growth_evaluator_output):
        """处于 pending 状态的 proposal 必须不会触发 SelfModel 写入。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore

        self_model_dir = tmp_data_root / "self_model"
        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]
        result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )

        # 验证 proposal 处于 pending
        assert result["proposal"].status == "pending"

        # 验证 SelfModel 目录未生成任何 jsonl
        jsonl_files = [
            f for f in self_model_dir.iterdir() if f.suffix == ".jsonl"
        ]
        assert len(jsonl_files) == 0

    def test_full_chain_memory_to_review(self, tmp_data_root, memory_event, growth_evaluator_output):
        """完整链路: memory event → proposal → review (approve)。"""
        from src.contracts.growth_schema import ChangeItem
        from src.growth.proposal_manager import ProposalManager
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_review import ProposalReviewer

        # 1. Memory event(已通过 fixture 提供)
        assert memory_event["role"] == "user"

        # 2. GrowthEvaluator 输出(已通过 fixture 提供)
        assert growth_evaluator_output["confidence"] >= 0.8

        # 3. 创建 proposal
        store_path = tmp_data_root / "proposals" / "proposals.jsonl"
        review_path = tmp_data_root / "growth" / "proposals" / "proposals.json"
        store = ProposalStore(path=str(store_path))
        mgr = ProposalManager(store=store, config={"auto_accept_enabled": False})

        changes = [
            ChangeItem(
                path=c["path"],
                before=c.get("before"),
                after=c.get("after"),
                reason=c.get("reason"),
            )
            for c in growth_evaluator_output["proposed_changes"]
        ]
        create_result = mgr.create_proposal(
            source_event=memory_event,
            proposed_changes=changes,
            confidence=growth_evaluator_output["confidence"],
            evidence_ids=growth_evaluator_output["evidence_ids"],
        )
        assert create_result["status"] == "created"
        pid = create_result["proposal"].id

        # 4. 同步到 review store
        review_data = {
            "version": "1.0",
            "proposals": [create_result["proposal"].to_dict()],
        }
        review_path.write_text(
            json.dumps(review_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 5. 人工 review
        reviewer = ProposalReviewer(review_path)
        review_result = reviewer.approve(
            proposal_id=pid,
            reviewer="alice",
            comment="完整链路测试通过",
        )

        # 6. 完整链路验证
        assert review_result["success"] is True
        assert review_result["decision"] == "approved"
        p = reviewer.get(pid)
        assert p["status"] == "approved"
        assert p["source_event_id"] == memory_event["id"]
        assert memory_event["id"] in p["evidence_ids"]
