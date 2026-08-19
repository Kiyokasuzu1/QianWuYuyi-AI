# -*- coding: utf-8 -*-
"""
tests/test_proposal_review.py

Phase C.4.3 — Proposal Review System 测试

目标:
验证 src/growth/proposal_review.py 的功能:
- list_pending / get_detail
- approve / reject / archive
- 状态机正确
- 字段兼容 v1 + v2 schema
- 不自动 accept
- 原子写

约束:
- 使用 tmp_path 隔离
- 不修改任何核心模块
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("YUYI_LLM_MOCK", "1")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def v1_proposals_data():
    """v1 schema 样本数据。"""
    return {
        "version": "1.0",
        "proposals": [
            {
                "proposal_id": "prop_v1_001",
                "timestamp": "2026-07-27T10:00:00",
                "proposal_type": "relationship",
                "status": "pending",
                "source": "test",
                "source_event_id": "evt_1",
                "user_id": "u_001",
                "affected_dimensions": {"trust": 0.1},
                "before_state": {"trust": 0.5},
                "after_state": {"trust": 0.6},
                "confidence": 0.85,
                "reason": "test1",
                "evidence": ["ev_1"],
            },
            {
                "proposal_id": "prop_v1_002",
                "timestamp": "2026-07-27T11:00:00",
                "proposal_type": "preference",
                "status": "pending",
                "source_event_id": "evt_2",
                "confidence": 0.65,
                "evidence": ["ev_2"],
                "before_state": {"interest": "A"},
                "after_state": {"interest": "B"},
                "affected_dimensions": {"interest": "B"},
            },
        ],
    }


@pytest.fixture
def v2_proposals_data():
    """v2 schema(GrowthProposal dataclass 格式)样本数据。"""
    return {
        "version": "1.0",
        "proposals": [
            {
                "id": "prop_v2_001",
                "source_event_id": "evt_1",
                "proposed_changes": [
                    {"path": "trust", "before": 0.5, "after": 0.6, "reason": "test"}
                ],
                "confidence": 0.9,
                "evidence_ids": ["ev_1", "ev_2"],
                "evaluator_meta": {},
                "timestamp": "2026-07-27T12:00:00Z",
                "status": "pending",
                "schema_version": "1.0",
            },
        ],
    }


@pytest.fixture
def proposals_path_v1(tmp_path, v1_proposals_data):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps(v1_proposals_data, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def proposals_path_v2(tmp_path, v2_proposals_data):
    p = tmp_path / "proposals.json"
    p.write_text(json.dumps(v2_proposals_data, ensure_ascii=False), encoding="utf-8")
    return p


# ============================================================
# 1. 加载与兼容
# ============================================================

class TestLoadAndCompat:
    def test_load_v1_schema(self, proposals_path_v1):
        """v1 schema(proposal_id)正确加载。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        all_p = r.list_all()
        assert len(all_p) == 2
        ids = [ProposalReviewer._get_id(p) for p in all_p]
        assert "prop_v1_001" in ids

    def test_load_v2_schema(self, proposals_path_v2):
        """v2 schema(id + proposed_changes)正确加载。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v2)
        all_p = r.list_all()
        assert len(all_p) == 1
        ids = [ProposalReviewer._get_id(p) for p in all_p]
        assert "prop_v2_001" in ids

    def test_load_missing_file(self, tmp_path):
        """文件不存在时,空列表不报错。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(tmp_path / "nonexistent.json")
        assert r.list_all() == []
        assert r.list_pending() == []


# ============================================================
# 2. 查询
# ============================================================

class TestQuery:
    def test_list_pending(self, proposals_path_v1):
        """list_pending 返回所有 pending/proposed。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        pending = r.list_pending()
        assert len(pending) == 2

    def test_get_by_id_v1(self, proposals_path_v1):
        """v1 schema 按 proposal_id 查找。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        p = r.get("prop_v1_001")
        assert p is not None
        assert p["proposal_id"] == "prop_v1_001"

    def test_get_by_id_v2(self, proposals_path_v2):
        """v2 schema 按 id 查找。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v2)
        p = r.get("prop_v2_001")
        assert p is not None
        assert p["id"] == "prop_v2_001"

    def test_get_detail_v1(self, proposals_path_v1):
        """v1 schema detail 包含 change_items(从 before/after 派生)。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        detail = r.get_detail("prop_v1_001")
        assert detail is not None
        assert detail["id"] == "prop_v1_001"
        assert detail["confidence"] == 0.85
        assert "trust" in [c["path"] for c in detail["change_items"]]

    def test_get_detail_v2(self, proposals_path_v2):
        """v2 schema detail 包含 proposed_changes。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v2)
        detail = r.get_detail("prop_v2_001")
        assert detail is not None
        assert detail["confidence"] == 0.9
        assert len(detail["change_items"]) == 1
        assert detail["change_items"][0]["path"] == "trust"

    def test_get_not_found(self, proposals_path_v1):
        """不存在的 id 返回 None。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        assert r.get("nonexistent") is None
        assert r.get_detail("nonexistent") is None

    def test_count_by_status(self, proposals_path_v1):
        """按状态统计正确。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        counts = r.count_by_status()
        assert counts.get("pending", 0) == 2


# ============================================================
# 3. Review 操作
# ============================================================

class TestReviewOperations:
    def test_approve_marks_status(self, proposals_path_v1):
        """approve 后 status 变为 approved。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        result = r.approve("prop_v1_001", reviewer="alice", comment="OK")
        assert result["success"] is True
        assert result["new_status"] == "approved"

        # 重新加载确认
        r2 = ProposalReviewer(proposals_path_v1)
        p = r2.get("prop_v1_001")
        assert p["status"] == "approved"
        assert p["reviewer_id"] == "alice"
        assert p["review_comment"] == "OK"
        assert p["reviewed_at"] != ""

    def test_reject_marks_status(self, proposals_path_v1):
        """reject 后 status 变为 rejected。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        result = r.reject("prop_v1_001", reviewer="bob", comment="not relevant")
        assert result["success"] is True
        assert result["new_status"] == "rejected"

        r2 = ProposalReviewer(proposals_path_v1)
        p = r2.get("prop_v1_001")
        assert p["status"] == "rejected"

    def test_archive_marks_status(self, proposals_path_v1):
        """archive 后 status 变为 archived。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        result = r.archive("prop_v1_001", reviewer="carol", comment="old")
        assert result["success"] is True
        assert result["new_status"] == "archived"

        r2 = ProposalReviewer(proposals_path_v1)
        p = r2.get("prop_v1_001")
        assert p["status"] == "archived"

    def test_approve_not_found(self, proposals_path_v1):
        """approve 不存在的 id 失败但不抛错。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        result = r.approve("nonexistent", reviewer="alice")
        assert result["success"] is False
        assert "error" in result

    def test_approve_requires_reviewer(self, proposals_path_v1):
        """reviewer 为空时拒绝操作。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        result = r.approve("prop_v1_001", reviewer="")
        assert result["success"] is False
        assert "error" in result


# ============================================================
# 4. 状态机: 严格不自动 accept
# ============================================================

class TestNoAutoAccept:
    def test_approve_does_not_apply_to_personality(self, proposals_path_v1):
        """approve 仅标记状态,绝不动 Personality。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        r.approve("prop_v1_001", reviewer="alice")

        # CoreIdentity 不变
        from src.personality.core_identity import CoreIdentity
        traits = CoreIdentity.get_core_traits()
        assert "温柔" in traits
        assert "善良" in traits

    def test_pending_count_decreases_after_review(self, proposals_path_v1):
        """review 后 pending 数减少。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        assert r.list_pending().__len__() == 2
        r.approve("prop_v1_001", reviewer="alice")
        r2 = ProposalReviewer(proposals_path_v1)
        assert r2.list_pending().__len__() == 1


# ============================================================
# 5. 摘要
# ============================================================

class TestSummary:
    def test_summary_structure(self, proposals_path_v1):
        """summary 包含必要字段。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        s = r.summary()
        assert "total" in s
        assert "pending_count" in s
        assert "approved_count" in s
        assert "rejected_count" in s
        assert "archived_count" in s
        assert "by_status" in s
        assert s["total"] == 2
        assert s["pending_count"] == 2

    def test_summary_after_reviews(self, proposals_path_v1):
        """review 后 summary 统计正确。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        r.approve("prop_v1_001", reviewer="alice")
        r.reject("prop_v1_002", reviewer="bob")

        r2 = ProposalReviewer(proposals_path_v1)
        s = r2.summary()
        assert s["approved_count"] == 1
        assert s["rejected_count"] == 1
        assert s["pending_count"] == 0


# ============================================================
# 6. 原子写
# ============================================================

class TestAtomicWrite:
    def test_save_creates_valid_json(self, proposals_path_v1):
        """review 后文件仍是合法 JSON。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        r.approve("prop_v1_001", reviewer="alice")

        # 重新读文件,确认是合法 JSON
        data = json.loads(proposals_path_v1.read_text(encoding="utf-8"))
        assert "proposals" in data
        p = next(p for p in data["proposals"] if p.get("proposal_id") == "prop_v1_001")
        assert p["status"] == "approved"

    def test_no_tmp_file_left(self, proposals_path_v1):
        """review 后无残留 .tmp 文件。"""
        from src.growth.proposal_review import ProposalReviewer

        r = ProposalReviewer(proposals_path_v1)
        r.approve("prop_v1_001", reviewer="alice")

        # 确认无 .tmp 文件
        tmp = proposals_path_v1.with_suffix(proposals_path_v1.suffix + ".tmp")
        assert not tmp.exists()
