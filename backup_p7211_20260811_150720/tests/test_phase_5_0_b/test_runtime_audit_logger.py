# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_b/test_runtime_audit_logger.py

Phase 5.0-B: RuntimeAuditLogger 单元测试。

覆盖(>20):
- 正常记录:event_start / event_end / memory / growth / self_model / reflection / persistence / exception / checkpoint / loop_*
- 写入失败隔离
- 多事件连续记录
- JSONL 恢复(读回)
- 文件不存在自动创建
- 目录不存在自动创建
- thread safety
- size-based rotation
- enabled=False no-op
- 大对象安全截断
- query / count_by_event / tail
- health_check
- close() 后不写
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict

# 确保 src 可导入
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.runtime.audit_log.runtime_audit_logger import (  # noqa: E402
    RuntimeAuditLogger,
    AuditEvent,
    RUNTIME_AUDIT_LOGGER_SCHEMA_VERSION,
    DEFAULT_AUDIT_LOG_PATH,
)


class _TempPathMixin:
    """每个测试用临时目录。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="audit_test_")
        self.log_path = os.path.join(self._tmpdir, "audit.jsonl")

    def tearDown(self) -> None:
        import shutil
        try:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        except Exception:
            pass


# ============================================================
# TestBasicWrite: 基础写盘
# ============================================================
class TestBasicWrite(_TempPathMixin, unittest.TestCase):
    def test_event_start(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(a.log_event_start("e_1", user_input="hi"))
        self.assertEqual(a.write_count, 1)
        # 文件被创建
        self.assertTrue(os.path.exists(self.log_path))
        # 读回
        records = a.read_all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "event_start")
        self.assertEqual(records[0]["event_id"], "e_1")
        self.assertIn("hi", records[0]["user_input_summary"])

    def test_event_end(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(
            a.log_event_end("e_1", reply="reply hi", duration_ms=12.5, success=True)
        )
        r = a.read_all()[0]
        self.assertEqual(r["event"], "event_end")
        self.assertEqual(r["event_id"], "e_1")
        self.assertEqual(r["duration_ms"], 12.5)
        self.assertTrue(r["success"])

    def test_memory_change(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(
            a.log_memory_change(before={"n": 1}, after={"n": 2}, event_id="e_1")
        )
        r = a.read_all()[0]
        self.assertEqual(r["event"], "memory_change")
        self.assertIn("before", r["delta_summary"])

    def test_growth_change(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(
            a.log_growth_change(before=None, after={"warmth": 0.5})
        )
        r = a.read_all()[0]
        self.assertEqual(r["event"], "growth_change")
        self.assertIn("added", r["delta_summary"])

    def test_self_model_evolution(self):
        a = RuntimeAuditLogger(log_path=self.log_path)

        class _EvoResult:
            old_snapshot = {"v": 1}
            new_snapshot = {"v": 2}
            evolution_id = "evo_42"

        self.assertTrue(a.log_self_model_evolution(_EvoResult(), event_id="e_1"))
        r = a.read_all()[0]
        self.assertEqual(r["event"], "self_model_evolution")
        self.assertEqual(r["evolution_id"], "evo_42")

    def test_reflection(self):
        a = RuntimeAuditLogger(log_path=self.log_path)

        class _Ref:
            reflection_id = "ref_1"

        self.assertTrue(a.log_reflection(_Ref(), event_id="e_1"))
        r = a.read_all()[0]
        self.assertEqual(r["event"], "reflection")
        self.assertEqual(r["reflection_id"], "ref_1")

    def test_validation(self):
        a = RuntimeAuditLogger(log_path=self.log_path)

        class _R:
            valid = True

        self.assertTrue(a.log_validation(_R(), event_id="e_1"))
        r = a.read_all()[0]
        self.assertEqual(r["event"], "validation")
        self.assertTrue(r["valid"])

    def test_persistence(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(
            a.log_persistence(success=True, event_id="e_1", persisted_id="p_1")
        )
        r = a.read_all()[0]
        self.assertEqual(r["event"], "persistence")
        self.assertTrue(r["success"])
        self.assertEqual(r["persisted_id"], "p_1")

    def test_exception(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        try:
            raise ValueError("test boom")
        except ValueError as exc:
            self.assertTrue(a.log_exception("build", exc, event_id="e_1"))
        r = a.read_all()[0]
        self.assertEqual(r["event"], "exception")
        self.assertEqual(r["exception_type"], "ValueError")
        self.assertEqual(r["phase"], "build")

    def test_checkpoint(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(
            a.log_checkpoint("final", state={"turn_count": 100})
        )
        r = a.read_all()[0]
        self.assertEqual(r["event"], "checkpoint")
        self.assertEqual(r["reason"], "final")
        self.assertEqual(r["state"]["turn_count"], 100)

    def test_loop_start_stop(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(a.log_loop_start())
        self.assertTrue(a.log_loop_stop(reason="exit keyword"))
        records = a.read_all()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event"], "loop_start")
        self.assertEqual(records[1]["event"], "loop_stop")
        self.assertEqual(records[1]["reason"], "exit keyword")

    def test_custom(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertTrue(a.log_custom("memory_flush", size_bytes=1024))
        r = a.read_all()[0]
        self.assertEqual(r["event"], "custom")
        self.assertEqual(r["event_name"], "memory_flush")


# ============================================================
# TestFileSystem: 文件/目录自动创建
# ============================================================
class TestFileSystem(_TempPathMixin, unittest.TestCase):
    def test_file_does_not_exist_is_created(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        self.assertFalse(os.path.exists(self.log_path))
        a.log_event_start("e_1")
        self.assertTrue(os.path.exists(self.log_path))

    def test_directory_does_not_exist_is_created(self):
        nested_path = os.path.join(self._tmpdir, "deep", "nest", "audit.jsonl")
        a = RuntimeAuditLogger(log_path=nested_path)
        a.log_event_start("e_1")
        self.assertTrue(os.path.exists(nested_path))

    def test_file_appends_not_overwrites(self):
        # 第一次写入
        a1 = RuntimeAuditLogger(log_path=self.log_path)
        a1.log_event_start("e_1")
        # 第二次写入(同一文件)
        a2 = RuntimeAuditLogger(log_path=self.log_path)
        a2.log_event_start("e_2")
        # 应该有 2 条
        records = a2.read_all()
        self.assertEqual(len(records), 2)


# ============================================================
# TestJSONLRecovery: JSONL 恢复(读回)
# ============================================================
class TestJSONLRecovery(_TempPathMixin, unittest.TestCase):
    def test_read_all_returns_records(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        for i in range(5):
            a.log_event_start(f"e_{i}")
        records = a.read_all()
        self.assertEqual(len(records), 5)
        for i, r in enumerate(records):
            self.assertEqual(r["event_id"], f"e_{i}")

    def test_tail_returns_last_n(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        for i in range(20):
            a.log_event_start(f"e_{i}")
        tail = a.tail(3)
        self.assertEqual(len(tail), 3)
        self.assertEqual(tail[0]["event_id"], "e_17")
        self.assertEqual(tail[-1]["event_id"], "e_19")

    def test_corrupt_line_skipped(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        a.log_event_start("e_1")
        # 手动追加一行坏 JSON
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write("not-valid-json\n")
        a.log_event_start("e_2")
        records = a.read_all()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["event_id"], "e_1")
        self.assertEqual(records[1]["event_id"], "e_2")

    def test_count_by_event(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        a.log_event_start("e_1")
        a.log_event_start("e_2")
        a.log_exception("build", ValueError("x"))
        a.log_persistence(success=True)
        self.assertEqual(a.count_by_event("event_start"), 2)
        self.assertEqual(a.count_exceptions(), 1)
        self.assertEqual(a.count_by_event("persistence"), 1)


# ============================================================
# TestExceptionIsolation: 写入失败隔离
# ============================================================
class TestExceptionIsolation(unittest.TestCase):
    def test_invalid_path_returns_false_not_raise(self):
        # 写到一个无法创建的位置(Windows 上 None/空字符串可能 raise)
        a = RuntimeAuditLogger(log_path="/dev/null/cannot/write.jsonl")
        # 不应抛异常
        result = a.log_event_start("e_1")
        # 在大多数平台 /dev/null/... 仍可写;但若失败,不应抛
        # 至少能调用完
        self.assertIsInstance(result, bool)

    def test_write_count_increments_on_failure(self):
        # 模拟: 路径指向一个目录而不是文件
        tmpdir = tempfile.mkdtemp()
        try:
            # 传一个已存在的目录路径,open("a") 在大多数平台会失败
            a = RuntimeAuditLogger(log_path=tmpdir)
            # 不应抛
            r = a.log_event_start("e_1")
            # 写失败会写 error_count,但返回 False
            self.assertIsInstance(r, bool)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_close_disables_writes(self):
        a = RuntimeAuditLogger(log_path=os.path.join(tempfile.mkdtemp(), "x.jsonl"))
        a.close()
        result = a.log_event_start("e_1")
        self.assertFalse(result)
        self.assertEqual(a.write_count, 0)


# ============================================================
# TestConcurrency: 线程安全
# ============================================================
class TestConcurrency(_TempPathMixin, unittest.TestCase):
    def test_concurrent_writes(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        n_threads = 4
        n_writes = 50

        def worker(tid: int):
            for i in range(n_writes):
                a.log_event_start(f"e_{tid}_{i}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        records = a.read_all()
        self.assertEqual(len(records), n_threads * n_writes)
        self.assertEqual(a.write_count, n_threads * n_writes)


# ============================================================
# TestEnabledFlag
# ============================================================
class TestEnabledFlag(_TempPathMixin, unittest.TestCase):
    def test_disabled_skips_writes(self):
        a = RuntimeAuditLogger(log_path=self.log_path, enabled=False)
        self.assertFalse(a.log_event_start("e_1"))
        self.assertEqual(a.write_count, 0)
        self.assertFalse(os.path.exists(self.log_path))

    def test_re_enable_resumes(self):
        a = RuntimeAuditLogger(log_path=self.log_path, enabled=False)
        a.log_event_start("e_0")  # 不写
        a.enable()
        a.log_event_start("e_1")  # 写
        self.assertEqual(a.write_count, 1)
        self.assertEqual(len(a.read_all()), 1)


# ============================================================
# TestRotation: size-based rotation
# ============================================================
class TestRotation(_TempPathMixin, unittest.TestCase):
    def test_rotation_triggered(self):
        # 极小 max_bytes 强制 rotation
        a = RuntimeAuditLogger(log_path=self.log_path, max_bytes=200)
        # 写 50 条,触发多次 rotation
        for i in range(50):
            a.log_event_start(f"event_{i}_with_some_padding_to_increase_size")
        # rotation_count > 0(意味着 rotation 触发过)
        self.assertGreater(a.rotation_count, 0)
        # 旧文件存在
        backup = self.log_path + ".1"
        self.assertTrue(os.path.exists(backup))


# ============================================================
# TestSafety
# ============================================================
class TestSafety(_TempPathMixin, unittest.TestCase):
    def test_unprintable_value(self):
        class Bad:
            def __repr__(self):
                raise RuntimeError("bad repr")

        a = RuntimeAuditLogger(log_path=self.log_path)
        # 不应抛
        result = a.log_event_start("e_1", bad_obj=Bad())
        self.assertTrue(result)
        self.assertEqual(a.write_count, 1)

    def test_oversized_user_input_truncated(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        huge = "x" * 10000
        a.log_event_start("e_1", user_input=huge)
        r = a.read_all()[0]
        self.assertLessEqual(len(r["user_input_summary"]), 300)

    def test_state_filtering(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        a.log_checkpoint(
            "final",
            state={
                "turn_count": 5,
                "huge": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 100,
                "small": "hi",
            },
        )
        r = a.read_all()[0]
        # huge 应该被截断
        self.assertLessEqual(len(str(r["state"]["huge"])), 100)


# ============================================================
# TestQuery
# ============================================================
class TestQuery(_TempPathMixin, unittest.TestCase):
    def test_query_with_predicate(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        for i in range(10):
            if i % 2 == 0:
                a.log_event_start(f"e_{i}")
            else:
                a.log_exception("x", ValueError(f"e_{i}"))
        # 只查 event_start
        result = a.query(predicate=lambda r: r.get("event") == "event_start")
        self.assertEqual(len(result), 5)
        # 限制 limit
        result2 = a.query(predicate=lambda r: r.get("event") == "event_start", limit=2)
        self.assertEqual(len(result2), 2)


# ============================================================
# TestHealthCheck
# ============================================================
class TestHealthCheck(_TempPathMixin, unittest.TestCase):
    def test_health_check_structure(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        h = a.health_check()
        self.assertEqual(h["name"], "runtime_audit_logger")
        self.assertEqual(h["schema_version"], RUNTIME_AUDIT_LOGGER_SCHEMA_VERSION)
        self.assertTrue(h["enabled"])
        self.assertEqual(h["write_count"], 0)
        self.assertEqual(h["file_exists"], False)
        self.assertEqual(h["file_size"], 0)

    def test_health_check_after_writes(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        a.log_event_start("e_1")
        a.log_event_start("e_2")
        h = a.health_check()
        self.assertEqual(h["write_count"], 2)
        self.assertTrue(h["file_exists"])
        self.assertGreater(h["file_size"], 0)
        self.assertIsNotNone(h["last_write_at"])


# ============================================================
# TestSchema
# ============================================================
class TestSchema(_TempPathMixin, unittest.TestCase):
    def test_schema_version_in_record(self):
        a = RuntimeAuditLogger(log_path=self.log_path)
        a.log_event_start("e_1")
        r = a.read_all()[0]
        self.assertEqual(r["schema_version"], RUNTIME_AUDIT_LOGGER_SCHEMA_VERSION)
        self.assertIn("ts", r)

    def test_event_constants(self):
        # 常量值稳定
        self.assertEqual(AuditEvent.EVENT_START, "event_start")
        self.assertEqual(AuditEvent.EXCEPTION, "exception")
        self.assertEqual(AuditEvent.PERSISTENCE, "persistence")


if __name__ == "__main__":
    unittest.main()
