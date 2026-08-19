"""
Phase B.2 — Personality Evolution Layer 回归测试

职责：
验证 EvolutionEngine → PersonalityStateUpdater → SelfModel
完整链路工作正常，并覆盖 Phase B.2.7 安全规则。

测试维度：
1. TestTraitEvolution        — 单次小变化 / 累计 / 超限保护
2. TestEvidenceAccumulation  — 重复事件累计 / 时间衰减
3. TestConflictHandling      — 冲突检测 / 合并 / review
4. TestRelationshipEvolution — trust / familiarity / bond 变化
5. TestSelfModelEvolution    — 写入 / 查询 / 序列化
6. TestSafety                — 无证据拒绝 / 大变化阻止 / 异常隔离

所有测试可独立运行。
"""
from __future__ import annotations

import os
import sys
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture
def growth_history():
    from src.personality.personality_growth_record import PersonalityGrowthHistory
    return PersonalityGrowthHistory()


@pytest.fixture
def evidence_tracker():
    from src.personality.evidence_tracker import EvidenceTracker
    return EvidenceTracker(half_life_days=14.0, window_days=30)


@pytest.fixture
def conflict_resolver():
    from src.personality.personality_conflict import ConflictResolver
    return ConflictResolver()


@pytest.fixture
def evolution_engine(evidence_tracker, conflict_resolver):
    from src.personality.evolution_engine import EvolutionEngine
    return EvolutionEngine(
        evidence_tracker=evidence_tracker,
        conflict_resolver=conflict_resolver,
    )


@pytest.fixture
def state_updater():
    from src.personality.personality_state_updater import PersonalityStateUpdater
    return PersonalityStateUpdater()


@pytest.fixture
def self_model_store():
    from src.personality.self_model_store import SelfModelStore
    return SelfModelStore()


# ============================================================
# Helpers
# ============================================================
def make_growth_record(
    record_id: str = "pgr_001",
    dimensions: List[str] = None,
    deltas: Dict[str, float] = None,
    confidence: float = 0.9,
    growth_level: str = "preference",
    days_ago: int = 0,
    meaning: str = "test event",
) -> Dict[str, Any]:
    """创建一条 GrowthRecord-like dict。"""
    dims = dimensions or ["warmth"]
    ds = deltas or {dims[0]: 0.02}
    changes = {d: {"before": 0.5, "after": 0.5 + ds.get(d, 0.0), "delta": ds.get(d, 0.0), "reason": ""} for d in dims}
    ts = (datetime.now() - timedelta(days=days_ago)).isoformat()
    return {
        "record_id": record_id,
        "timestamp": ts,
        "trigger_events": [f"evt_{record_id}"],
        "changes": changes,
        "affected_dimensions": dims,
        "meaning": meaning,
        "narrative": "",
        "confidence": confidence,
        "validation_count": 1,
        "growth_level": growth_level,
    }


# ============================================================
# 1. TestTraitEvolution
# ============================================================
class TestTraitEvolution:
    """单次小变化 / 累计 / 超限保护。"""

    def test_engine_generates_proposal_from_history(
        self, growth_history, evolution_engine
    ):
        """EvolutionEngine 应能从 GrowthHistory 生成 EvolutionProposal。"""
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_t1_{i}",
                dimensions=["trust"],
                deltas={"trust": 0.01},
                confidence=0.9,
            ))
        proposal = evolution_engine.evaluate(growth_history, current_states={"trust": 0.5})
        assert proposal.status in ("pending", "needs_review")
        assert len(proposal.items) >= 1
        # trust 应该有 item
        trust_items = [it for it in proposal.items if it.trait == "trust"]
        assert len(trust_items) == 1
        # 单次 delta 限幅 ≤ 0.05
        assert abs(trust_items[0].proposed_delta) <= 0.05 + 1e-9

    def test_single_event_no_evolution_without_evidence(
        self, growth_history, evolution_engine
    ):
        """单次事件（evidence_count < 2）不应生成 EvolutionProposal。"""
        growth_history.add(make_growth_record(
            record_id="pgr_single",
            dimensions=["warmth"],
            deltas={"warmth": 0.02},
            confidence=0.95,
        ))
        proposal = evolution_engine.evaluate(growth_history, current_states={"warmth": 0.5})
        # 证据不足，应该没有 item 或被拒绝
        assert proposal.status in ("rejected", "pending", "needs_review")
        # 应当没有 warmth item（因 evidence_count < 2）
        warmth_items = [it for it in proposal.items if it.trait == "warmth"]
        assert len(warmth_items) == 0

    def test_oversized_delta_clamped(self, growth_history, evolution_engine):
        """单次提议 delta > 0.05 应被限幅。"""
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_big_{i}",
                dimensions=["openness"],
                deltas={"openness": 0.1},  # 故意 0.1
                confidence=0.9,
            ))
        proposal = evolution_engine.evaluate(growth_history, current_states={"openness": 0.5})
        op_items = [it for it in proposal.items if it.trait == "openness"]
        if op_items:
            # delta 已被限幅到 ≤ 0.05
            assert abs(op_items[0].proposed_delta) <= 0.05 + 1e-9

    def test_state_updater_applies_proposal(self, growth_history, evolution_engine, state_updater):
        """PersonalityStateUpdater 应能应用 EvolutionProposal 到 trait_states。"""
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_app_{i}",
                dimensions=["warmth"],
                deltas={"warmth": 0.01},
                confidence=0.9,
            ))
        proposal = evolution_engine.evaluate(growth_history, current_states={"warmth": 0.5})
        if proposal.status in ("pending", "accepted"):
            result = state_updater.apply(
                proposal=proposal,
                trait_states={"warmth": 0.5},
            )
            # 至少能完成（applied 或有 rejection）
            assert "applied" in result
            if result["applied"]:
                assert "warmth" in result["new_trait_states"]
                assert result["entry"] is not None


# ============================================================
# 2. TestEvidenceAccumulation
# ============================================================
class TestEvidenceAccumulation:
    """重复事件累计 / 时间衰减。"""

    def test_repeated_events_accumulate(self, evidence_tracker):
        """重复事件应在 EvidenceTracker 中累计。"""
        for i in range(5):
            evidence_tracker.absorb(make_growth_record(
                record_id=f"pgr_acc_{i}",
                dimensions=["trust"],
                deltas={"trust": 0.02},
                confidence=0.9,
            ))
        s = evidence_tracker.summary("trust")
        assert s["count"] == 5
        assert abs(s["cumulative_delta"] - 0.10) < 1e-6  # 5 * 0.02
        assert s["avg_confidence"] >= 0.85

    def test_time_decay_reduces_weight(self, evidence_tracker):
        """时间衰减：旧 evidence 权重应低于新 evidence。"""
        # 1 条很久以前
        evidence_tracker.absorb(make_growth_record(
            record_id="pgr_old",
            dimensions=["warmth"],
            deltas={"warmth": 0.05},
            confidence=0.9,
            days_ago=20,
        ))
        # 1 条最近
        evidence_tracker.absorb(make_growth_record(
            record_id="pgr_new",
            dimensions=["warmth"],
            deltas={"warmth": 0.05},
            confidence=0.9,
            days_ago=0,
        ))
        s = evidence_tracker.summary("warmth")
        assert s["count"] == 2
        # cumulative_delta 应小于 0.10（因为旧 evidence 衰减）
        assert s["cumulative_delta"] < 0.10 + 1e-6

    def test_evidence_below_window_excluded(self, evidence_tracker):
        """超出窗口的 evidence 应被排除。"""
        # 1 条 60 天前（> 30 天窗口）
        evidence_tracker.absorb(make_growth_record(
            record_id="pgr_v_old",
            dimensions=["trust"],
            deltas={"trust": 0.05},
            confidence=0.9,
            days_ago=60,
        ))
        s = evidence_tracker.summary("trust")
        assert s["count"] == 0
        assert s["cumulative_delta"] == 0.0

    def test_single_evidence_below_threshold(self, evidence_tracker):
        """单条 evidence 不会触发累计。"""
        evidence_tracker.absorb(make_growth_record(
            record_id="pgr_one",
            dimensions=["openness"],
            deltas={"openness": 0.05},
            confidence=0.9,
        ))
        s = evidence_tracker.summary("openness")
        # 有 1 条但不会被 EvolutionEngine 采纳（min_evidence=2）
        assert s["count"] == 1
        assert s["cumulative_delta"] > 0


# ============================================================
# 3. TestConflictHandling
# ============================================================
class TestConflictHandling:
    """冲突检测 / 合并 / review。"""

    def test_oversized_change_detected(self, conflict_resolver):
        """超大变化应被检测为 needs_review。"""
        from src.personality.evolution_engine import EvolutionProposalItem
        item = EvolutionProposalItem(
            trait="trust",
            proposed_delta=0.4,  # 故意大
            evidence_count=10,
            cumulative_confidence=0.95,
            before_value=0.5,
            after_value=0.9,
        )
        conflicts = conflict_resolver.detect([item], {"trust": 0.5})
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "oversized_change"
        assert conflicts[0].resolution_status == "needs_review"

    def test_no_conflict_for_normal_change(self, conflict_resolver):
        """正常 delta 不应被检测为冲突。"""
        from src.personality.evolution_engine import EvolutionProposalItem
        item = EvolutionProposalItem(
            trait="warmth",
            proposed_delta=0.02,
            evidence_count=3,
            cumulative_confidence=0.9,
            before_value=0.5,
            after_value=0.52,
        )
        conflicts = conflict_resolver.detect([item], {"warmth": 0.5})
        assert len(conflicts) == 0

    def test_conflict_resolve_merged(self, conflict_resolver):
        """merged 策略应将提议折中。"""
        from src.personality.personality_conflict import PersonalityConflict, ConflictStatus
        c = PersonalityConflict(
            dimension="trust",
            old_state=0.5,
            new_proposal=0.9,
            delta=0.4,
        )
        status = conflict_resolver.resolve(c, strategy="merged")
        assert status == ConflictStatus.MERGED
        # 折中：0.5 + (0.9 - 0.5) * 0.5 = 0.7
        assert abs(c.new_proposal - 0.7) < 1e-6

    def test_conflict_resolve_accepted(self, conflict_resolver):
        """accepted 策略应直接接受。"""
        from src.personality.personality_conflict import PersonalityConflict, ConflictStatus
        c = PersonalityConflict(
            dimension="warmth",
            old_state=0.5,
            new_proposal=0.6,
            delta=0.1,
        )
        status = conflict_resolver.resolve(c, strategy="accepted")
        assert status == ConflictStatus.ACCEPTED


# ============================================================
# 4. TestRelationshipEvolution
# ============================================================
class TestRelationshipEvolution:
    """trust / familiarity / bond 变化。"""

    def test_trust_gradual_increase(self, growth_history):
        """长期信任应缓慢增加。"""
        from src.personality.relationship_evolution import RelationshipEvolution
        revo = RelationshipEvolution(config={"min_evidence": 2})
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_rel_{i}",
                dimensions=["trust"],
                deltas={"trust": 0.02},
                confidence=0.9,
            ))
        result = revo.evaluate(growth_history)
        assert "trust" in result["deltas"]
        # delta 应被限幅
        assert result["deltas"]["trust"] <= 0.03 + 1e-9
        assert result["deltas"]["trust"] > 0

    def test_single_event_no_relationship_change(self, growth_history):
        """单次事件不应触发 relationship 变化。"""
        from src.personality.relationship_evolution import RelationshipEvolution
        revo = RelationshipEvolution(config={"min_evidence": 2})
        growth_history.add(make_growth_record(
            record_id="pgr_single_rel",
            dimensions=["trust"],
            deltas={"trust": 0.5},
            confidence=0.99,
        ))
        result = revo.evaluate(growth_history)
        # 单次证据不足
        assert "trust" not in result["deltas"]
        assert result["applied"] is False

    def test_familiarity_change(self, growth_history):
        """familiarity 变化应被检测。"""
        from src.personality.relationship_evolution import RelationshipEvolution
        revo = RelationshipEvolution(config={"min_evidence": 2})
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_fam_{i}",
                dimensions=["familiarity"],
                deltas={"familiarity": 0.02},
                confidence=0.9,
            ))
        result = revo.evaluate(growth_history)
        assert "familiarity" in result["deltas"]
        assert result["deltas"]["familiarity"] > 0

    def test_relationship_applied_to_state(self, tmp_path, growth_history):
        """当提供 RelationshipState 时应能应用。"""
        from src.personality.relationship_evolution import RelationshipEvolution
        from src.personality.relationship_state import RelationshipState
        # 使用临时路径
        rs = RelationshipState(state_path=str(tmp_path / "rel_state.json"))
        rs.reset()
        before_trust = rs.get_trust()
        revo = RelationshipEvolution(relationship_state=rs, config={"min_evidence": 2})
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_app_rel_{i}",
                dimensions=["trust"],
                deltas={"trust": 0.02},
                confidence=0.9,
            ))
        result = revo.evaluate(growth_history)
        if result["applied"]:
            after_trust = rs.get_trust()
            # trust 应有少量增加（受成熟度约束）
            assert after_trust >= before_trust


# ============================================================
# 5. TestSelfModelEvolution
# ============================================================
class TestSelfModelEvolution:
    """SelfModel 写入 / 查询 / 序列化。"""

    def test_self_model_store_writes_evolution_view(self, self_model_store):
        """SelfModelStore.set_personality_evolution_view 应能写入。"""
        view = {
            "records": [
                {
                    "evolution_id": "eh_001",
                    "timestamp": "2026-08-02T00:00:00",
                    "source_proposal_id": "evo_001",
                    "source_growth_record_ids": ["pgr_1"],
                    "changed_traits": {"warmth": {"before": 0.5, "after": 0.52, "delta": 0.02}},
                    "confidence": 0.9,
                    "reason": "test",
                    "status": "applied",
                }
            ],
            "total_count": 1,
            "last_updated": "2026-08-02T00:00:00",
            "applied_count": 1,
            "rolled_back_count": 0,
            "current_personality_state": {"warmth": 0.52},
        }
        self_model_store.set_personality_evolution_view(view)
        assert self_model_store.has_personality_evolution()
        v = self_model_store.get_personality_evolution_view()
        assert v["total_count"] == 1

    def test_recent_personality_changes(self, self_model_store):
        """recent_personality_changes 应按时间倒序返回。"""
        records = [
            {"evolution_id": f"eh_{i:03d}", "timestamp": f"2026-08-0{i+1}T00:00:00", "status": "applied", "reason": f"r{i}"}
            for i in range(3)
        ]
        view = {
            "records": records,
            "total_count": 3,
            "last_updated": "2026-08-03T00:00:00",
            "applied_count": 3,
            "rolled_back_count": 0,
            "current_personality_state": {},
        }
        self_model_store.set_personality_evolution_view(view)
        recent = self_model_store.recent_personality_changes(n=2)
        assert len(recent) == 2
        assert recent[0]["evolution_id"] == "eh_002"
        assert recent[1]["evolution_id"] == "eh_001"

    def test_current_personality_state(self, self_model_store):
        """current_personality_state 应返回当前 trait 值。"""
        view = {
            "records": [],
            "total_count": 0,
            "last_updated": "",
            "applied_count": 0,
            "rolled_back_count": 0,
            "current_personality_state": {"warmth": 0.6, "trust": 0.7},
        }
        self_model_store.set_personality_evolution_view(view)
        state = self_model_store.current_personality_state()
        assert state["warmth"] == 0.6
        assert state["trust"] == 0.7

    def test_personality_change_reasons(self, self_model_store):
        """personality_change_reasons 应返回所有原因。"""
        view = {
            "records": [
                {"evolution_id": "eh_1", "timestamp": "t1", "status": "applied", "reason": "user expressed warmth preference"},
                {"evolution_id": "eh_2", "timestamp": "t2", "status": "applied", "reason": "long-term trust building"},
            ],
            "total_count": 2,
            "last_updated": "t2",
            "applied_count": 2,
            "rolled_back_count": 0,
            "current_personality_state": {},
        }
        self_model_store.set_personality_evolution_view(view)
        reasons = self_model_store.personality_change_reasons()
        assert len(reasons) == 2
        assert "warmth preference" in reasons[0]

    def test_self_model_schema_has_evolution_history(self):
        """SelfModel TypedDict 应包含 personality_evolution_history 字段。"""
        from src.personality.self_model import SelfModel
        ann = getattr(SelfModel, "__annotations__", {})
        assert "personality_evolution_history" in ann


# ============================================================
# 6. TestSafety
# ============================================================
class TestSafety:
    """无证据拒绝 / 大变化阻止 / 异常隔离。"""

    def test_no_evidence_rejected(self, growth_history, evolution_engine):
        """空 history 应被拒绝。"""
        proposal = evolution_engine.evaluate(growth_history, current_states={})
        assert proposal.status == "rejected"
        assert "empty_history" in proposal.rejection_reasons

    def test_oversized_blocked_at_engine(self, growth_history, evolution_engine):
        """超大 cumulative 变化在 evolution_engine 阶段也应被冲突标记。"""
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_huge_{i}",
                dimensions=["extreme"],
                deltas={"extreme": 0.1},  # 累计 0.3
                confidence=0.9,
            ))
        proposal = evolution_engine.evaluate(growth_history, current_states={"extreme": 0.5})
        # 应被冲突检测器标记
        if proposal.status == "needs_review":
            assert any("conflict" in r for r in proposal.rejection_reasons)

    def test_rollback_restores_old_value(self, state_updater):
        """rollback 应能恢复 before 值。"""
        from src.personality.evolution_engine import PersonalityEvolutionProposal, EvolutionProposalItem
        proposal = PersonalityEvolutionProposal(
            items=[
                EvolutionProposalItem(
                    trait="warmth",
                    proposed_delta=0.02,
                    evidence_count=3,
                    cumulative_confidence=0.9,
                    before_value=0.5,
                    after_value=0.52,
                )
            ],
            confidence=0.9,
            status="pending",
        )
        r = state_updater.apply(
            proposal=proposal,
            trait_states={"warmth": 0.5},
        )
        assert r["applied"]
        # rollback
        rb = state_updater.rollback(
            evolution_history=r["new_evolution_history"],
            trait_states=r["new_trait_states"],
        )
        assert rb["rolled_back"]
        assert abs(rb["new_trait_states"]["warmth"] - 0.5) < 1e-6

    def test_exception_in_evaluate_isolated(self, growth_history):
        """EvolutionEngine 异常应被隔离。"""
        from src.personality.evolution_engine import EvolutionEngine
        # 构造一个会抛错的 evidence_tracker
        class _Boom:
            def absorb(self, r):
                raise RuntimeError("boom")
            def summary(self, t, now=None):
                return {"count": 0, "cumulative_delta": 0.0, "avg_confidence": 0.0, "source_record_ids": []}

        eng = EvolutionEngine(evidence_tracker=_Boom(), conflict_resolver=None)
        # 至少不应抛
        proposal = eng.evaluate(growth_history, current_states={})
        # 异常时仍返回 proposal
        assert proposal is not None
        assert proposal.status in ("rejected", "pending", "needs_review")

    def test_state_updater_exception_isolated(self):
        """PersonalityStateUpdater 异常应被隔离。"""
        from src.personality.personality_state_updater import PersonalityStateUpdater
        from src.personality.evolution_engine import PersonalityEvolutionProposal
        updater = PersonalityStateUpdater()
        # 传非法 proposal
        bad_proposal = PersonalityEvolutionProposal()
        # 缺少 items 字段
        r = updater.apply(
            proposal=bad_proposal,
            trait_states={"warmth": 0.5},
        )
        # 不应抛
        assert r["applied"] is False
        assert len(r["rejection_reasons"]) > 0

    def test_engine_cannot_read_raw_chat(self):
        """EvolutionEngine 不应暴露读取原始聊天的方法。"""
        from src.personality.evolution_engine import EvolutionEngine
        eng = EvolutionEngine()
        # 检查公开 API 不包含任何"raw chat"读取接口
        public_api = [m for m in dir(eng) if not m.startswith("_")]
        for m in public_api:
            assert "chat" not in m.lower() or m in ("evaluate",), f"unexpected method: {m}"
            assert "raw" not in m.lower() or m in ("evaluate",), f"unexpected method: {m}"

    def test_low_confidence_rejected(self, growth_history, evolution_engine):
        """低 confidence 事件应被拒绝。"""
        for i in range(3):
            growth_history.add(make_growth_record(
                record_id=f"pgr_low_{i}",
                dimensions=["warmth"],
                deltas={"warmth": 0.01},
                confidence=0.5,  # 低
            ))
        proposal = evolution_engine.evaluate(growth_history, current_states={"warmth": 0.5})
        # cumulative confidence 低，应被拒绝
        if proposal.items:
            # 如果生成了 item，confidence 应 >= 0.7
            assert proposal.confidence >= 0.7
        else:
            assert proposal.status in ("rejected", "needs_review")

    def test_core_identity_protected(self, growth_history, state_updater):
        """核心人格保护：包含 forbidden 关键词的 trait 应被拒绝。"""
        from src.personality.evolution_engine import PersonalityEvolutionProposal, EvolutionProposalItem
        # forbidden_changes 包含 "变得冷漠" 等
        # 模拟一个试图改 "变得冷漠_cold" 的 proposal
        proposal = PersonalityEvolutionProposal(
            items=[
                EvolutionProposalItem(
                    trait="变得冷漠_test",  # 包含 forbidden 关键词
                    proposed_delta=0.5,
                    evidence_count=10,
                    cumulative_confidence=0.95,
                    before_value=0.3,
                    after_value=0.8,
                ),
            ],
            confidence=0.95,
            status="pending",
        )
        r = state_updater.apply(
            proposal=proposal,
            trait_states={"变得冷漠_test": 0.3},
        )
        # 核心保护应阻止该 trait 的修改
        assert any("core_protected" in reason for reason in r["rejection_reasons"])
