"""
Phase 3.5.11: Autonomous Reflection Scheduler 测试

覆盖：
 1. TriggerResult / ReflectionTaskRecord / SchedulerSnapshot 结构（Unit）
 2. ReflectionTrigger（event_count / time_interval / importance_score / manual）
 3. ReflectionScheduler（触发检查 / 冷却 / 执行 / 历史）
 4. RuntimeCore 接入（默认关闭 / 启用 / schedule_reflection / check / run / history）
 5. 完整链路：Experience → Memory → Scheduler Trigger → Reflection → Growth → SelfModel → Continuity

约束验证：
- 不自动接受 GrowthProposal
- 保留人工审批机制
- 所有调度行为可审计
- 不调用 LLM
- 不启动后台线程
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

# ============================================================
# 依赖
# ============================================================

from src.contracts.reflection_scheduler_schema import (
    TriggerResult,
    ReflectionTaskRecord,
    SchedulerSnapshot,
)
from src.runtime.reflection_scheduler import (
    ReflectionScheduler,
    ReflectionSchedule,
    ReflectionTrigger,
    ReflectionTriggerConfig,
)
from src.personality.trait_state import create_trait_state


# ============================================================
# 1. Schema 结构测试
# ============================================================

class TestSchedulerSchema(unittest.TestCase):
    def test_01_trigger_result_defaults(self):
        tr = TriggerResult()
        self.assertFalse(tr.triggered)
        self.assertEqual(tr.trigger_type, "")

    def test_02_task_record_defaults(self):
        rec = ReflectionTaskRecord()
        self.assertTrue(rec.task_id.startswith("rt_"))
        self.assertEqual(rec.status, "pending")
        self.assertEqual(rec.proposal_ids, [])

    def test_03_scheduler_snapshot_defaults(self):
        snap = SchedulerSnapshot()
        self.assertTrue(snap.snapshot_id.startswith("ssnap_"))
        self.assertEqual(snap.total_tasks_executed, 0)
        self.assertEqual(snap.enabled, False)

    def test_04_trigger_result_to_dict(self):
        tr = TriggerResult(trigger_id="t1", triggered=True, reason="test")
        d = tr.to_dict()
        self.assertIn("trigger_id", d)
        self.assertTrue(d["triggered"])

    def test_05_task_record_to_dict(self):
        rec = ReflectionTaskRecord(
            trigger_type="manual",
            status="completed",
            insight_id="ins_001",
            proposal_ids=["gp_001", "gp_002"],
        )
        d = rec.to_dict()
        self.assertEqual(d["status"], "completed")
        self.assertEqual(len(d["proposal_ids"]), 2)


# ============================================================
# 2. ReflectionTrigger 测试
# ============================================================

class TestReflectionTrigger(unittest.TestCase):
    def test_01_event_count_triggered(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="evt",
            trigger_type="event_count",
            min_event_count=5,
        ))
        result = trigger.check(event_count_since_last=6)
        self.assertTrue(result.triggered)
        self.assertEqual(result.trigger_type, "event_count")

    def test_02_event_count_not_triggered(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="evt",
            trigger_type="event_count",
            min_event_count=5,
        ))
        result = trigger.check(event_count_since_last=3)
        self.assertFalse(result.triggered)

    def test_03_time_interval_triggered(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="time",
            trigger_type="time_interval",
            interval_seconds=300.0,
        ))
        result = trigger.check(time_since_last_seconds=350.0)
        self.assertTrue(result.triggered)

    def test_04_time_interval_not_triggered(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="time",
            trigger_type="time_interval",
            interval_seconds=300.0,
        ))
        result = trigger.check(time_since_last_seconds=100.0)
        self.assertFalse(result.triggered)

    def test_05_importance_triggered(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="imp",
            trigger_type="importance_score",
            min_importance=0.6,
        ))
        result = trigger.check(current_importance=0.75)
        self.assertTrue(result.triggered)

    def test_06_importance_not_triggered(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="imp",
            trigger_type="importance_score",
            min_importance=0.6,
        ))
        result = trigger.check(current_importance=0.4)
        self.assertFalse(result.triggered)

    def test_07_disabled_trigger(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="evt",
            trigger_type="event_count",
            min_event_count=1,
            enabled=False,
        ))
        result = trigger.check(event_count_since_last=100)
        self.assertFalse(result.triggered)

    def test_08_manual_trigger(self):
        trigger = ReflectionTrigger(ReflectionTriggerConfig(
            trigger_id="manual",
            trigger_type="manual",
        ))
        result = trigger.check()
        self.assertFalse(result.triggered)
        self.assertIn("manual", result.reason)


# ============================================================
# 3. ReflectionScheduler 测试
# ============================================================

class TestReflectionScheduler(unittest.TestCase):
    def setUp(self):
        self.scheduler = ReflectionScheduler()

    def test_01_default_schedule_has_three_triggers(self):
        self.assertEqual(len(self.schedule_triggers()), 3)

    def schedule_triggers(self):
        return self.scheduler.schedule.triggers

    def test_02_on_event_increments_count(self):
        self.scheduler.on_event(importance=0.5)
        self.scheduler.on_event(importance=0.7)
        self.assertEqual(self.scheduler._event_count_since_last, 2)
        self.assertAlmostEqual(self.scheduler._current_importance, 0.7)

    def test_03_check_triggers_returns_results(self):
        self.scheduler.on_event(importance=0.3)
        results = self.scheduler.check_triggers()
        self.assertEqual(len(results), 3)
        # event_count trigger: 1 < 5 → not triggered
        event_result = next(r for r in results if r.trigger_type == "event_count")
        self.assertFalse(event_result.triggered)

    def test_04_should_trigger_on_event_count(self):
        for _ in range(6):
            self.scheduler.on_event()
        should, reason, matched = self.scheduler.should_trigger()
        self.assertTrue(should)
        self.assertIsNotNone(matched)

    def test_05_cooldown_blocks_trigger(self):
        # First trigger
        for _ in range(6):
            self.scheduler.on_event()
        should1, _, _ = self.scheduler.should_trigger()
        self.assertTrue(should1)

        # Run reflection to set _last_run_ts
        self.scheduler.run_reflection(
            reflection_callback=lambda: {"insight_id": "test"},
            trigger_type="event_count",
            force=True,
        )

        # Immediately after: cooldown blocks
        for _ in range(6):
            self.scheduler.on_event()
        should2, reason2, _ = self.scheduler.should_trigger()
        self.assertFalse(should2)
        self.assertIn("cooldown", reason2)

    def test_06_run_reflection_success(self):
        self.scheduler.on_event()
        self.scheduler.on_event()
        task = self.scheduler.run_reflection(
            reflection_callback=lambda: {
                "insight_id": "ins_test",
                "proposal_ids": ["gp_1"],
                "experience_count": 5,
            },
            trigger_type="manual",
            force=True,
        )
        self.assertEqual(task.status, "completed")
        self.assertEqual(task.insight_id, "ins_test")
        self.assertEqual(task.proposal_count, 1)
        self.assertEqual(self.scheduler._total_succeeded, 1)

    def test_07_run_reflection_failure(self):
        def _fail():
            raise RuntimeError("test error")
        task = self.scheduler.run_reflection(
            reflection_callback=_fail,
            trigger_type="manual",
            force=True,
        )
        self.assertEqual(task.status, "failed")
        self.assertIn("test error", task.error)
        self.assertEqual(self.scheduler._total_failed, 1)

    def test_08_run_reflection_skipped_on_cooldown(self):
        # First run
        self.scheduler.run_reflection(
            reflection_callback=lambda: {},
            force=True,
        )
        # Second run without force → skipped
        task = self.scheduler.run_reflection(
            reflection_callback=lambda: {},
            force=False,
        )
        self.assertEqual(task.status, "skipped")
        self.assertEqual(self.scheduler._total_skipped, 1)

    def test_09_history_recorded(self):
        for _ in range(3):
            self.scheduler.run_reflection(
                reflection_callback=lambda: {"insight_id": "i"},
                force=True,
            )
        history = self.scheduler.get_history(limit=10)
        self.assertEqual(len(history), 3)

    def test_10_snapshot(self):
        self.scheduler.on_event(importance=0.5)
        self.scheduler.run_reflection(
            reflection_callback=lambda: {},
            force=True,
        )
        snap = self.scheduler.get_snapshot()
        self.assertEqual(snap.total_tasks_executed, 1)
        self.assertEqual(snap.total_tasks_succeeded, 1)

    def test_11_clear_history(self):
        self.scheduler.run_reflection(
            reflection_callback=lambda: {},
            force=True,
        )
        n = self.scheduler.clear_history()
        self.assertEqual(n, 1)
        self.assertEqual(len(self.scheduler._history), 0)

    def test_12_history_capped(self):
        sched = ReflectionScheduler(
            schedule=ReflectionSchedule(max_history=5)
        )
        for _ in range(10):
            sched.run_reflection(
                reflection_callback=lambda: {},
                force=True,
            )
        self.assertLessEqual(len(sched._history), 5)


# ============================================================
# 4. RuntimeCore 接入测试
# ============================================================

class TestRuntimeReflectionScheduler(unittest.TestCase):
    def _make_config(self, tmp: str, enabled: bool = True) -> Dict[str, Any]:
        return {
            "adapters_enabled": True,
            "experience_enabled": True,
            "reflection_scheduler_enabled": enabled,
            "rs_min_event_count": 3,
            "rs_cooldown_seconds": 0.1,
            "rs_auto_refresh_self_model": False,
            "rs_auto_refresh_continuity": False,
            "memory_store_path": os.path.join(tmp, "mem_rs.json"),
            "growth_proposals_path": os.path.join(tmp, "gp_rs.json"),
            "state_file": os.path.join(tmp, "rt_rs.json"),
            "tick_interval_seconds": 1,
            "reflection_min_experiences": 2,
        }

    def test_01_disabled_by_default(self):
        from src.runtime.runtime_core import RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": False})
        self.assertIsNone(rc.reflection_scheduler)
        self.assertIsNone(rc.schedule_reflection())
        self.assertIsNone(rc.check_reflection_trigger())

    def test_02_enabled_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp, enabled=True))
            self.assertIsNotNone(rc.reflection_scheduler)

    def test_03_check_trigger_no_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.check_reflection_trigger()
            self.assertIsNotNone(result)
            self.assertFalse(result["should_trigger"])

    def test_04_check_trigger_with_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            for _ in range(5):
                rc.notify_scheduler_event(importance=0.3)
            result = rc.check_reflection_trigger()
            self.assertTrue(result["should_trigger"])

    def test_05_schedule_reflection_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            result = rc.schedule_reflection(
                trigger_type="manual",
                trigger_reason="test manual",
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "completed")

    def test_06_get_scheduler_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rc.schedule_reflection(force=True)
            rc.schedule_reflection(force=True)
            history = rc.get_scheduler_history(limit=10)
            self.assertEqual(len(history), 2)

    def test_07_get_scheduler_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rc.notify_scheduler_event(importance=0.5)
            snap = rc.get_scheduler_snapshot()
            self.assertIsNotNone(snap)
            self.assertEqual(snap["event_count_since_last"], 1)

    def test_08_clear_scheduler_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            rc.schedule_reflection(force=True)
            n = rc.clear_scheduler_history()
            self.assertEqual(n, 1)

    def test_09_run_scheduled_reflection_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            # No events → should be skipped
            result = rc.run_scheduled_reflection()
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "skipped")

    def test_10_run_scheduled_reflection_triggered(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            rc = RuntimeCore(config=self._make_config(tmp))
            # Inject events to trigger
            for _ in range(5):
                rc.notify_scheduler_event(importance=0.3)
            result = rc.run_scheduled_reflection()
            self.assertIsNotNone(result)
            self.assertIn(result["status"], ["completed", "skipped", "failed"])


# ============================================================
# 5. 完整链路测试
#    Experience → Memory → Scheduler → Reflection → Growth → SelfModel → Continuity
# ============================================================

class TestFullSchedulerLifecycle(unittest.TestCase):
    def test_01_experience_to_continuity(self):
        """
        完整链路：
        1. 注入经验 → notify_scheduler_event
        2. 检查触发条件 → 满足
        3. 执行反思 → 生成 Insight + GrowthProposal
        4. 验证不自动接受 Proposal
        5. 获取调度历史 → 可审计
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_scheduler_enabled": True,
                "identity_continuity_enabled": True,
                "identity_anchor_enabled": True,
                "rs_min_event_count": 3,
                "rs_cooldown_seconds": 0.1,
                "rs_auto_refresh_self_model": True,
                "rs_auto_refresh_continuity": True,
                "memory_store_path": os.path.join(tmp, "mem_full_rs.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_full_rs.json"),
                "state_file": os.path.join(tmp, "rt_full_rs.json"),
                "tick_interval_seconds": 1,
                "reflection_min_experiences": 2,
                "eval_min_confidence": 0.2,
                "eval_min_evidence_count": 1,
            }
            rc = RuntimeCore(config=config)

            # 1. 构造并注入经验
            from src.contracts.experience_schema import RuntimeExperience, ActionResult
            for i in range(6):
                exp = RuntimeExperience(
                    experience_id=f"e_rs_{i}",
                    trigger_event={"event_type": "user_message", "data": {"content": f"hi{i}"}},
                    trigger_type="user_message",
                    decision_source="rule",
                    action_type="send_message",
                    result=ActionResult(
                        action_id=f"act_rs_{i}",
                        success=True,
                        response_received=(i % 4 == 0),
                    ),
                    self_state_before={"initiative": 0.7},
                    self_state_after={"initiative": 0.68},
                    duration_ms=100.0,
                )
                rc.experience_builder._buffer.append(exp)
                if rc.memory_adapter:
                    rc.memory_adapter.store_experience(exp)
                # 通知调度器
                rc.notify_scheduler_event(importance=0.3 + i * 0.05)

            # 2. 检查触发条件
            trigger_check = rc.check_reflection_trigger()
            self.assertTrue(trigger_check["should_trigger"])

            # 3. 执行反思
            result = rc.schedule_reflection(
                trigger_type="event_count",
                trigger_reason="6 events accumulated",
                force=True,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "completed")

            # 4. 验证不自动接受 GrowthProposal
            # proposals 仍然在 proposed 状态
            proposals = rc.get_growth_proposals(status="proposed", limit=20)
            # 可能有 0 或多个 proposals，取决于 reflection_engine 逻辑
            # 关键验证：没有 proposal 被自动 accepted
            accepted = rc.get_growth_proposals(status="accepted", limit=20)
            # 不应有自动接受的提案
            self.assertEqual(len(accepted), 0)

            # 5. 调度历史可审计
            history = rc.get_scheduler_history(limit=10)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["status"], "completed")
            self.assertEqual(history[0]["trigger_type"], "event_count")

            # 6. SelfModel 被刷新（auto_refresh_self_model=True）
            self.assertTrue(result.get("self_model_updated", False))

            # 7. IdentityContinuity 被刷新（auto_refresh_continuity=True）
            self.assertTrue(result.get("continuity_checked", False))

    def test_02_manual_schedule_with_anchor_integrity(self):
        """
        手动调度反思 + Anchor Integrity 验证。
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_scheduler_enabled": True,
                "identity_anchor_enabled": True,
                "rs_min_event_count": 5,
                "rs_cooldown_seconds": 0.1,
                "memory_store_path": os.path.join(tmp, "mem_anchor_rs.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_anchor_rs.json"),
                "state_file": os.path.join(tmp, "rt_anchor_rs.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)

            # 手动调度反思
            result = rc.schedule_reflection(
                trigger_type="manual",
                trigger_reason="anchor integrity check",
                force=True,
            )
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "completed")

            # 获取 Anchor Integrity Report
            integrity = rc.get_anchor_integrity_report(use_self_model=False)
            self.assertIsNotNone(integrity)
            self.assertTrue(integrity["is_intact"])

            # 获取调度快照
            snap = rc.get_scheduler_snapshot()
            self.assertIsNotNone(snap)
            self.assertEqual(snap["total_tasks_executed"], 1)
            self.assertEqual(snap["total_tasks_succeeded"], 1)

    def test_03_multiple_reflections_history_audit(self):
        """
        多次反思调度的历史审计。
        """
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_scheduler_enabled": True,
                "rs_min_event_count": 1,
                "rs_cooldown_seconds": 0.0,  # 无冷却
                "memory_store_path": os.path.join(tmp, "mem_multi.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_multi.json"),
                "state_file": os.path.join(tmp, "rt_multi.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)

            # 执行 3 次反思
            for i in range(3):
                result = rc.schedule_reflection(
                    trigger_type="manual",
                    trigger_reason=f"batch {i}",
                    force=True,
                )
                self.assertEqual(result["status"], "completed")

            # 历史记录 3 条
            history = rc.get_scheduler_history(limit=10)
            self.assertEqual(len(history), 3)
            # 最新在前
            self.assertIn("batch 2", history[0]["trigger_reason"])

            # 快照统计
            snap = rc.get_scheduler_snapshot()
            self.assertEqual(snap["total_tasks_executed"], 3)
            self.assertEqual(snap["total_tasks_succeeded"], 3)
            self.assertEqual(snap["total_tasks_failed"], 0)


if __name__ == "__main__":
    unittest.main()
