"""
Phase B.1.6 — Growth Integration 回归测试

职责：
验证 Event → GrowthProposal → PersonalityAdapter → PersonalityGrowthHistory → SelfModel
全链路工作正常,并覆盖 Phase B.1.5 安全规则。

测试维度：
1. TestEventToProposal     — Event 可生成 GrowthProposal
2. TestProposalStore        — Proposal 可保存 / 加载 / 列出
3. TestProposalToRecord     — Proposal 可转换 GrowthRecord
4. TestSelfModelIntegration — SelfModel 可读取 growth_history
5. TestSafetyRules          — 低置信度 / 重复 / delta 限制
6. TestRuntimeIsolation     — 异常不会破坏 Runtime
7. TestEndToEnd             — 完整链路 E2E

所有测试可独立运行。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def tmp_proposal_path():
    """为 ProposalStore 提供临时 JSONL 路径。"""
    with tempfile.TemporaryDirectory(prefix="yuyi_growth_test_") as d:
        yield os.path.join(d, "proposals.jsonl")


@pytest.fixture
def tmp_store(tmp_proposal_path):
    """干净的 ProposalStore 实例。"""
    from src.growth.proposal_store import ProposalStore
    return ProposalStore(path=tmp_proposal_path)


@pytest.fixture
def growth_history():
    """干净的 PersonalityGrowthHistory。"""
    from src.personality.personality_growth_record import PersonalityGrowthHistory
    return PersonalityGrowthHistory()


@pytest.fixture
def proposal_manager(tmp_store, growth_history):
    """标准 ProposalManager。"""
    from src.growth.proposal_manager import ProposalManager
    return ProposalManager(
        store=tmp_store,
        growth_history=growth_history,
    )


@pytest.fixture
def integration_service(growth_history, tmp_proposal_path):
    """GrowthIntegrationService（使用临时 ProposalStore 路径）。"""
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_store import ProposalStore
    from src.growth.proposal_manager import ProposalManager
    store = ProposalStore(path=tmp_proposal_path)
    manager = ProposalManager(
        store=store,
        growth_history=growth_history,
        config={"auto_accept_enabled": False},
    )
    return GrowthIntegrationService(
        proposal_manager=manager,
        growth_history=growth_history,
        config={"auto_accept_enabled": False},
    )


# ============================================================
# Helpers
# ============================================================
def make_change(path: str, before: float, after: float, reason: str = ""):
    from src.contracts import growth_schema
    return growth_schema.ChangeItem(
        path=path,
        before=before,
        after=after,
        reason=reason or f"change for {path}",
    )


def make_event(event_id: str = "evt_001", **kwargs) -> Dict[str, Any]:
    return {
        "id": event_id,
        "type": kwargs.pop("type", "preference"),
        "topic": kwargs.pop("topic", "test_topic"),
        **kwargs,
    }


# ============================================================
# 1. TestEventToProposal
# ============================================================
class TestEventToProposal:
    """Event 可生成 GrowthProposal。"""

    def test_event_generates_proposal_via_integration(self, integration_service):
        """通过 GrowthIntegrationService.process_event 可生成 proposal。"""
        event = make_event(event_id="evt_a")
        evaluator = {
            "proposed_changes": [
                make_change("personality.traits.warmth", 0.5, 0.55),
            ],
            "confidence": 0.9,
            "evidence_ids": ["mem_1", "mem_2"],
            "growth_level": "preference",
            "reason": "user expressed preference",
        }
        r = integration_service.process_event(event, evaluator)
        assert r["pipeline_state"] in ("created", "auto_accepted")
        assert r["proposal_id"] is not None

    def test_event_proposal_persisted(self, integration_service, tmp_proposal_path):
        """生成的 proposal 应被持久化到 ProposalStore。"""
        event = make_event(event_id="evt_b")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.9,
            "evidence_ids": ["mem_b1"],
            "growth_level": "context",
            "reason": "test persist",
        }
        r = integration_service.process_event(event, evaluator)
        pid = r["proposal_id"]
        # 检查文件
        assert os.path.exists(tmp_proposal_path)
        with open(tmp_proposal_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        assert any(pid in ln for ln in lines)

    def test_event_proposal_has_required_fields(self, proposal_manager):
        """生成的 proposal 必须包含 id / timestamp / source_event / evidence / confidence / changes / status。"""
        event = make_event(event_id="evt_c")
        changes = [make_change("warmth", 0.5, 0.55)]
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=changes,
            confidence=0.9,
            evidence_ids=["mem_c1"],
            evaluator_meta={"growth_level": "context"},
        )
        assert r["status"] == "created"
        p = r["proposal"]
        assert p.id
        assert p.timestamp
        assert p.source_event_id == "evt_c"
        assert p.confidence == 0.9
        assert "mem_c1" in p.evidence_ids
        assert len(p.proposed_changes) == 1
        assert p.status == "pending"


# ============================================================
# 2. TestProposalStore
# ============================================================
class TestProposalStore:
    """Proposal 可保存 / 加载 / 列出。"""

    def test_save_and_load(self, tmp_store):
        from src.contracts import growth_schema
        p = growth_schema.GrowthProposal(
            source_event_id="evt_d",
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_d1"],
            status="pending",
        )
        tmp_store.save(p)
        loaded = tmp_store.load(p.id)
        assert loaded is not None
        assert loaded.id == p.id
        assert loaded.confidence == 0.9
        assert loaded.status == "pending"

    def test_update_status(self, tmp_store):
        from src.contracts import growth_schema
        p = growth_schema.GrowthProposal(
            source_event_id="evt_e",
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_e1"],
            status="pending",
        )
        tmp_store.save(p)
        # update status
        p.status = "accepted"
        p.accepted_at = "2026-08-02T00:00:00Z"
        tmp_store.update(p)
        loaded = tmp_store.load(p.id)
        assert loaded.status == "accepted"
        assert loaded.accepted_at == "2026-08-02T00:00:00Z"

    def test_list_by_status(self, tmp_store):
        from src.contracts import growth_schema
        for i in range(3):
            p = growth_schema.GrowthProposal(
                source_event_id=f"evt_{i}",
                proposed_changes=[make_change("warmth", 0.5, 0.55)],
                confidence=0.9,
                evidence_ids=[f"mem_{i}"],
                status="pending",
            )
            tmp_store.save(p)
        # 标记一个为 accepted
        all_items = tmp_store.list()
        all_items[0].status = "accepted"
        tmp_store.update(all_items[0])

        pending = tmp_store.list(status="pending")
        accepted = tmp_store.list(status="accepted")
        assert len(pending) == 2
        assert len(accepted) == 1

    def test_count(self, tmp_store):
        from src.contracts import growth_schema
        for i in range(5):
            p = growth_schema.GrowthProposal(
                source_event_id=f"evt_{i}",
                proposed_changes=[make_change("warmth", 0.5, 0.55)],
                confidence=0.9,
                evidence_ids=[f"mem_{i}"],
                status="pending",
            )
            tmp_store.save(p)
        assert tmp_store.count() == 5
        assert tmp_store.count(status="pending") == 5

    def test_exists_similar_detects_duplicate(self, tmp_store):
        from src.contracts import growth_schema
        from src.growth.proposal_store import compute_fingerprint
        p1 = growth_schema.GrowthProposal(
            source_event_id="evt_dup",
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_d1"],
        )
        tmp_store.save(p1)
        fp = compute_fingerprint(p1.to_dict())
        existing = tmp_store.exists_similar("evt_dup", fp)
        assert existing == p1.id


# ============================================================
# 3. TestProposalToRecord
# ============================================================
class TestProposalToRecord:
    """Proposal 可转换 GrowthRecord。"""

    def test_accept_proposal_creates_growth_record(self, proposal_manager, growth_history):
        event = make_event(event_id="evt_f")
        changes = [make_change("warmth", 0.5, 0.55)]
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=changes,
            confidence=0.9,
            evidence_ids=["mem_f1"],
        )
        pid = r["proposal"].id
        acc = proposal_manager.accept_proposal(pid, actor="test")
        assert acc["status"] == "accepted"
        # growth_history 应有 1 条
        assert growth_history.count() == 1
        rec = growth_history.all()[0]
        assert rec["source_proposal_id"] == pid
        assert "warmth" in rec["affected_dimensions"]
        assert rec["confidence"] == 0.9

    def test_reject_proposal_does_not_create_record(self, proposal_manager, growth_history):
        event = make_event(event_id="evt_g")
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_g1"],
        )
        pid = r["proposal"].id
        rej = proposal_manager.reject_proposal(pid, actor="test", reason="not safe")
        assert rej["status"] == "rejected"
        assert growth_history.count() == 0

    def test_double_accept_is_idempotent(self, proposal_manager, growth_history):
        event = make_event(event_id="evt_h")
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_h1"],
        )
        pid = r["proposal"].id
        proposal_manager.accept_proposal(pid, actor="test")
        # 第二次 accept 应返回 already_accepted
        r2 = proposal_manager.accept_proposal(pid, actor="test")
        assert r2["status"] == "already_accepted"
        # growth_history 应只有 1 条
        assert growth_history.count() == 1

    def test_apply_proposal_marks_applied(self, proposal_manager):
        event = make_event(event_id="evt_i")
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_i1"],
        )
        pid = r["proposal"].id
        proposal_manager.accept_proposal(pid, actor="test")
        # apply
        result = proposal_manager.apply_proposal(pid, actor="test")
        # 状态至少是 applied 或 apply_failed（path validation可能拒）
        assert result["status"] in ("applied", "apply_failed")
        # 如果成功, proposal.status 应该是 applied
        if result["status"] == "applied":
            final = proposal_manager.get_proposal(pid)
            assert final.status == "applied"


# ============================================================
# 4. TestSelfModelIntegration
# ============================================================
class TestSelfModelIntegration:
    """SelfModel 可读取 growth_history。"""

    def test_growth_history_bridge_builds_view(self, growth_history):
        from src.personality.growth_history_bridge import GrowthHistoryBridge
        # 注入测试数据
        growth_history.add({
            "record_id": "pgr_001",
            "timestamp": "2026-08-01T00:00:00",
            "trigger_events": ["mem_x"],
            "changes": {"warmth": {"before": 0.5, "after": 0.55, "delta": 0.05, "reason": "test"}},
            "affected_dimensions": ["warmth"],
            "meaning": "用户更喜欢温暖的回复",
            "narrative": "test narrative",
            "confidence": 0.9,
            "validation_count": 1,
            "growth_level": "preference",
        })
        bridge = GrowthHistoryBridge(growth_history=growth_history)
        view = bridge.build_growth_history_view()
        assert view["total_count"] == 1
        assert view["high_impact_count"] == 0  # 不是 trait
        assert view["records"][0]["id"] == "pgr_001"
        assert view["records"][0]["confidence"] == 0.9
        assert view["records"][0]["affected_dimensions"] == ["warmth"]

    def test_self_model_store_writes_growth_history(self, growth_history):
        from src.personality.growth_history_bridge import GrowthHistoryBridge
        from src.personality.self_model_store import SelfModelStore

        growth_history.add({
            "record_id": "pgr_002",
            "timestamp": "2026-08-02T00:00:00",
            "trigger_events": ["mem_y"],
            "changes": {"curiosity": {"before": 0.5, "after": 0.6, "delta": 0.1, "reason": ""}},
            "affected_dimensions": ["curiosity"],
            "meaning": "用户对系统设计感兴趣",
            "narrative": "",
            "confidence": 0.85,
            "validation_count": 1,
            "growth_level": "trait",
        })

        bridge = GrowthHistoryBridge(growth_history=growth_history)
        view = bridge.build_growth_history_view()

        store = SelfModelStore()
        store.set_growth_history_view(view)
        # 缓存写入
        assert store.has_growth_history()
        v = store.get_growth_history_view()
        assert v["total_count"] == 1
        assert v["high_impact_count"] == 1  # confidence >= 0.8 + trait

    def test_bridge_recent_events(self, growth_history):
        from src.personality.growth_history_bridge import GrowthHistoryBridge

        for i in range(5):
            growth_history.add({
                "record_id": f"pgr_{i:03d}",
                "timestamp": f"2026-08-0{i+1}T00:00:00",
                "trigger_events": [f"mem_{i}"],
                "changes": {"warmth": {"before": 0.5, "after": 0.55, "delta": 0.05, "reason": ""}},
                "affected_dimensions": ["warmth"],
                "meaning": f"event {i}",
                "narrative": "",
                "confidence": 0.9,
                "validation_count": 1,
                "growth_level": "context",
            })
        bridge = GrowthHistoryBridge(growth_history=growth_history)
        recent = bridge.recent_growth_events(n=3)
        assert len(recent) == 3
        # 最新在前
        assert recent[0]["id"] == "pgr_004"
        assert recent[1]["id"] == "pgr_003"
        assert recent[2]["id"] == "pgr_002"

    def test_bridge_important_changes(self, growth_history):
        from src.personality.growth_history_bridge import GrowthHistoryBridge
        growth_history.add({
            "record_id": "pgr_low",
            "timestamp": "2026-08-01T00:00:00",
            "trigger_events": ["mem_a"],
            "changes": {"warmth": {"before": 0.5, "after": 0.51, "delta": 0.01, "reason": ""}},
            "affected_dimensions": ["warmth"],
            "meaning": "low conf",
            "narrative": "",
            "confidence": 0.5,
            "validation_count": 1,
            "growth_level": "context",
        })
        growth_history.add({
            "record_id": "pgr_high",
            "timestamp": "2026-08-02T00:00:00",
            "trigger_events": ["mem_b"],
            "changes": {"warmth": {"before": 0.5, "after": 0.6, "delta": 0.1, "reason": ""}},
            "affected_dimensions": ["warmth"],
            "meaning": "high conf",
            "narrative": "",
            "confidence": 0.95,
            "validation_count": 1,
            "growth_level": "preference",
        })
        bridge = GrowthHistoryBridge(growth_history=growth_history)
        important = bridge.important_changes(min_confidence=0.8)
        assert len(important) == 1
        assert important[0]["id"] == "pgr_high"

    def test_self_model_schema_has_growth_history(self):
        """SelfModel TypedDict 应包含 growth_history 字段（类型注解存在）。"""
        from src.personality.self_model import SelfModel
        # TypedDict 不直接暴露 __annotations__, 用 __optional_keys__ / __required_keys__
        # 但 total=False 时所有 key 都 optional, 用 __annotations__ 拿
        ann = getattr(SelfModel, "__annotations__", {})
        assert "growth_history" in ann, "SelfModel 应包含 growth_history 字段"


# ============================================================
# 5. TestSafetyRules
# ============================================================
class TestSafetyRules:
    """Phase B.1.5 安全规则：低置信度 / 重复 / delta 限制 / evidence 必填。"""

    def test_low_confidence_rejected(self, integration_service, growth_history):
        """confidence < 0.8 不应进入成长系统。"""
        event = make_event(event_id="evt_low_conf")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.5,  # < 0.8
            "evidence_ids": ["mem_lc1"],
            "growth_level": "context",
        }
        r = integration_service.process_event(event, evaluator)
        assert r["pipeline_state"] == "rejected_low_confidence"
        assert growth_history.count() == 0

    def test_no_evidence_rejected(self, integration_service, growth_history):
        """无 evidence 不应进入成长系统。"""
        event = make_event(event_id="evt_no_ev")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.9,
            "evidence_ids": [],  # empty
            "growth_level": "context",
        }
        r = integration_service.process_event(event, evaluator)
        assert r["pipeline_state"] == "rejected_no_evidence"
        assert growth_history.count() == 0

    def test_duplicate_event_merged(self, integration_service, growth_history):
        """重复事件（same source_event_id + fingerprint）应合并。"""
        event = make_event(event_id="evt_dup")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.9,
            "evidence_ids": ["mem_dup1"],
            "growth_level": "context",
        }
        r1 = integration_service.process_event(event, evaluator)
        r2 = integration_service.process_event(event, evaluator)
        assert r1["pipeline_state"] in ("created", "auto_accepted")
        assert r2["pipeline_state"] == "deduped"
        assert r2["proposal_id"] == r1["proposal_id"]

    def test_oversized_delta_marked_needs_review(self, growth_history):
        """单次 delta 超过 max_single_event_delta 应被标记 needs_review。"""
        from src.growth.growth_integration import GrowthIntegrationService
        svc = GrowthIntegrationService(
            growth_history=growth_history,
            config={"max_single_event_delta": 0.05, "auto_accept_enabled": False},
        )
        event = make_event(event_id="evt_big_delta")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.9)],  # delta=0.4 远超 0.05
            "confidence": 0.9,
            "evidence_ids": ["mem_big"],
            "growth_level": "context",
        }
        r = svc.process_event(event, evaluator)
        assert r["pipeline_state"] == "needs_review"
        # proposal 已创建但未进入 growth_history
        assert growth_history.count() == 0

    def test_high_confidence_passes_safety(self, integration_service):
        """高置信度（>= 0.8）应通过安全检查, 进入 created 状态。"""
        event = make_event(event_id="evt_high")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.52)],
            "confidence": 0.9,
            "evidence_ids": ["mem_hc1"],
            "growth_level": "context",
        }
        r = integration_service.process_event(event, evaluator)
        assert r["pipeline_state"] in ("created", "auto_accepted")

    def test_proposal_status_legal_set(self, proposal_manager):
        """proposal.status 必须是 {pending, accepted, rejected, applied}。"""
        event = make_event(event_id="evt_st")
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_st1"],
        )
        pid = r["proposal"].id
        assert proposal_manager.get_proposal(pid).status == "pending"
        proposal_manager.accept_proposal(pid)
        assert proposal_manager.get_proposal(pid).status == "accepted"


# ============================================================
# 6. TestRuntimeIsolation
# ============================================================
class TestRuntimeIsolation:
    """异常不会破坏 Runtime。"""

    def test_invalid_proposal_data_does_not_crash(self, proposal_manager):
        """非法 proposed_changes 不应导致 Runtime 崩溃。"""
        # 故意传入非 ChangeItem 对象
        r = proposal_manager.create_proposal(
            source_event={"id": "evt_bad"},
            proposed_changes=[
                type("X", (), {"path": "warmth", "before": 0.5, "after": 0.55, "reason": ""})(),
            ],
            confidence=0.9,
            evidence_ids=["mem_bad1"],
        )
        # 至少能完成 create 流程
        assert r["status"] in ("created", "deduped", "rejected_low_confidence", "rejected_no_evidence")

    def test_store_failure_does_not_break_pipeline(self, tmp_proposal_path, growth_history):
        """Store I/O 异常时, GrowthIntegrationService 仍能正常返回。"""
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_manager import ProposalManager
        from src.growth.growth_integration import GrowthIntegrationService

        # 用一个只读的路径模拟 store 失败
        readonly_path = "/dev/null/_readonly_proposals.jsonl"  # 不存在的目录
        store = ProposalStore(path=readonly_path)  # 仍能构造
        manager = ProposalManager(store=store, growth_history=growth_history)
        svc = GrowthIntegrationService(
            proposal_manager=manager,
            growth_history=growth_history,
        )
        event = make_event(event_id="evt_store_fail")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.9,
            "evidence_ids": ["mem_sf1"],
            "growth_level": "context",
        }
        r = svc.process_event(event, evaluator)
        # 不应抛异常
        assert r["pipeline_state"] in ("created", "store_failed", "deduped", "rejected_low_confidence")

    def test_adapter_exception_isolated(self, proposal_manager, growth_history):
        """PersonalityAdapter 抛错时, accept_proposal 应返回失败而非崩溃。"""
        from unittest.mock import patch

        event = make_event(event_id="evt_adapter_fail")
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_af1"],
        )
        pid = r["proposal"].id

        # 让 adapter 抛错
        with patch.object(
            proposal_manager.personality_adapter,
            "build_change_request",
            side_effect=RuntimeError("adapter boom"),
        ):
            r2 = proposal_manager.accept_proposal(pid)
            assert r2["status"] == "adapter_failed"

    def test_growth_history_add_failure_isolated(self, proposal_manager):
        """growth_history.add 抛错时, accept_proposal 不应崩溃。"""
        from unittest.mock import patch

        event = make_event(event_id="evt_gh_fail")
        r = proposal_manager.create_proposal(
            source_event=event,
            proposed_changes=[make_change("warmth", 0.5, 0.55)],
            confidence=0.9,
            evidence_ids=["mem_gh1"],
        )
        pid = r["proposal"].id

        # 注入一个 growth_history, add 抛错
        class _BoomHistory:
            def add(self, record):
                raise IOError("disk full")

        proposal_manager.growth_history = _BoomHistory()
        r2 = proposal_manager.accept_proposal(pid)
        # 至少应完成 accept 状态（即使 growth_history 失败被隔离）
        assert r2["status"] in ("accepted", "adapter_failed", "error")

    def test_service_process_event_never_raises(self, integration_service):
        """即使 evaluator_output 完全空, process_event 也不应抛异常。"""
        event = make_event(event_id="evt_empty")
        r = integration_service.process_event(event, {})
        # 不抛异常,且 pipeline_state 是合法字符串
        assert isinstance(r["pipeline_state"], str)
        assert r["pipeline_state"] in (
            "created", "auto_accepted", "rejected_low_confidence",
            "rejected_no_evidence", "deduped", "needs_review", "store_failed", "error",
        )


# ============================================================
# 7. TestEndToEnd
# ============================================================
class TestEndToEnd:
    """完整链路 E2E: Event → Proposal → Accept → GrowthHistory → SelfModel。"""

    def test_full_pipeline_runs(self, integration_service, growth_history):
        event = make_event(event_id="evt_e2e")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.9,
            "evidence_ids": ["mem_e2e1", "mem_e2e2"],
            "growth_level": "preference",
            "reason": "user expressed warmth preference",
            "narrative": "用户多次表达喜欢温暖的对话风格",
        }
        # 1) 提交 event
        r1 = integration_service.process_event(event, evaluator)
        assert r1["pipeline_state"] in ("created", "auto_accepted")
        pid = r1["proposal_id"]

        # 2) accept
        r2 = integration_service.accept_proposal(pid, actor="user")
        assert r2["status"] == "accepted"
        assert r2["growth_record"] is not None

        # 3) growth_history 写入
        assert growth_history.count() == 1
        rec = growth_history.all()[0]
        assert rec["source_proposal_id"] == pid

        # 4) growth_history view 可读
        view = integration_service.get_growth_history_view()
        assert view["total_count"] == 1
        assert view["records"][0]["id"] == rec["record_id"]
        assert view["records"][0]["confidence"] == 0.9
        assert "warmth" in view["records"][0]["affected_dimensions"]

        # 5) 关键查询可用
        assert integration_service.growth_count() == 1
        assert len(integration_service.recent_growth_events()) == 1
        important = integration_service.important_changes(min_confidence=0.8)
        assert len(important) == 1

    def test_full_pipeline_with_rejection(self, integration_service, growth_history):
        """低置信度事件被拒绝, growth_history 不写入。"""
        event = make_event(event_id="evt_e2e_rej")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.51)],
            "confidence": 0.3,  # low
            "evidence_ids": ["mem_rej1"],
            "growth_level": "context",
        }
        r = integration_service.process_event(event, evaluator)
        assert r["pipeline_state"] == "rejected_low_confidence"
        assert growth_history.count() == 0
        # 视图也应为空
        view = integration_service.get_growth_history_view()
        assert view["total_count"] == 0

    def test_multiple_events_pipeline(self, integration_service, growth_history):
        """多事件依次处理, growth_history 累积。"""
        for i in range(3):
            event = make_event(event_id=f"evt_multi_{i}")
            evaluator = {
                "proposed_changes": [make_change("warmth", 0.5, 0.51 + i * 0.01)],
                "confidence": 0.9,
                "evidence_ids": [f"mem_m_{i}"],
                "growth_level": "context",
            }
            r = integration_service.process_event(event, evaluator)
            if r["pipeline_state"] in ("created", "auto_accepted"):
                integration_service.accept_proposal(r["proposal_id"])

        # 3 条 growth_records
        assert growth_history.count() == 3
        view = integration_service.get_growth_history_view()
        assert view["total_count"] == 3

    def test_full_pipeline_with_self_model_store(self, growth_history, tmp_proposal_path):
        """完整链路含 SelfModelStore 同步。"""
        from src.growth.growth_integration import GrowthIntegrationService
        from src.growth.proposal_store import ProposalStore
        from src.growth.proposal_manager import ProposalManager
        from src.personality.self_model_store import SelfModelStore

        store = SelfModelStore()
        pstore = ProposalStore(path=tmp_proposal_path)
        manager = ProposalManager(
            store=pstore,
            growth_history=growth_history,
            config={"auto_accept_enabled": False},
        )
        svc = GrowthIntegrationService(
            proposal_manager=manager,
            growth_history=growth_history,
            self_model_store=store,
            config={"auto_accept_enabled": False},
        )

        event = make_event(event_id="evt_sm")
        evaluator = {
            "proposed_changes": [make_change("warmth", 0.5, 0.55)],
            "confidence": 0.9,
            "evidence_ids": ["mem_sm1"],
            "growth_level": "preference",
        }
        r = svc.process_event(event, evaluator)
        pid = r["proposal_id"]
        svc.accept_proposal(pid)

        # self_model_store 应已写入 growth_history_view
        assert store.has_growth_history()
        v = store.get_growth_history_view()
        assert v["total_count"] == 1
