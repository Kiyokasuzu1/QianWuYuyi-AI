# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_context.py

Phase 5.0-D1 Step 2: LifecycleContext 测试。

覆盖:
- 构造参数校验
- 时钟 / runtime_state / snapshot_view 只读
- emit() 行为(含 quota / destroyed / cancelled / payload 校验)
- log() 行为
- check_quota() 行为
- 取消与销毁
- 上下文管理器(with)
- 隔离性: 不同 Context 互不影响
- task_id 注入
- to_dict
"""
import unittest

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_context import (
    LifecycleContext,
    RuntimeLifecycleStateView,
    build_context,
)


# ============================================================
# 简单假 emitter(测试用,Step 3 实现真正的 EventEmitter 后替换)
# ============================================================
class FakeEmitter:
    """轻量假 emitter,用于 Context 测试。"""

    def __init__(self) -> None:
        self.events: list = []
        self._counter = 0

    def emit(self, event_type, payload):
        self._counter += 1
        eid = f"evt_{self._counter}"
        self.events.append({"id": eid, "type": event_type, "payload": dict(payload)})
        return eid


class TestConstruction(unittest.TestCase):
    """构造参数。"""

    def test_minimal(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        self.assertIsInstance(c, LifecycleContext)
        self.assertEqual(c.task_id, "")

    def test_with_task_id(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), task_id="t1")
        self.assertEqual(c.task_id, "t1")

    def test_clock_required(self) -> None:
        with self.assertRaises(ValueError):
            LifecycleContext(clock=None)  # type: ignore[arg-type]

    def test_default_runtime_state(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        self.assertIsInstance(c.runtime_state, RuntimeLifecycleStateView)
        self.assertEqual(c.runtime_state.last_state, "UNKNOWN")


class TestClockAccess(unittest.TestCase):
    """时钟访问。"""

    def test_clock_property(self) -> None:
        fc = FrozenClock(initial=100.0)
        c = LifecycleContext(clock=fc)
        self.assertIs(c.clock, fc)

    def test_now_uses_clock(self) -> None:
        fc = FrozenClock(initial=200.0)
        c = LifecycleContext(clock=fc)
        self.assertEqual(c.now(), 200.0)

    def test_clock_advance_reflected(self) -> None:
        fc = FrozenClock(initial=0.0)
        c = LifecycleContext(clock=fc)
        fc.advance(50)
        self.assertEqual(c.now(), 50.0)


class TestRuntimeState(unittest.TestCase):
    """Runtime 状态视图。"""

    def test_custom_runtime_state(self) -> None:
        rs = RuntimeLifecycleStateView(
            source="snapshot",
            last_state="RUNNING",
            last_boot_mode="restore",
            last_turn_count=42,
        )
        c = LifecycleContext(clock=FrozenClock(), runtime_state=rs)
        self.assertEqual(c.runtime_state.last_state, "RUNNING")
        self.assertEqual(c.runtime_state.last_turn_count, 42)
        self.assertEqual(c.runtime_state.last_boot_mode, "restore")

    def test_to_dict_includes_runtime_state(self) -> None:
        rs = RuntimeLifecycleStateView(last_state="RUNNING")
        c = LifecycleContext(clock=FrozenClock(), runtime_state=rs)
        d = c.to_dict()
        self.assertIn("runtime_state", d)
        self.assertEqual(d["runtime_state"]["last_state"], "RUNNING")


class TestSnapshotView(unittest.TestCase):
    """Snapshot 视图只读。"""

    def test_snapshot_provided(self) -> None:
        snap = {"boot_count": 3, "last_turn_count": 100}
        c = LifecycleContext(clock=FrozenClock(), snapshot_view=snap)
        self.assertEqual(c.snapshot_view.get("boot_count"), 3)
        self.assertEqual(c.snapshot_view["last_turn_count"], 100)

    def test_snapshot_copied(self) -> None:
        """传入 dict 时 Context 应内部拷贝,避免外部修改污染。"""
        snap = {"k": 1}
        c = LifecycleContext(clock=FrozenClock(), snapshot_view=snap)
        snap["k"] = 999  # 外部修改
        self.assertEqual(c.snapshot_view.get("k"), 1)

    def test_snapshot_default_empty(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        self.assertEqual(len(c.snapshot_view), 0)


class TestEmit(unittest.TestCase):
    """emit 行为。"""

    def test_emit_returns_event_id(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em, task_id="t1")
        eid = c.emit("test.event", {"k": 1})
        self.assertIsNotNone(eid)
        self.assertEqual(len(em.events), 1)
        self.assertEqual(em.events[0]["type"], "test.event")

    def test_emit_injects_task_id(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em, task_id="t1")
        c.emit("test.event", {})
        self.assertEqual(em.events[0]["payload"]["task_id"], "t1")

    def test_emit_preserves_existing_task_id(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em, task_id="t1")
        c.emit("test.event", {"task_id": "explicit"})
        self.assertEqual(em.events[0]["payload"]["task_id"], "explicit")

    def test_emit_no_emitter_returns_none(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), emitter=None)
        self.assertIsNone(c.emit("x", {}))

    def test_emit_invalid_event_type(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em)
        self.assertIsNone(c.emit("", {}))
        self.assertIsNone(c.emit(123, {}))  # type: ignore[arg-type]
        self.assertEqual(len(em.events), 0)

    def test_emit_invalid_payload(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em)
        self.assertIsNone(c.emit("x", "not a dict"))  # type: ignore[arg-type]
        self.assertEqual(len(em.events), 0)

    def test_emit_quota(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(
            clock=FrozenClock(), emitter=em, quota_max=2
        )
        self.assertTrue(c.emit("a", {}) is not None)
        self.assertTrue(c.emit("b", {}) is not None)
        self.assertIsNone(c.emit("c", {}))  # 超额
        self.assertEqual(len(em.events), 2)

    def test_emit_unlimited_quota(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(
            clock=FrozenClock(), emitter=em, quota_max=0
        )
        for _ in range(100):
            c.emit("a", {})
        self.assertEqual(len(em.events), 100)

    def test_emit_after_cancelled_returns_none(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em)
        c.cancel()
        self.assertIsNone(c.emit("x", {}))

    def test_emit_after_destroyed_returns_none(self) -> None:
        em = FakeEmitter()
        c = LifecycleContext(clock=FrozenClock(), emitter=em)
        c.destroy()
        self.assertIsNone(c.emit("x", {}))

    def test_emit_emitter_throws_isolated(self) -> None:
        class BadEmitter:
            def emit(self, *args, **kwargs):
                raise RuntimeError("boom")

        c = LifecycleContext(clock=FrozenClock(), emitter=BadEmitter())  # type: ignore[arg-type]
        # 不抛
        self.assertIsNone(c.emit("x", {}))


class TestLog(unittest.TestCase):
    """log 行为。"""

    def test_log_does_not_throw(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), task_id="t1")
        for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            c.log(level, "msg")

    def test_log_invalid_level_defaults_info(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), task_id="t1")
        c.log("UNKNOWN_LEVEL", "msg")  # 不抛

    def test_log_with_fields(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), task_id="t1")
        c.log("INFO", "msg", extra_field="x", another=1)


class TestQuota(unittest.TestCase):
    """quota 检查。"""

    def test_unlimited(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), quota_max=0)
        self.assertTrue(c.check_quota())
        self.assertTrue(c.check_quota())

    def test_limited(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), quota_max=2)
        self.assertTrue(c.check_quota())
        c.emit("a", {})
        self.assertTrue(c.check_quota())
        c.emit("b", {})
        self.assertFalse(c.check_quota())

    def test_quota_used_counter(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), quota_max=10)
        c.emit("a", {})
        c.emit("b", {})
        self.assertEqual(c.quota_used, 2)


class TestCancellation(unittest.TestCase):
    """取消。"""

    def test_default_not_cancelled(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        self.assertFalse(c.is_cancelled)
        self.assertFalse(c.cancelled())

    def test_cancel(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        c.cancel()
        self.assertTrue(c.is_cancelled)
        self.assertTrue(c.cancelled())

    def test_cancel_idempotent(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        c.cancel()
        c.cancel()
        self.assertTrue(c.is_cancelled)


class TestDestroy(unittest.TestCase):
    """销毁。"""

    def test_destroy_implies_cancelled(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        c.destroy()
        self.assertTrue(c.is_cancelled)
        self.assertTrue(c.destroyed)

    def test_destroy_idempotent(self) -> None:
        c = LifecycleContext(clock=FrozenClock())
        c.destroy()
        c.destroy()  # 不抛

    def test_context_manager_destroy(self) -> None:
        with LifecycleContext(clock=FrozenClock()) as c:
            self.assertFalse(c.destroyed)
        self.assertTrue(c.destroyed)


class TestIsolation(unittest.TestCase):
    """不同 Context 互不影响。"""

    def test_two_contexts_independent(self) -> None:
        em1 = FakeEmitter()
        em2 = FakeEmitter()
        c1 = LifecycleContext(clock=FrozenClock(), emitter=em1, task_id="t1")
        c2 = LifecycleContext(clock=FrozenClock(), emitter=em2, task_id="t2")
        c1.emit("a", {})
        c1.cancel()
        self.assertEqual(len(em1.events), 1)
        self.assertEqual(len(em2.events), 0)
        self.assertFalse(c2.is_cancelled)

    def test_cancel_one_does_not_affect_another(self) -> None:
        c1 = LifecycleContext(clock=FrozenClock(), task_id="t1")
        c2 = LifecycleContext(clock=FrozenClock(), task_id="t2")
        c1.cancel()
        self.assertTrue(c1.is_cancelled)
        self.assertFalse(c2.is_cancelled)

    def test_emit_counters_independent(self) -> None:
        c1 = LifecycleContext(clock=FrozenClock(), quota_max=1)
        c2 = LifecycleContext(clock=FrozenClock(), quota_max=1)
        c1.emit("a", {})
        self.assertEqual(c1.quota_used, 1)
        self.assertEqual(c2.quota_used, 0)


class TestReprAndToDict(unittest.TestCase):
    """repr / to_dict。"""

    def test_repr_contains_task_id(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), task_id="t1")
        s = repr(c)
        self.assertIn("t1", s)

    def test_to_dict_keys(self) -> None:
        c = LifecycleContext(clock=FrozenClock(), task_id="t1")
        d = c.to_dict()
        for key in (
            "task_id",
            "now",
            "runtime_state",
            "snapshot_view",
            "is_cancelled",
            "quota_used",
            "quota_max",
        ):
            self.assertIn(key, d)


class TestFactory(unittest.TestCase):
    """build_context 工厂。"""

    def test_factory(self) -> None:
        c = build_context(clock=FrozenClock(), task_id="t1")
        self.assertIsInstance(c, LifecycleContext)
        self.assertEqual(c.task_id, "t1")


if __name__ == "__main__":
    unittest.main()
