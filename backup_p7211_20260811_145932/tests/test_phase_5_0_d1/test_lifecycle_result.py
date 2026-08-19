# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_result.py

Phase 5.0-D1 Step 1: 结果封装测试。

覆盖:
- 必填字段验证
- 字段自动规范化
- duration_ms 自动计算
- 5 个状态字段规则
- 5 个快捷构造
- to_dict / from_dict 序列化
- 视图方法
- repr
"""
import unittest

from src.runtime.lifecycle.lifecycle_errors import (
    ErrorCategory,
    LifecycleError,
)
from src.runtime.lifecycle.lifecycle_result import (
    LifecycleResult,
    LifecycleStatus,
)


class TestRequiredFields(unittest.TestCase):
    """必填字段。"""

    def test_minimal_construction(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=100.0,
            ended_at=101.0,
        )
        self.assertEqual(r.task_id, "t1")
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.started_at, 100.0)
        self.assertEqual(r.ended_at, 101.0)

    def test_default_empty_collections(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=0.0,
            ended_at=0.0,
        )
        self.assertEqual(r.produced_events, [])
        self.assertEqual(r.metrics, {})
        self.assertIsNone(r.error)
        self.assertIsNone(r.next_recommended_at)


class TestFieldNormalization(unittest.TestCase):
    """字段自动规范化。"""

    def test_task_id_to_string(self) -> None:
        r = LifecycleResult(
            task_id=123,  # noqa
            status=LifecycleStatus.SUCCESS,
            started_at=0.0,
            ended_at=0.0,
        )
        self.assertEqual(r.task_id, "123")

    def test_status_string_to_enum(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status="FAILED",  # type: ignore[arg-type]
            started_at=0.0,
            ended_at=0.0,
        )
        self.assertEqual(r.status, LifecycleStatus.FAILED)

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValueError):
            LifecycleResult(
                task_id="t1",
                status="FAKE",  # type: ignore[arg-type]
                started_at=0.0,
                ended_at=0.0,
            )

    def test_started_at_to_float(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=100,  # int
            ended_at=101,
        )
        self.assertEqual(r.started_at, 100.0)

    def test_produced_events_default_is_list(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=0.0,
            ended_at=0.0,
        )
        r.produced_events.append("evt_1")
        self.assertEqual(r.produced_events, ["evt_1"])


class TestDurationAutoCalculation(unittest.TestCase):
    """duration_ms 自动计算。"""

    def test_duration_zero_auto_calc(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=100.0,
            ended_at=100.5,
            duration_ms=0,  # 触发自动计算
        )
        self.assertEqual(r.duration_ms, 500)

    def test_duration_negative_becomes_zero(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=200.0,
            ended_at=100.0,  # 倒序
        )
        self.assertEqual(r.duration_ms, 0)

    def test_duration_explicit_preserved(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=0.0,
            ended_at=0.0,
            duration_ms=1234,
        )
        self.assertEqual(r.duration_ms, 1234)


class TestConvenienceConstructors(unittest.TestCase):
    """5 个快捷构造。"""

    def test_success(self) -> None:
        r = LifecycleResult.success(
            "t1",
            started_at=100.0,
            ended_at=101.0,
            metrics={"k": 1},
            events=["e1", "e2"],
            next_recommended_at=200.0,
        )
        self.assertEqual(r.status, LifecycleStatus.SUCCESS)
        self.assertEqual(r.metrics, {"k": 1})
        self.assertEqual(r.produced_events, ["e1", "e2"])
        self.assertEqual(r.next_recommended_at, 200.0)
        self.assertEqual(r.duration_ms, 1000)

    def test_skipped(self) -> None:
        r = LifecycleResult.skipped("t1", at=100.0, reason="no data")
        self.assertEqual(r.status, LifecycleStatus.SKIPPED)
        self.assertEqual(r.started_at, 100.0)
        self.assertEqual(r.ended_at, 100.0)
        self.assertEqual(r.metrics.get("skip_reason"), "no data")

    def test_skipped_no_reason(self) -> None:
        r = LifecycleResult.skipped("t1", at=100.0)
        self.assertEqual(r.status, LifecycleStatus.SKIPPED)

    def test_failed(self) -> None:
        err = LifecycleError.transient("io")
        r = LifecycleResult.failed(
            "t1",
            error=err,
            started_at=100.0,
            ended_at=101.0,
        )
        self.assertEqual(r.status, LifecycleStatus.FAILED)
        self.assertIs(r.error, err)
        self.assertEqual(r.duration_ms, 1000)

    def test_fatal(self) -> None:
        err = LifecycleError.internal("bug")
        r = LifecycleResult.fatal("t1", error=err, started_at=0.0, ended_at=0.0)
        self.assertEqual(r.status, LifecycleStatus.FATAL)
        self.assertIs(r.error, err)

    def test_timeout(self) -> None:
        r = LifecycleResult.timeout(
            "t1", started_at=0.0, ended_at=30.0, next_recommended_at=60.0
        )
        self.assertEqual(r.status, LifecycleStatus.TIMEOUT)
        self.assertEqual(r.next_recommended_at, 60.0)

    def test_cancelled(self) -> None:
        r = LifecycleResult.cancelled("t1", at=100.0)
        self.assertEqual(r.status, LifecycleStatus.CANCELLED)
        self.assertEqual(r.started_at, 100.0)


class TestSerialization(unittest.TestCase):
    """序列化。"""

    def test_to_dict_shape(self) -> None:
        r = LifecycleResult.success(
            "t1", started_at=100.0, ended_at=101.0, metrics={"k": 1}
        )
        d = r.to_dict()
        self.assertEqual(d["task_id"], "t1")
        self.assertEqual(d["status"], "SUCCESS")
        self.assertEqual(d["started_at"], 100.0)
        self.assertEqual(d["ended_at"], 101.0)
        self.assertIn("duration_ms", d)
        self.assertIn("produced_events", d)
        self.assertIn("metrics", d)
        self.assertIn("error", d)
        self.assertIn("next_recommended_at", d)
        self.assertEqual(d["metrics"], {"k": 1})

    def test_from_dict_roundtrip_success(self) -> None:
        r = LifecycleResult.success(
            "t1",
            started_at=100.0,
            ended_at=101.0,
            metrics={"a": 1},
            events=["e1"],
        )
        d = r.to_dict()
        r2 = LifecycleResult.from_dict(d)
        self.assertEqual(r2.task_id, r.task_id)
        self.assertEqual(r2.status, r.status)
        self.assertEqual(r2.started_at, r.started_at)
        self.assertEqual(r2.ended_at, r.ended_at)
        self.assertEqual(r2.metrics, r.metrics)
        self.assertEqual(r2.produced_events, r.produced_events)

    def test_from_dict_roundtrip_failed(self) -> None:
        err = LifecycleError.transient("io", retry_after=1.0)
        r = LifecycleResult.failed(
            "t1", error=err, started_at=100.0, ended_at=101.0
        )
        d = r.to_dict()
        r2 = LifecycleResult.from_dict(d)
        self.assertEqual(r2.status, LifecycleStatus.FAILED)
        self.assertIsNotNone(r2.error)
        self.assertEqual(r2.error.category, ErrorCategory.TRANSIENT)
        self.assertEqual(r2.error.retry_after, 1.0)

    def test_from_dict_minimal(self) -> None:
        r2 = LifecycleResult.from_dict({})
        self.assertEqual(r2.task_id, "")
        self.assertEqual(r2.status, LifecycleStatus.FAILED)


class TestViewMethods(unittest.TestCase):
    """视图方法。"""

    def test_is_terminal_success(self) -> None:
        r = LifecycleResult.success("t1", started_at=0.0, ended_at=0.0)
        self.assertTrue(r.is_terminal())

    def test_is_terminal_skipped(self) -> None:
        r = LifecycleResult.skipped("t1", at=0.0)
        self.assertTrue(r.is_terminal())

    def test_is_terminal_failed(self) -> None:
        err = LifecycleError.internal("x")
        r = LifecycleResult.fatal("t1", error=err, started_at=0.0, ended_at=0.0)
        self.assertTrue(r.is_terminal())

    def test_is_success(self) -> None:
        r = LifecycleResult.success("t1", started_at=0.0, ended_at=0.0)
        self.assertTrue(r.is_success())

    def test_is_failure_for_failed(self) -> None:
        err = LifecycleError.transient("x")
        r = LifecycleResult.failed("t1", error=err, started_at=0.0, ended_at=0.0)
        self.assertTrue(r.is_failure())

    def test_is_failure_for_fatal(self) -> None:
        err = LifecycleError.internal("x")
        r = LifecycleResult.fatal("t1", error=err, started_at=0.0, ended_at=0.0)
        self.assertTrue(r.is_failure())

    def test_is_failure_for_timeout(self) -> None:
        r = LifecycleResult.timeout("t1", started_at=0.0, ended_at=0.0)
        self.assertTrue(r.is_failure())

    def test_is_failure_false_for_success(self) -> None:
        r = LifecycleResult.success("t1", started_at=0.0, ended_at=0.0)
        self.assertFalse(r.is_failure())


class TestRepr(unittest.TestCase):
    """repr。"""

    def test_repr_basic(self) -> None:
        r = LifecycleResult.success("t1", started_at=0.0, ended_at=0.0)
        s = repr(r)
        self.assertIn("t1", s)
        self.assertIn("SUCCESS", s)

    def test_repr_failed_shows_error(self) -> None:
        err = LifecycleError.transient("io")
        r = LifecycleResult.failed("t1", error=err, started_at=0.0, ended_at=0.0)
        s = repr(r)
        self.assertIn("set", s)


class TestErrorConversion(unittest.TestCase):
    """error 字段自动转换。"""

    def test_non_lifecycle_error_converted(self) -> None:
        """传入普通 Exception 时应自动包装。"""
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.FAILED,
            started_at=0.0,
            ended_at=0.0,
            error=ValueError("bad"),  # type: ignore[arg-type]
        )
        self.assertIsInstance(r.error, LifecycleError)
        self.assertEqual(r.error.category, ErrorCategory.PERMANENT)

    def test_none_error_stays_none(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=0.0,
            ended_at=0.0,
            error=None,
        )
        self.assertIsNone(r.error)


class TestNextRecommendedAt(unittest.TestCase):
    """next_recommended_at 字段。"""

    def test_set_to_float(self) -> None:
        r = LifecycleResult.success(
            "t1", started_at=0.0, ended_at=0.0, next_recommended_at=200
        )
        self.assertEqual(r.next_recommended_at, 200.0)

    def test_invalid_string_ignored(self) -> None:
        r = LifecycleResult(
            task_id="t1",
            status=LifecycleStatus.SUCCESS,
            started_at=0.0,
            ended_at=0.0,
            next_recommended_at="not_a_number",  # type: ignore[arg-type]
        )
        self.assertIsNone(r.next_recommended_at)


if __name__ == "__main__":
    unittest.main()
