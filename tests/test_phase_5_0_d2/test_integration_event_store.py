# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d2/test_integration_event_store.py

Phase 5.0-D2 Step 4: IntegrationEventStore 单元测试。

覆盖:
- 默认构造与路径
- append() 写入 JSONL 文件,自动创建目录
- append() 拒绝非 IntegrationEvent
- append_many() 批量写入
- read_all() / tail() 读取
- 文件不存在/为空时 read_all 返回 []
- 写入失败隔离(silent)
- enable() / disable() / close() / clear()
- 健康检查 / describe
"""
import os
import json
import tempfile
import unittest
import shutil
from typing import List

from src.runtime.integration.integration_event import (
    INTEGRATION_LIFECYCLE_TICK_COMPLETE,
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.integration_event_store import (
    DEFAULT_STORE_PATH,
    IntegrationEventStore,
    IntegrationEventStoreError,
    build_default_event_store,
)


# ============================================================
# Fixtures
# ============================================================
def _make_event(
    event_type: str = INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    source: str = "test_source",
    payload: dict = None,
) -> IntegrationEvent:
    return make_integration_event(
        event_type=event_type,
        source=source,
        payload=payload or {},
    )


class _TempDirMixin:
    """为每个测试创建独立临时目录。"""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="evt_store_test_")
        self._path = os.path.join(self._tmpdir, "events.jsonl")

    def tearDown(self):
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass


# ============================================================
# 默认构造
# ============================================================
class TestEventStoreDefaults(unittest.TestCase):
    def test_default_construct(self):
        s = IntegrationEventStore()
        self.assertEqual(s.name, "integration_event_store")
        self.assertEqual(s.path, DEFAULT_STORE_PATH)
        self.assertTrue(s.enabled)
        self.assertFalse(s.is_closed)
        self.assertEqual(s.write_count, 0)
        self.assertEqual(s.write_failures, 0)

    def test_custom_name_and_path(self):
        s = IntegrationEventStore(name="my_store", path="data/x.jsonl")
        self.assertEqual(s.name, "my_store")
        self.assertEqual(s.path, "data/x.jsonl")

    def test_factory(self):
        s = build_default_event_store(name="f", path="data/y.jsonl", enabled=True)
        self.assertEqual(s.name, "f")
        self.assertEqual(s.path, "data/y.jsonl")
        self.assertTrue(s.enabled)


# ============================================================
# 写入
# ============================================================
class TestEventStoreAppend(_TempDirMixin, unittest.TestCase):
    def test_append_creates_file_and_directory(self):
        """append 应自动创建父目录和文件。"""
        nested = os.path.join(self._tmpdir, "a", "b", "c", "events.jsonl")
        s = IntegrationEventStore(path=nested)
        ev = _make_event()
        self.assertTrue(s.append(ev))
        self.assertTrue(os.path.exists(nested))
        self.assertEqual(s.write_count, 1)
        self.assertEqual(s.write_failures, 0)

    def test_append_writes_jsonl(self):
        s = IntegrationEventStore(path=self._path)
        ev1 = _make_event(payload={"i": 1})
        ev2 = _make_event(payload={"i": 2})
        s.append(ev1)
        s.append(ev2)
        with open(self._path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(lines), 2)
        d1 = json.loads(lines[0])
        d2 = json.loads(lines[1])
        self.assertEqual(d1["event_id"], ev1.event_id)
        self.assertEqual(d2["event_id"], ev2.event_id)
        self.assertEqual(d1["payload"]["i"], 1)
        self.assertEqual(d2["payload"]["i"], 2)

    def test_append_updates_last_event(self):
        s = IntegrationEventStore(path=self._path)
        ev = _make_event()
        s.append(ev)
        self.assertEqual(s.last_event_id, ev.event_id)
        self.assertEqual(s.last_event_type, ev.event_type)
        self.assertGreater(s.last_write_at, 0.0)

    def test_append_non_event_silent(self):
        s = IntegrationEventStore(path=self._path)
        ok = s.append("not an event")
        self.assertFalse(ok)
        self.assertEqual(s.write_count, 0)
        self.assertEqual(s.write_failures, 1)
        self.assertIn("IntegrationEvent", s.last_error)

    def test_append_non_event_silent_false_raises(self):
        s = IntegrationEventStore(path=self._path)
        with self.assertRaises(IntegrationEventStoreError):
            s.append(123, silent=False)  # type: ignore[arg-type]

    def test_append_after_close(self):
        s = IntegrationEventStore(path=self._path)
        s.close()
        ok = s.append(_make_event())
        self.assertFalse(ok)
        self.assertEqual(s.write_count, 0)
        self.assertEqual(s.write_failures, 1)

    def test_append_when_disabled(self):
        s = IntegrationEventStore(path=self._path, enabled=False)
        ok = s.append(_make_event())
        # 禁用时静默跳过,既不写入也不报错
        self.assertFalse(ok)
        self.assertEqual(s.write_count, 0)
        self.assertEqual(s.write_failures, 0)

    def test_append_many(self):
        s = IntegrationEventStore(path=self._path)
        events = [_make_event(payload={"i": i}) for i in range(5)]
        n = s.append_many(events)
        self.assertEqual(n, 5)
        self.assertEqual(s.write_count, 5)

    def test_append_many_empty(self):
        s = IntegrationEventStore(path=self._path)
        n = s.append_many([])
        self.assertEqual(n, 0)


# ============================================================
# 读取
# ============================================================
class TestEventStoreRead(_TempDirMixin, unittest.TestCase):
    def test_read_all_empty_file(self):
        s = IntegrationEventStore(path=self._path)
        events = s.read_all()
        self.assertEqual(events, [])
        self.assertEqual(s.read_count, 1)

    def test_read_all_not_exists(self):
        s = IntegrationEventStore(path=os.path.join(self._tmpdir, "nope.jsonl"))
        events = s.read_all()
        self.assertEqual(events, [])

    def test_read_all_round_trip(self):
        s = IntegrationEventStore(path=self._path)
        ev1 = _make_event(payload={"x": 1})
        ev2 = _make_event(payload={"x": 2})
        s.append(ev1)
        s.append(ev2)
        events = s.read_all()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].event_id, ev1.event_id)
        self.assertEqual(events[1].event_id, ev2.event_id)
        self.assertEqual(events[0].payload.get("x"), 1)
        self.assertEqual(events[1].payload.get("x"), 2)

    def test_read_all_with_limit(self):
        s = IntegrationEventStore(path=self._path)
        for i in range(10):
            s.append(_make_event(payload={"i": i}))
        events = s.read_all(limit=3)
        self.assertEqual(len(events), 3)
        # 取最后 3 条
        self.assertEqual(events[0].payload.get("i"), 7)
        self.assertEqual(events[-1].payload.get("i"), 9)

    def test_tail_default(self):
        s = IntegrationEventStore(path=self._path)
        for i in range(15):
            s.append(_make_event(payload={"i": i}))
        events = s.tail(5)
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0].payload.get("i"), 10)
        self.assertEqual(events[-1].payload.get("i"), 14)

    def test_tail_zero(self):
        s = IntegrationEventStore(path=self._path)
        s.append(_make_event())
        events = s.tail(0)
        self.assertEqual(events, [])

    def test_read_all_skips_corrupt_lines(self):
        """JSON 损坏的行应被跳过,read_failures 增加。"""
        s = IntegrationEventStore(path=self._path)
        s.append(_make_event())
        # 手动追加一行损坏的 JSON
        with open(self._path, "a", encoding="utf-8") as f:
            f.write("this is not json\n")
        s.append(_make_event())
        events = s.read_all()
        # 应该跳过损坏行,只返回 2 条有效事件
        self.assertEqual(len(events), 2)
        self.assertGreaterEqual(s.read_failures, 1)


# ============================================================
# 维护
# ============================================================
class TestEventStoreMaintenance(_TempDirMixin, unittest.TestCase):
    def test_clear_deletes_file(self):
        s = IntegrationEventStore(path=self._path)
        s.append(_make_event())
        self.assertTrue(os.path.exists(self._path))
        s.clear()
        self.assertFalse(os.path.exists(self._path))

    def test_clear_no_file(self):
        s = IntegrationEventStore(path=self._path)
        result = s.clear()
        self.assertTrue(result)

    def test_disable_enable(self):
        s = IntegrationEventStore(path=self._path)
        s.disable()
        self.assertFalse(s.enabled)
        s.enable()
        self.assertTrue(s.enabled)

    def test_close(self):
        s = IntegrationEventStore(path=self._path)
        self.assertFalse(s.is_closed)
        self.assertTrue(s.close())
        self.assertTrue(s.is_closed)
        # 第二次 close 返回 False
        self.assertFalse(s.close())


# ============================================================
# 健康 / 描述
# ============================================================
class TestEventStoreHealth(_TempDirMixin, unittest.TestCase):
    def test_health_check(self):
        s = IntegrationEventStore(path=self._path, name="hc")
        s.append(_make_event())
        h = s.health_check()
        self.assertEqual(h["name"], "hc")
        self.assertEqual(h["path"], self._path)
        self.assertTrue(h["enabled"])
        self.assertFalse(h["is_closed"])
        self.assertTrue(h["exists"])
        self.assertEqual(h["write_count"], 1)
        self.assertEqual(h["write_failures"], 0)
        self.assertIn("last_event_id", h)
        self.assertIn("last_write_at", h)
        self.assertIn("last_error", h)

    def test_describe(self):
        s = IntegrationEventStore(path=self._path)
        d = s.describe()
        self.assertIn("name", d)
        self.assertIn("path", d)
        self.assertIn("write_count", d)

    def test_repr(self):
        s = IntegrationEventStore(path=self._path, name="r")
        r = repr(s)
        self.assertIn("IntegrationEventStore", r)
        self.assertIn("r", r)
        # path 在 repr 中会被 Python 转义(每个 \ 变成 \\)
        self.assertIn(self._path.replace("\\", "\\\\"), r)

    def test_exists(self):
        s = IntegrationEventStore(path=self._path)
        self.assertFalse(s.exists())
        s.append(_make_event())
        self.assertTrue(s.exists())


# ============================================================
# 集成场景
# ============================================================
class TestEventStoreIntegration(_TempDirMixin, unittest.TestCase):
    def test_persistence_round_trip(self):
        """append 多个 event,read_all 完整恢复。"""
        s = IntegrationEventStore(path=self._path)
        events = [
            make_integration_event(
                event_type=INTEGRATION_LIFECYCLE_TICK_COMPLETE,
                source="host",
                payload={"tick": i, "n": i * 2},
            )
            for i in range(20)
        ]
        s.append_many(events)
        loaded = s.read_all()
        self.assertEqual(len(loaded), 20)
        for i, e in enumerate(loaded):
            self.assertEqual(e.event_type, INTEGRATION_LIFECYCLE_TICK_COMPLETE)
            self.assertEqual(e.payload.get("tick"), i)
            self.assertEqual(e.payload.get("n"), i * 2)

    def test_chinese_payload_preserved(self):
        """中文字符不被转义。"""
        s = IntegrationEventStore(path=self._path)
        ev = make_integration_event(
            event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
            source="memory",
            payload={"name": "羽依", "msg": "你好,世界"},
        )
        s.append(ev)
        loaded = s.read_all()
        self.assertEqual(loaded[0].payload.get("name"), "羽依")
        self.assertEqual(loaded[0].payload.get("msg"), "你好,世界")

    def test_after_reload_preserves_order(self):
        """多个 store 实例读同一文件,顺序保留。"""
        s1 = IntegrationEventStore(path=self._path)
        for i in range(5):
            s1.append(_make_event(payload={"i": i}))
        s2 = IntegrationEventStore(path=self._path)
        loaded = s2.read_all()
        self.assertEqual(len(loaded), 5)
        for i, e in enumerate(loaded):
            self.assertEqual(e.payload.get("i"), i)


if __name__ == "__main__":
    unittest.main()
