# -*- coding: utf-8 -*-
"""
Phase D.6.1.3 tests: 3 Adapters + Timeline Builder
====================================================

核心闸口:
T1. Proposal Adapter: status=applied → well_formed Milestone
T2. Proposal Adapter: status=proposed → 拒绝产出 (不流动=存在记录)
T3. SelfModel beliefs: 高 evidence_count + high confidence → Milestone
T4. SelfModel beliefs: min_evidence 不够 → 无 Milestone(防噪声)
T5. SelfModel stable_traits: stability >= 0.60 OR trend → Milestone
T6. SelfModel history:event_type 不在映射表 → 不产出(不"猜"类型)
T7. SelfModel reflections: 内容太短 OR confidence 低 → 不产出
T8. Evolution records: applied vs blocked 产出正确类型 + 证据链
T9. Timeline L1: 无证据 / 非法类型 → 全部被 L1 拒绝
T10. Timeline L2: confidence 低于阈值被过滤
T11. Timeline L3: time window cutoff 过滤过旧数据
T12. Timeline Dedup: 2 个同 (type, ts 1s, akeys) 记录 → 保留高 confidence
T13. Timeline Sort: desc = 最近在前
T14. Timeline 默认 order=desc + limit=50 生效
T15. 3 Adapter 结果合并到 Builder → 排序 去重 统计 by_type 正确
T16. Adapter 绝不调用 LLM(无 network/llm import 检测)
T17. Adapter 输出不填 why_created (留空给 D.8)
T18. Adapter 输出 source_type ∈ VALID_SOURCE_TYPES
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from src.contracts.existence import (
    MILESTONE_BELIEF,
    MILESTONE_GROWTH,
    MILESTONE_REFLECTION,
    MILESTONE_TRAIT_CHANGE,
    SOURCE_GROWTH_PROPOSAL,
    SOURCE_PERSONALITY_EVOLUTION,
    SOURCE_REFLECTION_INSIGHT,
    SOURCE_SELF_MODEL_BELIEF,
    SOURCE_SELF_MODEL_HISTORY,
    SOURCE_SELF_MODEL_TRAIT,
    VALID_SOURCE_TYPES,
    now_iso,
)
from src.contracts.existence_adapters import (
    EvolutionMilestoneAdapter,
    ProposalMilestoneAdapter,
    SelfModelMilestoneAdapter,
    evolution_to_milestones,
    proposals_to_milestones,
    selfmodel_to_milestones,
)
from src.contracts.existence_timeline import (
    ExistenceTimelineBuilder,
    TimelineBuildOptions,
    TimelineBuildStats,
    build_existence_timeline,
)


def _iso_z(days_ago: int = 0, hours: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago, hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ============================================================
# T1~T2: Proposal Adapter
# ============================================================

class TestProposalAdapter:
    def _make_proposal(
        self,
        *,
        status: str = "applied",
        prop_id: str = "pcr_test_001",
        confidence: float = 0.85,
        changes=None,
        accepted_at=None,
    ):
        return {
            "id": prop_id,
            "source_event_id": "evt_src_123",
            "proposed_changes": changes or [
                {"path": "traits.warmth", "before": 0.60, "after": 0.64, "reason": "positive interactions"},
            ],
            "confidence": confidence,
            "evidence_ids": ["mem_a", "evt_b"],
            "evaluator_meta": {"_source_schema": "v1.0"},
            "timestamp": _iso_z(days_ago=3),
            "status": status,
            "accepted_at": accepted_at or _iso_z(days_ago=2),
            "rejected_at": None,
            "schema_version": "1.0",
        }

    def test_t1_applied_proposal_yields_well_formed_milestone(self):
        p = self._make_proposal(status="applied", prop_id="pcr_t1")
        [m] = ProposalMilestoneAdapter().convert_many([p])
        assert m.is_well_formed() is True
        assert m.milestone_type == MILESTONE_GROWTH
        # 时间戳优先 accepted_at
        assert m.timestamp == p["accepted_at"]
        # 证据链: proposal 本身 + 2 evidence_ids
        assert len(m.sources) == 3
        # 证据 source_id 对应真实对象
        real_source_ids = {s.source_id for s in m.sources if s.has_real_source()}
        assert "pcr_t1" in real_source_ids
        assert "mem_a" in real_source_ids
        # 受影响 keys = path
        assert "traits.warmth" in m.affected_keys
        # 影响范围 = personality(因为 trait)
        assert m.impact.personality is True
        # why_created 保持空(D.8 填充)
        assert m.explanation.why_created == ""

    def test_t2_proposed_status_skipped_not_in_timeline(self):
        """proposal 在 proposed 阶段仍在流动,不算存在记录。"""
        p = self._make_proposal(status="proposed")
        out = ProposalMilestoneAdapter().convert_many([p])
        assert out == []

    def test_t2b_rejected_cancelled_expired_yield_milestone(self):
        for st in ("rejected", "cancelled", "expired"):
            p = self._make_proposal(status=st)
            out = ProposalMilestoneAdapter().convert_many([p])
            assert len(out) == 1, f"status={st} 没产出 Milestone"
            m = out[0]
            # rejected 没有 accepted_at,回落到 timestamp
            assert m.milestone_type == MILESTONE_GROWTH

    def test_all_sources_have_valid_type(self):
        """T18:任何 Evidence 的 source_type 必须 ∈ VALID_SOURCE_TYPES。"""
        p = self._make_proposal()
        [m] = ProposalMilestoneAdapter().convert_many([p])
        for s in m.sources:
            assert s.source_type in VALID_SOURCE_TYPES


# ============================================================
# T3~T7: SelfModel Adapter
# ============================================================

class TestSelfModelAdapter:
    def _belief_item(self, bid, conf=0.75, ev_cnt=5, active=True, ts=None):
        return {
            "belief_id": bid,
            "domain": "core",
            "content": "理解比回答更重要,用户真正感到被接纳时回应质量会显著提升",
            "confidence": conf,
            "version": 2,
            "evidence_count": ev_cnt,
            "sources": [{"source_type": "growth_record", "source_id": "p1"}],
            "active": active,
            "first_seen": _iso_z(30),
            "last_confirmed": ts or _iso_z(2),
        }

    def _stable_trait_item(self, tid, name, stab=0.75, val_pct=72, evid=12, trend=0.08):
        return {
            "trait_id": tid,
            "name": name,
            "value_pct": val_pct,
            "stability": stab,
            "evidence_count": evid,
            "first_observed": _iso_z(60),
            "last_observed": _iso_z(1),
            "trend_30d": trend,
        }

    def test_t3_belief_high_conf_high_evidence_yields_milestone(self):
        env = {"available": True, "total": 1, "items": [self._belief_item("b_t3")]}
        out = SelfModelMilestoneAdapter().convert_beliefs(env)
        assert len(out) == 1
        m = out[0]
        assert m.is_well_formed() is True
        assert m.milestone_type == MILESTONE_BELIEF
        assert m.sources[0].source_type == SOURCE_SELF_MODEL_BELIEF
        assert m.impact.belief is True

    def test_t4_belief_noisy_not_yielded(self):
        """T4:min evidence 不够 → 防噪声。"""
        # 只有 1 条证据 = 偶然现象,不算存在记录
        weak = self._belief_item("b_t4", ev_cnt=1, conf=0.5)
        env = {"items": [weak]}
        out = SelfModelMilestoneAdapter().convert_beliefs(env)
        assert out == []
        # inactive 也不产出
        inactive = self._belief_item("b_t4b", active=False)
        env2 = {"items": [inactive]}
        out2 = SelfModelMilestoneAdapter().convert_beliefs(env2)
        assert out2 == []

    def test_t5_stable_trait_high_stability_or_trend_yields(self):
        """T5:stability ≥ 0.60 OR 有趋势。"""
        stab_ok = self._stable_trait_item("t_ok", "warmth", stab=0.70, trend=None)
        trend_ok = self._stable_trait_item("t_tr", "curiosity", stab=0.50, trend=0.10)
        both_low = self._stable_trait_item("t_lo", "coldness", stab=0.40, trend=0.01)
        env = {"items": [stab_ok, trend_ok, both_low]}
        out = SelfModelMilestoneAdapter().convert_stable_traits(env)
        out_ids = [m.affected_keys[0] for m in out]
        assert "t_ok" in out_ids
        assert "t_tr" in out_ids
        assert "t_lo" not in out_ids
        for m in out:
            assert m.milestone_type == MILESTONE_TRAIT_CHANGE
            assert m.sources[0].source_type == SOURCE_SELF_MODEL_TRAIT

    def test_t6_history_unknown_event_type_skipped(self):
        """T6:不猜 milestone 类型。不在映射表 → 不产出。"""
        env = {
            "items": [
                # event_type ∈ 映射表 → 产出
                {"event_id": "h_ok", "event_type": "trait_change",
                 "timestamp": _iso_z(1), "summary": "warmth changed",
                 "source_type": "selfmodel_history", "source_id": "h_ok",
                 "affected_traits": {"warmth": 0.02}, "affected_beliefs": []},
                # event_type 不在映射表 → 跳过(不猜)
                {"event_id": "h_unk", "event_type": "mysterious_glow",
                 "timestamp": _iso_z(1), "summary": "something weird happened",
                 "source_type": "unknown", "source_id": "h_unk"},
            ]
        }
        out = SelfModelMilestoneAdapter().convert_history(env)
        assert len(out) == 1
        assert out[0].milestone_type == MILESTONE_TRAIT_CHANGE

    def test_t7_reflections_filter_short_or_low_confidence(self):
        env = {
            "items": [
                # 合格:conf 高 + 长内容
                {"note_id": "r_good", "timestamp": _iso_z(3),
                 "trigger_source": "chat", "reflection_type": "growth",
                 "content": "最近的多次对话中,我发现用户在谈及家庭时更容易流露脆弱,"
                            "这时候单纯的问答式回应会让对话更冷。或许应该先承认情绪存在,"
                            "再回应具体问题会更好。这可能与 relationship 维度的建立有关。",
                 "confidence": 0.78,
                 "related_belief_ids": ["b_core_001"],
                 "related_trait_changes": {"warmth": +0.03},
                 "sources": [{"source_type": "growth_proposal", "source_id": "pcr_01"}]},
                # 不合格:conf 太低
                {"note_id": "r_lowc", "timestamp": _iso_z(3),
                 "content": "a" * 50, "confidence": 0.30},
                # 不合格:内容太短
                {"note_id": "r_short", "timestamp": _iso_z(3),
                 "content": "I'm fine.", "confidence": 0.90},
            ]
        }
        out = SelfModelMilestoneAdapter().convert_reflections(env)
        assert len(out) == 1
        m = out[0]
        assert m.milestone_type == MILESTONE_REFLECTION
        assert m.sources[0].source_type == SOURCE_REFLECTION_INSIGHT
        # explanation 证据数 ≥ 2 (reflection 本身 + 附加 growth proposal)
        assert m.explanation.evidence_count >= 2


# ============================================================
# T8: Evolution Adapter
# ============================================================

class TestEvolutionAdapter:
    def test_t8_evolution_applied_blocked_records(self):
        records = [
            # applied:有 trait diff
            {
                "record_id": "per_applied_01",
                "timestamp": _iso_z(2),
                "proposal_id": "pcr_aa",
                "status": "applied",
                "trait_states_before": {"warmth": 0.60, "curiosity": 0.50},
                "trait_states_after": {"warmth": 0.64, "curiosity": 0.50},
                "evolution_record": {"confidence": 0.88},
            },
            # blocked:identity protection
            {
                "record_id": "per_blocked_01",
                "timestamp": _iso_z(1),
                "proposal_id": "pcr_bb",
                "status": "blocked",
                "reason": "identity anchor mismatch, core trait change too aggressive",
                "trait_states_before": {"core_trust": 0.75},
                "trait_states_after": {"core_trust": 0.40},
            },
        ]
        out = EvolutionMilestoneAdapter().convert_evolution_records(records)
        assert len(out) == 2
        applied = [m for m in out if "applied" in m.title][0]
        blocked = [m for m in out if "blocked" in m.title][0]
        # applied: personality 有变化
        assert applied.milestone_type == MILESTONE_TRAIT_CHANGE
        assert applied.impact.personality is True
        # blocked: identity (保护锚点)
        assert blocked.impact.identity is True
        # applied 带 2 条证据:evolution + proposal
        sids = {s.source_id for s in applied.sources if s.has_real_source()}
        assert "per_applied_01" in sids
        assert "pcr_aa" in sids
        assert applied.sources[0].source_type == SOURCE_PERSONALITY_EVOLUTION


# ============================================================
# T9~T14: Timeline Builder (L1/L2/L3/Dedup/Sort/Limit)
# ============================================================

class TestTimelineBuilder:
    def _make_good_milestone(
        self,
        *,
        days_ago: int = 1,
        conf: float = 0.8,
        mtype: str = MILESTONE_GROWTH,
        akeys=None,
        adapter: str = "test",
    ):
        from src.contracts.existence import (
            EvidenceReference, ExistenceMilestone, ExplanationMetadata, ImpactMap,
        )
        return ExistenceMilestone(
            timestamp=_iso_z(days_ago),
            milestone_type=mtype,
            title=f"Event {days_ago}d ago",
            summary="summary text",
            sources=[EvidenceReference(
                source_type=SOURCE_GROWTH_PROPOSAL,
                source_id=f"p_{days_ago}_{mtype}_{akeys}",
                confidence=conf,
            )],
            impact=ImpactMap(personality=True),
            explanation=ExplanationMetadata(evidence_count=1, confidence=conf),
            affected_keys=list(akeys) if akeys else ["k1"],
            adapter_name=adapter,
        )

    def test_t9_layer1_not_well_formed_all_rejected(self):
        from src.contracts.existence import ExistenceMilestone
        bad = [ExistenceMilestone() for _ in range(5)]
        tl, stats = ExistenceTimelineBuilder.build(
            ExistenceTimelineBuilder(bad),
            TimelineBuildOptions(min_confidence=0.0, window_days=None),
        ) if False else (
            lambda b, o: b.build(o)
        )(ExistenceTimelineBuilder(bad), TimelineBuildOptions(min_confidence=0.0, window_days=None))
        assert tl == []
        assert stats.layer1_rejected_well_formed == 5
        assert stats.input_count == 5

    def test_t9b_simple_add_and_build_well_formed_only(self):
        b = ExistenceTimelineBuilder()
        b.add([self._make_good_milestone(days_ago=1, conf=0.7)])
        b.add([self._make_good_milestone(days_ago=0, conf=0.9, mtype=MILESTONE_BELIEF, akeys=["bb1"])])
        tl, stats = b.build(TimelineBuildOptions(min_confidence=0.0, window_days=None))
        assert stats.final_count == 2
        # 排序 desc:days_ago=0 在前
        assert tl[0].timestamp > tl[1].timestamp

    def test_t10_layer2_confidence_filter(self):
        b = ExistenceTimelineBuilder()
        b.add([
            self._make_good_milestone(days_ago=1, conf=0.8),        # PASS
            self._make_good_milestone(days_ago=2, conf=0.10),       # FAIL (DEFAULT 0.20 阈值)
            self._make_good_milestone(days_ago=3, conf=0.25),       # PASS
        ])
        tl, stats = b.build()  # 默认 min_confidence = 0.20
        assert tl.__len__() == 2
        assert stats.layer2_rejected_confidence == 1

    def test_t11_layer3_time_window(self):
        b = ExistenceTimelineBuilder()
        b.add([
            self._make_good_milestone(days_ago=5),      # PASS (默认 365d 内)
            self._make_good_milestone(days_ago=400),    # FAIL (400 > 365)
        ])
        tl, stats = b.build()
        assert len(tl) == 1
        assert stats.layer3_rejected_window == 1

    def test_t12_dedup_prefers_higher_confidence(self):
        b = ExistenceTimelineBuilder()
        low = self._make_good_milestone(days_ago=1, conf=0.5, akeys=["a", "b"], adapter="X")
        high = self._make_good_milestone(days_ago=1, conf=0.9, akeys=["a", "b"], adapter="X")
        # 1s bucket 相同:日期一致
        low.timestamp = "2026-08-01T12:00:01Z"
        high.timestamp = "2026-08-01T12:00:01.500Z"
        b.add([low, high])
        tl, stats = b.build(TimelineBuildOptions(min_confidence=0.0, window_days=None))
        assert len(tl) == 1
        assert stats.dedup_removed_duplicates == 1
        assert tl[0].confidence() == pytest.approx(0.9)

    def test_t13_timeline_sort_desc_latest_first(self):
        b = ExistenceTimelineBuilder()
        # 乱序添加
        b.add([
            self._make_good_milestone(days_ago=30, mtype=MILESTONE_BELIEF, akeys=["k1"]),
            self._make_good_milestone(days_ago=1,  mtype=MILESTONE_TRAIT_CHANGE, akeys=["k2"]),
            self._make_good_milestone(days_ago=10, mtype=MILESTONE_GROWTH, akeys=["k3"]),
        ])
        tl, _ = b.build(TimelineBuildOptions(min_confidence=0.0, window_days=None, limit=1000))
        timestamps = [m.timestamp for m in tl]
        # DESC:从最新到最旧
        assert timestamps == sorted(timestamps, reverse=True)

    def test_t14_limit_truncates_result(self):
        b = ExistenceTimelineBuilder()
        for i in range(100):
            b.add_one(self._make_good_milestone(days_ago=i, akeys=[f"k{i}"]))
        tl, stats = b.build(TimelineBuildOptions(min_confidence=0.0, window_days=None, limit=10))
        assert len(tl) == 10
        assert stats.final_count == 10


# ============================================================
# T15: 3 Adapter + Build 合并
# ============================================================

class TestEndToEndPipeline:
    def test_t15_three_sources_merged_sorted_deduped(self):
        # 1. Proposals: 2 applied
        p1 = {"id": "pcr_e2e_1", "status": "applied", "confidence": 0.8,
              "proposed_changes": [{"path": "traits.warmth", "before": 0.6, "after": 0.62}],
              "evidence_ids": [], "evaluator_meta": {},
              "timestamp": _iso_z(10), "accepted_at": _iso_z(9),
              "rejected_at": None, "source_event_id": "", "schema_version": "1.0"}
        p2 = {"id": "pcr_e2e_2", "status": "applied", "confidence": 0.7,
              "proposed_changes": [{"path": "belief.core_01", "before": None, "after": "v2"}],
              "evidence_ids": [], "evaluator_meta": {},
              "timestamp": _iso_z(5), "accepted_at": _iso_z(5),
              "rejected_at": None, "source_event_id": "", "schema_version": "1.0"}
        pm = proposals_to_milestones([p1, p2])
        assert len(pm) == 2

        # 2. SelfModel: belief + stable trait
        beliefs_env = {"items": [{
            "belief_id": "b_e2e_01", "domain": "core",
            "content": "帮助用户感到被理解比给出正确答案更重要(反复被验证)" * 2,
            "confidence": 0.8, "version": 3, "evidence_count": 6,
            "sources": [], "active": True,
            "first_seen": _iso_z(40), "last_confirmed": _iso_z(4),
        }]}
        traits_env = {"items": [{
            "trait_id": "warmth", "name": "warmth", "value_pct": 72,
            "stability": 0.72, "evidence_count": 18,
            "first_observed": _iso_z(50), "last_observed": _iso_z(3),
            "trend_30d": 0.06,
        }]}
        sm = selfmodel_to_milestones(
            beliefs_envelope=beliefs_env,
            stable_traits_envelope=traits_env,
        )
        assert len(sm) >= 2

        # 3. Evolution: 1 applied record
        evo_records = [{
            "record_id": "per_e2e_01", "timestamp": _iso_z(8),
            "proposal_id": "pcr_e2e_1", "status": "applied",
            "trait_states_before": {"warmth": 0.60},
            "trait_states_after": {"warmth": 0.62},
            "evolution_record": {"confidence": 0.8},
        }]
        em = evolution_to_milestones(evo_records)
        assert len(em) == 1

        # 合并
        timeline, stats = build_existence_timeline(
            pm, sm, em,
            options=TimelineBuildOptions(
                min_confidence=0.0,
                window_days=365,
                order="desc",
                limit=200,
            ),
        )
        # 至少 5 条(2 proposal + 2 selfmodel + 1 evolution)
        assert stats.final_count >= 5
        # by_type 包含 growth + trait_change + belief
        assert MILESTONE_GROWTH in stats.by_type
        assert MILESTONE_TRAIT_CHANGE in stats.by_type
        # 排序 desc:第一个是最近的
        ts_list = [m.timestamp for m in timeline]
        assert ts_list == sorted(ts_list, reverse=True)


# ============================================================
# T16/T17:架构边界检查(不调用 LLM,why_created 空)
# ============================================================

class TestArchitecturalBoundaries:
    def test_t16_no_llm_or_network_imports_in_adapters(self):
        """T16:Adapter 代码绝不 should not import openai / langchain / requests。"""
        import inspect
        from src.contracts import existence_adapters, existence_timeline
        forbidden_tokens = (
            "openai", "langchain", "anthropic", "requests", "httpx",
            "urllib", "chatbot", "llm_caller", "generative",
        )
        sources = "\n".join([
            inspect.getsource(existence_adapters),
            inspect.getsource(existence_timeline),
        ])
        lowered = sources.lower()
        for tok in forbidden_tokens:
            # import 语句中出现 → FAIL
            import_lines = [ln for ln in lowered.splitlines()
                            if ln.lstrip().startswith(("import ", "from "))]
            for ln in import_lines:
                assert tok not in ln, (
                    f"Forbidden import token '{tok}' found in Adapter/Timeline: {ln!r}"
                )

    def test_t17_why_created_always_empty_in_d61(self):
        """T17:why_created 留给 D.8,D.6.x 任何 Adapter/Timeline 不填。"""
        # 构造 3 个 adapter 的最小合格输入
        proposals = [
            {"id": "p_wh", "status": "applied", "confidence": 0.7,
             "proposed_changes": [{"path": "t.c"}],
             "evidence_ids": [], "evaluator_meta": {},
             "timestamp": _iso_z(1), "accepted_at": _iso_z(1),
             "rejected_at": None, "source_event_id": "", "schema_version": "1.0"}
        ]
        beliefs = {"items": [{
            "belief_id": "b_wh", "domain": "core",
            "content": ("A" * 40),
            "confidence": 0.7, "evidence_count": 4,
            "active": True, "sources": [],
            "first_seen": _iso_z(10), "last_confirmed": _iso_z(1),
        }]}
        evo = [{
            "record_id": "r_wh", "timestamp": _iso_z(1),
            "status": "applied", "proposal_id": "p_wh",
            "trait_states_before": {"x": 1}, "trait_states_after": {"x": 2},
        }]
        all_ms = (
            proposals_to_milestones(proposals)
            + selfmodel_to_milestones(beliefs_envelope=beliefs)
            + evolution_to_milestones(evo)
        )
        for m in all_ms:
            assert m.explanation.why_created == "", (
                f"adapter 填了 why_created = {m.explanation.why_created!r}"
                f",adapter_name={m.adapter_name}"
            )
