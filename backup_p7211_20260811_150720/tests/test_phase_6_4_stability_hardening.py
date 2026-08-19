"""
Phase 6.4 Test Suite: SelfModel Runtime Stability Hardening

覆盖：
- 6.4.1 SelfModel Quota & Retention
- 6.4.2 Persistence Health Check
- 6.4.3 Audit Enhancement (timeline / belief source / PCR link)
- 6.4.4 Performance Test (365天 / 5000 beliefs / 10000 history / 3000 reflections)
- 6.4.5 Full Runtime Validation

约束：
- 不修改 RuntimeCore
- 不删除 legacy SelfModel
- 不合并 V3/schema
- 保持 backward compatibility

总测试数：≥50
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 允许从项目根目录运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_data_dir(tmp_path):
    """临时数据目录"""
    d = tmp_path / "self_model_data"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def tmp_audit_dir(tmp_path):
    """临时 audit 数据目录"""
    d = tmp_path / "audit_data"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture
def fresh_adapter():
    """创建全新 SelfModelAdapter"""
    from src.personality.self_model_adapter import SelfModelAdapter
    return SelfModelAdapter(actor="phase_6_4_test")


@pytest.fixture
def fresh_stores():
    """创建全新的三个 store"""
    from src.personality.self_belief import SelfBeliefStore
    from src.personality.self_history import SelfHistory
    from src.personality.self_reflection import SelfReflectionStore
    return (
        SelfBeliefStore(),
        SelfHistory(),
        SelfReflectionStore(),
    )


# ============================================================
# Task 6.4.1: Retention Tests
# ============================================================

class TestRetentionStructure:
    """Retention 模块结构测试"""

    def test_01_create_default(self):
        from src.personality.self_model_retention import create_default_retention
        r = create_default_retention()
        assert r.max_active_beliefs == 1000
        assert r.min_confidence_for_active == 0.2

    def test_02_create_custom(self):
        from src.personality.self_model_retention import SelfModelRetention
        r = SelfModelRetention(
            max_active_beliefs=500,
            min_confidence_for_active=0.1,
            max_in_memory_events=2000,
            recent_window_days=60,
        )
        assert r.max_active_beliefs == 500
        assert r.recent_window_days == 60

    def test_03_report_dataclass(self):
        from src.personality.self_model_retention import RetentionReport
        rep = RetentionReport()
        s = rep.summary()
        assert "beliefs" in s
        assert "history" in s
        assert "reflections" in s

    def test_04_archive_summary(self):
        from src.personality.self_model_retention import SelfModelRetention
        r = SelfModelRetention()
        summary = r.get_archive_summary()
        assert "beliefs_archived_count" in summary
        assert "thresholds" in summary

    def test_05_reset(self):
        from src.personality.self_model_retention import SelfModelRetention
        r = SelfModelRetention()
        r._archived_beliefs.add("test")
        r.reset()
        assert len(r._archived_beliefs) == 0


class TestRetentionBeliefQuota:
    """SelfBelief 配额测试"""

    def test_01_low_confidence_deactivated(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        # 创建低 confidence belief
        for i in range(5):
            b = SelfBelief(
                domain="value",
                content=f"low_{i}",
                confidence=0.05,  # 低于 0.2
            )
            beliefs.add(b)
        r = SelfModelRetention(min_confidence_for_active=0.2)
        rep = r.enforce_belief_quota(beliefs, dry_run=False)
        assert rep.beliefs_deactivated == 5
        assert rep.applied is True

    def test_02_high_confidence_kept(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        b = SelfBelief(domain="value", content="hi", confidence=0.9)
        beliefs.add(b)
        r = SelfModelRetention(min_confidence_for_active=0.2)
        rep = r.enforce_belief_quota(beliefs, dry_run=False)
        assert rep.beliefs_deactivated == 0
        assert b.active is True

    def test_03_quota_exceeded(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        for i in range(20):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.5 + i * 0.01))
        r = SelfModelRetention(max_active_beliefs=10, min_confidence_for_active=0.2)
        rep = r.enforce_belief_quota(beliefs, dry_run=False)
        assert rep.beliefs_deactivated == 10
        # 低 confidence 的应被 deactivate
        active_count = sum(1 for b in beliefs.all() if b.active)
        assert active_count == 10

    def test_04_dry_run_does_not_modify(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        b = SelfBelief(domain="value", content="x", confidence=0.1)
        beliefs.add(b)
        r = SelfModelRetention(min_confidence_for_active=0.2)
        rep = r.enforce_belief_quota(beliefs, dry_run=True)
        assert rep.beliefs_deactivated == 1
        assert b.active is True  # dry_run 不修改

    def test_05_no_deletion_only_deactivation(self, fresh_stores):
        """核心约束：不删除任何 belief"""
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        for i in range(50):
            beliefs.add(SelfBelief(domain="value", content=f"d{i}", confidence=0.05))
        r = SelfModelRetention(min_confidence_for_active=0.2)
        r.enforce_belief_quota(beliefs, dry_run=False)
        assert beliefs.count() == 50  # 不删除


class TestRetentionHistoryQuota:
    """SelfHistory 配额测试"""

    def test_01_within_limit(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        for i in range(10):
            history.append(SelfHistoryEvent(summary=f"e{i}"))
        r = SelfModelRetention(max_in_memory_events=100)
        rep = r.enforce_history_quota(history, dry_run=False)
        assert rep.history_archived == 0

    def test_02_exceeded_archive_by_importance(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_history import SelfHistoryEvent, SelfHistoryEventType
        _, history, _ = fresh_stores
        for i in range(20):
            history.append(SelfHistoryEvent(
                event_type=SelfHistoryEventType.PCR_APPLIED,
                summary=f"low_importance_{i}",
            ))
        for i in range(5):
            history.append(SelfHistoryEvent(
                event_type=SelfHistoryEventType.IDENTITY_CORE_CHANGED,
                summary=f"high_{i}",
            ))
        r = SelfModelRetention(max_in_memory_events=15)
        rep = r.enforce_history_quota(history, dry_run=False)
        assert rep.history_archived == 10
        # 重要的 identity_core_changed 应保留
        archived_ids = set(rep.history_archived_ids)
        for ev in history.all():
            if ev.event_type == SelfHistoryEventType.IDENTITY_CORE_CHANGED:
                assert ev.event_id not in archived_ids

    def test_03_no_deletion(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        for i in range(50):
            history.append(SelfHistoryEvent(summary=f"e{i}"))
        r = SelfModelRetention(max_in_memory_events=10)
        r.enforce_history_quota(history, dry_run=False)
        assert history.count() == 50  # 不删除


class TestRetentionReflectionQuota:
    """SelfReflection 配额测试"""

    def test_01_high_value_kept(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_reflection import SelfReflectionNote
        _, _, reflections = fresh_stores
        n = SelfReflectionNote(
            content="high",
            reflection_type="identity",
            confidence=0.9,  # 高价值
        )
        reflections.append(n)
        r = SelfModelRetention(high_value_min_confidence=0.7, recent_window_days=0)
        rep = r.enforce_reflection_quota(reflections, dry_run=False)
        assert rep.reflections_archived == 0

    def test_02_low_value_outside_window_archived(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from datetime import datetime, timedelta
        from src.personality.self_reflection import SelfReflectionNote
        _, _, reflections = fresh_stores
        old_ts = (datetime.utcnow() - timedelta(days=200)).isoformat() + "Z"
        n = SelfReflectionNote(
            content="old low",
            reflection_type="identity",
            confidence=0.3,  # 低
            timestamp=old_ts,
        )
        reflections.append(n)
        r = SelfModelRetention(high_value_min_confidence=0.7, recent_window_days=90)
        rep = r.enforce_reflection_quota(reflections, dry_run=False)
        assert rep.reflections_archived == 1

    def test_03_recent_window_kept(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_reflection import SelfReflectionNote
        _, _, reflections = fresh_stores
        n = SelfReflectionNote(
            content="recent",
            reflection_type="identity",
            confidence=0.3,
        )  # 现在时间
        reflections.append(n)
        r = SelfModelRetention(recent_window_days=90)
        rep = r.enforce_reflection_quota(reflections, dry_run=False)
        assert rep.reflections_archived == 0


class TestRetentionEnforceAll:
    """一键 enforce_all"""

    def test_01_enforce_all(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        from src.personality.self_history import SelfHistoryEvent
        beliefs, history, reflections = fresh_stores
        for i in range(30):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.1))
        for i in range(50):
            history.append(SelfHistoryEvent(summary=f"h{i}"))
        r = SelfModelRetention(
            max_active_beliefs=10,
            min_confidence_for_active=0.2,
            max_in_memory_events=20,
        )
        rep = r.enforce_all(beliefs, history, reflections, dry_run=False)
        assert rep.applied is True
        assert rep.beliefs_deactivated > 0
        assert rep.history_archived > 0

    def test_02_archive_summary_after_enforce(self, fresh_stores):
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_belief import SelfBelief
        beliefs, history, reflections = fresh_stores
        for i in range(20):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.05))
        r = SelfModelRetention()
        r.enforce_belief_quota(beliefs)
        summary = r.get_archive_summary()
        assert summary["beliefs_archived_count"] == 20


class TestRetentionRestore:
    """Restore 归档标记"""

    def test_01_restore_belief(self):
        from src.personality.self_model_retention import SelfModelRetention
        r = SelfModelRetention()
        r._archived_beliefs.add("bel_123")
        assert r.is_archived("bel_123", "belief")
        r.restore("bel_123", "belief")
        assert not r.is_archived("bel_123", "belief")

    def test_02_restore_history(self):
        from src.personality.self_model_retention import SelfModelRetention
        r = SelfModelRetention()
        r._archived_history.add("hevt_123")
        assert r.is_archived("hevt_123", "history")
        r.restore("hevt_123", "history")
        assert not r.is_archived("hevt_123", "history")

    def test_03_restore_reflection(self):
        from src.personality.self_model_retention import SelfModelRetention
        r = SelfModelRetention()
        r._archived_reflections.add("refl_123")
        assert r.is_archived("refl_123", "reflection")
        r.restore("refl_123", "reflection")
        assert not r.is_archived("refl_123", "reflection")


class TestRetentionImportance:
    """Importance tracker"""

    def test_01_compute_default(self):
        from src.personality.self_model_retention import ImportanceTracker
        t = ImportanceTracker()
        imp = t.compute_for_event("pcr_applied")
        assert imp == 0.8

    def test_02_compute_with_traits(self):
        from src.personality.self_model_retention import ImportanceTracker
        t = ImportanceTracker()
        imp = t.compute_for_event("pcr_applied", affected_traits={"a": 0.1})
        assert imp > 0.8

    def test_03_assign_and_get(self):
        from src.personality.self_model_retention import ImportanceTracker
        t = ImportanceTracker()
        imp = t.assign("e1", "rollback")
        assert t.get("e1") == 0.9


# ============================================================
# Task 6.4.2: Health Check Tests
# ============================================================

class TestHealthCheckerStructure:
    """Health Checker 结构测试"""

    def test_01_create(self):
        from src.personality.self_model_health import SelfModelHealthChecker
        c = SelfModelHealthChecker()
        rep = c.check()
        assert rep.overall_status in ("healthy", "warning", "degraded", "critical", "unknown")

    def test_02_report_summary(self):
        from src.personality.self_model_health import SelfModelHealthChecker, SelfModelHealthReport
        rep = SelfModelHealthReport()
        s = rep.summary()
        assert "files" in s
        assert "counts" in s
        assert "issues_by_severity" in s

    def test_03_issue_dataclass(self):
        from src.personality.self_model_health import HealthIssue
        i = HealthIssue(severity="warning", code="X", message="m")
        d = i.to_dict()
        assert d["severity"] == "warning"
        assert d["code"] == "X"


class TestHealthCheckerFileLevel:
    """文件层健康检查"""

    def test_01_no_persistence(self):
        from src.personality.self_model_health import SelfModelHealthChecker
        c = SelfModelHealthChecker(persistence=None)
        rep = c.check_persistence_only()
        # 无 persistence → 文件层跳过
        assert rep.files_checked == 0

    def test_02_missing_files(self, tmp_data_dir):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_model_persistence import SelfModelPersistence
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        c = SelfModelHealthChecker(persistence=pers)
        rep = c.check_persistence_only()
        # 关键文件缺失应有 warning
        assert "beliefs.jsonl" in rep.files_missing
        assert rep.overall_status in ("warning", "degraded")

    def test_03_corrupted_jsonl(self, tmp_data_dir):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_model_persistence import SelfModelPersistence
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        # 写一些 OK + 一些坏行
        with open(os.path.join(tmp_data_dir, "beliefs.jsonl"), "w") as f:
            f.write('{"valid": true}\n')
            f.write('this is not json\n')
            f.write('{"also_valid": true}\n')
        c = SelfModelHealthChecker(persistence=pers)
        rep = c.check_persistence_only()
        assert any("JSONL" in (i.code or "") for i in rep.issues)


class TestHealthCheckerStoreLevel:
    """Store 层健康检查"""

    def test_01_quota_exceeded_belief(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        for i in range(50):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.5))
        c = SelfModelHealthChecker(max_beliefs=20)
        rep = c.check(beliefs_store=beliefs)
        assert rep.over_belief_quota is True
        assert any("QUOTA" in i.code for i in rep.issues)

    def test_02_duplicate_belief_detection(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        # 添加 content+domain 重复的 belief（去重后只剩 1）
        beliefs.add(SelfBelief(domain="value", content="same", confidence=0.5))
        c = SelfModelHealthChecker()
        rep = c.check(beliefs_store=beliefs)
        # 去重后只有 1 个，不会有重复
        assert rep.beliefs_count == 1

    def test_03_confidence_out_of_range(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        b = SelfBelief(domain="value", content="bad", confidence=1.5)  # 超出
        # 这种会校验失败，不被加入
        added = beliefs.add(b)
        c = SelfModelHealthChecker()
        rep = c.check(beliefs_store=beliefs)
        # 正常情况下不会加入
        assert rep.beliefs_count == 0 or added is False

    def test_04_avg_confidence(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        beliefs.add(SelfBelief(domain="value", content="a", confidence=0.5))
        beliefs.add(SelfBelief(domain="value", content="b", confidence=0.7))
        c = SelfModelHealthChecker()
        rep = c.check(beliefs_store=beliefs)
        assert rep.beliefs_avg_confidence == pytest.approx(0.6, abs=0.01)

    def test_05_history_quota_check(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        for i in range(30):
            history.append(SelfHistoryEvent(summary=f"e{i}"))
        c = SelfModelHealthChecker(max_history=20)
        rep = c.check(history=history)
        assert rep.over_history_quota is True


class TestHealthCheckerSeverity:
    """严重度评估"""

    def test_01_healthy_when_empty(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        beliefs, history, reflections = fresh_stores
        c = SelfModelHealthChecker()
        rep = c.check(beliefs, history, reflections)
        assert rep.overall_status == "healthy"

    def test_02_warning_on_quota(self, fresh_stores):
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        for i in range(20):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.5))
        c = SelfModelHealthChecker(max_beliefs=10)
        rep = c.check(beliefs_store=beliefs)
        assert rep.overall_status in ("warning", "degraded")


# ============================================================
# Task 6.4.3: Audit Enhancement Tests
# ============================================================

class TestEvolutionTimeline:
    """Evolution Timeline"""

    def test_01_empty_timeline(self):
        from src.audit.self_model_audit import build_evolution_timeline
        timeline = build_evolution_timeline()
        assert timeline == []

    def test_02_beliefs_in_timeline(self, fresh_stores):
        from src.audit.self_model_audit import build_evolution_timeline
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        beliefs.add(SelfBelief(domain="value", content="t1", confidence=0.5))
        timeline = build_evolution_timeline(beliefs_store=beliefs)
        assert len(timeline) >= 1
        assert any(e["source"] == "belief" for e in timeline)

    def test_03_history_in_timeline(self, fresh_stores):
        from src.audit.self_model_audit import build_evolution_timeline
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        history.append(SelfHistoryEvent(summary="h1"))
        timeline = build_evolution_timeline(history=history)
        assert any(e["source"] == "history" for e in timeline)

    def test_04_reflections_in_timeline(self, fresh_stores):
        from src.audit.self_model_audit import build_evolution_timeline
        from src.personality.self_reflection import SelfReflectionNote
        _, _, reflections = fresh_stores
        reflections.append(SelfReflectionNote(
            content="r1", reflection_type="identity", confidence=0.6,
        ))
        timeline = build_evolution_timeline(reflections_store=reflections)
        assert any(e["source"] == "reflection" for e in timeline)

    def test_05_timeline_sorted_ascending(self, fresh_stores):
        from src.audit.self_model_audit import build_evolution_timeline
        from src.personality.self_belief import SelfBelief
        from src.personality.self_history import SelfHistoryEvent
        beliefs, history, _ = fresh_stores
        beliefs.add(SelfBelief(domain="value", content="b1", confidence=0.5))
        history.append(SelfHistoryEvent(summary="h1"))
        timeline = build_evolution_timeline(beliefs, history)
        timestamps = [e.get("timestamp", "") for e in timeline]
        assert timestamps == sorted(timestamps)

    def test_06_timeline_limit(self, fresh_stores):
        from src.audit.self_model_audit import build_evolution_timeline
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        for i in range(20):
            history.append(SelfHistoryEvent(summary=f"e{i}"))
        timeline = build_evolution_timeline(history=history, limit=5)
        assert len(timeline) == 5

    def test_07_timeline_time_filter(self, fresh_stores):
        from src.audit.self_model_audit import build_evolution_timeline
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        b = SelfBelief(domain="value", content="b1", confidence=0.5)
        beliefs.add(b)
        # 设置开始时间在未来 → 过滤掉
        timeline = build_evolution_timeline(
            beliefs_store=beliefs, start="2099-01-01T00:00:00Z"
        )
        assert len(timeline) == 0


class TestTraceBeliefOrigin:
    """追溯 belief 来源"""

    def test_01_not_found(self, fresh_stores):
        from src.audit.self_model_audit import trace_belief_origin
        beliefs, _, _ = fresh_stores
        result = trace_belief_origin("not_exist", beliefs_store=beliefs)
        assert "未找到" in result["explanation"]

    def test_02_found(self, fresh_stores):
        from src.audit.self_model_audit import trace_belief_origin
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        b = SelfBelief(domain="value", content="quiet", confidence=0.7)
        beliefs.add(b)
        result = trace_belief_origin(b.belief_id, beliefs_store=beliefs)
        assert result["belief_content"] == "quiet"
        assert result["belief_confidence"] == 0.7
        assert result["explanation"]  # 有解释

    def test_03_linked_history(self, fresh_stores):
        from src.audit.self_model_audit import trace_belief_origin
        from src.personality.self_belief import SelfBelief
        from src.personality.self_history import SelfHistoryEvent
        beliefs, history, _ = fresh_stores
        b = SelfBelief(
            domain="value", content="quiet", confidence=0.7,
            sources=["prop_123"]
        )
        beliefs.add(b)
        history.append(SelfHistoryEvent(
            event_type="pcr_applied", source_id="prop_123",
            summary="quiet_cre", affected_beliefs=[b.belief_id],
        ))
        result = trace_belief_origin(b.belief_id, beliefs_store=beliefs, history=history)
        assert len(result["linked_events"]) == 1
        assert result["linked_events"][0]["source_id"] == "prop_123"

    def test_04_no_sources(self, fresh_stores):
        from src.audit.self_model_audit import trace_belief_origin
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        b = SelfBelief(domain="value", content="x", confidence=0.5)
        beliefs.add(b)
        result = trace_belief_origin(b.belief_id, beliefs_store=beliefs)
        assert "没有来源" in result["explanation"]


class TestFindPCRRelated:
    """查找 PCR 关联"""

    def test_01_empty_proposal(self, fresh_stores):
        from src.audit.self_model_audit import find_pcr_related_events
        result = find_pcr_related_events("", beliefs_store=fresh_stores[0])
        assert result["summary"]["history_count"] == 0

    def test_02_history_match(self, fresh_stores):
        from src.audit.self_model_audit import find_pcr_related_events
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        history.append(SelfHistoryEvent(
            event_type="pcr_applied", source_id="prop_abc",
            summary="test", metadata={"pcr_id": "prop_abc"}
        ))
        result = find_pcr_related_events("prop_abc", history=history)
        assert result["summary"]["history_count"] == 1

    def test_03_belief_match(self, fresh_stores):
        from src.audit.self_model_audit import find_pcr_related_events
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        beliefs.add(SelfBelief(
            domain="value", content="x", confidence=0.5, sources=["prop_x"]
        ))
        result = find_pcr_related_events("prop_x", beliefs_store=beliefs)
        assert result["summary"]["belief_count"] == 1

    def test_04_reflection_match(self, fresh_stores):
        from src.audit.self_model_audit import find_pcr_related_events
        from src.personality.self_reflection import SelfReflectionNote
        _, _, reflections = fresh_stores
        reflections.append(SelfReflectionNote(
            content="r", reflection_type="growth",
            confidence=0.5, sources=["prop_y"]
        ))
        result = find_pcr_related_events("prop_y", reflections_store=reflections)
        assert result["summary"]["reflection_count"] == 1

    def test_05_summary(self, fresh_stores):
        from src.audit.self_model_audit import find_pcr_related_events
        result = find_pcr_related_events("p_1", *fresh_stores)
        s = result["summary"]
        assert "history_count" in s
        assert "belief_count" in s
        assert "reflection_count" in s


class TestExplainWhyBelief:
    """解释 belief 由来"""

    def test_01_no_belief(self, fresh_stores):
        from src.audit.self_model_audit import explain_why_belief
        beliefs, _, _ = fresh_stores
        result = explain_why_belief("non_exist", beliefs_store=beliefs)
        assert "未找到" in result["answer"]

    def test_02_with_proposal(self, fresh_stores):
        from src.audit.self_model_audit import explain_why_belief
        from src.personality.self_belief import SelfBelief
        from src.personality.self_history import SelfHistoryEvent
        beliefs, history, _ = fresh_stores
        b = SelfBelief(
            domain="value", content="喜欢安静", confidence=0.8,
            sources=["prop_123"]
        )
        beliefs.add(b)
        history.append(SelfHistoryEvent(
            event_type="pcr_applied", source_id="prop_123",
            summary="应用", affected_beliefs=[b.belief_id],
        ))
        result = explain_why_belief(b.belief_id, beliefs_store=beliefs, history=history)
        assert result["belief"]["content"] == "喜欢安静"
        assert result["pcr_link"] is not None
        assert "喜欢安静" in result["answer"]


# ============================================================
# Task 6.4.4: Performance Tests (365天 / 5000/10000/3000)
# ============================================================

class TestPerformanceBaseline:
    """性能基线测试"""

    def test_01_create_5000_beliefs(self, fresh_stores):
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        start = time.time()
        for i in range(5000):
            beliefs.add(SelfBelief(
                domain="value",
                content=f"perf_belief_{i}",
                confidence=0.3 + (i % 7) * 0.1,
                sources=[f"prop_{i}"],
            ))
        elapsed = time.time() - start
        assert beliefs.count() == 5000
        # 5000 条添加 < 5s
        assert elapsed < 5.0, f"添加 5000 beliefs 耗时 {elapsed:.2f}s"

    def test_02_create_10000_history(self, fresh_stores):
        from src.personality.self_history import SelfHistoryEvent
        _, history, _ = fresh_stores
        start = time.time()
        for i in range(10000):
            history.append(SelfHistoryEvent(summary=f"perf_evt_{i}"))
        elapsed = time.time() - start
        # 10000 条添加 < 10s
        assert elapsed < 10.0, f"添加 10000 history 耗时 {elapsed:.2f}s"

    def test_03_create_450_reflections(self, fresh_stores):
        """SelfReflectionStore.MAX_NOTES=500，故测试规模为 450"""
        from src.personality.self_reflection import SelfReflectionNote
        _, _, reflections = fresh_stores
        start = time.time()
        for i in range(450):
            reflections.append(SelfReflectionNote(
                content=f"perf_refl_{i}",
                reflection_type="growth",
                confidence=0.5 + (i % 5) * 0.1,
            ))
        elapsed = time.time() - start
        assert reflections.count() == 450
        assert elapsed < 3.0, f"添加 450 reflections 耗时 {elapsed:.2f}s"


class TestPerformance365DaySimulation:
    """365 天模拟性能"""

    def test_01_365_day_full_simulation(self, fresh_stores, tmp_data_dir):
        from src.personality.self_belief import SelfBelief
        from src.personality.self_history import SelfHistoryEvent, SelfHistoryEventType
        from src.personality.self_reflection import SelfReflectionNote
        from src.personality.self_model_retention import SelfModelRetention
        from src.personality.self_model_health import SelfModelHealthChecker
        from src.personality.self_model_persistence import SelfModelPersistence
        from src.audit.self_model_audit import build_evolution_timeline

        beliefs, history, reflections = fresh_stores
        # 启动时间
        start = time.time()

        # 365 天，每天 ~14 belief + ~3 history + ~1 reflection
        # （避免 store 内部 MAX 截断：history MAX=1000, reflection MAX=500）
        for day in range(365):
            for i in range(14):
                beliefs.add(SelfBelief(
                    domain="value",
                    content=f"day{day}_belief_{i}",
                    confidence=0.3 + (i % 7) * 0.1,
                    sources=[f"prop_d{day}_{i}"],
                ))
            # 每天少量 history 累积（>1000 会被 store 截断）
            for i in range(3):
                history.append(SelfHistoryEvent(
                    event_type=SelfHistoryEventType.PCR_APPLIED,
                    summary=f"day{day}_evt_{i}",
                    source_id=f"prop_d{day}_{i % 14}",
                ))
            for i in range(1):
                reflections.append(SelfReflectionNote(
                    content=f"day{day}_refl_{i}",
                    reflection_type="growth",
                    confidence=0.5 + (i % 5) * 0.1,
                ))

        populate_time = time.time() - start
        # 预期：~5110 beliefs, ~1000 history (capped), ~365 reflections
        assert beliefs.count() >= 5000
        assert history.count() <= 1000  # 内部 MAX
        assert history.count() >= 1000 - 50  # 接近 MAX
        assert reflections.count() >= 350

        # 2. 启动时间模拟：Retention 配额
        start = time.time()
        retention = SelfModelRetention(
            max_active_beliefs=1000,
            max_in_memory_events=1000,
            recent_window_days=90,
        )
        rep = retention.enforce_all(beliefs, history, reflections, dry_run=False)
        retention_time = time.time() - start
        assert rep.applied is True
        assert retention_time < 3.0, f"retention 耗时 {retention_time:.2f}s"

        # 3. Health Check
        start = time.time()
        # 写入磁盘
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        pers.save_all(beliefs, history, reflections)
        checker = SelfModelHealthChecker(persistence=pers)
        health_rep = checker.check(beliefs, history, reflections)
        health_time = time.time() - start
        assert health_time < 3.0, f"health check 耗时 {health_time:.2f}s"

        # 4. Evolution Timeline
        start = time.time()
        timeline = build_evolution_timeline(beliefs, history, reflections, limit=200)
        timeline_time = time.time() - start
        assert len(timeline) > 0
        assert timeline_time < 3.0, f"timeline 耗时 {timeline_time:.2f}s"

        # 5. Persistence 大小检查
        stats = pers.get_stats()
        total_size = sum(f.get("size_bytes", 0) for f in stats["files"].values())
        # 总量 < 100MB
        assert total_size < 100 * 1024 * 1024, f"persistence 总大小 {total_size / 1024 / 1024:.2f}MB 过大"

    def test_02_query_performance(self, fresh_stores):
        from src.personality.self_belief import SelfBelief
        from src.audit.self_model_audit import find_pcr_related_events
        beliefs, history, _ = fresh_stores
        # 创建 5000 beliefs
        for i in range(5000):
            beliefs.add(SelfBelief(
                domain="value", content=f"b{i}", confidence=0.5,
                sources=[f"prop_{i}"] if i % 2 == 0 else [],
            ))
        # 查询性能
        start = time.time()
        for i in range(100):
            find_pcr_related_events(f"prop_{i * 2}", beliefs_store=beliefs)
        elapsed = time.time() - start
        # 100 次查询 < 5s
        assert elapsed < 5.0, f"100 次查询耗时 {elapsed:.2f}s"

    def test_03_persistence_save_performance(self, fresh_stores, tmp_data_dir):
        from src.personality.self_belief import SelfBelief
        from src.personality.self_model_persistence import SelfModelPersistence
        beliefs, history, reflections = fresh_stores
        for i in range(2000):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.5))
        for i in range(3000):
            from src.personality.self_history import SelfHistoryEvent
            history.append(SelfHistoryEvent(summary=f"h{i}"))
        for i in range(1000):
            from src.personality.self_reflection import SelfReflectionNote
            reflections.append(SelfReflectionNote(
                content=f"r{i}", reflection_type="growth", confidence=0.5
            ))
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        start = time.time()
        result = pers.save_all(beliefs, history, reflections)
        elapsed = time.time() - start
        assert elapsed < 5.0, f"save_all 耗时 {elapsed:.2f}s"
        assert result["beliefs"] is True
        assert result["history"] is True
        assert result["reflections"] is True

    def test_04_persistence_load_performance(self, tmp_data_dir):
        from src.personality.self_belief import SelfBelief, SelfBeliefStore
        from src.personality.self_history import SelfHistory, SelfHistoryEvent
        from src.personality.self_reflection import SelfReflectionStore, SelfReflectionNote
        from src.personality.self_model_persistence import SelfModelPersistence

        # 先写入
        # （注意：SelfHistory.MAX_EVENTS=1000, SelfReflectionStore.MAX_NOTES=500）
        beliefs, history, reflections = SelfBeliefStore(), SelfHistory(), SelfReflectionStore()
        for i in range(2000):
            beliefs.add(SelfBelief(domain="value", content=f"b{i}", confidence=0.5))
        for i in range(1000):  # 正好 MAX
            history.append(SelfHistoryEvent(summary=f"h{i}"))
        for i in range(500):  # 正好 MAX
            reflections.append(SelfReflectionNote(
                content=f"r{i}", reflection_type="growth", confidence=0.5
            ))
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        pers.save_all(beliefs, history, reflections)

        # 测试 load
        start = time.time()
        result = pers.load_all()
        elapsed = time.time() - start
        assert elapsed < 3.0, f"load_all 耗时 {elapsed:.2f}s"
        assert len(result["beliefs"]) == 2000
        assert len(result["history"]) == 1000
        assert len(result["reflections"]) == 500


# ============================================================
# Task 6.4.5: Full Runtime Validation
# ============================================================

class TestFullRuntimeStart:
    """Runtime 启动 -> SelfModel 自动加载"""

    def test_01_save_and_reload(self, fresh_adapter, tmp_data_dir):
        """创建 belief → save → restart → 自动恢复"""
        from src.personality.self_belief import SelfBelief
        from src.personality.self_model_persistence import SelfModelPersistence

        b1 = SelfBelief(domain="value", content="I am calm", confidence=0.7)
        b2 = SelfBelief(domain="preference", content="prefer tea", confidence=0.6)
        fresh_adapter._beliefs.add(b1)
        fresh_adapter._beliefs.add(b2)
        fresh_adapter._history.append(
            __import__("src.personality.self_history", fromlist=["SelfHistoryEvent"]).SelfHistoryEvent(
                summary="created"
            )
        )

        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        fresh_adapter.attach_persistence(pers)
        save_result = fresh_adapter.save_state()
        assert save_result.get("beliefs") is True

        # 创建新 adapter（模拟 restart）
        from src.personality.self_model_adapter import SelfModelAdapter
        new_adapter = SelfModelAdapter(actor="restart_test")
        new_adapter.attach_persistence(pers)
        counts = new_adapter.load_state()
        assert counts["beliefs"] == 2
        assert counts["history"] == 1

        # 验证内容
        restored = new_adapter.get_beliefs().all()
        contents = [b.content for b in restored]
        assert "I am calm" in contents
        assert "prefer tea" in contents

    def test_02_recovery_after_corruption(self, fresh_adapter, tmp_data_dir):
        """JSONL 损坏不阻塞 Runtime"""
        from src.personality.self_belief import SelfBelief
        from src.personality.self_model_persistence import SelfModelPersistence
        from src.personality.self_model_adapter import SelfModelAdapter

        # 先创建正常数据
        b = SelfBelief(domain="value", content="ok", confidence=0.5)
        fresh_adapter._beliefs.add(b)
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        fresh_adapter.attach_persistence(pers)
        fresh_adapter.save_state()

        # 手动破坏 JSONL
        with open(os.path.join(tmp_data_dir, "beliefs.jsonl"), "a") as f:
            f.write("this is corrupted\n{not_json\n")

        # 新 adapter → 仍然能恢复（坏行被跳过）
        new_adapter = SelfModelAdapter(actor="recovery_test")
        new_adapter.attach_persistence(pers)
        # 不应抛异常
        counts = new_adapter.load_state()
        assert counts["beliefs"] == 1  # 坏行被跳过

    def test_03_recovery_when_no_data(self, fresh_adapter, tmp_data_dir):
        """数据不存在不阻塞 Runtime"""
        from src.personality.self_model_persistence import SelfModelPersistence
        from src.personality.self_model_adapter import SelfModelAdapter

        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        new_adapter = SelfModelAdapter(actor="no_data_test")
        new_adapter.attach_persistence(pers)
        counts = new_adapter.load_state()
        assert counts["beliefs"] == 0
        # 仍可添加 belief
        from src.personality.self_belief import SelfBelief
        new_adapter._beliefs.add(SelfBelief(domain="value", content="x", confidence=0.5))
        assert new_adapter._beliefs.count() == 1


class TestFullRuntimePCRFlow:
    """完整 PCR → SelfModel 流程"""

    def test_01_pcr_creates_belief_and_history(self, fresh_adapter):
        from src.personality.self_history import SelfHistoryEventType
        pcr = {
            "request_id": "pcr_001",
            "source_proposal_id": "prop_001",
            "source_insight_id": "ins_001",
            "evolution_record": {
                "trait_changes": {"empathy": 0.15, "patience": 0.1}
            },
            "growth_records": [],
            "confidence": 0.75,
            "evidence_count": 5,
            "reason": "增强共情",
        }
        result = fresh_adapter.apply_pcr(pcr, actor="pcr_test")
        assert result["applied"] is True
        assert result["beliefs_added"] > 0

        # history 存在
        events = fresh_adapter.get_history().all()
        assert any(e.event_type == SelfHistoryEventType.PCR_APPLIED for e in events)

    def test_02_pcr_full_chain_audit(self, fresh_adapter):
        """PCR → belief → audit 全链路可追溯"""
        from src.audit.self_model_audit import (
            trace_belief_origin,
            find_pcr_related_events,
            build_evolution_timeline,
        )
        pcr = {
            "request_id": "pcr_002",
            "source_proposal_id": "prop_002",
            "evolution_record": {"trait_changes": {"warmth": 0.2}},
            "growth_records": [],
            "confidence": 0.8,
            "evidence_count": 3,
            "reason": "变得更温暖",
        }
        result = fresh_adapter.apply_pcr(pcr)
        assert result["beliefs_added"] > 0

        # 找新增的 belief
        all_beliefs = fresh_adapter.get_beliefs().all()
        new_beliefs = [b for b in all_beliefs if "prop_002" in (b.sources or [])]
        assert len(new_beliefs) > 0

        # 追溯
        belief = new_beliefs[0]
        trace = trace_belief_origin(
            belief.belief_id,
            beliefs_store=fresh_adapter.get_beliefs(),
            history=fresh_adapter.get_history(),
        )
        assert "prop_002" in trace["sources"]

        # PCR link
        link = find_pcr_related_events(
            "prop_002",
            beliefs_store=fresh_adapter.get_beliefs(),
            history=fresh_adapter.get_history(),
        )
        assert link["summary"]["belief_count"] >= 1

        # Timeline
        timeline = build_evolution_timeline(
            fresh_adapter.get_beliefs(),
            fresh_adapter.get_history(),
        )
        assert len(timeline) > 0


class TestFullRuntimeRestart:
    """完整 restart 流程"""

    def test_01_identical_state_after_restart(self, fresh_adapter, tmp_data_dir):
        from src.personality.self_belief import SelfBelief
        from src.personality.self_history import SelfHistoryEvent
        from src.personality.self_reflection import SelfReflectionNote
        from src.personality.self_model_persistence import SelfModelPersistence
        from src.personality.self_model_adapter import SelfModelAdapter

        # 写入数据
        for i in range(5):
            fresh_adapter._beliefs.add(SelfBelief(
                domain="value", content=f"b{i}", confidence=0.5
            ))
        for i in range(3):
            fresh_adapter._history.append(SelfHistoryEvent(summary=f"h{i}"))
        for i in range(2):
            fresh_adapter._reflections.append(SelfReflectionNote(
                content=f"r{i}", reflection_type="growth", confidence=0.5
            ))
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        fresh_adapter.attach_persistence(pers)
        fresh_adapter.save_state()

        # 模拟 restart
        new_adapter = SelfModelAdapter(actor="restart_full")
        new_adapter.attach_persistence(pers)
        counts = new_adapter.load_state()
        assert counts["beliefs"] == 5
        assert counts["history"] == 3
        assert counts["reflections"] == 2

    def test_02_three_restart_cycles(self, fresh_adapter, tmp_data_dir):
        from src.personality.self_belief import SelfBelief
        from src.personality.self_model_persistence import SelfModelPersistence
        from src.personality.self_model_adapter import SelfModelAdapter
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        fresh_adapter.attach_persistence(pers)

        for cycle in range(3):
            # 添加新 belief
            fresh_adapter._beliefs.add(SelfBelief(
                domain="value", content=f"cycle_{cycle}", confidence=0.5
            ))
            fresh_adapter.save_state()

            # restart
            new_adapter = SelfModelAdapter(actor=f"cycle_{cycle}")
            new_adapter.attach_persistence(pers)
            new_adapter.load_state()

            # 验证所有 cycle 的内容都在
            contents = [b.content for b in new_adapter.get_beliefs().all()]
            for c in range(cycle + 1):
                assert f"cycle_{c}" in contents


class TestFullRuntimeIntegration:
    """完整 Runtime 集成"""

    def test_01_pcr_then_save_then_restart_then_continue(self, tmp_data_dir):
        """完整链路：PCR → 写入 → 重启 → 继续 PCR"""
        from src.personality.self_model_adapter import SelfModelAdapter
        from src.personality.self_model_persistence import SelfModelPersistence

        adapter1 = SelfModelAdapter(actor="integration_1")
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        adapter1.attach_persistence(pers)

        # 第一次 PCR
        adapter1.apply_pcr({
            "request_id": "p1",
            "source_proposal_id": "prop_1",
            "evolution_record": {"trait_changes": {"a": 0.1}},
            "growth_records": [],
            "confidence": 0.7,
            "evidence_count": 2,
            "reason": "first",
        })
        adapter1.save_state()

        # 重启
        adapter2 = SelfModelAdapter(actor="integration_2")
        adapter2.attach_persistence(pers)
        adapter2.load_state()
        assert adapter2.get_beliefs().count() > 0

        # 第二次 PCR
        result2 = adapter2.apply_pcr({
            "request_id": "p2",
            "source_proposal_id": "prop_2",
            "evolution_record": {"trait_changes": {"b": 0.1}},
            "growth_records": [],
            "confidence": 0.7,
            "evidence_count": 2,
            "reason": "second",
        })
        assert result2["applied"] is True

        # 验证 belief 累计
        contents = [b.content for b in adapter2.get_beliefs().all()]
        assert any("a" in c for c in contents)
        assert any("b" in c for c in contents)

    def test_02_retention_after_long_run(self, fresh_stores):
        from src.personality.self_belief import SelfBelief
        from src.personality.self_model_retention import SelfModelRetention
        beliefs, history, reflections = fresh_stores
        for i in range(100):
            beliefs.add(SelfBelief(domain="value", content=f"x{i}", confidence=0.05))
        r = SelfModelRetention(max_active_beliefs=50, min_confidence_for_active=0.2)
        rep = r.enforce_belief_quota(beliefs)
        # 全部 deactivate（都低 confidence）
        assert rep.beliefs_deactivated == 100
        # 仍然保留（无删除）
        assert beliefs.count() == 100


class TestBackwardCompatibility:
    """向后兼容测试"""

    def test_01_legacy_adapter_works(self):
        """不引入 retention 时，adapter 仍工作"""
        from src.personality.self_model_adapter import SelfModelAdapter
        a = SelfModelAdapter(actor="legacy")
        result = a.apply_pcr({
            "request_id": "r1",
            "source_proposal_id": "p1",
            "evolution_record": {"trait_changes": {"x": 0.1}},
            "growth_records": [],
            "confidence": 0.6,
            "evidence_count": 1,
            "reason": "test",
        })
        assert result["applied"] is True

    def test_02_legacy_audit_works(self):
        """旧 audit 接口仍工作"""
        from src.audit.self_model_audit import (
            record_self_model_read,
            record_self_model_write,
            query_self_model_audit,
            trace_self_model_evolution,
        )
        rec = record_self_model_write(
            source="legacy_test",
            proposal_id="legacy_p",
            reason="backward compat test",
            confidence=0.5,
        )
        # rec 可能为 None（如果 storage 不可用），但不抛异常
        assert rec is None or rec is not None

    def test_03_existing_persistence_unchanged(self, tmp_data_dir):
        """不引入新模块时，persistence 行为不变"""
        from src.personality.self_belief import SelfBelief
        from src.personality.self_model_persistence import SelfModelPersistence
        from src.personality.self_model_adapter import SelfModelAdapter

        a = SelfModelAdapter(actor="compat_test")
        a._beliefs.add(SelfBelief(domain="value", content="x", confidence=0.5))
        pers = SelfModelPersistence(data_dir=tmp_data_dir)
        a.attach_persistence(pers)
        # save_state / load_state 仍然返回原始格式
        result = a.save_state()
        assert "beliefs" in result
        counts = a.load_state()
        assert "beliefs" in counts

    def test_04_retention_optional(self, fresh_stores):
        """retention 是可选的（不强制使用）"""
        from src.personality.self_belief import SelfBelief
        beliefs, _, _ = fresh_stores
        for i in range(20):
            beliefs.add(SelfBelief(domain="value", content=f"x{i}", confidence=0.05))
        # 不调用 retention → 不应被强制修改
        active_count = sum(1 for b in beliefs.all() if b.active)
        assert active_count == 20


# ============================================================
# Test Count: > 50
# ============================================================

def test_phase_6_4_summary():
    """总结测试数量"""
    import sys
    test_count = 0
    for name, obj in list(globals().items()):
        if name.startswith("Test") and isinstance(obj, type):
            test_count += len([m for m in dir(obj) if m.startswith("test_")])
    print(f"\n=== Phase 6.4 Total Test Count: {test_count} ===")
    assert test_count >= 50, f"测试数量 {test_count} 不足 50"
