# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_task.py

Phase 5.0-D1 Step 2: LifecycleTask 测试。

覆盖:
- task_id / owner 校验
- priority 边界
- 默认 condition = Always
- 决策委托
- execute() 异常包装
- 返回值规范化 (LifecycleResult / dict / None / 其他)
- 钩子 on_success / on_failure
- context 取消
- budget 异常
- 钩子自身异常隔离
- SimpleTask 工厂
- 不可变性 (task_id 等)
"""
import unittest

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_decision import (
    Always,
    Decision,
    Never,
    TaskState,
)
from src.runtime.lifecycle.lifecycle_errors import (
    ErrorCategory,
    LifecycleError,
)
from src.runtime.lifecycle.lifecycle_result import (
    LifecycleResult,
    LifecycleStatus,
)
from src.runtime.lifecycle.lifecycle_task import (
    BaseLifecycleTask,
    DEFAULT_PRIORITY,
    MAX_PRIORITY,
    MIN_PRIORITY,
    SimpleTask,
)


# ============================================================
# 辅助
# ============================================================
def make_ctx(*, cancelled: bool = False) -> LifecycleContext:
    c = LifecycleContext(clock=FrozenClock(initial=100.0), task_id="t1")
    if cancelled:
        c.cancel()
    return c


class _CountingTask(BaseLifecycleTask):
    def __init__(self, **kwargs):
        super().__init__(task_id=kwargs.pop("task_id", "t.count"),
                         owner=kwargs.pop("owner", "test"),
                         **kwargs)
        self.run_count = 0
        self.success_count = 0
        self.failure_count = 0

    def execute(self, context):
        self.run_count += 1
        return None


class _FailTask(BaseLifecycleTask):
    def __init__(self, exc, **kwargs):
        super().__init__(task_id=kwargs.pop("task_id", "t.fail"),
                         owner=kwargs.pop("owner", "test"),
                         **kwargs)
        self._exc = exc

    def execute(self, context):
        raise self._exc


# ============================================================
# 构造与字段校验
# ============================================================
class TestConstruction(unittest.TestCase):
    def test_minimal(self) -> None:
        t = BaseLifecycleTask(task_id="t.demo", owner="test")
        self.assertEqual(t.task_id, "t.demo")
        self.assertEqual(t.owner, "test")

    def test_default_display_name(self) -> None:
        t = BaseLifecycleTask(task_id="t.demo", owner="test")
        self.assertEqual(t.display_name, "t.demo")

    def test_custom_display_name(self) -> None:
        t = BaseLifecycleTask(
            task_id="t.demo", owner="test", display_name="Demo Task"
        )
        self.assertEqual(t.display_name, "Demo Task")

    def test_default_priority(self) -> None:
        t = BaseLifecycleTask(task_id="t.demo", owner="test")
        self.assertEqual(t.priority, DEFAULT_PRIORITY)
        self.assertEqual(t.priority, 5)

    def test_invalid_task_id_empty(self) -> None:
        with self.assertRaises(ValueError):
            BaseLifecycleTask(task_id="", owner="test")

    def test_invalid_task_id_type(self) -> None:
        with self.assertRaises(ValueError):
            BaseLifecycleTask(task_id=123, owner="test")  # type: ignore[arg-type]

    def test_invalid_task_id_chars(self) -> None:
        with self.assertRaises(ValueError):
            BaseLifecycleTask(task_id="t demo", owner="test")

    def test_invalid_owner_empty(self) -> None:
        with self.assertRaises(ValueError):
            BaseLifecycleTask(task_id="t1", owner="")

    def test_invalid_owner_chars(self) -> None:
        with self.assertRaises(ValueError):
            BaseLifecycleTask(task_id="t1", owner="bad/owner")


class TestPriorityBounds(unittest.TestCase):
    def test_priority_clamped_high(self) -> None:
        t = BaseLifecycleTask(task_id="t1", owner="test", priority=100)
        self.assertEqual(t.priority, MAX_PRIORITY)

    def test_priority_clamped_low(self) -> None:
        t = BaseLifecycleTask(task_id="t1", owner="test", priority=0)
        self.assertEqual(t.priority, MIN_PRIORITY)

    def test_priority_normal(self) -> None:
        t = BaseLifecycleTask(task_id="t1", owner="test", priority=7)
        self.assertEqual(t.priority, 7)


class TestOptionalFields(unittest.TestCase):
    def test_default_condition_is_always(self) -> None:
        t = BaseLifecycleTask(task_id="t1", owner="test")
        d = t.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_run)

    def test_custom_condition(self) -> None:
        t = BaseLifecycleTask(
            task_id="t1", owner="test", condition=Never()
        )
        d = t.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)

    def test_interval_seconds(self) -> None:
        t = BaseLifecycleTask(
            task_id="t1", owner="test", interval_seconds=60.0
        )
        self.assertEqual(t.interval_seconds, 60.0)

    def test_budget_ms(self) -> None:
        t = BaseLifecycleTask(
            task_id="t1", owner="test", budget_ms=500
        )
        self.assertEqual(t.budget_ms, 500)

    def test_budget_zero_becomes_none(self) -> None:
        t = BaseLifecycleTask(
            task_id="t1", owner="test", budget_ms=0
        )
        self.assertIsNone(t.budget_ms)

    def test_max_concurrent(self) -> None:
        t = BaseLifecycleTask(
            task_id="t1", owner="test", max_concurrent=3
        )
        self.assertEqual(t.max_concurrent, 3)

    def test_max_concurrent_clamped(self) -> None:
        t = BaseLifecycleTask(
            task_id="t1", owner="test", max_concurrent=0
        )
        self.assertEqual(t.max_concurrent, 1)


# ============================================================
# 决策
# ============================================================
class TestShouldRun(unittest.TestCase):
    def test_default_runs(self) -> None:
        t = BaseLifecycleTask(task_id="t1", owner="test")
        d = t.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_run)

    def test_condition_failure_returns_skip(self) -> None:
        class Bad:
            def should_run(self, ctx, state):
                raise RuntimeError("boom")

        t = BaseLifecycleTask(task_id="t1", owner="test", condition=Bad())  # type: ignore[arg-type]
        d = t.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)
        self.assertIn("condition_failed", d.reason)

    def test_condition_invalid_return(self) -> None:
        class BadReturn:
            def should_run(self, ctx, state):
                return "not a decision"

        t = BaseLifecycleTask(task_id="t1", owner="test", condition=BadReturn())  # type: ignore[arg-type]
        d = t.should_run(make_ctx(), TaskState())
        self.assertTrue(d.is_skip)


# ============================================================
# execute 异常包装
# ============================================================
class TestExceptionWrapping(unittest.TestCase):
    def test_value_error_becomes_permanent(self) -> None:
        t = _FailTask(ValueError("bad"), task_id="t.fail", owner="test")
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.FAILED)
        self.assertIsNotNone(r.error)
        self.assertEqual(r.error.category, ErrorCategory.PERMANENT)

    def test_runtime_error_becomes_internal(self) -> None:
        t = _FailTask(RuntimeError("bug"), task_id="t.fail", owner="test")
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.FAILED)
        self.assertEqual(r.error.category, ErrorCategory.INTERNAL)

    def test_io_error_becomes_transient(self) -> None:
        t = _FailTask(IOError("io"), task_id="t.fail", owner="test")
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.FAILED)
        self.assertEqual(r.error.category, ErrorCategory.TRANSIENT)

    def test_lifecycle_error_passthrough(self) -> None:
        t = _FailTask(
            LifecycleError.transient("retry later"),
            task_id="t.fail", owner="test",
        )
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.FAILED)
        self.assertEqual(r.error.category, ErrorCategory.TRANSIENT)

    def test_lifecycle_error_timeout(self) -> None:
        t = _FailTask(
            LifecycleError.timeout("slow"),
            task_id="t.fail", owner="test",
        )
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.TIMEOUT)


# ============================================================
# 返回值规范化
# ============================================================
class TestReturnValueNormalization(unittest.TestCase):
    def test_return_none_success(self) -> None:
        t = _CountingTask(task_id="t.cnt", owner="test")
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.task_id, "t.cnt")

    def test_return_lifecycle_result_preserved(self) -> None:
        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.lr", owner="test")

            def execute(self, ctx):
                return LifecycleResult.success(
                    "t.lr",
                    started_at=ctx.now(),
                    ended_at=ctx.now() + 1,
                    metrics={"k": 1},
                )

        r = T().run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.metrics, {"k": 1})

    def test_return_dict_normalized(self) -> None:
        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.d", owner="test")

            def execute(self, ctx):
                return {"metrics": {"a": 1}, "events": ["e1"]}

        r = T().run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.metrics, {"a": 1})
        self.assertEqual(r.produced_events, ["e1"])

    def test_return_arbitrary_value(self) -> None:
        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.x", owner="test")

            def execute(self, ctx):
                return 42

        r = T().run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.metrics.get("return_type"), "int")


# ============================================================
# 钩子
# ============================================================
class TestHooks(unittest.TestCase):
    def test_on_success_called(self) -> None:
        received: list = []

        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.hook", owner="test")

            def execute(self, ctx):
                return {"metrics": {"k": 1}}

            def on_success(self, result):
                received.append(result)

        T().run_safely(make_ctx())
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].metrics, {"k": 1})

    def test_on_failure_called(self) -> None:
        received: list = []

        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.hook", owner="test")

            def execute(self, ctx):
                raise ValueError("x")

            def on_failure(self, error):
                received.append(error)

        T().run_safely(make_ctx())
        self.assertEqual(len(received), 1)

    def test_on_success_exception_isolated(self) -> None:
        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.hook", owner="test")

            def execute(self, ctx):
                return None

            def on_success(self, result):
                raise RuntimeError("hook bad")

        r = T().run_safely(make_ctx())
        # 仍然 SUCCESS(钩子异常被隔离)
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)

    def test_on_failure_exception_isolated(self) -> None:
        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(task_id="t.hook", owner="test")

            def execute(self, ctx):
                raise ValueError("x")

            def on_failure(self, error):
                raise RuntimeError("hook bad")

        r = T().run_safely(make_ctx())
        # 仍然 FAILED(钩子异常被隔离)
        self.assertEqual(r.status, LifecycleStatus.FAILED)


# ============================================================
# 取消
# ============================================================
class TestCancellation(unittest.TestCase):
    def test_cancelled_context_returns_cancelled(self) -> None:
        t = _CountingTask(task_id="t.c", owner="test")
        r = t.run_safely(make_ctx(cancelled=True))
        self.assertEqual(r.status, LifecycleStatus.CANCELLED)
        self.assertEqual(t.run_count, 0)  # execute 未执行


# ============================================================
# Budget
# ============================================================
class TestBudget(unittest.TestCase):
    def test_budget_one_normal_runs(self) -> None:
        """D1 阶段: budget 仅作占位,实际不强制超时。

        budget=1ms 仍正常执行完成。
        """

        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(
                    task_id="t.b", owner="test", budget_ms=1
                )

            def execute(self, ctx):
                return None

        r = T().run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)

    def test_budget_zero_treated_as_unlimited(self) -> None:
        """budget_ms=0 在构造时等价于 None(无限)。"""

        class T(BaseLifecycleTask):
            def __init__(self):
                super().__init__(
                    task_id="t.b", owner="test", budget_ms=0
                )

            def execute(self, ctx):
                return None

        t = T()
        self.assertIsNone(t.budget_ms)
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)


# ============================================================
# SimpleTask
# ============================================================
class TestSimpleTask(unittest.TestCase):
    def test_construction(self) -> None:
        t = SimpleTask(
            task_id="t.simple",
            owner="test",
            action=lambda ctx: {"metrics": {"k": 1}},
        )
        self.assertEqual(t.task_id, "t.simple")

    def test_execute(self) -> None:
        t = SimpleTask(
            task_id="t.simple",
            owner="test",
            action=lambda ctx: {"metrics": {"k": 1}},
        )
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.metrics, {"k": 1})

    def test_non_callable_action_raises(self) -> None:
        with self.assertRaises(ValueError):
            SimpleTask(
                task_id="t.simple", owner="test", action="not callable"  # type: ignore[arg-type]
            )

    def test_action_exception_wrapped(self) -> None:
        t = SimpleTask(
            task_id="t.simple",
            owner="test",
            action=lambda ctx: (_ for _ in ()).throw(ValueError("x")),
        )
        r = t.run_safely(make_ctx())
        self.assertEqual(r.status, LifecycleStatus.FAILED)


# ============================================================
# 比较 / 哈希 / repr
# ============================================================
class TestEquality(unittest.TestCase):
    def test_equal_same_task_id(self) -> None:
        t1 = BaseLifecycleTask(task_id="t1", owner="a")
        t2 = BaseLifecycleTask(task_id="t1", owner="b")
        self.assertEqual(t1, t2)

    def test_not_equal_different_task_id(self) -> None:
        t1 = BaseLifecycleTask(task_id="t1", owner="a")
        t2 = BaseLifecycleTask(task_id="t2", owner="a")
        self.assertNotEqual(t1, t2)

    def test_hash_consistent(self) -> None:
        t1 = BaseLifecycleTask(task_id="t1", owner="a")
        t2 = BaseLifecycleTask(task_id="t1", owner="a")
        self.assertEqual(hash(t1), hash(t2))

    def test_repr_contains_task_id(self) -> None:
        t = BaseLifecycleTask(task_id="t.demo", owner="test")
        s = repr(t)
        self.assertIn("t.demo", s)


# ============================================================
# NotImplemented
# ============================================================
class TestNotImplemented(unittest.TestCase):
    def test_execute_not_implemented(self) -> None:
        t = BaseLifecycleTask(task_id="t.x", owner="test")
        with self.assertRaises(NotImplementedError):
            t.execute(make_ctx())


if __name__ == "__main__":
    unittest.main()
