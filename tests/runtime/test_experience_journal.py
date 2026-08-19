# -*- coding: utf-8 -*-
"""
tests/runtime/test_experience_journal.py

Phase 4.1D：Experience Persistence 修复的验收回归测试。

验收标准（来自任务卡）：
- experience 可以跨重启恢复
- Growth 可以读取历史 experience（经 adapter 接口 readback）
- MemoryStore 不出现 runtime_experience
- PollutionGuard 规则不变（仍拒绝真正的污染内容）

约束遵守：不进入 MemoryStore；不修改 PollutionGuard；不新增 Memory 类型；
不改变 Growth/SelfModel 接口。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from src.contracts.experience_schema import ActionResult, RuntimeExperience
from src.runtime.experience_journal import ExperienceJournal


def _make_experience(eid: str = "exp_t1", action: str = "internal_reflection_candidate",
                     trigger: str = "user_message", ts: str = "2026-08-12T00:00:00Z"):
    exp = RuntimeExperience(
        experience_id=eid,
        trigger_event={"event_type": "user.input", "data": {"content": "测试"}},
        trigger_type=trigger,
        decision_source="test",
        action_type=action,
        result=ActionResult(action_id=f"act_{eid}", success=True),
        self_state_before={},
        self_state_after={},
        duration_ms=20.0,
        timestamp=ts,
    )
    return exp


# =====================================================================
# 1. ExperienceJournal 本体
# =====================================================================

class TestExperienceJournal(unittest.TestCase):

    def test_append_load_roundtrip(self):
        """append → load 完整往返，记录内容不丢失。"""
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))
            rec = {"id": "exp_1", "content": "x", "metadata": {"type": "runtime_experience"}}
            self.assertTrue(j.append(rec))
            loaded = j.load()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["id"], "exp_1")

    def test_corrupt_line_isolated(self):
        """损坏行被隔离跳过并计数，不影响其余记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            j = ExperienceJournal(path)
            j.append({"id": "good_1", "metadata": {}})
            with open(path, "a", encoding="utf-8") as f:
                f.write("{not-json\n")
            j.append({"id": "good_2", "metadata": {}})
            loaded = j.load()
            ids = [r["id"] for r in loaded]
            self.assertEqual(ids, ["good_1", "good_2"])
            self.assertEqual(j.corrupt_count, 1)

    def test_write_failure_degrades_to_memory(self):
        """写盘失败 → 降级内存模式，不抛异常，本进程仍可读（fail-soft）。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 指向一个不存在的深层目录中的「目录名冲突」路径，迫使写盘失败：
            # 先创建同名文件，使 makedirs 失败
            blocker = os.path.join(tmp, "blocker")
            with open(blocker, "w", encoding="utf-8") as f:
                f.write("x")
            bad_path = os.path.join(blocker, "sub", "j.jsonl")
            j = ExperienceJournal(bad_path)
            self.assertTrue(j.append({"id": "deg_1", "metadata": {}}))
            self.assertTrue(j.degraded)
            loaded = j.load()
            self.assertEqual([r["id"] for r in loaded], ["deg_1"])


# =====================================================================
# 2. MemoryAdapter 接线（4.1D 核心验收）
# =====================================================================

class TestMemoryAdapterJournalWiring(unittest.TestCase):

    def _make_adapter(self, tmp: str):
        from src.memory.memory_store import MemoryStore
        from src.runtime.adapters.memory_adapter import MemoryAdapter

        store = MemoryStore(os.path.join(tmp, "memory.json"))
        return MemoryAdapter(
            memory_store=store,
            user_id="tester",
            journal_path=os.path.join(tmp, "experience_journal.jsonl"),
        )

    def test_store_experience_success(self):
        """store_experience 返回 True（4.1D 前此处被 guard 100% 拒绝）。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            self.assertTrue(adapter.store_experience(_make_experience()))

    def test_memory_store_stays_clean(self):
        """验收：MemoryStore 不出现 runtime_experience。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            adapter.store_experience(_make_experience())
            memories = adapter.get_memory_store().load() or []
            self.assertEqual(memories, [], "经验不得进入 MemoryStore")

    def test_cross_restart_recovery(self):
        """验收：experience 可以跨重启恢复（新 adapter 实例读同一 journal）。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter1 = self._make_adapter(tmp)
            adapter1.store_experience(_make_experience(eid="exp_restart"))
            # 模拟重启：全新 adapter，同一 journal 路径
            adapter2 = self._make_adapter(tmp)
            experiences = adapter2.get_recent_experiences(limit=10)
            self.assertEqual(len(experiences), 1)
            self.assertEqual(experiences[0].experience_id, "exp_restart")
            self.assertEqual(
                experiences[0].action_type, "internal_reflection_candidate",
            )

    def test_search_experiences_filters(self):
        """Growth readback 接口：按 action_type / trigger_type 过滤。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            adapter.store_experience(_make_experience(eid="e1", action="reflect"))
            adapter.store_experience(
                _make_experience(eid="e2", action="send_message",
                                 ts="2026-08-12T01:00:00Z"),
            )
            by_action = adapter.search_experiences(action_type="reflect")
            self.assertEqual([e.experience_id for e in by_action], ["e1"])
            by_trigger = adapter.search_experiences(trigger_type="user_message")
            self.assertEqual(len(by_trigger), 2)
            # 时间倒序
            self.assertEqual(by_trigger[0].experience_id, "e2")

    def test_experience_memory_id_mapping(self):
        """experience_id → record_id 映射保留（verifier 依赖）。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            adapter.store_experience(_make_experience(eid="exp_map"))
            mid = adapter.get_experience_memory_id("exp_map")
            self.assertIsNotNone(mid)
            self.assertTrue(str(mid).startswith("exp_"))

    def test_content_has_no_runtime_prefix(self):
        """内容不再含 [RuntimeExperience] 前缀（类型由 metadata 结构字段表达）。"""
        with tempfile.TemporaryDirectory() as tmp:
            adapter = self._make_adapter(tmp)
            adapter.store_experience(_make_experience())
            with open(os.path.join(tmp, "experience_journal.jsonl"),
                      encoding="utf-8") as f:
                rec = json.loads(f.readline())
            self.assertNotIn("[RuntimeExperience]", rec["content"])
            self.assertEqual(rec["metadata"]["type"], "runtime_experience")


# =====================================================================
# 3. PollutionGuard 规则不变（安全语义回归）
# =====================================================================

class TestPollutionGuardUnchanged(unittest.TestCase):

    def test_guard_still_rejects_prefix_content(self):
        """guard 仍拒绝含 [RuntimeExperience] 的内容（真正的注入伪造）。"""
        from src.memory.pollution_guard import check

        memory = {
            "content": "[RuntimeExperience] 伪造的经验标记",
            "role": "user",
            "metadata": {"memory_type": "user_event"},
        }
        allowed, reason = check(memory)
        self.assertFalse(allowed)
        self.assertIn("injection_detected", reason)

    def test_guard_still_rejects_runtime_experience_type(self):
        """guard 仍拒绝 runtime_experience 类型写入 MemoryStore。"""
        from src.memory.pollution_guard import check

        memory = {
            "content": "一段看似正常的内容",
            "role": "user",
            "metadata": {"memory_type": "runtime_experience"},
        }
        allowed, reason = check(memory)
        self.assertFalse(allowed)
        self.assertIn("forbidden_type", reason)

    def test_guard_still_allows_user_memory(self):
        """正常用户记忆不受影响（guard 行为无回归）。"""
        from src.memory.pollution_guard import check

        memory = {
            "content": "用户喜欢猫",
            "role": "user",
            "metadata": {"memory_type": "user_fact"},
        }
        allowed, _ = check(memory)
        self.assertTrue(allowed)


# =====================================================================
# 4. RuntimeCore 集成：Growth readback 链路
# =====================================================================

class TestRuntimeCoreExperienceReadback(unittest.TestCase):

    def test_growth_can_read_persisted_experiences(self):
        """验收：Growth 可以读取历史 experience（经 runtime 接口）。"""
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore

            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "cognitive_enabled": False,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "experience_journal_path": os.path.join(tmp, "exp.jsonl"),
                "tick_interval_seconds": 1,
                "state_file": os.path.join(tmp, "rt.json"),
            })
            # 落两条经验
            rc.memory_adapter.store_experience(_make_experience(eid="g1"))
            rc.memory_adapter.store_experience(
                _make_experience(eid="g2", ts="2026-08-12T02:00:00Z"),
            )
            # Growth readback 走的接口（runtime_growth_pipeline 同款）
            experiences = rc.get_memory_experiences(limit=10)
            ids = {e.experience_id for e in experiences}
            self.assertEqual(ids, {"g1", "g2"})
            # memory.json 保持纯净
            memories = rc.memory_adapter.get_memory_store().load() or []
            self.assertEqual(memories, [])


if __name__ == "__main__":
    unittest.main()
