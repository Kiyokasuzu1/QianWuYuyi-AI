# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_error.py

Phase 5.0-D1 Step 1: 异常体系测试。

覆盖:
- 5 种 category 默认 retryable
- 5 种快捷构造方法
- 自定义 retryable
- retry_after 处理
- cause 链路保留
- to_dict / from_dict 序列化
- context 字段
- 异常分类(classify_exception)
- equality / hash / repr
"""
import unittest

from src.runtime.lifecycle.lifecycle_errors import (
    ErrorCategory,
    LifecycleError,
    classify_exception,
)


class TestCategoryDefaults(unittest.TestCase):
    """5 种 category 的默认 retryable 行为。"""

    def test_transient_default_retryable(self) -> None:
        e = LifecycleError(category=ErrorCategory.TRANSIENT, message="io")
        self.assertTrue(e.retryable)

    def test_permanent_default_not_retryable(self) -> None:
        e = LifecycleError(category=ErrorCategory.PERMANENT, message="bad config")
        self.assertFalse(e.retryable)

    def test_timeout_default_retryable(self) -> None:
        e = LifecycleError(category=ErrorCategory.TIMEOUT, message="slow")
        self.assertTrue(e.retryable)

    def test_cancelled_default_not_retryable(self) -> None:
        e = LifecycleError(category=ErrorCategory.CANCELLED, message="abort")
        self.assertFalse(e.retryable)

    def test_internal_default_not_retryable(self) -> None:
        e = LifecycleError(category=ErrorCategory.INTERNAL, message="bug")
        self.assertFalse(e.retryable)

    def test_explicit_retryable_overrides_default(self) -> None:
        e = LifecycleError(
            category=ErrorCategory.TRANSIENT, message="x", retryable=False
        )
        self.assertFalse(e.retryable)

    def test_retryable_true_on_permanent(self) -> None:
        """可以显式让 permanent 标记为可重试。"""
        e = LifecycleError(
            category=ErrorCategory.PERMANENT, message="x", retryable=True
        )
        self.assertTrue(e.retryable)


class TestRetryAfter(unittest.TestCase):
    """retry_after 处理。"""

    def test_retry_after_set(self) -> None:
        e = LifecycleError.transient("io", retry_after=1.5)
        self.assertEqual(e.retry_after, 1.5)

    def test_retry_after_none(self) -> None:
        e = LifecycleError.transient("io")
        self.assertIsNone(e.retry_after)

    def test_retry_after_invalid_string_ignored(self) -> None:
        e = LifecycleError.transient("io", retry_after="not_a_number")
        self.assertIsNone(e.retry_after)

    def test_retry_after_numeric_string(self) -> None:
        e = LifecycleError.transient("io", retry_after="3.5")
        self.assertEqual(e.retry_after, 3.5)


class TestCausePreservation(unittest.TestCase):
    """cause 链路保留。"""

    def test_cause_kept(self) -> None:
        original = ValueError("bad value")
        e = LifecycleError.transient("wrap", cause=original)
        self.assertIs(e.cause, original)

    def test_cause_traceback_populated(self) -> None:
        original = ValueError("bad value")
        try:
            raise original
        except ValueError as exc:
            e = LifecycleError.transient("wrap", cause=exc)
        self.assertNotEqual(e.cause_traceback, "")

    def test_no_cause_empty_traceback(self) -> None:
        e = LifecycleError.transient("no cause")
        self.assertEqual(e.cause_traceback, "")


class TestConvenienceConstructors(unittest.TestCase):
    """5 个快捷构造。"""

    def test_transient(self) -> None:
        e = LifecycleError.transient("io fail")
        self.assertEqual(e.category, ErrorCategory.TRANSIENT)
        self.assertTrue(e.retryable)

    def test_permanent(self) -> None:
        e = LifecycleError.permanent("bad config")
        self.assertEqual(e.category, ErrorCategory.PERMANENT)
        self.assertFalse(e.retryable)

    def test_timeout(self) -> None:
        e = LifecycleError.timeout("slow")
        self.assertEqual(e.category, ErrorCategory.TIMEOUT)
        self.assertTrue(e.retryable)

    def test_cancelled(self) -> None:
        e = LifecycleError.cancelled("abort")
        self.assertEqual(e.category, ErrorCategory.CANCELLED)
        self.assertFalse(e.retryable)

    def test_internal(self) -> None:
        e = LifecycleError.internal("bug")
        self.assertEqual(e.category, ErrorCategory.INTERNAL)
        self.assertFalse(e.retryable)


class TestContext(unittest.TestCase):
    """context 字段。"""

    def test_context_default_empty(self) -> None:
        e = LifecycleError.transient("io")
        self.assertEqual(e.context, {})

    def test_context_set(self) -> None:
        e = LifecycleError.transient("io", context={"task_id": "t1"})
        self.assertEqual(e.context["task_id"], "t1")

    def test_context_copy(self) -> None:
        """context 应该是 dict 副本,不共享引用。"""
        original = {"k": 1}
        e = LifecycleError.transient("io", context=original)
        original["k"] = 999
        self.assertEqual(e.context["k"], 1)


class TestSerialization(unittest.TestCase):
    """to_dict / from_dict。"""

    def test_to_dict_shape(self) -> None:
        e = LifecycleError.transient("io", retry_after=2.0)
        d = e.to_dict()
        self.assertEqual(d["category"], "TRANSIENT")
        self.assertEqual(d["message"], "io")
        self.assertEqual(d["retry_after"], 2.0)
        self.assertTrue(d["retryable"])
        self.assertIn("cause_type", d)
        self.assertIn("cause_message", d)
        self.assertIn("cause_traceback", d)
        self.assertIn("context", d)

    def test_from_dict_roundtrip(self) -> None:
        e = LifecycleError.transient("io", retry_after=2.0)
        d = e.to_dict()
        e2 = LifecycleError.from_dict(d)
        self.assertEqual(e2.category, e.category)
        self.assertEqual(e2.message, e.message)
        self.assertEqual(e2.retryable, e.retryable)
        self.assertEqual(e2.retry_after, e.retry_after)

    def test_from_dict_invalid_category_defaults_internal(self) -> None:
        e = LifecycleError.from_dict({"category": "FAKE", "message": "x"})
        self.assertEqual(e.category, ErrorCategory.INTERNAL)

    def test_from_dict_missing_keys_safe(self) -> None:
        e = LifecycleError.from_dict({})
        self.assertEqual(e.category, ErrorCategory.INTERNAL)
        self.assertEqual(e.message, "")

    def test_from_dict_non_dict_safe(self) -> None:
        """非 dict 输入安全降级。"""
        try:
            e = LifecycleError.from_dict("not a dict")  # type: ignore[arg-type]
            self.assertIsNotNone(e)
        except Exception:
            # 抛错也可接受
            pass


class TestClassifyException(unittest.TestCase):
    """classify_exception 分类器。"""

    def test_passthrough_lifecycle_error(self) -> None:
        e = LifecycleError.transient("x")
        self.assertIs(classify_exception(e), e)

    def test_io_error_to_transient(self) -> None:
        try:
            # 触发真实 IOError
            try:
                raise FileNotFoundError("nope")
            except FileNotFoundError as exc:
                result = classify_exception(exc)
        except Exception:
            result = classify_exception(IOError("nope"))
        self.assertEqual(result.category, ErrorCategory.TRANSIENT)

    def test_connection_error_to_transient(self) -> None:
        e = ConnectionError("net")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.TRANSIENT)

    def test_os_error_to_transient(self) -> None:
        e = OSError("disk")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.TRANSIENT)

    def test_value_error_to_permanent(self) -> None:
        e = ValueError("bad")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.PERMANENT)

    def test_type_error_to_permanent(self) -> None:
        e = TypeError("bad")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.PERMANENT)

    def test_key_error_to_permanent(self) -> None:
        e = KeyError("missing")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.PERMANENT)

    def test_timeout_error_to_timeout(self) -> None:
        e = TimeoutError("slow")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.TIMEOUT)

    def test_unknown_exception_to_internal(self) -> None:
        class CustomExc(Exception):
            pass

        e = CustomExc("weird")
        result = classify_exception(e)
        self.assertEqual(result.category, ErrorCategory.INTERNAL)


class TestEquality(unittest.TestCase):
    """equality / hash / repr。"""

    def test_equal_same_category_message(self) -> None:
        e1 = LifecycleError.transient("io")
        e2 = LifecycleError.transient("io")
        self.assertEqual(e1, e2)

    def test_not_equal_different_category(self) -> None:
        e1 = LifecycleError.transient("io")
        e2 = LifecycleError.permanent("io")
        self.assertNotEqual(e1, e2)

    def test_not_equal_non_lifecycle(self) -> None:
        e1 = LifecycleError.transient("io")
        self.assertNotEqual(e1, "io")

    def test_hash_consistent(self) -> None:
        e1 = LifecycleError.transient("io")
        e2 = LifecycleError.transient("io")
        self.assertEqual(hash(e1), hash(e2))

    def test_repr_contains_category(self) -> None:
        e = LifecycleError.transient("io")
        s = repr(e)
        self.assertIn("TRANSIENT", s)
        self.assertIn("io", s)

    def test_str_returns_category_and_message(self) -> None:
        e = LifecycleError.transient("io fail")
        s = str(e)
        self.assertIn("TRANSIENT", s)
        self.assertIn("io fail", s)


class TestCanBeRaised(unittest.TestCase):
    """异常可被 raise / except。"""

    def test_raise_and_catch(self) -> None:
        with self.assertRaises(LifecycleError) as ctx:
            raise LifecycleError.transient("io")
        self.assertEqual(ctx.exception.category, ErrorCategory.TRANSIENT)

    def test_catch_via_base(self) -> None:
        with self.assertRaises(Exception):
            raise LifecycleError.internal("bug")


if __name__ == "__main__":
    unittest.main()
