# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d1/test_lifecycle_registry.py

Phase 5.0-D1 Step 3: LifecycleRegistry 测试。

覆盖:
- 构造与默认属性
- 注册 / 注销 / 清空
- 重复 task_id 三种策略
- 按 owner / priority 查询
- 与 EventEmitter 集成
- 线程安全
- 迭代 / __len__ / __contains__
- 异常路径
- 快照
"""
from __future__ import annotations

import threading
import unittest
from typing import List

from src.runtime.lifecycle.internal.clock import SystemClock
from src.runtime.lifecycle.internal.event_emitter import (
    EVENT_TASK_REGISTERED,
    EVENT_TASK_UNREGISTERED,
    EventEmitter,
)
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_decision import Always
from src.runtime.lifecycle.lifecycle_registry import (
    DuplicateTaskError,
    IGNORE_DUPLICATE,
    LifecycleRegistry,
    REJECT_DUPLICATE,
    REPLACE_DUPLICATE,
)
from src.runtime.lifecycle.lifecycle_task import SimpleTask


def _make_task(task_id: str, owner: str = "test", priority: int = 5) -> SimpleTask:
    return SimpleTask(
        task_id=task_id,
        owner=owner,
        action=lambda ctx: None,
        priority=priority,
        condition=Always(),
    )


def _make_context(emitter: EventEmitter = None) -> LifecycleContext:
    return LifecycleContext(
        clock=SystemClock(),
        emitter=emitter,
        task_id="__test_ctx__",
    )


class TestConstruction(unittest.TestCase):
    def test_default_policy_is_reject(self):
        r = LifecycleRegistry()
        self.assertEqual(r.duplicate_policy, REJECT_DUPLICATE)
        self.assertEqual(r.size, 0)
        self.assertTrue(r.is_empty())

    def test_custom_name(self):
        r = LifecycleRegistry(name="custom")
        self.assertEqual(r.name, "custom")
        self.assertIn("custom", repr(r))

    def test_invalid_duplicate_policy(self):
        with self.assertRaises(ValueError):
            LifecycleRegistry(duplicate_policy="invalid")

    def test_with_emitter(self):
        e = EventEmitter()
        r = LifecycleRegistry(emitter=e)
        self.assertIs(r.emitter, e)


class TestRegister(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = LifecycleRegistry()

    def test_register_single(self):
        t = _make_task("t.alpha")
        self.assertTrue(self.registry.register(t))
        self.assertEqual(self.registry.size, 1)
        self.assertIn("t.alpha", self.registry)
        self.assertIs(self.registry.get("t.alpha"), t)

    def test_register_none_raises(self):
        with self.assertRaises(ValueError):
            self.registry.register(None)  # type: ignore[arg-type]

    def test_register_task_without_task_id_raises(self):
        class _Bad:
            owner = "test"
        with self.assertRaises(ValueError):
            self.registry.register(_Bad())  # type: ignore[arg-type]

    def test_register_task_without_owner_raises(self):
        class _Bad:
            task_id = "t.x"
        with self.assertRaises(ValueError):
            self.registry.register(_Bad())  # type: ignore[arg-type]

    def test_register_many_returns_count(self):
        tasks = [_make_task(f"t.{i}", owner=f"o.{i % 2}") for i in range(10)]
        n = self.registry.register_many(tasks)
        self.assertEqual(n, 10)
        self.assertEqual(self.registry.size, 10)

    def test_contains_after_register(self):
        self.registry.register(_make_task("t.x"))
        self.assertTrue(self.registry.contains("t.x"))
        self.assertFalse(self.registry.contains("t.not_exist"))
        self.assertFalse(self.registry.contains(123))  # type: ignore[arg-type]

    def test_get_invalid_id(self):
        self.assertIsNone(self.registry.get(""))
        self.assertIsNone(self.registry.get(None))  # type: ignore[arg-type]


class TestDuplicatePolicy(unittest.TestCase):
    def test_reject_raises(self):
        r = LifecycleRegistry(duplicate_policy=REJECT_DUPLICATE)
        r.register(_make_task("t.dup"))
        with self.assertRaises(DuplicateTaskError):
            r.register(_make_task("t.dup"))

    def test_replace_silent(self):
        r = LifecycleRegistry(duplicate_policy=REPLACE_DUPLICATE)
        t1 = _make_task("t.dup", owner="a")
        t2 = _make_task("t.dup", owner="b")
        r.register(t1)
        self.assertTrue(r.register(t2))
        self.assertIs(r.get("t.dup"), t2)
        # 旧 owner 索引应清理
        self.assertFalse(r.has_owner("a"))
        self.assertTrue(r.has_owner("b"))

    def test_ignore_silent(self):
        r = LifecycleRegistry(duplicate_policy=IGNORE_DUPLICATE)
        t1 = _make_task("t.dup")
        t2 = _make_task("t.dup")
        r.register(t1)
        self.assertFalse(r.register(t2))
        self.assertIs(r.get("t.dup"), t1)

    def test_register_replace_flag_overrides(self):
        r = LifecycleRegistry(duplicate_policy=REJECT_DUPLICATE)
        r.register(_make_task("t.x", owner="a"))
        # replace=False 在 reject 策略下仍抛错
        with self.assertRaises(DuplicateTaskError):
            r.register(_make_task("t.x"), replace=False)
        # replace=True 强制覆盖
        t2 = _make_task("t.x", owner="b")
        self.assertTrue(r.register(t2, replace=True))
        self.assertIs(r.get("t.x"), t2)

    def test_register_many_skips_duplicates(self):
        r = LifecycleRegistry(duplicate_policy=REJECT_DUPLICATE)
        tasks = [_make_task("t.dup") for _ in range(3)]
        n = r.register_many(tasks)
        self.assertEqual(n, 1)


class TestUnregister(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = LifecycleRegistry()

    def test_unregister_existing(self):
        t = _make_task("t.alpha")
        self.registry.register(t)
        result = self.registry.unregister("t.alpha")
        self.assertIs(result, t)
        self.assertEqual(self.registry.size, 0)

    def test_unregister_missing(self):
        self.assertIsNone(self.registry.unregister("t.missing"))
        self.assertIsNone(self.registry.unregister(""))

    def test_unregister_removes_owner_index(self):
        self.registry.register(_make_task("t.1", owner="mod_a"))
        self.registry.register(_make_task("t.2", owner="mod_a"))
        self.registry.unregister("t.1")
        self.assertTrue(self.registry.has_owner("mod_a"))
        self.registry.unregister("t.2")
        self.assertFalse(self.registry.has_owner("mod_a"))
        self.assertNotIn("mod_a", self.registry.owners())

    def test_unregister_invalid_id_returns_none(self):
        # 空 / 非字符串 task_id 视为不存在,直接返回 None(查询语义)
        self.assertIsNone(self.registry.unregister(None))  # type: ignore[arg-type]
        self.assertIsNone(self.registry.unregister(""))

    def test_clear(self):
        for i in range(5):
            self.registry.register(_make_task(f"t.{i}"))
        n = self.registry.clear()
        self.assertEqual(n, 5)
        self.assertEqual(self.registry.size, 0)
        self.assertEqual(self.registry.owners(), [])

    def test_unregister_many(self):
        ids = [f"t.{i}" for i in range(5)]
        for tid in ids:
            self.registry.register(_make_task(tid))
        n = self.registry.unregister_many(ids[:3])
        self.assertEqual(n, 3)
        self.assertEqual(self.registry.size, 2)


class TestQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = LifecycleRegistry()
        self.registry.register(_make_task("t.1", owner="mod_a", priority=3))
        self.registry.register(_make_task("t.2", owner="mod_a", priority=8))
        self.registry.register(_make_task("t.3", owner="mod_b", priority=5))
        self.registry.register(_make_task("t.4", owner="mod_c", priority=1))

    def test_task_ids_sorted(self):
        self.assertEqual(
            self.registry.task_ids(),
            ["t.1", "t.2", "t.3", "t.4"],
        )

    def test_owners_sorted(self):
        self.assertEqual(self.registry.owners(), ["mod_a", "mod_b", "mod_c"])

    def test_owner_count(self):
        self.assertEqual(self.registry.owner_count(), 3)

    def test_list_by_owner(self):
        a_tasks = self.registry.list_by_owner("mod_a")
        self.assertEqual(len(a_tasks), 2)
        self.assertEqual({t.task_id for t in a_tasks}, {"t.1", "t.2"})

    def test_list_by_owner_empty(self):
        self.assertEqual(self.registry.list_by_owner("nonexistent"), [])
        self.assertEqual(self.registry.list_by_owner(""), [])

    def test_list_by_owners(self):
        tasks = self.registry.list_by_owners(["mod_a", "mod_b"])
        self.assertEqual({t.task_id for t in tasks}, {"t.1", "t.2", "t.3"})

    def test_list_all_sorted_by_id(self):
        all_tasks = self.registry.list_all()
        self.assertEqual([t.task_id for t in all_tasks], ["t.1", "t.2", "t.3", "t.4"])

    def test_list_by_priority_desc(self):
        tasks = self.registry.list_by_priority_desc()
        # priority 8,5,3,1
        self.assertEqual([t.task_id for t in tasks], ["t.2", "t.3", "t.1", "t.4"])

    def test_list_by_priority_asc(self):
        tasks = self.registry.list_by_priority_asc()
        self.assertEqual([t.task_id for t in tasks], ["t.4", "t.1", "t.3", "t.2"])

    def test_find_with_predicate(self):
        tasks = self.registry.find(lambda t: t.priority >= 5)
        self.assertEqual({t.task_id for t in tasks}, {"t.2", "t.3"})

    def test_find_non_callable_raises(self):
        with self.assertRaises(ValueError):
            self.registry.find("not_callable")  # type: ignore[arg-type]

    def test_has_owner(self):
        self.assertTrue(self.registry.has_owner("mod_a"))
        self.assertFalse(self.registry.has_owner("mod_zzz"))
        self.assertFalse(self.registry.has_owner(""))


class TestIteration(unittest.TestCase):
    def test_iter(self):
        r = LifecycleRegistry()
        ids = ["t.a", "t.b", "t.c"]
        for tid in ids:
            r.register(_make_task(tid))
        seen = [t.task_id for t in r]
        self.assertEqual(seen, sorted(ids))

    def test_len(self):
        r = LifecycleRegistry()
        self.assertEqual(len(r), 0)
        r.register(_make_task("t.1"))
        self.assertEqual(len(r), 1)

    def test_contains_protocol(self):
        r = LifecycleRegistry()
        r.register(_make_task("t.1"))
        self.assertIn("t.1", r)
        self.assertNotIn("t.2", r)
        # 非字符串不抛错
        self.assertNotIn(123, r)  # type: ignore[operator]


class TestEmitterIntegration(unittest.TestCase):
    def test_register_emits_event(self):
        e = EventEmitter(history_capacity=10)
        r = LifecycleRegistry(emitter=e)
        r.register(_make_task("t.1", owner="mod_x"))
        self.assertEqual(e.history_size(), 1)
        ev = e.history()[0]
        self.assertEqual(ev.event_type, EVENT_TASK_REGISTERED)
        self.assertEqual(ev.payload["task_id"], "t.1")
        self.assertEqual(ev.payload["owner"], "mod_x")
        self.assertFalse(ev.payload["replaced"])

    def test_replace_emits_replaced_true(self):
        e = EventEmitter(history_capacity=10)
        r = LifecycleRegistry(duplicate_policy=REPLACE_DUPLICATE, emitter=e)
        r.register(_make_task("t.1", owner="a"))
        r.register(_make_task("t.1", owner="b"))
        events = e.history()
        self.assertEqual(len(events), 2)
        self.assertTrue(events[1].payload["replaced"])

    def test_unregister_emits_event(self):
        e = EventEmitter(history_capacity=10)
        r = LifecycleRegistry(emitter=e)
        r.register(_make_task("t.1", owner="mod_x"))
        r.unregister("t.1")
        events = e.history()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].event_type, EVENT_TASK_UNREGISTERED)
        self.assertEqual(events[1].payload["owner"], "mod_x")

    def test_unregister_missing_does_not_emit(self):
        e = EventEmitter(history_capacity=10)
        r = LifecycleRegistry(emitter=e)
        r.unregister("nonexistent")
        self.assertEqual(e.history_size(), 0)

    def test_emitter_closed_does_not_break_register(self):
        e = EventEmitter()
        e.close()
        r = LifecycleRegistry(emitter=e)
        # 注册本身应成功(emit 在 try 中)
        self.assertTrue(r.register(_make_task("t.1")))


class TestSnapshot(unittest.TestCase):
    def test_snapshot_empty(self):
        r = LifecycleRegistry(name="snap_test")
        snap = r.snapshot()
        self.assertEqual(snap["name"], "snap_test")
        self.assertEqual(snap["size"], 0)
        self.assertEqual(snap["owner_count"], 0)
        self.assertEqual(snap["task_ids"], [])
        self.assertEqual(snap["owners"], [])

    def test_snapshot_populated(self):
        r = LifecycleRegistry()
        r.register(_make_task("t.a", owner="mod_a"))
        r.register(_make_task("t.b", owner="mod_b"))
        snap = r.snapshot()
        self.assertEqual(snap["size"], 2)
        self.assertEqual(snap["owner_count"], 2)
        self.assertEqual(snap["task_ids"], ["t.a", "t.b"])


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_register(self):
        r = LifecycleRegistry(duplicate_policy=IGNORE_DUPLICATE)
        n_threads = 10
        per_thread = 50

        def worker(prefix: str):
            for i in range(per_thread):
                try:
                    r.register(_make_task(f"{prefix}.{i}", owner=prefix))
                except Exception:
                    pass

        threads = [
            threading.Thread(target=worker, args=(f"mod_{i}",))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 不抛错 + 注册数合理
        self.assertGreater(r.size, 0)
        self.assertLessEqual(r.size, n_threads * per_thread)

    def test_concurrent_register_unregister(self):
        r = LifecycleRegistry(duplicate_policy=REPLACE_DUPLICATE)
        barrier = threading.Barrier(4)

        def worker(idx: int):
            barrier.wait()
            for i in range(100):
                r.register(_make_task(f"t.{i % 10}", owner=f"o{idx}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 注册表应只剩 10 个 task
        self.assertLessEqual(r.size, 10)
        self.assertGreater(r.size, 0)


class TestEdgeCases(unittest.TestCase):
    def test_empty_owner_index_after_full_unregister(self):
        r = LifecycleRegistry()
        r.register(_make_task("t.1", owner="a"))
        r.unregister("t.1")
        self.assertEqual(r.owners(), [])
        self.assertEqual(r.owner_count(), 0)

    def test_repr_contains_info(self):
        r = LifecycleRegistry(name="r_test")
        r.register(_make_task("t.1"))
        s = repr(r)
        self.assertIn("r_test", s)
        self.assertIn("size=1", s)

    def test_register_same_owner_multiple_tasks(self):
        r = LifecycleRegistry()
        for i in range(5):
            r.register(_make_task(f"t.{i}", owner="same"))
        self.assertEqual(r.owner_count(), 1)
        self.assertEqual(len(r.list_by_owner("same")), 5)


if __name__ == "__main__":
    unittest.main()
