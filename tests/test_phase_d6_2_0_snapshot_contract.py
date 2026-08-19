# -*- coding: utf-8 -*-
"""
Phase D.6.2.0 tests: Snapshot Contract (HistorySnapshot + TimelineQuery)
==========================================================================

闸口测试 (Gate Tests):
T1. HistorySnapshot 新字段默认值: existence_timeline = [], existence_quality = UNKNOWN
T2. source_total_count History = 5 (原 2 + 新 3), source_available 初始 0
T3. LifeSnapshotBuilder 跑原 raw 数据 → D.6.2 新字段仍保持默认 UNKNOWN(不填)
T4. LifeSnapshot.to_dict() 序列化包含 existence_timeline_count + quality 子字段
T5. LifeSnapshotBuilder _fill_history 不 crash,不抛异常,向后兼容
T6. TimelineQuery 默认全部 None,不产生任何过滤副作用
T7. TimelineQuery.to_limit / to_min_confidence 回落到 DEFAULT_*
T8. TimelineQuery.types_filter_set() 拒绝非字符串元素
T9. TimelineQuery.to_build_options() 懒 import TimelineBuildOptions,无循环依赖
T10. Backward compat: 老 existence_milestones (D.6.0 旧占位) 不删除,默认 []
T11. existence_stats 默认 {}, existence_stats_q = UNKNOWN
T12. 旧 raw dict (D.5.5 最小 raw) build 后 new 字段保持默认 UNKNOWN,不伪造
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from yuyi_desktop.core.life_snapshot import (
    HistorySnapshot,
    LifeSnapshot,
    LifeSnapshotBuilder,
    DQUALITY_UNKNOWN,
    DQUALITY_OK,
)
from src.contracts.existence import (
    DEFAULT_TIMELINE_LIMIT,
    DEFAULT_TIMELINE_MIN_CONFIDENCE,
    MILESTONE_BELIEF,
    MILESTONE_GROWTH,
    TimelineQuery,
)


# ============================================================
# T1~T3: HistorySnapshot 默认值
# ============================================================

class TestHistorySnapshotDefaults:
    def test_t1_existence_timeline_empty_list_default(self):
        h = HistorySnapshot()
        assert h.existence_timeline == []
        assert isinstance(h.existence_timeline, list)

    def test_t1_existence_quality_default_unknown(self):
        h = HistorySnapshot()
        assert h.existence_quality.status == DQUALITY_UNKNOWN
        assert h.existence_quality.source == ""
        assert h.existence_quality.error == ""

    def test_t2_source_total_count_history_is_5(self):
        h = HistorySnapshot()
        # 2 (growth/memory) + 3 (timeline/traits/beliefs) = 5
        assert h.source_total_count() == 5

    def test_t2_source_available_count_starts_at_0(self):
        """所有 q 默认 unknown → 0 可用。"""
        h = HistorySnapshot()
        assert h.source_available_count() == 0

    def test_t2_partial_availability_counts_correctly(self):
        """把某几个 q 设为 ok,available count 增加。"""
        h = HistorySnapshot()
        h.growth_q.status = DQUALITY_OK
        h.existence_quality.status = DQUALITY_OK
        h.stable_traits_q.status = DQUALITY_OK
        # 3 个 ok
        assert h.source_available_count() == 3

    def test_t10_old_existence_milestones_not_deleted(self):
        """T10:旧 existence_milestones 占位结构不删除(向后兼容)。"""
        h = HistorySnapshot()
        assert hasattr(h, "existence_milestones")
        assert h.existence_milestones == []
        assert hasattr(h, "existence_milestones_q")
        assert h.existence_milestones_q.status == DQUALITY_UNKNOWN

    def test_t11_existence_stats_default_empty_dict(self):
        h = HistorySnapshot()
        assert isinstance(h.existence_stats, dict)
        assert h.existence_stats == {}
        assert h.existence_stats_q.status == DQUALITY_UNKNOWN


# ============================================================
# T3/T5/T12: LifeSnapshotBuilder 旧逻辑不影响新字段(保持 UNKNOWN)
# ============================================================

def _make_d55_minimal_raw():
    """D.5.5 时代的最小 raw dict(不含 D.6.0/D.6.1 的字段)。"""
    return {
        "personality_overview": {
            "identity_name": "浅雾羽依",
            "version": "ev_999",
            "available": True,
        },
        "personality_traits": [("warmth", 0.62), ("curiosity", 0.58)],
        "runtime_overview": {
            "online": True,
            "health": "ok",
            "available": True,
        },
        "runtime_health": {"status": "ok", "success": True},
        "emotion_overview": {
            "available": True,
            "mood": "calm",
            "intensity": 0.3,
        },
        "memory_overview": {
            "total": 128,
            "latest_event_date": "2026-08-02T00:00:00Z",
            "available": True,
        },
        "growth_overview": {
            "total": 42,
            "latest_date": "2026-08-03T00:00:00Z",
            "available": True,
        },
    }


class TestBuilderBackwardCompat:
    def test_t3_builder_minimal_raw_does_not_fill_d62_fields(self):
        """D.5.5 最小 raw → 新字段保持默认 UNKNOWN,绝不生成假 Milestone。"""
        raw = _make_d55_minimal_raw()
        snap = LifeSnapshotBuilder.build(raw, elapsed_ms=12.0)
        h = snap.history
        # 绝不伪造
        assert h.existence_timeline == []
        assert h.existence_quality.status == DQUALITY_UNKNOWN
        assert h.existence_stats == {}
        assert h.existence_stats_q.status == DQUALITY_UNKNOWN
        assert h.stable_traits == []
        assert h.stable_beliefs == []
        # 原 D.5.5 字段正常
        assert h.growth_total == 42
        assert h.memory_total == 128

    def test_t5_builder_does_not_crash_on_empty_raw(self):
        """空 dict 构建不 crash,所有字段回落默认。"""
        snap = LifeSnapshotBuilder.build({}, elapsed_ms=0.0)
        assert isinstance(snap, LifeSnapshot)
        h = snap.history
        assert h.existence_timeline == []
        assert h.existence_quality.status == DQUALITY_UNKNOWN
        # 数值类不伪造
        assert h.growth_total == 0
        assert h.memory_total == 0
        assert h.recent_events == []

    def test_t12_raw_with_partial_data_preserves_new_field_defaults(self):
        """即使 raw 里有 memory/growth,没有 selfmodel/growth envelopes → 新字段仍 UNKNOWN。"""
        raw = {
            "memory_overview": {"total": 10, "available": True},
            "growth_overview": {"total": 3, "available": True},
        }
        snap = LifeSnapshotBuilder.build(raw)
        h = snap.history
        # 旧字段被填
        assert h.memory_total == 10
        assert h.growth_total == 3
        # 新字段 = 默认 UNKNOWN(不伪造)
        assert h.existence_timeline == []
        assert h.existence_quality.status == DQUALITY_UNKNOWN
        assert h.stable_traits_q.status == DQUALITY_UNKNOWN


# ============================================================
# T4: to_dict 序列化结构
# ============================================================

class TestSnapshotSerialization:
    def test_t4_to_dict_contains_new_history_fields(self):
        snap = LifeSnapshotBuilder.build(_make_d55_minimal_raw(), 9.0)
        d = snap.to_dict()
        h = d["history"]
        assert "existence_timeline_count" in h
        assert h["existence_timeline_count"] == 0
        assert "stable_traits_count" in h
        assert h["stable_traits_count"] == 0
        assert "stable_beliefs_count" in h
        assert h["stable_beliefs_count"] == 0
        # quality 子结构
        eq = h["existence_quality"]
        assert eq["status"] == DQUALITY_UNKNOWN
        assert eq["source"] == ""
        # stats_tail 30 项以内且为 dict
        assert isinstance(h["existence_stats_tail"], dict)

    def test_t4_to_dict_after_partial_fill_counts_correct(self):
        """模拟某 Service 真的填满 existence_timeline 3 条 → count=3。"""
        snap = LifeSnapshot()
        # 用 D.6.1 ExistenceMilestone 对象实例填充 existence_timeline
        from src.contracts.existence import (
            ExistenceMilestone as RealExistenceMilestone,
            EvidenceReference,
            ExplanationMetadata,
            ImpactMap,
            SOURCE_GROWTH_PROPOSAL,
        )
        for i in range(3):
            snap.history.existence_timeline.append(RealExistenceMilestone(
                timestamp=f"2026-08-0{i+1}T00:00:00Z",
                milestone_type=MILESTONE_GROWTH,
                title=f"Event {i}",
                summary="test",
                sources=[EvidenceReference(
                    source_type=SOURCE_GROWTH_PROPOSAL,
                    source_id=f"p_{i}",
                )],
                impact=ImpactMap(personality=True),
                explanation=ExplanationMetadata(evidence_count=1, confidence=0.8),
            ))
        snap.history.existence_quality.status = DQUALITY_OK
        snap.history.stable_traits.append("stub_entry")  # 占位,只验证 count
        snap.history.stable_beliefs.extend([1, 2])
        d = snap.to_dict()
        assert d["history"]["existence_timeline_count"] == 3
        assert d["history"]["stable_traits_count"] == 1
        assert d["history"]["stable_beliefs_count"] == 2
        assert d["history"]["existence_quality"]["status"] == DQUALITY_OK


# ============================================================
# T6~T9: TimelineQuery 查询契约
# ============================================================

class TestTimelineQueryContract:
    def test_t6_default_all_none_no_filter(self):
        q = TimelineQuery()
        assert q.start_time is None
        assert q.end_time is None
        assert q.types is None
        assert q.min_confidence is None
        assert q.limit is None
        assert q.cursor is None
        assert q.cursor_before is True
        assert q.adapter_whitelist is None
        assert q.affected_keys is None
        # has_* 检查
        assert q.has_time_window() is False
        assert q.has_types_filter() is False
        assert q.has_cursor() is False

    def test_t7_to_limit_and_confidence_fall_to_defaults(self):
        q = TimelineQuery()
        assert q.to_limit() == DEFAULT_TIMELINE_LIMIT
        assert q.to_min_confidence() == pytest.approx(DEFAULT_TIMELINE_MIN_CONFIDENCE)

    def test_t7_to_limit_clamps_negative(self):
        q = TimelineQuery(limit=-10)
        assert q.to_limit() == DEFAULT_TIMELINE_LIMIT
        q2 = TimelineQuery(limit=0)
        assert q2.to_limit() == DEFAULT_TIMELINE_LIMIT

    def test_t7_to_min_confidence_clamps_0_1(self):
        q = TimelineQuery(min_confidence=999.0)
        assert q.to_min_confidence() == pytest.approx(1.0)
        q2 = TimelineQuery(min_confidence=-10.0)
        assert q2.to_min_confidence() == pytest.approx(0.0)

    def test_t8_types_filter_set_accepts_only_strings(self):
        q = TimelineQuery(types={MILESTONE_GROWTH, MILESTONE_BELIEF, 42, None, True})
        fs = q.types_filter_set()
        # 非字符串被过滤
        assert fs == {MILESTONE_GROWTH, MILESTONE_BELIEF}

    def test_t8_has_types_filter_distinguish_none_vs_empty_set(self):
        # None = 不过滤
        assert TimelineQuery(types=None).has_types_filter() is False
        # 空 set = 过滤掉全部 (types={})
        assert TimelineQuery(types=set()).has_types_filter() is True

    def test_t9_to_build_options_no_circular_import(self):
        """T9: to_build_options() 内部懒加载 TimelineBuildOptions,不抛 ImportError。"""
        q = TimelineQuery(
            types={MILESTONE_GROWTH, MILESTONE_BELIEF},
            limit=20,
            min_confidence=0.5,
        )
        ref = datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc)
        opts = q.to_build_options(reference_now_utc=ref)
        # 验证 opts 类型名
        cls_name = type(opts).__name__
        assert cls_name == "TimelineBuildOptions"
        assert opts.limit == 20
        assert opts.min_confidence == pytest.approx(0.5)
        # allowed_types 正确
        assert isinstance(opts.allowed_types, (set, frozenset)) or opts.allowed_types is None
        assert MILESTONE_GROWTH in opts.allowed_types
        assert MILESTONE_BELIEF in opts.allowed_types

    def test_t9_cursor_has_dedicated_predicate(self):
        q0 = TimelineQuery()
        assert q0.has_cursor() is False
        q1 = TimelineQuery(cursor="2026-08-01T00:00:00Z")
        assert q1.has_cursor() is True

    def test_t9_to_dict_round_trip(self):
        q = TimelineQuery(
            start_time="2026-07-01T00:00:00Z",
            end_time="2026-08-01T00:00:00Z",
            types={MILESTONE_GROWTH},
            min_confidence=0.3,
            limit=15,
            cursor="cursor_xyz",
            cursor_before=False,
            adapter_whitelist=["proposal_milestone_adapter_v1"],
            affected_keys=["warmth"],
            metadata={"ui": "archive_tab"},
        )
        d = q.to_dict()
        assert d["start_time"] == "2026-07-01T00:00:00Z"
        assert d["cursor"] == "cursor_xyz"
        assert d["cursor_before"] is False
        assert d["affected_keys"] == ["warmth"]
        # types 归一化到 list (set 无序,仅验证成员)
        assert set(d["types"] or []) == {MILESTONE_GROWTH}
        assert d["metadata"]["ui"] == "archive_tab"
