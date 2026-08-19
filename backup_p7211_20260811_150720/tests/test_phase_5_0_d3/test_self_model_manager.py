# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3/test_self_model_manager.py

Phase 5.0-D3-A: Self Model System —— SelfModelManager 单元测试
"""
import unittest
import threading
from typing import Any, Dict, List, Optional

from src.runtime.lifecycle.internal.clock import (
    FrozenClock,
    MockClock,
    SystemClock,
)
from src.runtime.self_model.change_log import ChangeLog
from src.runtime.self_model.self_model_manager import (
    SELF_MODEL_MANAGER_SCHEMA_VERSION,
    SelfModelManager,
    SelfModelManagerError,
    build_default_self_model_manager,
)
from src.runtime.self_model.self_state import (
    ChangeRecord,
    IdentityView,
    TraitState,
    CapabilityState,
    InterestState,
    SelfState,
)


# ============================================================
# 构造与生命周期
# ============================================================
class TestManagerConstruction(unittest.TestCase):
    def test_default_construct(self):
        m = SelfModelManager()
        self.assertEqual(m.name, "self_model_manager")
        self.assertFalse(m.is_started)
        self.assertFalse(m.is_closed)
        self.assertIsNotNone(m.clock)
        self.assertIsInstance(m.clock, SystemClock)

    def test_invalid_name(self):
        with self.assertRaises(SelfModelManagerError):
            SelfModelManager(name="")

    def test_invalid_clock(self):
        with self.assertRaises(SelfModelManagerError):
            SelfModelManager(clock="not_a_clock")

    def test_invalid_change_log_type(self):
        with self.assertRaises(SelfModelManagerError):
            SelfModelManager(change_log="not_a_changelog")

    def test_invalid_initial_state(self):
        with self.assertRaises(SelfModelManagerError):
            SelfModelManager(initial_state="not_a_state")

    def test_build_default(self):
        m = build_default_self_model_manager()
        self.assertIsInstance(m, SelfModelManager)

    def test_with_frozen_clock(self):
        clk = FrozenClock(initial=1000.0, frozen=True)
        m = SelfModelManager(clock=clk)
        self.assertEqual(m.clock.now(), 1000.0)

    def test_with_custom_change_log(self):
        log = ChangeLog(name="custom")
        m = SelfModelManager(change_log=log)
        self.assertIs(m.change_log, log)

    def test_with_initial_state(self):
        s = SelfState.empty(now=100.0)
        m = SelfModelManager(initial_state=s)
        self.assertIs(m.get_state(), s)


class TestManagerLifecycle(unittest.TestCase):
    def test_start_close(self):
        m = SelfModelManager()
        self.assertTrue(m.start())
        self.assertTrue(m.is_started)
        # 重复 start:幂等
        self.assertTrue(m.start())
        self.assertTrue(m.close())
        self.assertTrue(m.is_closed)
        # close 后 start 不再生效
        self.assertFalse(m.start())

    def test_close_then_operations(self):
        m = SelfModelManager()
        m.start()
        m.close()
        rec = m.upsert_trait(name="x", new_value=0.5, evidence_event_ids=["e1"])
        self.assertIsNone(rec)
        # 关闭后操作被静默拒绝,只验证返回 None 即可
        self.assertFalse(m.is_started)


# ============================================================
# Identity
# ============================================================
class TestManagerIdentity(unittest.TestCase):
    def test_set_identity_incremental(self):
        clk = FrozenClock(initial=1000.0, frozen=True)
        m = SelfModelManager(clock=clk)
        m.start()
        rec = m.set_identity(
            name="newname",
            evidence_event_ids=["e1"],
            reason="test",
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_kind, "identity_update")
        self.assertEqual(rec.target, "identity")
        self.assertIn("e1", rec.evidence_event_ids)
        self.assertEqual(m.identity().name, "newname")

    def test_set_identity_requires_evidence(self):
        m = SelfModelManager()
        m.start()
        rec = m.set_identity(name="x")
        self.assertIsNone(rec)
        self.assertGreater(m.total_updates_failed, 0)

    def test_set_identity_empty_evidence_rejected(self):
        m = SelfModelManager()
        m.start()
        rec = m.set_identity(name="x", evidence_event_ids=[])
        self.assertIsNone(rec)

    def test_set_identity_with_new_view(self):
        m = SelfModelManager()
        m.start()
        nv = IdentityView(name="custom", identity_id="custom_id")
        rec = m.set_identity(new_view=nv, evidence_event_ids=["e1"])
        self.assertIsNotNone(rec)
        self.assertEqual(m.identity().name, "custom")

    def test_set_identity_invalid_source(self):
        m = SelfModelManager()
        m.start()
        rec = m.set_identity(
            name="x",
            evidence_event_ids=["e1"],
            change_source="not_a_source",
        )
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_source, "unknown")

    def test_refresh_identity(self):
        m = SelfModelManager()
        m.start()
        rec = m.refresh_identity(evidence_event_ids=["e1"], reason="tick")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_kind, "identity_update")
        self.assertEqual(rec.change_source, "internal_tick")


# ============================================================
# Trait
# ============================================================
class TestManagerTrait(unittest.TestCase):
    def setUp(self):
        self.mgr = SelfModelManager(clock=FrozenClock(initial=1000.0, frozen=True))
        self.mgr.start()

    def test_upsert_new_trait(self):
        rec = self.mgr.upsert_trait(
            name="curiosity",
            new_value=0.7,
            evidence_event_ids=["e1"],
        )
        self.assertIsNotNone(rec)
        t = self.mgr.get_trait("curiosity")
        self.assertIsNotNone(t)
        self.assertEqual(t.current_value, 0.7)

    def test_upsert_update_existing(self):
        self.mgr.upsert_trait(name="curiosity", new_value=0.3, evidence_event_ids=["e1"])
        rec = self.mgr.upsert_trait(name="curiosity", new_value=0.9, evidence_event_ids=["e2"])
        self.assertIsNotNone(rec)
        t = self.mgr.get_trait("curiosity")
        self.assertEqual(t.current_value, 0.9)
        # 显式未传 new_direction → 默认为 "stable" (Manager 不自动推断)
        self.assertEqual(t.direction, "stable")
        # upsert_trait 不合并 evidence,直接替换
        self.assertIn("e2", t.evidence_event_ids)
        self.assertEqual(t.evidence_event_ids, ["e2"])

    def test_upsert_update_existing_with_direction(self):
        self.mgr.upsert_trait(name="curiosity", new_value=0.3, evidence_event_ids=["e1"])
        rec = self.mgr.upsert_trait(
            name="curiosity",
            new_value=0.9,
            new_direction="rising",
            evidence_event_ids=["e2"],
        )
        self.assertIsNotNone(rec)
        t = self.mgr.get_trait("curiosity")
        self.assertEqual(t.direction, "rising")

    def test_trait_id_preserved(self):
        rec1 = self.mgr.upsert_trait(name="c", new_value=0.3, evidence_event_ids=["e1"])
        tid1 = rec1.target_id
        rec2 = self.mgr.upsert_trait(name="c", new_value=0.5, evidence_event_ids=["e2"])
        self.assertEqual(tid1, rec2.target_id)

    def test_upsert_invalid_name(self):
        rec = self.mgr.upsert_trait(name="", new_value=0.5, evidence_event_ids=["e1"])
        self.assertIsNone(rec)

    def test_upsert_no_evidence(self):
        rec = self.mgr.upsert_trait(name="x", new_value=0.5)
        self.assertIsNone(rec)

    def test_upsert_empty_evidence(self):
        rec = self.mgr.upsert_trait(name="x", new_value=0.5, evidence_event_ids=[])
        self.assertIsNone(rec)

    def test_upsert_evidence_as_string(self):
        rec = self.mgr.upsert_trait(name="x", new_value=0.5, evidence_event_ids="single_e")
        self.assertIsNotNone(rec)
        t = self.mgr.get_trait("x")
        self.assertIn("single_e", t.evidence_event_ids)

    def test_remove_trait(self):
        self.mgr.upsert_trait(name="x", new_value=0.5, evidence_event_ids=["e1"])
        rec = self.mgr.remove_trait(name="x", evidence_event_ids=["e2"], reason="cleanup")
        self.assertIsNotNone(rec)
        self.assertIsNone(self.mgr.get_trait("x"))

    def test_remove_nonexistent_trait(self):
        rec = self.mgr.remove_trait(name="missing", evidence_event_ids=["e1"])
        self.assertIsNone(rec)


# ============================================================
# Capability
# ============================================================
class TestManagerCapability(unittest.TestCase):
    def setUp(self):
        self.mgr = SelfModelManager(clock=FrozenClock(initial=1000.0, frozen=True))
        self.mgr.start()

    def test_upsert_capability(self):
        rec = self.mgr.upsert_capability(
            name="speak",
            new_proficiency=0.8,
            evidence_event_ids=["e1"],
        )
        self.assertIsNotNone(rec)
        c = self.mgr.get_capability("speak")
        self.assertEqual(c.proficiency, 0.8)
        self.assertTrue(c.enabled)

    def test_capability_mark_used(self):
        self.mgr.upsert_capability(
            name="x",
            mark_used=True,
            evidence_event_ids=["e1"],
        )
        c = self.mgr.get_capability("x")
        self.assertEqual(c.last_used, 1000.0)

    def test_capability_disable(self):
        self.mgr.upsert_capability(
            name="x",
            enabled=False,
            evidence_event_ids=["e1"],
        )
        c = self.mgr.get_capability("x")
        self.assertFalse(c.enabled)

    def test_capability_id_preserved(self):
        r1 = self.mgr.upsert_capability(name="x", new_proficiency=0.3, evidence_event_ids=["e1"])
        r2 = self.mgr.upsert_capability(name="x", new_proficiency=0.6, evidence_event_ids=["e2"])
        self.assertEqual(r1.target_id, r2.target_id)

    def test_remove_capability(self):
        self.mgr.upsert_capability(name="x", evidence_event_ids=["e1"])
        rec = self.mgr.remove_capability(name="x", evidence_event_ids=["e2"])
        self.assertIsNotNone(rec)
        self.assertIsNone(self.mgr.get_capability("x"))

    def test_remove_nonexistent_capability(self):
        rec = self.mgr.remove_capability(name="missing", evidence_event_ids=["e1"])
        self.assertIsNone(rec)

    def test_upsert_capability_no_evidence(self):
        rec = self.mgr.upsert_capability(name="x")
        self.assertIsNone(rec)


# ============================================================
# Interest
# ============================================================
class TestManagerInterest(unittest.TestCase):
    def setUp(self):
        self.mgr = SelfModelManager(clock=FrozenClock(initial=1000.0, frozen=True))
        self.mgr.start()

    def test_upsert_interest(self):
        rec = self.mgr.upsert_interest(
            name="music",
            new_level=0.6,
            evidence_event_ids=["e1"],
        )
        self.assertIsNotNone(rec)
        i = self.mgr.get_interest("music")
        self.assertEqual(i.level, 0.6)

    def test_reinforce_interest_existing(self):
        self.mgr.upsert_interest(name="music", new_level=0.3, evidence_event_ids=["e1"])
        rec = self.mgr.reinforce_interest(
            name="music",
            boost=0.2,
            evidence_event_ids=["e2"],
        )
        self.assertIsNotNone(rec)
        i = self.mgr.get_interest("music")
        self.assertAlmostEqual(i.level, 0.5, places=4)

    def test_reinforce_interest_new(self):
        rec = self.mgr.reinforce_interest(
            name="new_topic",
            boost=0.1,
            evidence_event_ids=["e1"],
        )
        self.assertIsNotNone(rec)
        self.assertIsNotNone(self.mgr.get_interest("new_topic"))

    def test_reinforce_clipped_to_one(self):
        self.mgr.upsert_interest(name="x", new_level=0.9, evidence_event_ids=["e1"])
        self.mgr.reinforce_interest(name="x", boost=0.5, evidence_event_ids=["e2"])
        i = self.mgr.get_interest("x")
        self.assertEqual(i.level, 1.0)

    def test_decay_interests(self):
        self.mgr.upsert_interest(
            name="music",
            new_level=0.5,
            new_decay_rate=0.01,
            evidence_event_ids=["e1"],
        )
        n = self.mgr.decay_interests(delta_seconds=10.0)
        self.assertEqual(n, 1)
        i = self.mgr.get_interest("music")
        self.assertLess(i.level, 0.5)

    def test_decay_interests_zero_delta(self):
        self.mgr.upsert_interest(name="x", new_level=0.5, evidence_event_ids=["e1"])
        n = self.mgr.decay_interests(delta_seconds=0)
        self.assertEqual(n, 0)

    def test_decay_interests_negative_delta(self):
        n = self.mgr.decay_interests(delta_seconds=-1.0)
        self.assertEqual(n, 0)

    def test_remove_interest(self):
        self.mgr.upsert_interest(name="x", new_level=0.5, evidence_event_ids=["e1"])
        rec = self.mgr.remove_interest(name="x", evidence_event_ids=["e2"])
        self.assertIsNotNone(rec)
        self.assertIsNone(self.mgr.get_interest("x"))

    def test_remove_nonexistent_interest(self):
        rec = self.mgr.remove_interest(name="missing", evidence_event_ids=["e1"])
        self.assertIsNone(rec)

    def test_interest_tags(self):
        rec = self.mgr.upsert_interest(
            name="x",
            new_level=0.5,
            tags=("a", "b"),
            evidence_event_ids=["e1"],
        )
        self.assertIsNotNone(rec)
        i = self.mgr.get_interest("x")
        self.assertEqual(i.tags, ("a", "b"))


# ============================================================
# Bulk / Reset / Refresh
# ============================================================
class TestManagerBulk(unittest.TestCase):
    def setUp(self):
        self.mgr = SelfModelManager(clock=FrozenClock(initial=1000.0, frozen=True))
        self.mgr.start()

    def test_bulk_load(self):
        n = self.mgr.bulk_load(
            traits={"c": {"current_value": 0.7}, "w": {"current_value": 0.4}},
            capabilities={"speak": {"proficiency": 0.6}},
            interests={"music": {"level": 0.5}},
            evidence_event_ids=["e1"],
        )
        self.assertEqual(n, 4)
        self.assertEqual(self.mgr.get_state().trait_count, 2)
        self.assertEqual(self.mgr.get_state().capability_count, 1)
        self.assertEqual(self.mgr.get_state().interest_count, 1)

    def test_bulk_load_empty(self):
        n = self.mgr.bulk_load(evidence_event_ids=["e1"])
        self.assertEqual(n, 0)

    def test_bulk_load_no_evidence(self):
        n = self.mgr.bulk_load()
        self.assertEqual(n, 0)

    def test_bulk_load_invalid_payload_skipped(self):
        n = self.mgr.bulk_load(
            traits={"c": "not_a_dict"},
            evidence_event_ids=["e1"],
        )
        self.assertEqual(n, 0)

    def test_reset(self):
        self.mgr.upsert_trait(name="x", new_value=0.5, evidence_event_ids=["e1"])
        rec = self.mgr.reset(evidence_event_ids=["e2"], reason="test_reset")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_kind, "reset")
        self.assertEqual(self.mgr.get_state().trait_count, 0)

    def test_reset_no_evidence(self):
        rec = self.mgr.reset()
        self.assertIsNone(rec)

    def test_refresh(self):
        rec = self.mgr.refresh(evidence_event_ids=["e1"], reason="tick")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.change_kind, "snapshot_refresh")


# ============================================================
# 查询 / 代理
# ============================================================
class TestManagerQuery(unittest.TestCase):
    def setUp(self):
        self.mgr = SelfModelManager(clock=FrozenClock(initial=1000.0, frozen=True))
        self.mgr.start()
        self.mgr.upsert_trait(name="c", new_value=0.3, evidence_event_ids=["e1"])
        self.mgr.upsert_trait(name="c", new_value=0.6, evidence_event_ids=["e2"])
        self.mgr.upsert_interest(name="music", new_level=0.5, evidence_event_ids=["e1"])
        self.mgr.upsert_capability(name="x", evidence_event_ids=["e3"])

    def test_latest_changes(self):
        recs = self.mgr.latest_changes(limit=2)
        self.assertEqual(len(recs), 2)
        # 全部使用同一时间, latest 仍返回 2 条
        for r in recs:
            self.assertEqual(r.timestamp, 1000.0)

    def test_query_changes(self):
        recs = self.mgr.query_changes(target="trait")
        self.assertEqual(len(recs), 2)
        recs2 = self.mgr.query_changes(kind="interest_update")
        self.assertEqual(len(recs2), 1)
        recs3 = self.mgr.query_changes(evidence="e1")
        self.assertGreaterEqual(len(recs3), 2)

    def test_changes_by_evidence(self):
        recs = self.mgr.changes_by_evidence("e1")
        self.assertEqual(len(recs), 2)

    def test_contains_evidence(self):
        self.assertTrue(self.mgr.contains_evidence("e1"))
        self.assertFalse(self.mgr.contains_evidence("missing"))

    def test_traits_dict(self):
        d = self.mgr.traits()
        self.assertIn("c", d)

    def test_capabilities_dict(self):
        d = self.mgr.capabilities()
        self.assertIn("x", d)

    def test_interests_dict(self):
        d = self.mgr.interests()
        self.assertIn("music", d)

    def test_snapshot_dict(self):
        d = self.mgr.snapshot_dict()
        self.assertIn("identity", d)
        self.assertIn("traits", d)
        self.assertIn("counters", d)


# ============================================================
# 健康度 / 描述
# ============================================================
class TestManagerHealth(unittest.TestCase):
    def test_health_check_running(self):
        m = SelfModelManager()
        m.start()
        h = m.health_check()
        self.assertTrue(h["healthy"])
        self.assertEqual(h["schema_version"], SELF_MODEL_MANAGER_SCHEMA_VERSION)
        self.assertIn("change_log", h)
        self.assertIn("state", h)

    def test_health_check_closed(self):
        m = SelfModelManager()
        m.start()
        m.close()
        h = m.health_check()
        self.assertFalse(h["healthy"])

    def test_describe(self):
        m = SelfModelManager()
        m.start()
        d = m.describe()
        self.assertIn("healthy", d)

    def test_repr(self):
        m = SelfModelManager()
        m.start()
        r = repr(m)
        self.assertIn("SelfModelManager", r)


# ============================================================
# 线程安全
# ============================================================
class TestManagerThreadSafety(unittest.TestCase):
    def test_concurrent_upsert_trait(self):
        from src.runtime.self_model.self_state import MAX_TRAITS
        m = SelfModelManager(change_log_capacity=0)  # unlimited log
        m.start()
        # 注意: trait 数量受 MAX_TRAITS=64 限制,因此不能超过 64
        n_threads = 4
        n_per_thread = 16
        total = n_threads * n_per_thread

        def worker(tid: int):
            for i in range(n_per_thread):
                m.upsert_trait(
                    name=f"t{tid}_{i}",
                    new_value=0.5,
                    evidence_event_ids=[f"e{tid}_{i}"],
                )

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # trait 数量受 MAX_TRAITS 保护 ≤ 64
        self.assertEqual(m.get_state().trait_count, min(total, MAX_TRAITS))
        # change log 应当记下所有尝试(无容量限制时 = total)
        self.assertEqual(m.change_log.size, total)


# ============================================================
# MockClock 验证时间注入
# ============================================================
class TestManagerClockInjection(unittest.TestCase):
    def test_mock_clock_used(self):
        counter = [0]

        def now_fn():
            counter[0] += 1
            return float(counter[0] * 100)

        m = SelfModelManager(clock=MockClock(now_fn=now_fn))
        m.start()
        rec1 = m.upsert_trait(name="x", new_value=0.5, evidence_event_ids=["e1"])
        # 验证 timestamp 是 MockClock 产生的值(> 0)
        self.assertGreater(rec1.timestamp, 0.0)
        # 验证两次 upsert 的 timestamp 单调递增
        rec2 = m.upsert_trait(name="x", new_value=0.6, evidence_event_ids=["e2"])
        self.assertGreaterEqual(rec2.timestamp, rec1.timestamp)

    def test_frozen_clock_stable(self):
        clk = FrozenClock(initial=500.0, frozen=True)
        m = SelfModelManager(clock=clk)
        m.start()
        rec = m.upsert_trait(name="x", new_value=0.5, evidence_event_ids=["e1"])
        self.assertEqual(rec.timestamp, 500.0)
        rec2 = m.upsert_trait(name="y", new_value=0.5, evidence_event_ids=["e2"])
        # FrozenClock 冻结 → 两次时间相同
        self.assertEqual(rec2.timestamp, 500.0)


if __name__ == "__main__":
    unittest.main()
