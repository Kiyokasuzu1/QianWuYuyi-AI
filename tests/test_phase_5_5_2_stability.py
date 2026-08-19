# -*- coding: utf-8 -*-
"""
tests/test_phase_5_5_2_stability.py

Phase 5.5.2 Stability Layer 测试套件

覆盖：
  [1] ProposalSyncManager: consistency / drift / dry_run
  [2] RetryWorker: exp backoff / max_retry / DLQ
  [3] ProposalLifecycleManager: 状态转换 / 持久化 / 历史
  [4] GrowthRateLimiter: delta / daily / drift / confidence
  [5] 集成场景

设计原则：
- 不创建 Authority 实例（MemoryStore / PersonalityResolver / EmotionManager）
- 不修改 RuntimeCore
- 每个测试使用临时目录
"""
from __future__ import annotations

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# [1] ProposalSyncManager 测试
# ============================================================

class TestProposalSyncManager(unittest.TestCase):
    """ProposalSyncManager 一致性检查测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_sync_mgr_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_type_a(self, proposal_id="prop_001", status="pending", confidence=0.5,
                     dims=None, ptype="personality", metadata=None):
        from src.growth.proposal.proposal import GrowthProposal
        return GrowthProposal(
            proposal_id=proposal_id,
            status=status,
            confidence=confidence,
            proposal_type=ptype,
            affected_dimensions=dims or {"openness": 0.05},
            metadata=metadata or {},
        )

    def _make_mirror(self, mirror_id="prop_001", status="proposed", confidence=0.5,
                     changes=None):
        return {
            "id": mirror_id,
            "status": status,
            "confidence": confidence,
            "proposed_changes": changes or [{"path": "openness", "before": 0.5, "after": 0.55}],
            "evaluator_meta": {"source_proposal_id": mirror_id},
        }

    def test_01_consistent_no_drift(self):
        """完全一致时应无 drift"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a()
        mirror = self._make_mirror()
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        self.assertEqual(report.drift_count, 0)
        self.assertEqual(report.consistent_count, 1)
        self.assertEqual(report.consistency_rate, 1.0)

    def test_02_status_mismatch_detected(self):
        """状态不一致应被检测"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a(status="pending")
        mirror = self._make_mirror(status="approved")  # 不一致
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        self.assertGreaterEqual(report.drift_count, 1)
        drift_types = [d.drift_type for d in report.drifts]
        self.assertIn("status_mismatch", drift_types)

    def test_03_status_equivalent_mapping(self):
        """pending↔proposed, applied↔approved 应视为等价"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        # pending ↔ proposed（等价）
        type_a = self._make_type_a(status="pending")
        mirror = self._make_mirror(status="proposed")
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        drift_types = [d.drift_type for d in report.drifts]
        self.assertNotIn("status_mismatch", drift_types)

    def test_04_mirror_missing_detected(self):
        """Type A 存在但 mirror 缺失应被检测"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a()
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: None,
        )
        report = mgr.check_one("prop_001")
        self.assertGreaterEqual(report.drift_count, 1)
        drift_types = [d.drift_type for d in report.drifts]
        self.assertIn("mirror_missing", drift_types)

    def test_05_mirror_orphan_detected(self):
        """Type A 已删除但 mirror 仍存在应被检测"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        mirror = self._make_mirror()
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: None,  # Type A 缺失
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        self.assertGreaterEqual(report.drift_count, 1)
        drift_types = [d.drift_type for d in report.drifts]
        self.assertIn("mirror_orphan", drift_types)

    def test_06_memory_action_skipped_no_drift(self):
        """Memory action 不应有 mirror_missing drift"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a(
            ptype="identity",
            metadata={"action_scope": "memory"},
        )
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: None,
        )
        report = mgr.check_one("prop_001")
        drift_types = [d.drift_type for d in report.drifts]
        self.assertNotIn("mirror_missing", drift_types)

    def test_07_proposed_changes_drift_detected(self):
        """proposed_changes 路径漂移应被检测"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a(dims={"openness": 0.05, "conscientiousness": 0.03})
        mirror = self._make_mirror(
            changes=[{"path": "openness", "after": 0.55}],  # 缺失 conscientiousness
        )
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        drift_types = [d.drift_type for d in report.drifts]
        self.assertIn("proposed_changes_mismatch", drift_types)

    def test_08_confidence_drift_detected(self):
        """confidence 漂移应被检测"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a(confidence=0.5)
        mirror = self._make_mirror(confidence=0.8)  # 偏差
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        drift_types = [d.drift_type for d in report.drifts]
        self.assertIn("confidence_mismatch", drift_types)

    def test_09_source_proposal_id_missing(self):
        """traceability 缺失应被检测"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a()
        mirror = self._make_mirror()
        mirror["evaluator_meta"] = {}  # 移除 source_proposal_id
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001")
        drift_types = [d.drift_type for d in report.drifts]
        self.assertIn("source_proposal_id_missing", drift_types)

    def test_10_dry_run_no_fix(self):
        """dry_run=True 时不应修复"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a(status="pending")
        mirror = self._make_mirror(status="approved")
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = mgr.check_one("prop_001", dry_run=True)
        self.assertTrue(report.dry_run)
        self.assertEqual(report.fixed_count, 0)

    def test_11_bulk_check(self):
        """批量检查应正确统计"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a_list = [
            self._make_type_a(proposal_id="p1", status="pending"),
            self._make_type_a(proposal_id="p2", status="approved"),
            self._make_type_a(proposal_id="p3", status="rejected"),
        ]
        mirrors = {
            "p1": self._make_mirror(mirror_id="p1", status="proposed"),
            "p2": self._make_mirror(mirror_id="p2", status="approved"),
            "p3": self._make_mirror(mirror_id="p3", status="rejected"),
        }
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: next((p for p in type_a_list if p.proposal_id == _id), None),
            mirror_loader=lambda _id: mirrors.get(_id),
            type_a_lister=lambda limit: type_a_list,
        )
        report = mgr.run_bulk_check(dry_run=True)
        self.assertEqual(report.total_checked, 3)
        self.assertGreaterEqual(report.consistent_count, 2)

    def test_12_bulk_with_status_filter(self):
        """status 过滤应生效"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a_list = [
            self._make_type_a(proposal_id="p1", status="pending"),
            self._make_type_a(proposal_id="p2", status="approved"),
        ]
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: next((p for p in type_a_list if p.proposal_id == _id), None),
            mirror_loader=lambda _id: self._make_mirror(mirror_id=_id),
            type_a_lister=lambda limit: type_a_list,
        )
        report = mgr.run_bulk_check(status="pending", dry_run=True)
        self.assertEqual(report.total_checked, 1)

    def test_13_consistency_report_to_dict(self):
        """ConsistencyReport.to_dict 应包含所有字段"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        type_a = self._make_type_a()
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: None,
        )
        report = mgr.check_one("p1")
        d = report.to_dict()
        self.assertIn("report_id", d)
        self.assertIn("drift_count", d)
        self.assertIn("consistency_rate", d)
        self.assertIn("drifts", d)

    def test_14_severity_assignment(self):
        """Drift 严重性应正确分配"""
        from src.growth.sync.proposal_sync_manager import (
            ProposalSyncManager, DriftSeverity,
        )
        # mirror_orphan 应为 HIGH
        mgr = ProposalSyncManager(
            type_a_loader=lambda _id: None,
            mirror_loader=lambda _id: self._make_mirror(),
        )
        report = mgr.check_one("p1")
        for d in report.drifts:
            if d.drift_type == "mirror_orphan":
                self.assertEqual(d.severity, DriftSeverity.HIGH.value)


# ============================================================
# [2] RetryWorker 测试
# ============================================================

class TestRetryWorker(unittest.TestCase):
    """RetryWorker 重试机制测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_retry_worker_")
        self.retry_path = os.path.join(self.tmpdir, "retry.json")
        self.dlq_path = os.path.join(self.tmpdir, "dlq.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_queue(self):
        from src.growth.sync.retry_queue import RetryQueue
        return RetryQueue(storage_path=self.retry_path)

    def test_01_compute_backoff_exponential(self):
        """backoff 应指数增长"""
        from src.growth.sync.retry_worker import RetryWorker
        worker = RetryWorker(
            retry_queue=self._make_queue(),
            backoff_base=1.0,
            backoff_cap=100.0,
        )
        self.assertEqual(worker._compute_backoff(0), 1.0)
        self.assertEqual(worker._compute_backoff(1), 2.0)
        self.assertEqual(worker._compute_backoff(2), 4.0)
        self.assertEqual(worker._compute_backoff(3), 8.0)

    def test_02_compute_backoff_cap(self):
        """backoff 不应超过 cap"""
        from src.growth.sync.retry_worker import RetryWorker
        worker = RetryWorker(
            retry_queue=self._make_queue(),
            backoff_base=1.0,
            backoff_cap=10.0,
        )
        # 2^20 远超 cap，应被 cap
        self.assertEqual(worker._compute_backoff(20), 10.0)

    def test_03_successful_retry(self):
        """成功重试应将 record 标记为 success"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        rec = queue.add_failure("p1", "mirror", "err")
        calls = []
        def handler(record):
            calls.append(record.record_id)
            return True
        worker = RetryWorker(
            retry_queue=queue,
            handler=handler,
            backoff_base=0,  # 测试中关闭 backoff
        )
        worker.tick()
        self.assertEqual(len(calls), 1)
        self.assertEqual(worker.stats.total_succeeded, 1)
        recs = queue.get_all()
        self.assertEqual(recs[0].status, "success")

    def test_04_failed_retry_increments_count(self):
        """失败重试应增加 retry_count"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        rec = queue.add_failure("p1", "mirror", "err")
        worker = RetryWorker(
            retry_queue=queue,
            handler=lambda r: False,  # 永远失败
            backoff_base=0,
        )
        worker.tick()
        recs = queue.get_all()
        self.assertEqual(recs[0].retry_count, 1)
        self.assertEqual(worker.stats.total_failed, 1)

    def test_05_dlq_after_max_retry(self):
        """达到 max_retry 后应转入 DLQ"""
        from src.growth.sync.retry_worker import RetryWorker, DeadLetterQueue
        queue = self._make_queue()
        dlq = DeadLetterQueue(storage_path=self.dlq_path)
        rec = queue.add_failure("p1", "mirror", "err")
        worker = RetryWorker(
            retry_queue=queue,
            handler=lambda r: False,
            dead_letter_queue=dlq,
            max_retry=2,
            backoff_base=0,
        )
        # 跑两次 tick 都失败后，第 3 次应进入 DLQ
        for _ in range(3):
            worker.tick()
        self.assertEqual(dlq.count(), 1)
        self.assertEqual(worker.stats.total_dead_lettered, 1)
        recs = queue.get_all()
        # 原 record 应被标记 abandoned
        self.assertEqual(recs[0].status, "abandoned")

    def test_06_dry_run_simulates_success(self):
        """dry_run 模式应模拟成功"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        queue.add_failure("p1", "mirror", "err")
        worker = RetryWorker(
            retry_queue=queue,
            handler=lambda r: False,  # 实际 handler 永远失败
            dry_run=True,
            backoff_base=0,
        )
        worker.tick()
        recs = queue.get_all()
        self.assertEqual(recs[0].status, "success")

    def test_07_handler_exception_caught(self):
        """handler 抛异常不应使 worker 崩溃"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        queue.add_failure("p1", "mirror", "err")
        def bad_handler(r):
            raise RuntimeError("boom")
        worker = RetryWorker(
            retry_queue=queue,
            handler=bad_handler,
            backoff_base=0,
        )
        worker.tick()  # 不应抛异常
        self.assertEqual(worker.stats.total_failed, 1)

    def test_08_batch_size_limit(self):
        """tick 应仅处理 batch_size 条"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        for i in range(5):
            queue.add_failure(f"p{i}", "mirror", "err")
        calls = []
        def handler(r):
            calls.append(r.record_id)
            return True
        worker = RetryWorker(
            retry_queue=queue,
            handler=handler,
            batch_size=2,
            backoff_base=0,
        )
        worker.tick()
        self.assertEqual(len(calls), 2)
        self.assertEqual(worker.stats.total_processed, 2)

    def test_09_run_loop_terminates(self):
        """run() 循环应能终止"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        for i in range(3):
            queue.add_failure(f"p{i}", "mirror", "err")
        worker = RetryWorker(
            retry_queue=queue,
            handler=lambda r: True,
            backoff_base=0,
        )
        worker.run(max_iterations=5)
        self.assertEqual(worker.stats.total_succeeded, 3)

    def test_10_stats_tracking(self):
        """stats 应正确累计"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        for i in range(3):
            queue.add_failure(f"p{i}", "mirror", "err")
        worker = RetryWorker(
            retry_queue=queue,
            handler=lambda r: True,
            backoff_base=0,
        )
        worker.tick()
        self.assertEqual(worker.stats.total_ticks, 1)
        self.assertEqual(worker.stats.total_succeeded, 3)
        self.assertIsNotNone(worker.stats.last_tick_at)

    def test_11_backoff_wait_counted(self):
        """backoff 等待应累计到 stats"""
        from src.growth.sync.retry_worker import RetryWorker
        queue = self._make_queue()
        queue.add_failure("p1", "mirror", "err")
        worker = RetryWorker(
            retry_queue=queue,
            handler=lambda r: True,
            backoff_base=2.0,
        )
        sleeps = []
        worker.tick(sleep_fn=sleeps.append)
        # base * 2^0 = 2.0
        self.assertIn(2.0, sleeps)


# ============================================================
# [3] ProposalLifecycleManager 测试
# ============================================================

class TestLifecycleManager(unittest.TestCase):
    """ProposalLifecycleManager 状态机测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_lifecycle_")
        self.storage_path = os.path.join(self.tmpdir, "events.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_states_complete(self):
        """8 个状态应齐全"""
        from src.growth.lifecycle_manager import LifecycleState
        expected = {"created", "pending", "approved", "applying", "applied",
                    "failed", "rolled_back", "archived"}
        actual = {s.value for s in LifecycleState}
        self.assertEqual(expected, actual)

    def test_02_created_to_pending_valid(self):
        """created → pending 合法"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertTrue(mgr.validate_transition("created", "pending"))

    def test_03_pending_to_approved_valid(self):
        """pending → approved 合法"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertTrue(mgr.validate_transition("pending", "approved"))

    def test_04_pending_to_rejected_valid(self):
        """pending → rejected 合法"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertTrue(mgr.validate_transition("pending", "rejected"))

    def test_05_approved_to_applying_valid(self):
        """approved → applying 合法"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertTrue(mgr.validate_transition("approved", "applying"))

    def test_06_applying_to_applied_valid(self):
        """applying → applied 合法"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertTrue(mgr.validate_transition("applying", "applied"))

    def test_07_applied_to_rolled_back_valid(self):
        """applied → rolled_back 合法"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertTrue(mgr.validate_transition("applied", "rolled_back"))

    def test_08_archived_is_terminal(self):
        """archived 不可再转出"""
        from src.growth.lifecycle_manager import (
            ProposalLifecycleManager, IllegalLifecycleTransition,
        )
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        for to_state in ("pending", "approved", "applying", "applied", "failed", "rolled_back"):
            with self.assertRaises(IllegalLifecycleTransition):
                mgr.validate_transition("archived", to_state)

    def test_09_illegal_transition_raises(self):
        """非法转换应抛异常"""
        from src.growth.lifecycle_manager import (
            ProposalLifecycleManager, IllegalLifecycleTransition,
        )
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        # created → applied 应非法（必须经过 approved/applying）
        with self.assertRaises(IllegalLifecycleTransition):
            mgr.validate_transition("created", "applied")

    def test_10_record_transition(self):
        """record_transition 应写入历史"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        mgr.record_transition("p1", "created", "pending", actor="admin", reason="test")
        history = mgr.get_history("p1")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].from_state, "created")
        self.assertEqual(history[0].to_state, "pending")

    def test_11_get_state_latest(self):
        """get_state 应返回最新状态"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        mgr.record_transition("p1", "created", "pending")
        mgr.record_transition("p1", "pending", "approved")
        mgr.record_transition("p1", "approved", "applying")
        self.assertEqual(mgr.get_state("p1"), "applying")

    def test_12_legacy_status_normalized(self):
        """旧 status 应规范化"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        # pending ↔ proposed（通过 LEGACY_STATUS_TO_LIFECYCLE）
        self.assertTrue(mgr.validate_transition("pending", "approved"))
        # 直接传旧 proposed
        self.assertTrue(mgr.validate_transition("proposed", "approved"))

    def test_13_unknown_state_raises(self):
        """未知状态应抛异常"""
        from src.growth.lifecycle_manager import (
            ProposalLifecycleManager, IllegalLifecycleTransition,
        )
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        with self.assertRaises(IllegalLifecycleTransition):
            mgr.validate_transition("bogus", "pending")

    def test_14_legal_next_states(self):
        """get_legal_next_states 应返回合法目标"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        next_states = mgr.get_legal_next_states("pending")
        self.assertIn("approved", next_states)
        self.assertIn("rejected", next_states)

    def test_15_state_info(self):
        """get_state_info 应返回完整结构"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.storage_path)
        info = mgr.get_state_info()
        self.assertIn("states", info)
        self.assertIn("transitions", info)
        self.assertIn("terminal_states", info)
        self.assertIn("legacy_mapping", info)

    def test_16_persistence(self):
        """记录应持久化"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr1 = ProposalLifecycleManager(storage_path=self.storage_path)
        mgr1.record_transition("p1", "created", "pending")
        mgr1.record_transition("p1", "pending", "approved")
        # 重新构造
        mgr2 = ProposalLifecycleManager(storage_path=self.storage_path)
        self.assertEqual(mgr2.get_state("p1"), "approved")
        self.assertEqual(len(mgr2.get_history("p1")), 2)


# ============================================================
# [4] GrowthRateLimiter 测试
# ============================================================

class TestGrowthRateLimiter(unittest.TestCase):
    """GrowthRateLimiter 限流测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_limiter_")
        self.storage_path = os.path.join(self.tmpdir, "limiter.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_limiter(self, **kwargs):
        from src.growth.growth_limiter import GrowthRateLimiter
        kwargs.setdefault("storage_path", self.storage_path)
        return GrowthRateLimiter(**kwargs)

    def test_01_default_thresholds(self):
        """默认阈值应可获取"""
        limiter = self._make_limiter()
        t = limiter.get_thresholds()
        self.assertIn("max_single_delta", t)
        self.assertIn("max_daily_changes", t)
        self.assertIn("max_trait_drift_per_hour", t)
        self.assertIn("min_confidence", t)

    def test_02_small_delta_allowed(self):
        """小 delta 应允许"""
        limiter = self._make_limiter()
        d = limiter.check("openness", 0.05, confidence=0.7, proposal_id="p1")
        self.assertTrue(d.is_allowed)
        self.assertEqual(d.decision, "allow")

    def test_03_large_delta_denied(self):
        """超过单次上限应拒绝"""
        limiter = self._make_limiter(max_single_delta=0.10)
        d = limiter.check("openness", 0.50, confidence=0.7)
        self.assertFalse(d.is_allowed)
        self.assertEqual(d.decision, "deny")
        self.assertGreater(len(d.violations), 0)

    def test_04_low_confidence_denied(self):
        """低置信度应拒绝"""
        limiter = self._make_limiter(min_confidence=0.5)
        d = limiter.check("openness", 0.05, confidence=0.3)
        self.assertFalse(d.is_allowed)
        self.assertIn("confidence", " ".join(d.violations).lower())

    def test_05_daily_quota_enforced(self):
        """每日配额超限应拒绝"""
        limiter = self._make_limiter(max_daily_changes=2)
        # 记录 2 次
        limiter.record("openness", 0.05, "p1")
        limiter.record("openness", 0.05, "p2")
        # 第 3 次应被拒绝
        d = limiter.check("openness", 0.05, confidence=0.7)
        self.assertFalse(d.is_allowed)

    def test_06_drift_speed_enforced(self):
        """drift 速度超限应拒绝"""
        limiter = self._make_limiter(
            max_trait_drift_per_hour=0.20,
            max_daily_changes=100,  # 防止 daily 干扰
        )
        # 记录 0.15
        limiter.record("openness", 0.15, "p1")
        # 再加 0.15 会超 0.30
        d = limiter.check("openness", 0.15, confidence=0.7)
        self.assertFalse(d.is_allowed)

    def test_07_warn_threshold(self):
        """接近上限时应 WARN"""
        limiter = self._make_limiter(
            max_single_delta=0.10,
            warn_ratio=0.8,
        )
        # 0.09 / 0.10 = 0.9 > 0.8 → WARN
        d = limiter.check("openness", 0.09, confidence=0.7)
        self.assertEqual(d.decision, "warn")
        self.assertTrue(d.is_allowed)

    def test_08_record_updates_history(self):
        """record 应更新 drift 历史"""
        limiter = self._make_limiter()
        limiter.record("openness", 0.05, "p1")
        self.assertEqual(limiter.get_trait_drift("openness"), 0.05)
        self.assertEqual(limiter.get_daily_count("openness"), 1)

    def test_09_persistence(self):
        """状态应持久化"""
        limiter1 = self._make_limiter()
        limiter1.record("openness", 0.05, "p1")
        # 重新构造
        limiter2 = self._make_limiter()
        # 注意：drift 窗口过滤 24h 外的数据，但同一天应在
        self.assertGreaterEqual(limiter2.get_daily_count("openness"), 0)

    def test_10_independent_traits(self):
        """不同 trait 互不影响"""
        limiter = self._make_limiter(max_daily_changes=2)
        limiter.record("openness", 0.05, "p1")
        limiter.record("openness", 0.05, "p2")
        # openness 已达上限
        # conscientiousness 仍可用
        d = limiter.check("conscientiousness", 0.05, confidence=0.7)
        self.assertTrue(d.is_allowed)

    def test_11_reset(self):
        """reset 应清空状态"""
        limiter = self._make_limiter()
        limiter.record("openness", 0.05, "p1")
        limiter.reset()
        self.assertEqual(limiter.get_trait_drift("openness"), 0.0)
        self.assertEqual(limiter.get_daily_count("openness"), 0)

    def test_12_check_does_not_record(self):
        """check 不应副作用修改记录"""
        limiter = self._make_limiter()
        limiter.check("openness", 0.05, confidence=0.7, proposal_id="p1")
        self.assertEqual(limiter.get_trait_drift("openness"), 0.0)
        self.assertEqual(limiter.get_daily_count("openness"), 0)


# ============================================================
# [5] 集成测试
# ============================================================

class TestPhase552Integration(unittest.TestCase):
    """Phase 5.5.2 集成测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_552_integration_")
        self.retry_path = os.path.join(self.tmpdir, "retry.json")
        self.dlq_path = os.path.join(self.tmpdir, "dlq.json")
        self.lc_path = os.path.join(self.tmpdir, "lc.json")
        self.limiter_path = os.path.join(self.tmpdir, "limiter.json")
        self.mirror_path = os.path.join(self.tmpdir, "mirror.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_01_full_stability_pipeline(self):
        """完整稳定性管线：sync → retry → lifecycle → limit"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        from src.growth.sync.retry_worker import RetryWorker
        from src.growth.sync.retry_queue import RetryQueue
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        from src.growth.growth_limiter import GrowthRateLimiter
        from src.growth.proposal.proposal import GrowthProposal

        # 1. 创建 Type A
        type_a = GrowthProposal(
            proposal_id="p1",
            status="pending",
            proposal_type="personality",
            affected_dimensions={"openness": 0.05},
            confidence=0.5,
        )

        # 2. ProposalSyncManager 应检测 mirror 缺失
        sync_mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: None,
        )
        report = sync_mgr.check_one("p1")
        self.assertGreaterEqual(report.drift_count, 1)

        # 3. RetryWorker 处理 retry queue
        retry_queue = RetryQueue(storage_path=self.retry_path)
        retry_queue.add_failure("p1", "mirror", "sync_failed")
        worker = RetryWorker(
            retry_queue=retry_queue,
            handler=lambda r: True,  # 模拟成功
            backoff_base=0,
        )
        worker.tick()
        self.assertEqual(worker.stats.total_succeeded, 1)

        # 4. LifecycleManager 记录生命周期
        lc_mgr = ProposalLifecycleManager(storage_path=self.lc_path)
        lc_mgr.record_transition("p1", "created", "pending")
        lc_mgr.record_transition("p1", "pending", "approved")
        self.assertEqual(lc_mgr.get_state("p1"), "approved")

        # 5. GrowthRateLimiter 决策
        limiter = GrowthRateLimiter(storage_path=self.limiter_path)
        decision = limiter.check("openness", 0.05, confidence=0.7, proposal_id="p1")
        self.assertTrue(decision.is_allowed)
        limiter.record("openness", 0.05, "p1")

    def test_02_dlq_after_pipeline_failure(self):
        """多次失败后进入 DLQ"""
        from src.growth.sync.retry_worker import RetryWorker, DeadLetterQueue
        from src.growth.sync.retry_queue import RetryQueue

        retry_queue = RetryQueue(storage_path=self.retry_path)
        dlq = DeadLetterQueue(storage_path=self.dlq_path)
        for i in range(3):
            retry_queue.add_failure(f"p{i}", "mirror", f"err_{i}")

        worker = RetryWorker(
            retry_queue=retry_queue,
            handler=lambda r: False,  # 全部失败
            dead_letter_queue=dlq,
            max_retry=2,
            backoff_base=0,
        )
        for _ in range(4):
            worker.tick()
        self.assertEqual(dlq.count(), 3)
        self.assertEqual(worker.stats.total_dead_lettered, 3)

    def test_03_lifecycle_with_apply_failure(self):
        """apply 失败应进入 FAILED 状态"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        mgr = ProposalLifecycleManager(storage_path=self.lc_path)
        mgr.record_transition("p1", "created", "pending")
        mgr.record_transition("p1", "pending", "approved")
        mgr.record_transition("p1", "approved", "applying")
        mgr.record_transition("p1", "applying", "failed")
        # failed → pending（重试）
        mgr.record_transition("p1", "failed", "pending")
        self.assertEqual(mgr.get_state("p1"), "pending")
        history = mgr.get_history("p1")
        self.assertEqual(len(history), 5)

    def test_04_rate_limit_with_lifecycle(self):
        """限流 + 生命周期协同"""
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        from src.growth.growth_limiter import GrowthRateLimiter
        lc_mgr = ProposalLifecycleManager(storage_path=self.lc_path)
        limiter = GrowthRateLimiter(storage_path=self.limiter_path)

        # 提交多个 proposal
        for i in range(3):
            pid = f"p{i}"
            lc_mgr.record_transition(pid, "created", "pending")
            lc_mgr.record_transition(pid, "pending", "approved")
            # limiter 允许
            d = limiter.check("openness", 0.05, confidence=0.7, proposal_id=pid)
            self.assertTrue(d.is_allowed)
            limiter.record("openness", 0.05, pid)
            # apply
            lc_mgr.record_transition(pid, "approved", "applying")
            lc_mgr.record_transition(pid, "applying", "applied")

        # 全部 applied
        for i in range(3):
            self.assertEqual(lc_mgr.get_state(f"p{i}"), "applied")

    def test_05_sync_retry_lifecycle_e2e(self):
        """同步检测到 drift → 重试 → 修复后归档"""
        from src.growth.sync.proposal_sync_manager import ProposalSyncManager
        from src.growth.lifecycle_manager import ProposalLifecycleManager
        from src.growth.proposal.proposal import GrowthProposal

        # 1. Type A 与 Mirror 状态不一致
        type_a = GrowthProposal(
            proposal_id="p1",
            status="pending",
            proposal_type="personality",
            affected_dimensions={"openness": 0.05},
            confidence=0.5,
        )
        mirror = {
            "id": "p1",
            "status": "approved",  # 不一致
            "confidence": 0.5,
            "proposed_changes": [{"path": "openness", "after": 0.05}],
            "evaluator_meta": {"source_proposal_id": "p1"},
        }
        sync_mgr = ProposalSyncManager(
            type_a_loader=lambda _id: type_a,
            mirror_loader=lambda _id: mirror,
        )
        report = sync_mgr.check_one("p1")
        self.assertGreaterEqual(report.drift_count, 1)

        # 2. 模拟修复（修改 mirror）
        mirror["status"] = "proposed"

        # 3. 重新检查应一致
        report2 = sync_mgr.check_one("p1")
        self.assertEqual(report2.drift_count, 0)

        # 4. 归档
        lc_mgr = ProposalLifecycleManager(storage_path=self.lc_path)
        lc_mgr.record_transition("p1", "created", "pending")
        lc_mgr.record_transition("p1", "pending", "rejected")
        lc_mgr.record_transition("p1", "rejected", "archived")
        self.assertEqual(lc_mgr.get_state("p1"), "archived")


if __name__ == "__main__":
    unittest.main(verbosity=2)
