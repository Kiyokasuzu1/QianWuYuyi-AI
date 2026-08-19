# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_decision.py

Phase 5.0-D1 Step 2: 决策模型测试。

覆盖:
- Decision 数据类
- TaskState
- Always / Never
- AfterInterval
- AfterFirstRun
- SnapshotBootCount
- And / Or / Not
- Predicate
- DecisionEngine
- 异常隔离
- 可重现性
"""
import unittest

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_decision import (
    AfterFirstRun,
    AfterInterval,
    Always,
    And,
    Decision,
    DecisionEngine,
    LifecycleCondition,
    Never,
    Not,
    Or,
    Predicate,
    SnapshotBootCount,
    TaskState,
    Verdict,
)


# ============================================================
# 辅助
# ============================================================
def make_ctx(
    *,
    clock: FrozenClock = None,
    snapshot: dict = None,
    task_id: str = "t1",
):
    if clock is None:
        clock = FrozenClock(initial=0.0)
    return LifecycleContext(
        clock=clock,
        snapshot_view=snapshot or {},
        task_id=task_id,
    )


# ============================================================
# Decision 数据类
# ============================================================
class TestDecision(unittest.TestCase):
    """Decision 数据类。"""

    def test_run(self) -> None:
        d = Decision.run("ok")
        self.assertEqual(d.verdict, Verdict.RUN)
        self.assertEqual(d.reason, "ok")

    def test_skip(self) -> None:
        d = Decision.skip("nope")
        self.assertEqual(d.verdict, Verdict.SKIP)

    def test_defer_requires_until(self) -> None:
        """DEFER 可不带 defer_until(由调用方保证)。"""
        d = Decision(verdict=Verdict.DEFER)
        self.assertTrue(d.is_defer)
        self.assertIsNone(d.defer_until)

    def test_defer(self) -> None:
        d = Decision.defer(until=100.0, reason="wait")
        self.assertEqual(d.verdict, Verdict.DEFER)
        self.assertEqual(d.defer_until, 100.0)

    def test_is_run_skip_defer(self) -> None:
        r = Decision.run()
        s = Decision.skip()
        df = Decision.defer(until=1.0)
        self.assertTrue(r.is_run)
        self.assertFalse(r.is_skip)
        self.assertFalse(r.is_defer)
        self.assertTrue(s.is_skip)
        self.assertTrue(df.is_defer)

    def test_confidence_clamped(self) -> None:
        d1 = Decision(verdict=Verdict.RUN, confidence=1.5)
        d2 = Decision(verdict=Verdict.RUN, confidence=-0.5)
        self.assertEqual(d1.confidence, 1.0)
        self.assertEqual(d2.confidence, 0.0)

    def test_immutable(self) -> None:
        d = Decision.run()
        with self.assertRaises(Exception):
            d.verdict = Verdict.SKIP  # type: ignore[misc]

    def test_to_dict(self) -> None:
        d = Decision.defer(until=5.0, reason="x")
        out = d.to_dict()
        self.assertEqual(out["verdict"], "DEFER")
        self.assertEqual(out["defer_until"], 5.0)


# ============================================================
# TaskState
# ============================================================
class TestTaskState(unittest.TestCase):
    """TaskState。"""

    def test_default(self) -> None:
        s = TaskState(task_id="t1")
        self.assertEqual(s.task_id, "t1")
        self.assertIsNone(s.last_run_at)
        self.assertEqual(s.run_count, 0)

    def test_counters(self) -> None:
        s = TaskState(task_id="t1", run_count=5, failure_count=2, skip_count=1)
        self.assertEqual(s.run_count, 5)

    def test_to_dict(self) -> None:
        s = TaskState(task_id="t1", run_count=3)
        d = s.to_dict()
        self.assertIn("task_id", d)
        self.assertEqual(d["run_count"], 3)


# ============================================================
# Always / Never
# ============================================================
class TestAlways(unittest.TestCase):
    def test_always_runs(self) -> None:
        d = Always().should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_run)

    def test_custom_reason(self) -> None:
        d = Always("custom").should_run(make_ctx(), TaskState())
        self.assertEqual(d.reason, "custom")


class TestNever(unittest.TestCase):
    def test_never_skips(self) -> None:
        d = Never().should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)


# ============================================================
# AfterInterval
# ============================================================
class TestAfterInterval(unittest.TestCase):
    def test_first_run(self) -> None:
        clock = FrozenClock(initial=100.0)
        ctx = make_ctx(clock=clock)
        state = TaskState(last_run_at=None)
        d = AfterInterval(60).should_run(ctx, state)
        self.assertTrue(d.is_run)

    def test_within_interval(self) -> None:
        clock = FrozenClock(initial=100.0)
        ctx = make_ctx(clock=clock)
        state = TaskState(last_run_at=90.0)  # 10s ago,interval=60
        d = AfterInterval(60).should_run(ctx, state)
        self.assertTrue(d.is_skip)

    def test_after_interval(self) -> None:
        clock = FrozenClock(initial=100.0)
        ctx = make_ctx(clock=clock)
        state = TaskState(last_run_at=30.0)  # 70s ago
        d = AfterInterval(60).should_run(ctx, state)
        self.assertTrue(d.is_run)

    def test_exact_boundary(self) -> None:
        clock = FrozenClock(initial=100.0)
        ctx = make_ctx(clock=clock)
        state = TaskState(last_run_at=40.0)  # 60s ago, exactly interval
        d = AfterInterval(60).should_run(ctx, state)
        self.assertTrue(d.is_run)

    def test_zero_interval(self) -> None:
        clock = FrozenClock(initial=100.0)
        ctx = make_ctx(clock=clock)
        state = TaskState(last_run_at=99.0)  # 1s ago
        d = AfterInterval(0).should_run(ctx, state)
        self.assertTrue(d.is_run)

    def test_reproducible(self) -> None:
        """同 Context + TaskState → 同 Decision。"""
        clock = FrozenClock(initial=100.0)
        ctx = make_ctx(clock=clock, snapshot={"boot_count": 2})
        state = TaskState(task_id="t1", last_run_at=50.0, run_count=3)
        cond = AfterInterval(60)
        results = [cond.should_run(ctx, state) for _ in range(10)]
        first = results[0]
        for r in results[1:]:
            self.assertEqual(r.verdict, first.verdict)
            self.assertEqual(r.reason, first.reason)


# ============================================================
# AfterFirstRun
# ============================================================
class TestAfterFirstRun(unittest.TestCase):
    def test_skip_if_not_run(self) -> None:
        ctx = make_ctx()
        state = TaskState(last_run_at=None)
        d = AfterFirstRun().should_run(ctx, state)
        self.assertTrue(d.is_skip)

    def test_run_if_run(self) -> None:
        ctx = make_ctx()
        state = TaskState(last_run_at=10.0)
        d = AfterFirstRun().should_run(ctx, state)
        self.assertTrue(d.is_run)

    def test_with_inner(self) -> None:
        ctx = make_ctx()
        state = TaskState(last_run_at=10.0)
        # last_run_at 存在 → 进入 inner.should_run
        # AfterInterval(60) + now=0.0 → elapsed=-10 → skip
        d = AfterFirstRun(AfterInterval(60)).should_run(ctx, state)
        self.assertTrue(d.is_skip)


# ============================================================
# SnapshotBootCount
# ============================================================
class TestSnapshotBootCount(unittest.TestCase):
    def test_min_pass(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 3})
        d = SnapshotBootCount(min=2).should_run(ctx, TaskState())
        self.assertTrue(d.is_run)

    def test_min_fail(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 1})
        d = SnapshotBootCount(min=2).should_run(ctx, TaskState())
        self.assertTrue(d.is_skip)

    def test_max_pass(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 3})
        d = SnapshotBootCount(max=5).should_run(ctx, TaskState())
        self.assertTrue(d.is_run)

    def test_max_fail(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 10})
        d = SnapshotBootCount(max=5).should_run(ctx, TaskState())
        self.assertTrue(d.is_skip)

    def test_range(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 3})
        d = SnapshotBootCount(min=2, max=5).should_run(ctx, TaskState())
        self.assertTrue(d.is_run)

    def test_no_constraint(self) -> None:
        """无 min/max 时永远 RUN。"""
        ctx = make_ctx(snapshot={"boot_count": 1000})
        d = SnapshotBootCount().should_run(ctx, TaskState())
        self.assertTrue(d.is_run)

    def test_missing_boot_count_defaults_zero(self) -> None:
        ctx = make_ctx(snapshot={})
        d = SnapshotBootCount(min=1).should_run(ctx, TaskState())
        self.assertTrue(d.is_skip)


# ============================================================
# And / Or / Not
# ============================================================
class TestAnd(unittest.TestCase):
    def test_all_run(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 5})
        d = And(Always(), SnapshotBootCount(min=2)).should_run(ctx, TaskState())
        self.assertTrue(d.is_run)

    def test_one_skip(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 1})
        d = And(Always(), SnapshotBootCount(min=2)).should_run(ctx, TaskState())
        self.assertTrue(d.is_skip)

    def test_empty(self) -> None:
        d = And().should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_run)

    def test_confidence_takes_min(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 1})
        d = And(
            Always("a"),  # confidence=1.0
            Always("b"),  # confidence=1.0
        ).should_run(ctx, TaskState())
        # 两个 Always 都是 1.0,min=1.0
        self.assertEqual(d.confidence, 1.0)

    def test_confidence_takes_min_with_predicate(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 1})
        d = And(
            Predicate(lambda c, s: Decision.run("a", confidence=0.9)),
            Predicate(lambda c, s: Decision.run("b", confidence=0.7)),
        ).should_run(ctx, TaskState())
        self.assertEqual(d.confidence, 0.7)

    def test_child_exception_becomes_skip(self) -> None:
        class Bad:
            def should_run(self, ctx, state):
                raise RuntimeError("boom")

        ctx = make_ctx()
        d = And(Always(), Bad()).should_run(ctx, TaskState())  # type: ignore[arg-type]
        self.assertTrue(d.is_skip)


class TestOr(unittest.TestCase):
    def test_any_run(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 1})
        d = Or(SnapshotBootCount(min=10), Always()).should_run(ctx, TaskState())
        self.assertTrue(d.is_run)

    def test_all_skip(self) -> None:
        ctx = make_ctx(snapshot={"boot_count": 1})
        d = Or(SnapshotBootCount(min=10), SnapshotBootCount(min=20)).should_run(
            ctx, TaskState()
        )
        self.assertTrue(d.is_skip)

    def test_empty(self) -> None:
        d = Or().should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)


class TestNot(unittest.TestCase):
    def test_run_to_skip(self) -> None:
        d = Not(Always()).should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)

    def test_skip_to_run(self) -> None:
        d = Not(Never()).should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_run)

    def test_defer_passes_through(self) -> None:
        d = Not(Decision.defer(until=5.0)).should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_defer)

    def test_inner_exception(self) -> None:
        class Bad:
            def should_run(self, ctx, state):
                raise RuntimeError("boom")

        d = Not(Bad()).should_run(make_ctx(), TaskState())  # type: ignore[arg-type]
        self.assertTrue(d.is_skip)


# ============================================================
# Predicate
# ============================================================
class TestPredicate(unittest.TestCase):
    def test_run(self) -> None:
        p = Predicate(lambda ctx, state: Decision.run("custom"))
        d = p.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_run)
        self.assertEqual(d.reason, "custom")

    def test_exception_becomes_skip(self) -> None:
        p = Predicate(lambda ctx, state: (_ for _ in ()).throw(RuntimeError("bad")))
        d = p.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)
        self.assertIn("bad", d.reason)

    def test_non_decision_becomes_skip(self) -> None:
        p = Predicate(lambda ctx, state: "not a decision")
        d = p.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)


# ============================================================
# DecisionEngine
# ============================================================
class TestDecisionEngine(unittest.TestCase):
    def test_evaluate_normal(self) -> None:
        engine = DecisionEngine()
        d = engine.evaluate(Always(), make_ctx(), TaskState())
        self.assertTrue(d.is_run)

    def test_evaluate_exception(self) -> None:
        class Bad:
            def should_run(self, ctx, state):
                raise RuntimeError("boom")

        engine = DecisionEngine()
        d = engine.evaluate(Bad(), make_ctx(), TaskState())  # type: ignore[arg-type]
        self.assertTrue(d.is_skip)

    def test_evaluate_invalid_object(self) -> None:
        engine = DecisionEngine()
        d = engine.evaluate("not_a_condition", make_ctx(), TaskState())  # type: ignore[arg-type]
        self.assertTrue(d.is_skip)

    def test_evaluate_invalid_decision(self) -> None:
        class BadReturn:
            def should_run(self, ctx, state):
                return "not_a_decision"

        engine = DecisionEngine()
        d = engine.evaluate(BadReturn(), make_ctx(), TaskState())  # type: ignore[arg-type]
        self.assertTrue(d.is_skip)


# ============================================================
# 复合 + 可重现性
# ============================================================
class TestCompositeReproducibility(unittest.TestCase):
    """复合条件可重现性。"""

    def test_complex_reproducible(self) -> None:
        cond = And(
            SnapshotBootCount(min=2),
            AfterInterval(60),
        )
        clock = FrozenClock(initial=1000.0)
        ctx = make_ctx(clock=clock, snapshot={"boot_count": 3})
        state = TaskState(task_id="t1", last_run_at=900.0, run_count=5)
        results = [cond.should_run(ctx, state) for _ in range(100)]
        first = results[0]
        for r in results[1:]:
            self.assertEqual(r.verdict, first.verdict)
            self.assertEqual(r.reason, first.reason)
            self.assertEqual(r.confidence, first.confidence)


# ============================================================
# Decision 边界
# ============================================================
class TestDecisionEdge(unittest.TestCase):
    def test_invalid_verdict_raises(self) -> None:
        with self.assertRaises(ValueError):
            Decision(verdict="FAKE")  # type: ignore[arg-type]

    def test_string_verdict_normalized(self) -> None:
        d = Decision(verdict="RUN")  # type: ignore[arg-type]
        self.assertEqual(d.verdict, Verdict.RUN)

    def test_invalid_defer_until_ignored(self) -> None:
        d = Decision(verdict=Verdict.DEFER, defer_until="not_a_number")  # type: ignore[arg-type]
        self.assertIsNone(d.defer_until)


if __name__ == "__main__":
    unittest.main()
