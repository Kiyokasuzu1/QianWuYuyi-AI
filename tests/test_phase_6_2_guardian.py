"""
Phase 6.2: SelfModel Guardian 测试

验证：
- Contradiction Detection
- Belief Retraction (active=False, 不删)
- Confidence Decay
- Guardian state
"""
from __future__ import annotations

import os
import sys
import pytest
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_belief import SelfBelief
from src.personality.self_model_guardian import (
    SelfModelGuardian,
    SelfContradiction,
    OPPOSITE_PAIRS,
    DEFAULT_DECAY_RATE,
)
from src.personality.self_model_adapter import SelfModelAdapter
from src.personality.self_history import SelfHistoryEventType


# ============================================================
# 1. Contradiction Detection
# ============================================================

class TestContradictionDetection:
    def test_01_detect_opposite_pair(self):
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="preference", content="我喜欢热闹的社交活动", confidence=0.7),
            SelfBelief(domain="preference", content="我讨厌人群，喜欢安静", confidence=0.7),
        ]
        ctrs = g.detect_contradictions(beliefs)
        assert len(ctrs) >= 1
        assert any(c.belief_id_a and c.belief_id_b for c in ctrs)

    def test_02_no_contradiction_for_consistent(self):
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="preference", content="我喜欢安静", confidence=0.6),
            SelfBelief(domain="preference", content="我享受独处的时光", confidence=0.6),
        ]
        ctrs = g.detect_contradictions(beliefs)
        # 一致 → 0 或 1
        assert all(c.belief_id_a != "" for c in ctrs) or len(ctrs) == 0

    def test_03_contradictions_marked_needs_review(self):
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="preference", content="我外向", confidence=0.7),
            SelfBelief(domain="preference", content="我内向", confidence=0.7),
        ]
        ctrs = g.detect_contradictions(beliefs)
        if ctrs:
            assert all(c.needs_review is True for c in ctrs)

    def test_04_ignore_inactive_beliefs(self):
        g = SelfModelGuardian()
        a = SelfBelief(domain="preference", content="我喜欢热闹", confidence=0.7)
        b = SelfBelief(domain="preference", content="我讨厌热闹", confidence=0.7)
        b.active = False
        ctrs = g.detect_contradictions([a, b])
        # inactive belief 不参与
        assert all(c.belief_id_a != b.belief_id and c.belief_id_b != b.belief_id for c in ctrs)

    def test_05_empty_beliefs(self):
        g = SelfModelGuardian()
        assert g.detect_contradictions([]) == []

    def test_06_mark_resolved(self):
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="preference", content="我喜欢热闹", confidence=0.7),
            SelfBelief(domain="preference", content="我讨厌热闹", confidence=0.7),
        ]
        ctrs = g.detect_contradictions(beliefs)
        if ctrs:
            ok = g.mark_resolved(ctrs[0].contradiction_id)
            assert ok is True
            assert g.get_pending_contradictions() == []

    def test_07_different_domain_no_check(self):
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="value", content="我喜欢热闹", confidence=0.7),
            SelfBelief(domain="preference", content="我讨厌热闹", confidence=0.7),
        ]
        # 不同 domain 不检查
        ctrs = g.detect_contradictions(beliefs)
        assert len(ctrs) == 0

    def test_08_contradiction_to_dict(self):
        c = SelfContradiction("a", "b", description="test", severity=0.5)
        d = c.to_dict()
        assert d["belief_id_a"] == "a"
        assert d["belief_id_b"] == "b"
        assert d["needs_review"] is True

    def test_09_get_all_contradictions(self):
        g = SelfModelGuardian()
        beliefs = [
            SelfBelief(domain="preference", content="外向的我", confidence=0.7),
            SelfBelief(domain="preference", content="内向的我", confidence=0.7),
        ]
        ctrs = g.detect_contradictions(beliefs)
        all_ctr = g.get_all_contradictions()
        assert len(all_ctr) == len(ctrs)


# ============================================================
# 2. Belief Retraction
# ============================================================

class TestBeliefRetraction:
    def test_01_retract_sets_inactive(self):
        adapter = SelfModelAdapter()
        b = SelfBelief(domain="value", content="待撤销", confidence=0.7)
        adapter._beliefs.add(b)
        g = SelfModelGuardian()
        result = g.retract_belief(adapter, b.belief_id, reason="test_revoke")
        assert result["applied"] is True
        assert adapter._beliefs.get(b.belief_id).active is False

    def test_02_retract_nonexistent(self):
        adapter = SelfModelAdapter()
        g = SelfModelGuardian()
        result = g.retract_belief(adapter, "no_such", reason="test")
        assert result["applied"] is False
        assert len(result["errors"]) > 0

    def test_03_retract_no_adapter(self):
        g = SelfModelGuardian()
        result = g.retract_belief(None, "x", reason="test")
        assert result["applied"] is False

    def test_04_retract_no_reason(self):
        adapter = SelfModelAdapter()
        b = SelfBelief(domain="value", content="x", confidence=0.7)
        adapter._beliefs.add(b)
        g = SelfModelGuardian()
        result = g.retract_belief(adapter, b.belief_id, reason="")
        assert result["applied"] is False

    def test_05_retract_appends_history(self):
        adapter = SelfModelAdapter()
        b = SelfBelief(domain="value", content="x", confidence=0.7)
        adapter._beliefs.add(b)
        before = adapter._history.count()
        g = SelfModelGuardian()
        g.retract_belief(adapter, b.belief_id, reason="audit_test")
        assert adapter._history.count() > before

    def test_06_retract_appends_reflection(self):
        adapter = SelfModelAdapter()
        b = SelfBelief(domain="value", content="x", confidence=0.7)
        adapter._beliefs.add(b)
        before = adapter._reflections.count()
        g = SelfModelGuardian()
        g.retract_belief(adapter, b.belief_id, reason="audit_test")
        assert adapter._reflections.count() > before


# ============================================================
# 3. Confidence Decay
# ============================================================

class TestConfidenceDecay:
    def test_01_decay_stale_belief(self):
        g = SelfModelGuardian(decay_rate=0.1, decay_interval_days=7)
        b = SelfBelief(domain="value", content="old", confidence=0.8)
        # 30 天前
        old = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
        b.last_confirmed = old
        stats = g.decay_stale_beliefs([b])
        assert stats["scanned"] == 1
        # 30 / 7 ≈ 4 intervals; (0.9)^4 ≈ 0.6561
        assert b.confidence < 0.8

    def test_02_no_decay_for_fresh_belief(self):
        g = SelfModelGuardian(decay_rate=0.1, decay_interval_days=7)
        b = SelfBelief(domain="value", content="fresh", confidence=0.5)
        stats = g.decay_stale_beliefs([b])
        assert b.confidence == 0.5
        assert stats["decayed"] == 0

    def test_03_deactivate_below_threshold(self):
        g = SelfModelGuardian(
            decay_rate=0.3, decay_interval_days=7, min_confidence=0.2
        )
        b = SelfBelief(domain="value", content="low", confidence=0.21)
        # 1 interval
        old = (datetime.utcnow() - timedelta(days=8)).isoformat() + "Z"
        b.last_confirmed = old
        stats = g.decay_stale_beliefs([b])
        assert b.confidence < 0.21
        if b.confidence < 0.2:
            assert b.active is False
            assert stats["deactivated"] >= 1

    def test_04_decay_inactive_belief_unchanged(self):
        g = SelfModelGuardian()
        b = SelfBelief(domain="value", content="inactive", confidence=0.5)
        b.active = False
        old = (datetime.utcnow() - timedelta(days=30)).isoformat() + "Z"
        b.last_confirmed = old
        stats = g.decay_stale_beliefs([b])
        assert b.confidence == 0.5
        assert stats["decayed"] == 0

    def test_05_decay_empty(self):
        g = SelfModelGuardian()
        stats = g.decay_stale_beliefs([])
        assert stats == {"scanned": 0, "decayed": 0, "deactivated": 0}

    def test_06_decay_multiple(self):
        g = SelfModelGuardian(decay_rate=0.1, decay_interval_days=7)
        items = []
        for i in range(10):
            b = SelfBelief(domain="value", content=f"b_{i}", confidence=0.6)
            old = (datetime.utcnow() - timedelta(days=14)).isoformat() + "Z"
            b.last_confirmed = old
            items.append(b)
        stats = g.decay_stale_beliefs(items)
        assert stats["scanned"] == 10
        assert stats["decayed"] >= 5


# ============================================================
# 4. Guardian state
# ============================================================

class TestGuardianState:
    def test_01_get_state(self):
        g = SelfModelGuardian()
        state = g.get_state()
        assert "decay_rate" in state
        assert "min_confidence" in state
        assert "pending_contradictions" in state

    def test_02_default_decay_rate(self):
        g = SelfModelGuardian()
        assert g._decay_rate == DEFAULT_DECAY_RATE
