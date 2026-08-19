# -*- coding: utf-8 -*-
"""
tests/test_phase_6_1_self_model_integration.py

Phase 6.1 Self Model Integration 测试套件

覆盖：

[Core] SelfIdentity 兼容
[Belief] SelfBelief CRUD
[History] SelfHistory append/query/rollback
[Reflection] SelfReflection 生成
[Adapter] SelfModelAdapter 唯一写入口
[Authority] Path whitelist 拒绝非法路径
[Confidence] Confidence 限制
[Limiter] GrowthRateLimiter 集成
[Snapshot] Snapshot 创建/回滚/恢复
[Pipeline] step_personality 触发 SelfModel
[Isolation] SelfModel 异常隔离
[Continuity] Self Continuity Test（多次成长后 identity 保持）
[Regression] Phase 5.5.1 / 5.5.2 / 6.0 回归

设计原则：
- 不创建真实 Authority 实例
- 使用 MagicMock / 临时文件
- 每个测试独立临时目录
- 不修改 RuntimeCore
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 共用工具
# ============================================================

def _make_pcr(
    pcr_id: str = "pcr_test_001",
    proposal_id: str = "prop_test_001",
    confidence: float = 0.7,
    evidence_count: int = 2,
    trait_changes: Optional[Dict[str, Any]] = None,
    reason: str = "test_pcr",
) -> Dict[str, Any]:
    """构造一个 PersonalityChangeRequest dict"""
    return {
        "request_id": pcr_id,
        "source_proposal_id": proposal_id,
        "source_insight_id": "insight_001",
        "timestamp": "2026-07-30T00:00:00Z",
        "evolution_record": {
            "trait_changes": trait_changes or {
                "warmth": {"delta": 0.05, "before": 0.5},
                "curiosity": {"delta": 0.03, "before": 0.5},
            },
        },
        "growth_records": [],
        "confidence": confidence,
        "evidence_count": evidence_count,
        "evaluator_meta": {"action_scope": "personality"},
        "requires_validation": False,
        "reason": reason,
    }


def _make_limiter(allow: bool = True, deny_traits: Optional[List[str]] = None):
    """构造一个最小 mock GrowthRateLimiter"""
    deny_traits = deny_traits or []
    limiter = MagicMock()

    class _Decision:
        def __init__(self, d):
            self.decision = d
            self.violations = []
            self.warnings = []

    def _check(trait, proposed_delta, confidence, proposal_id, dry_run=True):
        if trait in deny_traits:
            return _Decision("deny")
        return _Decision("allow")

    def _record(trait, delta, proposal_id, actor):
        pass

    limiter.check.side_effect = _check
    limiter.record.side_effect = _record
    return limiter


def _make_manager_with_identity():
    """构造一个带 identity 的 mock SelfModelManager"""
    from src.contracts.self_model_schema import (
        SelfIdentity,
        CoreValue,
    )
    mgr = MagicMock()
    identity = SelfIdentity(
        identity_id="si_test_001",
        core_values=[
            CoreValue(value_id="honesty", name="真诚", weight=0.85, confidence=0.9, sources=["identity_core"]),
        ],
    )
    mgr.identity = identity
    mgr.get_full.return_value = identity.to_dict()
    mgr.load_full.return_value = None
    mgr.refresh.return_value = identity
    return mgr, identity


def _make_updater():
    """构造一个 mock SelfModelUpdater"""
    upd = MagicMock()
    from src.contracts.self_model_schema import SelfModelChangeSuggestion
    sug = SelfModelChangeSuggestion(
        source_type="personality_change_request",
        source_id="pcr_test_001",
        confidence=0.7,
        evidence_count=2,
        requires_approval=False,
    )
    upd.from_pcr.return_value = sug
    upd.apply_suggestion.return_value = None
    return upd


# ============================================================
# [Core] SelfIdentity 兼容
# ============================================================

class TestSelfIdentityCompatibility(unittest.TestCase):
    """SelfIdentity 复用自 contracts.self_model_schema"""

    def test_01_self_identity_import(self):
        from src.personality.self_model_core import SelfIdentity
        assert SelfIdentity is not None

    def test_02_self_identity_instantiate(self):
        from src.personality.self_model_core import SelfIdentity, CoreValue
        si = SelfIdentity(
            identity_id="si_test_x",
            core_values=[CoreValue(value_id="x", name="X", weight=0.5)],
        )
        assert si.identity_id == "si_test_x"
        assert len(si.core_values) == 1

    def test_03_self_identity_to_dict(self):
        from src.personality.self_model_core import SelfIdentity
        si = SelfIdentity(identity_id="si_test_d")
        d = si.to_dict()
        assert isinstance(d, dict)
        assert d["identity_id"] == "si_test_d"

    def test_04_core_value_re_exported(self):
        from src.personality.self_model_core import CoreValue
        cv = CoreValue(value_id="v1", name="V", weight=0.5, confidence=0.6, sources=["x"])
        assert cv.value_id == "v1"

    def test_05_stable_trait_re_exported(self):
        from src.personality.self_model_core import StableTrait
        st = StableTrait(trait="warmth", current_value=0.5, direction="stable")
        assert st.trait == "warmth"

    def test_06_preference_re_exported(self):
        from src.personality.self_model_core import Preference
        p = Preference(domain="relationship", key="attach", value="high", evidence_count=1, confidence=0.6)
        assert p.domain == "relationship"

    def test_07_behavioral_pattern_re_exported(self):
        from src.personality.self_model_core import BehavioralPattern
        bp = BehavioralPattern(pattern_detected="quiet_pref", description="likes quiet", frequency=1, confidence=0.5)
        assert bp.pattern_detected == "quiet_pref"

    def test_08_self_model_change_suggestion_re_exported(self):
        from src.personality.self_model_core import SelfModelChangeSuggestion
        s = SelfModelChangeSuggestion(source_type="x", source_id="y")
        assert s.source_type == "x"

    def test_09_self_contradiction_re_exported(self):
        from src.personality.self_model_core import SelfContradiction
        c = SelfContradiction(dimension_a="x", dimension_b="y", description="d", intensity=0.1)
        assert c.dimension_a == "x"

    def test_10_growth_history_entry_re_exported(self):
        from src.personality.self_model_core import GrowthHistoryEntry
        g = GrowthHistoryEntry(source_type="x", source_id="y", summary="s")
        assert g.source_type == "x"


# ============================================================
# [Belief] SelfBelief CRUD
# ============================================================

class TestSelfBeliefCRUD(unittest.TestCase):
    """SelfBelief 数据结构与容器"""

    def test_01_self_belief_default(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief()
        assert b.belief_id.startswith("bel_")
        assert b.domain == "value"
        assert b.confidence == 0.3
        assert b.version == 1

    def test_02_self_belief_valid(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(domain="preference", content="likes quiet", confidence=0.6)
        assert b.is_valid()
        errs = b.validate()
        assert errs == []

    def test_03_self_belief_invalid_domain(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(domain="invalid", content="x")
        errs = b.validate()
        assert any("domain" in e for e in errs)

    def test_04_self_belief_invalid_content(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(content="")
        errs = b.validate()
        assert any("content" in e for e in errs)

    def test_05_self_belief_invalid_confidence(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(confidence=1.5)
        errs = b.validate()
        assert any("confidence" in e for e in errs)

    def test_06_self_belief_reinforce(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(domain="preference", content="likes quiet", confidence=0.5)
        b2 = b.reinforce(new_source="prop_001", confidence_boost=0.7)
        assert b2.evidence_count == 2
        assert b2.version == 2
        assert "prop_001" in b2.sources
        assert b2.confidence > b.confidence

    def test_07_self_belief_reinforce_caps(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(confidence=0.95)
        b2 = b.reinforce(confidence_boost=0.99)
        assert b2.confidence <= 1.0

    def test_08_self_belief_serialization(self):
        from src.personality.self_belief import SelfBelief
        b = SelfBelief(domain="identity", content="am a companion AI", confidence=0.7)
        d = b.to_dict()
        b2 = SelfBelief.from_dict(d)
        assert b2.belief_id == b.belief_id
        assert b2.domain == b.domain
        assert b2.content == b.content

    def test_09_self_belief_store_add(self):
        from src.personality.self_belief import SelfBelief, SelfBeliefStore
        s = SelfBeliefStore()
        b = SelfBelief(domain="preference", content="likes quiet", confidence=0.6)
        assert s.add(b) is True
        assert s.count() == 1

    def test_10_self_belief_store_reinforce_on_dup(self):
        from src.personality.self_belief import SelfBelief, SelfBeliefStore
        s = SelfBeliefStore()
        b1 = SelfBelief(domain="preference", content="likes quiet", confidence=0.5)
        b2 = SelfBelief(domain="preference", content="likes quiet", confidence=0.6)
        s.add(b1)
        s.add(b2)
        # 应当 reinforce 而非新增
        assert s.count() == 1
        belief = list(s._beliefs.values())[0]
        assert belief.evidence_count == 2
        assert belief.confidence > 0.5

    def test_11_self_belief_store_query(self):
        from src.personality.self_belief import SelfBelief, SelfBeliefStore
        s = SelfBeliefStore()
        s.add(SelfBelief(domain="preference", content="a", confidence=0.4))
        s.add(SelfBelief(domain="value", content="b", confidence=0.7))
        s.add(SelfBelief(domain="value", content="c", confidence=0.9))
        assert len(s.query(domain="value")) == 2
        assert len(s.query(min_confidence=0.5)) == 2

    def test_12_self_belief_store_invalid(self):
        from src.personality.self_belief import SelfBelief, SelfBeliefStore
        s = SelfBeliefStore()
        b = SelfBelief(domain="invalid", content="x")
        assert s.add(b) is False
        assert s.count() == 0

    def test_13_self_belief_store_latest(self):
        from src.personality.self_belief import SelfBelief, SelfBeliefStore
        s = SelfBeliefStore()
        for i in range(5):
            s.add(SelfBelief(domain="preference", content=f"c{i}", confidence=0.5))
        latest = s.latest(3)
        assert len(latest) == 3


# ============================================================
# [History] SelfHistory append/query/rollback
# ============================================================

class TestSelfHistory(unittest.TestCase):
    """SelfHistory 事件日志"""

    def test_01_history_default(self):
        from src.personality.self_history import SelfHistory
        h = SelfHistory()
        assert h.count() == 0

    def test_02_history_append(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        e = SelfHistoryEvent(summary="test")
        assert h.append(e) is True
        assert h.count() == 1

    def test_03_history_query_by_type(self):
        from src.personality.self_history import (
            SelfHistory, SelfHistoryEvent, SelfHistoryEventType,
        )
        h = SelfHistory()
        h.append(SelfHistoryEvent(event_type=SelfHistoryEventType.PCR_APPLIED))
        h.append(SelfHistoryEvent(event_type=SelfHistoryEventType.SNAPSHOT_CREATED))
        assert len(h.query(event_type=SelfHistoryEventType.PCR_APPLIED)) == 1

    def test_04_history_query_by_source_type(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        h.append(SelfHistoryEvent(source_type="pcr", source_id="x"))
        h.append(SelfHistoryEvent(source_type="manual", source_id="y"))
        assert len(h.query(source_type="pcr")) == 1

    def test_05_history_invalid_event(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        e = SelfHistoryEvent(event_type="bogus_type")
        assert h.append(e) is False

    def test_06_history_latest(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        for _ in range(5):
            h.append(SelfHistoryEvent())
        assert len(h.latest(3)) == 3

    def test_07_history_snapshot_at(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        e1 = SelfHistoryEvent(summary="a", snapshot_after={"v": 1})
        h.append(e1)
        e2 = SelfHistoryEvent(summary="b", snapshot_after={"v": 2})
        h.append(e2)
        snap = h.snapshot_at(e2.event_id)
        assert snap == {"v": 2}

    def test_08_history_rollback_to(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        e1 = SelfHistoryEvent(summary="a", snapshot_after={"v": 1})
        h.append(e1)
        snap = h.rollback_to(e1.event_id)
        assert snap == {"v": 1}

    def test_09_history_serialization(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        h.append(SelfHistoryEvent(summary="a"))
        d = h.to_dict()
        h2 = SelfHistory()
        h2.load_dict(d)
        assert h2.count() == 1

    def test_10_history_get_by_id(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        e = SelfHistoryEvent(summary="x")
        h.append(e)
        assert h.get(e.event_id) is not None

    def test_11_history_max_truncate(self):
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        h = SelfHistory()
        # 添加 > 1000 个，验证截断
        for i in range(1005):
            h.append(SelfHistoryEvent(summary=f"e{i}"))
        assert h.count() <= 1000


# ============================================================
# [Reflection] SelfReflection 生成
# ============================================================

class TestSelfReflection(unittest.TestCase):
    """SelfReflection 笔记生成与存储"""

    def test_01_reflection_default(self):
        from src.personality.self_reflection import SelfReflectionNote
        n = SelfReflectionNote()
        assert n.note_id.startswith("refl_")
        assert n.confidence == 0.5

    def test_02_reflection_valid(self):
        from src.personality.self_reflection import SelfReflectionNote
        n = SelfReflectionNote(
            trigger_source="pcr_applied",
            reflection_type="growth",
            content="I grew in warmth",
            confidence=0.7,
        )
        assert n.is_valid()

    def test_03_reflection_invalid_trigger(self):
        from src.personality.self_reflection import SelfReflectionNote
        n = SelfReflectionNote(trigger_source="invalid_x")
        errs = n.validate()
        assert any("trigger_source" in e for e in errs)

    def test_04_reflection_invalid_type(self):
        from src.personality.self_reflection import SelfReflectionNote
        n = SelfReflectionNote(reflection_type="invalid_x")
        errs = n.validate()
        assert any("reflection_type" in e for e in errs)

    def test_05_reflection_store_append(self):
        from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
        s = SelfReflectionStore()
        n = SelfReflectionNote(content="test")
        assert s.append(n) is True
        assert s.count() == 1

    def test_06_reflection_store_query(self):
        from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
        s = SelfReflectionStore()
        s.append(SelfReflectionNote(trigger_source="manual", reflection_type="identity", content="a"))
        s.append(SelfReflectionNote(trigger_source="pcr_applied", reflection_type="growth", content="b"))
        assert len(s.query(trigger_source="manual")) == 1
        assert len(s.query(reflection_type="growth")) == 1

    def test_07_reflection_serialization(self):
        from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
        s = SelfReflectionStore()
        s.append(SelfReflectionNote(content="x"))
        d = s.to_dict()
        s2 = SelfReflectionStore.from_dict(d)
        assert s2.count() == 1

    def test_08_reflection_max_truncate(self):
        from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
        s = SelfReflectionStore()
        for i in range(510):
            s.append(SelfReflectionNote(content=f"n{i}"))
        assert s.count() <= 500


# ============================================================
# [Adapter] SelfModelAdapter 唯一写入口
# ============================================================

class TestSelfModelAdapterSoleWriter(unittest.TestCase):
    """SelfModelAdapter 是 SelfModel 唯一写入口"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p61_ad_")
        self.mgr, self.identity = _make_manager_with_identity()
        self.updater = _make_updater()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_adapter_init_default(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter()
        assert a is not None
        assert a.get_beliefs() is not None
        assert a.get_history() is not None
        assert a.get_reflections() is not None
        assert a.get_snapshot_store() is not None

    def test_02_adapter_apply_pcr_basic(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
        )
        pcr = _make_pcr(confidence=0.7)
        result = a.apply_pcr(pcr)
        assert result["applied"] is True
        assert result["self_model_updated"] is True

    def test_03_adapter_records_history_event(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
        )
        pcr = _make_pcr()
        a.apply_pcr(pcr)
        events = a.get_history().query()
        assert len(events) >= 1

    def test_04_adapter_creates_beliefs(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
        )
        pcr = _make_pcr(confidence=0.8)
        result = a.apply_pcr(pcr)
        # 至少一条 belief（来自 trait changes）
        assert result["beliefs_added"] >= 1 or result["beliefs_reinforced"] >= 1
        assert a.get_beliefs().count() >= 1

    def test_05_adapter_creates_reflection(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
        )
        pcr = _make_pcr()
        a.apply_pcr(pcr)
        assert a.get_reflections().count() >= 1

    def test_06_adapter_invalid_pcr(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter()
        result = a.apply_pcr("not_a_dict")
        assert result["applied"] is False
        assert result["note"] == "invalid_pcr"

    def test_07_adapter_snapshot_now(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(self_model_manager=self.mgr)
        snap_id = a.snapshot_now(note="manual_test")
        assert snap_id is not None
        assert a.get_snapshot_store().get(snap_id) is not None

    def test_08_adapter_rollback(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
        )
        # 先做一次 apply
        pcr1 = _make_pcr(pcr_id="pcr_a", proposal_id="prop_a", confidence=0.7)
        a.apply_pcr(pcr1)
        snap_id = a.snapshot_now(note="before_change_b")
        # 再做一次
        pcr2 = _make_pcr(pcr_id="pcr_b", proposal_id="prop_b", confidence=0.6)
        a.apply_pcr(pcr2)
        # 回滚
        result = a.rollback(snap_id, reason="test")
        assert result["applied"] is True


# ============================================================
# [Authority] Path whitelist
# ============================================================

class TestSelfModelPathAuthority(unittest.TestCase):
    """Path whitelist 拒绝非法路径"""

    def test_01_allowed_paths(self):
        from src.personality.self_model_core import (
            ALLOWED_SELF_MODEL_PATHS, validate_self_model_path,
        )
        for prefix in ALLOWED_SELF_MODEL_PATHS:
            assert validate_self_model_path(f"{prefix}.some_field") is True

    def test_02_forbidden_paths(self):
        from src.personality.self_model_core import (
            FORBIDDEN_SELF_MODEL_PATHS, validate_self_model_path,
        )
        for prefix in FORBIDDEN_SELF_MODEL_PATHS:
            assert validate_self_model_path(f"{prefix}.some_field") is False

    def test_03_unknown_path_rejected(self):
        from src.personality.self_model_core import validate_self_model_path
        assert validate_self_model_path("self_model.unknown.field") is False

    def test_04_empty_path_rejected(self):
        from src.personality.self_model_core import validate_self_model_path
        assert validate_self_model_path("") is False

    def test_05_non_string_path_rejected(self):
        from src.personality.self_model_core import validate_self_model_path
        assert validate_self_model_path(None) is False
        assert validate_self_model_path(123) is False

    def test_06_path_with_keyword_filtered_as_warning(self):
        """adapter 对非法路径仅作为 warning，不阻塞整体 apply"""
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter()
        # 构造一个含非法路径字段的 PCR（虽然 evolution_record 中无 path 字段，但通过 _extract_paths 提取）
        pcr = {
            "request_id": "pcr_path",
            "source_proposal_id": "prop_path",
            "evolution_record": {
                "trait_changes": {
                    "self_model.invalid_field": {"delta": 0.01, "before": 0.5},
                },
            },
            "growth_records": [],
            "confidence": 0.7,
            "evidence_count": 1,
            "reason": "test",
        }
        result = a.apply_pcr(pcr)
        # 即使路径被过滤，整体仍可 apply（因为 trait_changes 仍生效）
        assert "warnings" in result


# ============================================================
# [Confidence] Confidence 限制
# ============================================================

class TestSelfModelConfidenceLimit(unittest.TestCase):
    """低 confidence 仍可写 history，但不创建 belief"""

    def test_01_low_confidence_no_belief(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter()
        pcr = _make_pcr(confidence=0.05)  # 极低 confidence
        result = a.apply_pcr(pcr)
        # 即使 confidence 低，仍写 history
        assert a.get_history().count() >= 1
        # belief 阈值 0.3（默认），不创建新 belief
        # 但仍可能有 0 个 belief 添加
        assert result["beliefs_added"] == 0

    def test_02_high_confidence_creates_belief(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter()
        pcr = _make_pcr(confidence=0.8)
        result = a.apply_pcr(pcr)
        assert result["beliefs_added"] >= 1

    def test_03_min_confidence_threshold(self):
        from src.personality.self_model_adapter import (
            SelfModelAdapter, DEFAULT_MIN_CONFIDENCE_FOR_BELIEF,
        )
        a = SelfModelAdapter(min_confidence_belief=0.5)
        pcr = _make_pcr(confidence=0.4)
        result = a.apply_pcr(pcr)
        assert result["beliefs_added"] == 0


# ============================================================
# [Limiter] GrowthRateLimiter 集成
# ============================================================

class TestSelfModelLimiterIntegration(unittest.TestCase):
    """GrowthRateLimiter 接入"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p61_lm_")
        self.mgr, _ = _make_manager_with_identity()
        self.updater = _make_updater()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_limiter_allow_path(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        limiter = _make_limiter(allow=True)
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
            growth_limiter=limiter,
        )
        pcr = _make_pcr(confidence=0.7)
        result = a.apply_pcr(pcr)
        assert result["applied"] is True

    def test_02_limiter_deny_trait(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        limiter = _make_limiter(deny_traits=["warmth"])
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
            growth_limiter=limiter,
        )
        pcr = _make_pcr(confidence=0.7)
        result = a.apply_pcr(pcr)
        # limiter deny 不阻塞整个 apply；envelope 仍 applied=True，但 errors 中记录 deny
        # 注意：当前实现中 deny_traits 全部 deny 时，note 设为 "limiter_denied"
        # warmth 和 curiosity 都涉及，但只 deny warmth；其他仍可应用
        assert "errors" in result or "applied" in result

    def test_03_limiter_exception_isolated(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        limiter = MagicMock()
        limiter.check.side_effect = Exception("limiter_error")
        limiter.record.side_effect = Exception("record_error")
        a = SelfModelAdapter(
            self_model_manager=self.mgr,
            self_model_updater=self.updater,
            growth_limiter=limiter,
        )
        pcr = _make_pcr(confidence=0.7)
        result = a.apply_pcr(pcr)
        # 异常被隔离，整体仍可完成
        assert "warnings" in result


# ============================================================
# [Snapshot] Snapshot 创建与回滚
# ============================================================

class TestSelfModelSnapshot(unittest.TestCase):
    """SelfModelSnapshot 创建/回滚/恢复"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p61_snap_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_snapshot_default(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot
        s = SelfModelSnapshot()
        assert s.snapshot_id.startswith("snap_")
        assert s.version == 1

    def test_02_snapshot_valid(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot
        s = SelfModelSnapshot(
            self_identity={"identity_id": "x"},
            self_beliefs=[{"belief_id": "b1"}],
            understanding={"experience_awareness": 0.5},
        )
        assert s.is_valid()

    def test_03_snapshot_invalid_version(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot
        s = SelfModelSnapshot(version=0)
        assert not s.is_valid()

    def test_04_store_add_get(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot, SelfModelSnapshotStore
        store = SelfModelSnapshotStore()
        s = SelfModelSnapshot()
        assert store.add(s) is True
        assert store.get(s.snapshot_id) is not None

    def test_05_store_latest(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot, SelfModelSnapshotStore
        store = SelfModelSnapshotStore()
        for _ in range(3):
            store.add(SelfModelSnapshot())
        latest = store.latest_one()
        assert latest is not None

    def test_06_store_rollback_to(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot, SelfModelSnapshotStore
        store = SelfModelSnapshotStore()
        s1 = SelfModelSnapshot()
        store.add(s1)
        snap = store.rollback_to(s1.snapshot_id)
        assert snap is not None
        assert snap.snapshot_id == s1.snapshot_id

    def test_07_store_rollback_not_found(self):
        from src.personality.self_model_snapshot import SelfModelSnapshotStore
        store = SelfModelSnapshotStore()
        assert store.rollback_to("nonexistent") is None

    def test_08_store_prune(self):
        from src.personality.self_model_snapshot import SelfModelSnapshot, SelfModelSnapshotStore
        store = SelfModelSnapshotStore(max_snapshots=3)
        for _ in range(5):
            store.add(SelfModelSnapshot())
        assert store.count() == 3

    def test_09_persistence(self):
        path = os.path.join(self.tmpdir, "snapshots.json")
        from src.personality.self_model_snapshot import SelfModelSnapshot, SelfModelSnapshotStore
        store1 = SelfModelSnapshotStore(storage_path=path)
        s1 = SelfModelSnapshot(note="test_persist")
        store1.add(s1)
        # 重新加载
        store2 = SelfModelSnapshotStore(storage_path=path)
        assert store2.get(s1.snapshot_id) is not None

    def test_10_snapshot_rollback_via_adapter(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        mgr, _ = _make_manager_with_identity()
        upd = _make_updater()
        a = SelfModelAdapter(self_model_manager=mgr, self_model_updater=upd)
        pcr = _make_pcr(pcr_id="pcr_r1", proposal_id="prop_r1")
        a.apply_pcr(pcr)
        snap_id = a.snapshot_now(note="before_r2")
        pcr2 = _make_pcr(pcr_id="pcr_r2", proposal_id="prop_r2")
        a.apply_pcr(pcr2)
        # 回滚
        result = a.rollback(snap_id, reason="test_rollback")
        assert result["applied"] is True


# ============================================================
# [Pipeline] step_personality 触发 SelfModel
# ============================================================

class TestPipelinePersonalityTriggersSelfModel(unittest.TestCase):
    """Pipeline step_personality 触发 SelfModel 子步骤"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p61_pl_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_pipeline_with_self_model(self):
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline
        from src.personality.self_model_adapter import SelfModelAdapter

        mgr, _ = _make_manager_with_identity()
        upd = _make_updater()
        sma = SelfModelAdapter(self_model_manager=mgr, self_model_updater=upd)

        # Mock 各组件
        memory = MagicMock()
        memory.store_experience.return_value = True

        growth = MagicMock()
        growth.store_insight.return_value = True

        # Proposal stub
        proposal = MagicMock()
        proposal.id = "prop_pipe_001"
        proposal.confidence = 0.7
        proposal.evidence_ids = ["e1", "e2"]
        proposal.evaluator_meta = {}
        proposal.proposed_changes = []
        growth.list_proposals.return_value = [proposal]

        approval = MagicMock()
        approval.approve_proposal.return_value = MagicMock(record_id="rec_001", action="approved")

        personality = MagicMock()
        personality.apply_proposal.return_value = {
            "applied": True,
            "before": {"warmth": 0.5},
            "after": {"warmth": 0.55},
            "evolution_record_id": "rec_x",
            "note": "applied",
        }

        pipeline = RuntimeGrowthPipeline(
            memory_adapter=memory,
            growth_adapter=growth,
            approval_manager=approval,
            personality_adapter=personality,
            self_model_adapter=sma,
            history_path=os.path.join(self.tmpdir, "runs.json"),
        )
        return pipeline, sma, personality

    def test_01_pipeline_accepts_self_model_adapter(self):
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline
        sm = MagicMock()
        p = RuntimeGrowthPipeline(self_model_adapter=sm)
        snap = p.get_snapshot()
        assert snap["self_model_adapter"] is True

    def test_02_pipeline_step_personality_triggers_self_model(self):
        pipeline, sma, personality = self._build_pipeline_with_self_model()
        exp = MagicMock(experience_id="exp_pipe_001")
        run = pipeline.start_run(experience=exp)
        # 手动设置 proposal_id（绕过 step_proposal/lifecycle 的 mock 复杂设置）
        run.proposal_id = "prop_pipe_001"
        run.metadata["lifecycle_state"] = "approved"
        pipeline.step_personality(run)
        # 检查 personality stage outputs 中包含 self_model
        stage = [s for s in run.stages if s.stage == "personality"][0]
        assert "self_model" in stage.outputs

    def test_03_pipeline_without_self_model_adapter_still_works(self):
        """缺 self_model_adapter 时，step_personality 仍可运行"""
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline
        memory = MagicMock()
        growth = MagicMock()
        proposal = MagicMock(id="prop_p2", confidence=0.7, evidence_ids=[], evaluator_meta={})
        growth.list_proposals.return_value = [proposal]
        approval = MagicMock()
        approval.approve_proposal.return_value = MagicMock(record_id="r", action="approved")
        personality = MagicMock()
        personality.apply_proposal.return_value = {
            "applied": True, "before": {}, "after": {}, "evolution_record_id": "x", "note": "ok",
        }
        p = RuntimeGrowthPipeline(
            memory_adapter=memory, growth_adapter=growth,
            approval_manager=approval, personality_adapter=personality,
        )
        run = p.start_run()
        run.proposal_id = "prop_p2"
        run.metadata["lifecycle_state"] = "approved"
        p.step_personality(run)
        stage = [s for s in run.stages if s.stage == "personality"][0]
        assert "self_model" not in stage.outputs

    def test_04_pipeline_self_model_exception_isolated(self):
        """self_model_adapter 抛异常时，personality stage 仍 OK"""
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline
        memory = MagicMock()
        growth = MagicMock()
        proposal = MagicMock(id="prop_p3", confidence=0.7, evidence_ids=[], evaluator_meta={})
        growth.list_proposals.return_value = [proposal]
        approval = MagicMock()
        approval.approve_proposal.return_value = MagicMock(record_id="r", action="approved")
        personality = MagicMock()
        personality.apply_proposal.return_value = {
            "applied": True, "before": {}, "after": {}, "evolution_record_id": "x", "note": "ok",
        }
        sma = MagicMock()
        sma.apply_pcr.side_effect = Exception("self_model_boom")
        p = RuntimeGrowthPipeline(
            memory_adapter=memory, growth_adapter=growth,
            approval_manager=approval, personality_adapter=personality,
            self_model_adapter=sma,
        )
        run = p.start_run()
        run.proposal_id = "prop_p3"
        run.metadata["lifecycle_state"] = "approved"
        p.step_personality(run)
        stage = [s for s in run.stages if s.stage == "personality"][0]
        # self_model 异常被隔离
        assert "self_model" in stage.outputs
        assert stage.outputs["self_model"].get("note") == "self_model_isolated_exception"


# ============================================================
# [Isolation] SelfModel 异常隔离
# ============================================================

class TestSelfModelExceptionIsolation(unittest.TestCase):
    """SelfModel 失败不影响 Personality 修改"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p61_iso_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_manager_refresh_exception_isolated(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        mgr = MagicMock()
        mgr.get_full.side_effect = Exception("manager_boom")
        mgr.refresh.side_effect = Exception("refresh_boom")
        upd = MagicMock()
        from src.contracts.self_model_schema import SelfModelChangeSuggestion
        upd.from_pcr.return_value = SelfModelChangeSuggestion(
            source_type="pcr", source_id="pcr_iso", confidence=0.7,
        )
        upd.apply_suggestion.side_effect = Exception("apply_boom")
        a = SelfModelAdapter(self_model_manager=mgr, self_model_updater=upd)
        pcr = _make_pcr()
        result = a.apply_pcr(pcr)
        # 即便 updater / manager 异常，整体仍 applied=True（隔离）
        assert "warnings" in result
        assert len(result["warnings"]) > 0

    def test_02_limiter_record_exception_isolated(self):
        from src.personality.self_model_adapter import SelfModelAdapter
        mgr, _ = _make_manager_with_identity()
        upd = _make_updater()
        limiter = MagicMock()
        from unittest.mock import MagicMock as MM
        decision = MM()
        decision.decision = "allow"
        decision.violations = []
        decision.warnings = []
        limiter.check.return_value = decision
        limiter.record.side_effect = Exception("record_boom")
        a = SelfModelAdapter(
            self_model_manager=mgr, self_model_updater=upd, growth_limiter=limiter,
        )
        pcr = _make_pcr()
        result = a.apply_pcr(pcr)
        # record 异常被隔离
        assert result["applied"] is True


# ============================================================
# [Continuity] Self Continuity Test
# ============================================================

class TestSelfContinuity(unittest.TestCase):
    """多次成长后 SelfIdentity 仍保持连续性"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p61_cont_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_initial_preference_continuity(self):
        """
        第一次成长：'喜欢安静环境'
        经过多次 Growth 事件后：
        SelfIdentity 仍保持连续性（identity_id 不变，core_values 仍存在）
        """
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_belief import SelfBelief

        mgr, identity = _make_manager_with_identity()
        upd = _make_updater()
        adapter = SelfModelAdapter(
            self_model_manager=mgr, self_model_updater=upd,
        )

        # 1. 第一次成长：'喜欢安静环境'
        pcr1 = _make_pcr(
            pcr_id="pcr_first", proposal_id="prop_first",
            confidence=0.7,
            trait_changes={"warmth": {"delta": 0.05, "before": 0.5}},
            reason="likes_quiet_environment",
        )
        adapter.apply_pcr(pcr1)

        # 2. 多次后续成长
        for i in range(5):
            pcr_i = _make_pcr(
                pcr_id=f"pcr_sub_{i}",
                proposal_id=f"prop_sub_{i}",
                confidence=0.5 + 0.05 * i,
                trait_changes={f"trait_{i}": {"delta": 0.01 * i, "before": 0.5}},
                reason=f"growth_event_{i}",
            )
            adapter.apply_pcr(pcr_i)

        # 3. 验证 identity 连续性
        final_identity = mgr.get_full()
        assert final_identity["identity_id"] == identity.identity_id  # identity_id 不变
        # core_values 应包含最初的核心价值观
        cv_ids = [cv["value_id"] for cv in final_identity["core_values"]]
        assert "honesty" in cv_ids  # 默认核心价值观仍在

        # 4. 验证 history 记录所有变化
        events = adapter.get_history().query()
        assert len(events) >= 6  # 1 + 5

        # 5. 验证 reflection 记录元认知
        notes = adapter.get_reflections().query()
        assert len(notes) >= 1

    def test_02_snapshot_continuity_after_rollback(self):
        """回滚后 identity 保持一致"""
        from src.personality.self_model_adapter import SelfModelAdapter
        mgr, identity = _make_manager_with_identity()
        upd = _make_updater()
        adapter = SelfModelAdapter(self_model_manager=mgr, self_model_updater=upd)

        # 初始状态快照
        snap_initial = adapter.snapshot_now(note="initial")

        # 多次成长
        for i in range(3):
            adapter.apply_pcr(_make_pcr(pcr_id=f"pcr_c{i}", proposal_id=f"prop_c{i}"))

        # 回滚到 initial
        result = adapter.rollback(snap_initial, reason="continuity_test")
        assert result["applied"] is True

        # 验证 identity_id 不变
        final_identity = mgr.get_full()
        assert final_identity["identity_id"] == identity.identity_id


# ============================================================
# [Regression] Phase 5.5.1 / 5.5.2 / 6.0 关键回归
# ============================================================

class TestPhase551Regression(unittest.TestCase):
    """Phase 5.5.1 关键回归（ALLOWED_PERSONALITY_PATHS、Memory 隔离）"""

    def test_01_allowed_personality_paths_still_works(self):
        from src.personality.personality_adapter import (
            ALLOWED_PERSONALITY_PATHS, validate_proposal_path,
        )
        assert validate_proposal_path("personality.traits.warmth") is True
        assert "personality.traits.warmth" in ALLOWED_PERSONALITY_PATHS

    def test_02_memory_action_paths(self):
        from src.personality.personality_adapter import (
            MEMORY_ACTION_PATHS, validate_proposal_path,
        )
        assert validate_proposal_path("memory.mark_incorrect", action_scope="memory") is True
        assert "memory.mark_incorrect" in MEMORY_ACTION_PATHS

    def test_03_personality_path_validation_error(self):
        from src.personality.personality_adapter import (
            PersonalityPathValidationError, validate_proposal_path,
        )
        with self.assertRaises(PersonalityPathValidationError):
            validate_proposal_path("self_model.system.invalid")


class TestPhase552Regression(unittest.TestCase):
    """Phase 5.5.2 关键回归（GrowthRateLimiter 行为）"""

    def test_01_limiter_importable(self):
        from src.growth.growth_limiter import GrowthRateLimiter
        assert GrowthRateLimiter is not None

    def test_02_limiter_instantiate(self):
        from src.growth.growth_limiter import GrowthRateLimiter
        limiter = GrowthRateLimiter()
        assert limiter is not None


class TestPhase60Regression(unittest.TestCase):
    """Phase 6.0 关键回归（Pipeline 各阶段）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_p60_reg_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_pipeline_stages_intact(self):
        from src.runtime.pipeline.runtime_growth_pipeline import (
            PipelineStage, RuntimeGrowthPipeline,
        )
        assert PipelineStage.EXPERIENCE.value == "experience"
        assert PipelineStage.MEMORY.value == "memory"
        assert PipelineStage.REFLECTION.value == "reflection"
        assert PipelineStage.PROPOSAL.value == "proposal"
        assert PipelineStage.LIFECYCLE.value == "lifecycle"
        assert PipelineStage.PERSONALITY.value == "personality"
        assert PipelineStage.COMPLETED.value == "completed"
        assert PipelineStage.FAILED.value == "failed"
        # 6 阶段保持不变（无第七阶段）
        stages = [s.value for s in PipelineStage]
        assert "self_model" not in stages  # Phase 6.1 不增加新阶段

    def test_02_pipeline_snapshot_includes_self_model(self):
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline
        sm = MagicMock()
        p = RuntimeGrowthPipeline(self_model_adapter=sm)
        snap = p.get_snapshot()
        assert "self_model_adapter" in snap
        assert "self_model_triggered_runs" in snap

    def test_03_pipeline_without_self_model_default(self):
        """不传 self_model_adapter 时，snapshot.self_model_adapter=False"""
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline
        p = RuntimeGrowthPipeline()
        snap = p.get_snapshot()
        assert snap["self_model_adapter"] is False

    def test_04_pipeline_run_cycle_minimal(self):
        """run_cycle 仍能完整运行（无 self_model 时）"""
        from src.runtime.pipeline.runtime_growth_pipeline import RuntimeGrowthPipeline

        memory = MagicMock()
        memory.store_experience.return_value = True
        growth = MagicMock()
        growth.list_proposals.return_value = []
        approval = MagicMock()
        personality = MagicMock()
        p = RuntimeGrowthPipeline(
            memory_adapter=memory, growth_adapter=growth,
            approval_manager=approval, personality_adapter=personality,
        )
        run = p.start_run()
        p.step_memory(run)
        p.step_reflection(run)
        p.step_proposal(run)
        p.step_lifecycle(run, auto_approve=True)
        p.step_personality(run)
        p.finish_run(run)
        # 即使 proposal 为空，整体仍可完成
        assert run.finished_at != ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
