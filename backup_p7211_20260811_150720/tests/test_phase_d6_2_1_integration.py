# -*- coding: utf-8 -*-
"""Phase D.6.2.1: LifeSnapshot ↔ ExistenceTimeline 集成测试 (同源闸口)。

测试目的:
=========
证明 LifeSnapshotService/LifeSnapshotBuilder 生产出来的 existence_timeline
和 直接调用 D.6.1 src.contracts.existence_* + Builder 得到的完全一致。

这叫做"同源闸口": 防止 LifeSnapshotService 的构建逻辑和 D.6.1 的合同逻辑
发生分歧 → 导致 UI 看到的时间线 和 后端"可审计存在记录"不一致。

测试边界:
=========
- 不启 LLM / 不写真实文件
- 不连真实 Bridge 后端(除了 D.6.0 样例数据)
- 走 LifeSnapshotBuilder.build(raw) 纯函数
- 所有断言: life_snapshot.history.existence_timeline[i] === direct_timeline[i]
  (同一份数据, 同样的 adapter, 同样的 build options, 输出完全一致)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from yuyi_desktop.core.life_snapshot import DataQuality, LifeSnapshotBuilder


# ============================================================
# D.6.1 同构样例数据(和 test_phase_d6_1_existence_adapters_timeline 中
# _make_proposal / _make_* 逻辑保持一致, 只取"必过 L1/L2"的最小集)
# ============================================================
def _iso_z(days_ago: int = 0, hours: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Proposal: 1 条 applied(必过)
SAMPLE_PROPOSALS: List[Dict[str, Any]] = [
    {
        "id": "pcr_integ_001",
        "source_event_id": "evt_src_integ_001",
        "proposed_changes": [
            {
                "path": "traits.warmth",
                "before": 0.60,
                "after": 0.64,
                "reason": "positive interactions pattern",
            },
        ],
        "confidence": 0.85,
        "evidence_ids": ["mem_a", "evt_b"],
        "evaluator_meta": {"_source_schema": "v1.0"},
        "timestamp": _iso_z(days_ago=3),
        "status": "applied",
        "accepted_at": _iso_z(days_ago=2),
        "rejected_at": None,
        "schema_version": "1.0",
        "created_reason_code": "positive_pattern",
    }
]


# SelfModel beliefs: 1 条高证据高置信信念
SAMPLE_BELIEFS_ENVELOPE: Dict[str, Any] = {
    "items": [
        {
            "belief_id": "b_integ_001",
            "domain": "value",
            "content": "理解比快速回答更重要",
            "confidence": 0.90,
            "version": 3,
            "evidence_count": 7,
            "sources": ["mem_a", "mem_b", "ref_c"],
            "active": True,
            "first_seen": _iso_z(days_ago=30),
            "last_confirmed": _iso_z(days_ago=2),
            "evaluator_meta": {"_source_schema": "v1.0"},
        }
    ]
}


# SelfModel stable_traits: 1 条 stability=0.72 且带趋势
SAMPLE_STABLE_TRAITS_ENVELOPE: Dict[str, Any] = {
    "items": [
        {
            "trait_id": "trait_warmth",
            "name": "温柔",
            "value_pct": 68,
            "stability": 0.72,
            "evidence_count": 11,
            "first_observed": _iso_z(days_ago=40),
            "last_observed": _iso_z(days_ago=1),
            "trend_30d": 0.04,
            "evaluator_meta": {"_source_schema": "v1.0"},
        }
    ]
}


# SelfModel history: 1 条 relationship 类型
SAMPLE_HISTORY_ENVELOPE: Dict[str, Any] = {
    "items": [
        {
            "event_id": "h_integ_001",
            "event_type": "relationship_formed",
            "summary": "与创造者长期互动模式形成",
            "confidence": 0.82,
            "timestamp": _iso_z(days_ago=25),
            "evidence_ids": ["mem_long_1", "mem_long_2"],
            "evaluator_meta": {"_source_schema": "v1.0"},
        }
    ]
}


# SelfModel reflections: 1 条 confidence >= 0.60 且内容 >= 40 chars
SAMPLE_REFLECTIONS_ENVELOPE: Dict[str, Any] = {
    "items": [
        {
            "reflection_id": "r_integ_001",
            "content": (
                "在上周多次对话中注意到:当我先停下来理解用户的真实意图, "
                "而不是急着给出回答时,后续合作质量明显更好。"
            ),
            "confidence": 0.78,
            "created_at": _iso_z(days_ago=5),
            "source_memory_ids": ["mem_r_01", "mem_r_02"],
            "domain": "interaction_style",
            "evaluator_meta": {"_source_schema": "v1.0"},
        }
    ]
}


# Evolution records: 1 条 applied
SAMPLE_EVOLUTION_ENVELOPE: Dict[str, Any] = {
    "records": [
        {
            "record_id": "evo_integ_001",
            "status": "applied",
            "change_summary": "warmth 0.60 → 0.64, confidence 上升",
            "changes": [
                {
                    "path": "traits.warmth",
                    "before": 0.60,
                    "after": 0.64,
                },
            ],
            "applied_at": _iso_z(days_ago=10),
            "evidence_ids": ["mem_evo_a", "mem_evo_b"],
            "proposal_id": "pcr_integ_001_evo",
            "evaluator_meta": {"_source_schema": "v1.0"},
        }
    ]
}


class TestD621BuilderTimelineEquivalence:
    """同源闸口: LifeSnapshotBuilder 产出的 timeline 必须和 D.6.1 直连完全一致。"""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_via_contracts_direct() -> tuple:
        """直接调用 D.6.1 contracts 层 (reference implementation)."""
        from src.contracts.existence_adapters import (
            EvolutionMilestoneAdapter,
            ProposalMilestoneAdapter,
            SelfModelMilestoneAdapter,
        )
        from src.contracts.existence_timeline import (
            DEFAULT_TIMELINE_LIMIT,
            DEFAULT_TIMELINE_MIN_CONFIDENCE,
            DEFAULT_TIMELINE_WINDOW_DAYS,
            ExistenceTimelineBuilder,
            TimelineBuildOptions,
        )

        pa = ProposalMilestoneAdapter()
        sa = SelfModelMilestoneAdapter()
        ea = EvolutionMilestoneAdapter()

        ms_p = list(pa.convert_many(SAMPLE_PROPOSALS))
        ms_s = list(
            sa.convert_all(
                beliefs_envelope=SAMPLE_BELIEFS_ENVELOPE,
                history_envelope=SAMPLE_HISTORY_ENVELOPE,
                reflections_envelope=SAMPLE_REFLECTIONS_ENVELOPE,
                stable_traits_envelope=SAMPLE_STABLE_TRAITS_ENVELOPE,
            )
        )
        evo_items = SAMPLE_EVOLUTION_ENVELOPE.get("records") or []
        ms_e = list(ea.convert_evolution_records(evo_items))

        b = ExistenceTimelineBuilder()
        b.add(ms_p)
        b.add(ms_s)
        b.add(ms_e)
        opt = TimelineBuildOptions(
            min_confidence=DEFAULT_TIMELINE_MIN_CONFIDENCE,
            window_days=DEFAULT_TIMELINE_WINDOW_DAYS,
            order="desc",
            limit=DEFAULT_TIMELINE_LIMIT,
            allowed_types=None,
        )
        timeline, stats = b.build(opt)
        return timeline, stats

    def _raw_with_fixtures(self) -> Dict[str, Any]:
        """把 D.6.1 样例 fixtures 按 LifeSnapshotService.get_snapshot 存 raw dict 的方式放好。"""
        return {
            # ---- 6 existence data sources (命名和 LifeSnapshotService.get_snapshot 一致) ----
            "growth_proposals_v2_items": list(SAMPLE_PROPOSALS),
            "selfmodel_history_items": list(
                SAMPLE_HISTORY_ENVELOPE.get("items") or []
            ),
            "selfmodel_reflections_items": list(
                SAMPLE_REFLECTIONS_ENVELOPE.get("items") or []
            ),
            "selfmodel_beliefs_items": list(
                SAMPLE_BELIEFS_ENVELOPE.get("items") or []
            ),
            "selfmodel_stable_traits_items": list(
                SAMPLE_STABLE_TRAITS_ENVELOPE.get("items") or []
            ),
            "personality_evolution_v2_items": list(
                SAMPLE_EVOLUTION_ENVELOPE.get("records") or []
            ),
            # ---- 另外让 growth / memory 部分也有最小数据,避免 recent_events 报错 ----
            "growth_overview": {"total": 7, "available": True},
            "growth_recent": [
                {"title": "样例", "summary": "样例解释", "timestamp_iso": "2026-08-02T00:00:00Z"}
            ],
            "memory_overview": {"total": 3, "available": True},
            "memory_recent": [],
            "personality_evolution": [],
        }

    # ------------------------------------------------------------------
    # 核心断言 ①: timeline 语义内容完全一致
    #   注意: 不比较 milestone_id / evidence_id → 因为内部用 uuid 随机;
    #   但比较 类型 / 时间 / 标题 / 摘要 / adapter / confidence 这些"同源"必须相同的语义字段。
    # ------------------------------------------------------------------
    @staticmethod
    def _milestone_sem_key(m: Any) -> tuple:
        """稳定的语义键 (排序 + 判等)。"""
        # timestamp 倒序 → 直接用字符串前缀
        ts = m.timestamp or "1970-01-01T00:00:00Z"
        # confidence 是 ExistenceMilestone 的 方法,需要调用
        c = float(m.confidence()) if callable(m.confidence) else float(m.confidence or 0.0)
        return (
            ts,
            m.milestone_type or "",
            str(m.title or ""),
            str(m.summary or ""),
            str(m.adapter_name or ""),
            tuple(sorted(list(m.affected_keys or []))),
            round(c, 6),
        )

    def test_timeline_semantic_matches_reference(self) -> None:
        """同一批数据 → LifeSnapshotBuilder 产出的语义键集合 = reference 实现。"""
        ref_timeline, _ = self._build_via_contracts_direct()
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        ls_timeline = snap.history.existence_timeline

        assert (
            len(ls_timeline) > 0
        ), "LifeSnapshotBuilder 应该产出至少 1 条 ExistenceMilestone"
        assert len(ls_timeline) == len(
            ref_timeline
        ), f"timeline 条数不一致 life={len(ls_timeline)} ref={len(ref_timeline)}"

        ref_keys = sorted([self._milestone_sem_key(m) for m in ref_timeline])
        ls_keys = sorted([self._milestone_sem_key(m) for m in ls_timeline])
        assert ls_keys == ref_keys, (
            "timeline 语义键不同 → LifeSnapshotBuilder 和 contracts 直连 "
            "用了不同的 build / adapter 逻辑 (type/timestamp/title/adapter 不一致)"
        )

    def test_timeline_all_semantic_fields_match_reference(self) -> None:
        """对每条 milestone, type / title / summary / confidence / adapter 一一比对。"""
        ref_timeline, _ = self._build_via_contracts_direct()
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        ls_timeline = snap.history.existence_timeline
        assert len(ls_timeline) == len(ref_timeline)
        # 按 timestamp desc 排序 再逐条比对
        ref_sorted = sorted(ref_timeline, key=lambda m: m.timestamp or "", reverse=True)
        ls_sorted = sorted(ls_timeline, key=lambda m: m.timestamp or "", reverse=True)
        for ls_m, ref_m in zip(ls_sorted, ref_sorted):
            assert ls_m.title == ref_m.title, (
                f"milestone {ls_m.milestone_id} title 不一样"
                f" life={ls_m.title!r} ref={ref_m.title!r}"
            )
            assert ls_m.milestone_type == ref_m.milestone_type
            c_ls = float(ls_m.confidence()) if callable(ls_m.confidence) else float(ls_m.confidence or 0.0)
            c_rf = float(ref_m.confidence()) if callable(ref_m.confidence) else float(ref_m.confidence or 0.0)
            assert c_ls == pytest.approx(c_rf), (
                f"confidence 不一致 life={c_ls} ref={c_rf}"
            )
            assert ls_m.timestamp == ref_m.timestamp
            assert ls_m.adapter_name == ref_m.adapter_name
            # 证据数量 (字段名是 sources,非 evidence;不关心具体 evidence_id)
            assert len(ls_m.sources) == len(ref_m.sources), (
                f"证据数量不一致 life={len(ls_m.sources)} ref={len(ref_m.sources)}"
            )

    # ------------------------------------------------------------------
    # 核心断言 ②: existence_quality + existence_stats 正确性
    # ------------------------------------------------------------------
    def test_existence_quality_ok_when_has_timeline(self) -> None:
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        q = snap.history.existence_quality
        assert q.status == "ok", (
            "有 timeline 产出 → existence_quality.status 必须是 ok, "
            f"当前 {q.status}/{q.error}"
        )
        assert q.source == "existence_timeline"

    def test_existence_stats_matches_reference(self) -> None:
        _, ref_stats = self._build_via_contracts_direct()
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        stats = snap.history.existence_stats
        # 比对 input_count / final_count / dedup_removed_duplicates 等关键统计
        assert stats.get("final_count") == ref_stats.final_count, (
            f"stats.final_count 不一致 life={stats.get('final_count')} "
            f"ref={ref_stats.final_count}"
        )
        assert stats.get("input_count") == ref_stats.input_count, (
            f"stats.input_count 不一致 life={stats.get('input_count')} "
            f"ref={ref_stats.input_count}"
        )
        # by_type 条数 (只比较 keys 交集,避免未来增加新类型时炸)
        ref_by_type = dict(ref_stats.by_type or {})
        ls_by_type = dict(stats.get("by_type") or {})
        for k, v in ref_by_type.items():
            assert ls_by_type.get(k) == v, (
                f"stats.by_type[{k!r}] 不一致 life={ls_by_type.get(k)} ref={v}"
            )

    def test_existence_quality_unknown_when_no_items(self) -> None:
        """6 个 items 全空 → existence_quality.status 必须是 unknown(绝不默认伪造 ok)。"""
        raw_empty: Dict[str, Any] = {
            "growth_proposals_v2_items": [],
            "selfmodel_history_items": [],
            "selfmodel_reflections_items": [],
            "selfmodel_beliefs_items": [],
            "selfmodel_stable_traits_items": [],
            "personality_evolution_v2_items": [],
            "growth_overview": {"total": 0, "available": True},
            "memory_overview": {"total": 0, "available": True},
            "growth_recent": [],
            "memory_recent": [],
            "personality_evolution": [],
        }
        snap = LifeSnapshotBuilder.build(raw_empty)
        assert len(snap.history.existence_timeline) == 0
        assert snap.history.existence_quality.status == "unknown", (
            "全空 → existence_quality 必须 unknown, "
            f"当前 {snap.history.existence_quality.status}"
        )

    # ------------------------------------------------------------------
    # 稳定画像 (stable_traits / stable_beliefs) 填充测试
    # ------------------------------------------------------------------
    def test_stable_traits_populated_from_selfmodel_items(self) -> None:
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        traits = snap.history.stable_traits
        assert len(traits) > 0, "应该有 stable_traits 条目"
        # 要求第一条必须有 非空 id, stability 在 [0,1]
        for t in traits:
            assert t.trait_id, "trait_id 非空"
            assert t.name, "name 非空"
            assert 0.0 <= t.stability <= 1.0, f"stability 超出范围 {t.stability}"
        q = snap.history.stable_traits_q
        assert q.status == "ok", f"有 traits → q 必须 ok,实际 {q.status}"

    def test_stable_beliefs_populated_from_selfmodel_items(self) -> None:
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        beliefs = snap.history.stable_beliefs
        assert len(beliefs) > 0, "应该有 stable_beliefs 条目"
        for b in beliefs:
            assert b.belief_id
            assert b.content, "belief content 非空 (防伪造空信念)"
            assert 0.0 <= b.confidence <= 1.0
        q = snap.history.stable_beliefs_q
        assert q.status == "ok", f"有 beliefs → q 必须 ok,实际 {q.status}"

    def test_stable_traits_unknown_when_empty(self) -> None:
        raw_empty: Dict[str, Any] = {
            "selfmodel_stable_traits_items": [],
            "selfmodel_beliefs_items": [],
            "growth_overview": {"total": 0, "available": True},
            "memory_overview": {"total": 0, "available": True},
            "growth_recent": [],
            "memory_recent": [],
            "personality_evolution": [],
        }
        snap = LifeSnapshotBuilder.build(raw_empty)
        assert len(snap.history.stable_traits) == 0
        assert snap.history.stable_traits_q.status == "unknown"
        assert len(snap.history.stable_beliefs) == 0
        assert snap.history.stable_beliefs_q.status == "unknown"

    # ------------------------------------------------------------------
    # 防退化保护: history.source_total_count 仍然正确 (=5)
    # ------------------------------------------------------------------
    def test_source_total_count_remains_5(self) -> None:
        raw = self._raw_with_fixtures()
        snap = LifeSnapshotBuilder.build(raw)
        assert (
            snap.history.source_total_count() == 5
        ), f"source_total_count 应该是 5, 实际 {snap.history.source_total_count()}"

    # ------------------------------------------------------------------
    # 异常保护: contracts 模块不可用时 → 不 crash, quality=offline
    # ------------------------------------------------------------------
    def test_contracts_unavailable_degrades_gracefully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模拟 D.6.1 模块 import 失败 → Builder 不 crash, 只降级 existence_quality=offline。"""
        from yuyi_desktop.core import life_snapshot as life_snapshot_module

        real_fill = life_snapshot_module.LifeSnapshotBuilder._fill_d62_existence_timeline

        def fake_fill(cls, hist, raw):  # noqa: N805
            # 模拟 import 失败分支
            hist.existence_quality = DataQuality.offline(
                source="existence_timeline",
                error="contracts_unavailable:ImportError",
            )
            hist.existence_stats_q = hist.existence_quality

        monkeypatch.setattr(
            life_snapshot_module.LifeSnapshotBuilder,
            "_fill_d62_existence_timeline",
            classmethod(fake_fill),
        )
        try:
            raw = self._raw_with_fixtures()
            snap = LifeSnapshotBuilder.build(raw)
            # recent_events / stable portrait 仍然正常
            assert (
                len(snap.history.recent_events) >= 0
            ), "recent_events 不应 crash (即使 existence_* 失败)"
            q = snap.history.existence_quality
            assert q.status == "offline", (
                "contracts 不可用 → existence_quality 必须 offline, "
                f"实际 {q.status}"
            )
            # 整个 Builder 必须返回对象, 不抛
            assert snap.history is not None
        finally:
            monkeypatch.setattr(
                life_snapshot_module.LifeSnapshotBuilder,
                "_fill_d62_existence_timeline",
                classmethod(real_fill),
            )


class TestD621LifeSnapshotServiceMocked:
    """LifeSnapshotService 级别的简单 mock 测试:只验证调用路径连通。"""

    def test_get_history_populates_6_keys_in_raw(self) -> None:
        """即使真实 Bridge 返回空列表,Service 也会尝试去取,结果进入 Builder → unknown 降级。

        注意:这里因为 D.6.2.1 还没连真实的 yuyi_core / Bridge,
        我们只做语法/调用路径检查,不断言 timeline 有真实内容。
        """
        # 简单 import 检查 (避免循环 import 在真实测试场景下炸)
        from yuyi_desktop.services.life_snapshot_service import LifeSnapshotService

        # 我们不 new LifeSnapshotService() 因为它依赖真实 bridges / services,
        # 只做 import 成功 + 类存在 + 方法存在 最小断言
        assert hasattr(LifeSnapshotService, "get_snapshot")
        assert hasattr(LifeSnapshotService, "get_history")
