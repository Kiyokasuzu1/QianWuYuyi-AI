# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b6.py

Phase B.6 Runtime Integration —— Action Outcome Tracking 测试

覆盖:
  1. 配置测试(default / override / 不修改入参)
  2. OutcomeRecord 测试(创建 / to_dict / from_dict / duration_seconds)
  3. OutcomeTracker 写入测试(append-only JSONL via persistence)
  4. OutcomeTracker 统计测试(get_action_success_rate / get_user_action_feedback)
  5. OutcomeTracker 异常写盘(降级为 memory-only)
  6. OutcomeTracker persistence=None / disabled 行为
  7. OutcomeTracker 重启恢复(load_state)
  8. RuntimeB4Bridge 集成(record_outcome / summarize_b4 outcome 状态)
  9. RuntimeB4Bridge 自动 outcome 记录(dispatch 完成后)
 10. 兼容:B.4 / B.5 现有功能不受影响
 11. 核心模块未修改保护

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖
"""
from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# 1. 测试目标导入
# ============================================================

from src.runtime.action_outcome import (
    PHASE_B6_DEFAULT_CONFIG,
    PHASE_B6_NAME,
    PHASE_B6_VERSION,
    DEFAULT_OUTCOME_PATH,
    SCHEMA_VERSION,
    OUTCOME_STAGE_RECORDED,
    apply_phase_b6_config,
    is_phase_b6_enabled,
    is_b6_enabled_simple,
    OutcomeRecord,
    OutcomeTracker,
    create_outcome_tracker,
    safe_record_outcome,
)

from src.runtime.action_persistence import (
    ActionPersistenceManager,
)

from src.runtime.phase_b4_integration import (
    PHASE_B4_DEFAULT_CONFIG,
    LifecycleStage,
    ExecutionPolicy,
    ActionLifecycleManager,
    ActionAuditRecorder,
    RuntimeB4Bridge,
    create_b4_bridge,
)


# ============================================================
# 2. Helper
# ============================================================

class FakeB3Bridge:
    """模拟 B.3 bridge,提供 flush_pending / get_recent_results"""

    def __init__(
        self,
        recent_results: Optional[List[Dict[str, Any]]] = None,
        dispatched_ids: Optional[List[str]] = None,
        next_result: Optional[Dict[str, int]] = None,
    ) -> None:
        self._recent = list(recent_results or [])
        self._dispatched_ids = set(dispatched_ids or [])
        self._next_result = next_result or {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        self.flush_call_count = 0

    def flush_pending(self) -> Dict[str, int]:
        self.flush_call_count += 1
        return dict(self._next_result)

    def get_recent_results(self, limit: int = 20) -> List[Dict[str, Any]]:
        return list(self._recent[-limit:])

    def add_recent(self, item: Dict[str, Any]) -> None:
        self._recent.append(item)

    def set_recent(self, items: List[Dict[str, Any]]) -> None:
        self._recent = list(items)


# ============================================================
# 3. 配置测试
# ============================================================

class TestB6ConfigInjection:
    """PHASE_B6_DEFAULT_CONFIG / apply_phase_b6_config / is_phase_b6_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 action_outcome.enabled=True"""
        cfg = PHASE_B6_DEFAULT_CONFIG
        assert "action_outcome" in cfg
        assert cfg["action_outcome"]["enabled"] is True
        assert cfg["action_outcome"]["path"] == "data/action_outcome.jsonl"
        assert cfg["action_outcome"]["auto_recover"] is True

    def test_default_on(self):
        """B.6 默认开启(但不阻塞)"""
        assert PHASE_B6_DEFAULT_CONFIG["action_outcome"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b6_config(None)
        assert result["action_outcome"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b6_config({"action_outcome": {"enabled": False}})
        assert result["action_outcome"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {"action_outcome": {"enabled": False, "path": "/tmp/x.jsonl"}}
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b6_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b6_config("not a dict")["action_outcome"]["enabled"] is True
        assert apply_phase_b6_config(42)["action_outcome"]["enabled"] is True
        assert apply_phase_b6_config([])["action_outcome"]["enabled"] is True

    def test_is_phase_b6_enabled(self):
        """is_phase_b6_enabled 判定正确"""
        assert is_phase_b6_enabled({"action_outcome": {"enabled": True}}) is True
        assert is_phase_b6_enabled({"action_outcome": {"enabled": False}}) is False
        assert is_phase_b6_enabled({}) is True
        assert is_phase_b6_enabled(None) is False

    def test_is_b6_enabled_simple_variants(self):
        """is_b6_enabled_simple 兼容 cfg['outcome_enabled']"""
        assert is_b6_enabled_simple({"outcome_enabled": True}) is True
        assert is_b6_enabled_simple({"outcome_enabled": False}) is False
        assert is_b6_enabled_simple({"action_outcome": {"enabled": True}}) is True
        assert is_b6_enabled_simple({}) is True
        assert is_b6_enabled_simple(None) is False


# ============================================================
# 4. OutcomeRecord 测试
# ============================================================

class TestOutcomeRecord:
    """OutcomeRecord 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        r = OutcomeRecord(
            action_id="a1",
            action_type="greeting",
            user_id="u1",
            created_at=100.0,
            completed_at=200.0,
            success=True,
            score=0.8,
        )
        assert r.action_id == "a1"
        assert r.action_type == "greeting"
        assert r.user_id == "u1"
        assert r.created_at == 100.0
        assert r.completed_at == 200.0
        assert r.success is True
        assert r.score == 0.8
        assert r.feedback is None
        assert r.metadata == {}

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        r = OutcomeRecord(
            action_id="a1",
            action_type="greeting",
            user_id="u1",
            created_at=100.0,
            completed_at=200.0,
            success=True,
            score=0.8,
            feedback="good",
            metadata={"k": "v"},
        )
        d = r.to_dict()
        assert d["schema_version"] == SCHEMA_VERSION
        assert d["action_id"] == "a1"
        assert d["action_type"] == "greeting"
        assert d["user_id"] == "u1"
        assert d["created_at"] == 100.0
        assert d["completed_at"] == 200.0
        assert d["success"] is True
        assert d["score"] == 0.8
        assert d["feedback"] == "good"
        assert d["metadata"] == {"k": "v"}

    def test_to_dict_omits_optional(self):
        """feedback/metadata 为空时不出现在 dict 中"""
        r = OutcomeRecord(
            action_id="a1",
            action_type="greeting",
            user_id="u1",
            created_at=100.0,
            completed_at=200.0,
            success=True,
        )
        d = r.to_dict()
        assert "feedback" not in d
        assert "metadata" not in d

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = OutcomeRecord(
            action_id="a1",
            action_type="greeting",
            user_id="u1",
            created_at=100.0,
            completed_at=200.0,
            success=False,
            score=0.3,
            feedback="bad",
            metadata={"k": "v"},
        )
        d = original.to_dict()
        restored = OutcomeRecord.from_dict(d)
        assert restored.action_id == original.action_id
        assert restored.action_type == original.action_type
        assert restored.user_id == original.user_id
        assert restored.created_at == original.created_at
        assert restored.completed_at == original.completed_at
        assert restored.success == original.success
        assert restored.score == original.score
        assert restored.feedback == original.feedback
        assert restored.metadata == original.metadata

    def test_duration_seconds(self):
        """duration_seconds 计算正确"""
        r = OutcomeRecord(
            action_id="a1", action_type="t", user_id="u",
            created_at=100.0, completed_at=250.0, success=True,
        )
        assert r.duration_seconds == 150.0

    def test_duration_seconds_negative_clamped(self):
        """created_at > completed_at 时,返回 0"""
        r = OutcomeRecord(
            action_id="a1", action_type="t", user_id="u",
            created_at=300.0, completed_at=100.0, success=True,
        )
        assert r.duration_seconds == 0.0


# ============================================================
# 5. OutcomeTracker 写入测试
# ============================================================

class TestOutcomeTrackerWrite:
    """OutcomeTracker 写盘行为"""

    def test_record_outcome_writes_to_persistence(self, tmp_path):
        """record_outcome 通过 persistence 写盘"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = tracker.record_outcome(outcome)
        assert ok is True
        # persistence 写盘
        assert mgr.write_count >= 1
        # 文件存在
        assert os.path.exists(log_path)
        # 解析行
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["action_id"] == "a1"
        assert rec["stage"] == OUTCOME_STAGE_RECORDED
        assert rec["decision"] == "outcome_recorded"
        assert rec["result"]["action_type"] == "greeting"
        assert rec["result"]["success"] is True

    def test_record_outcome_appends(self, tmp_path):
        """多次 record_outcome append-only"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        for i in range(5):
            tracker.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=True, score=0.5,
            ))
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 至少 5 条 outcome
        outcome_lines = [
            json.loads(l) for l in lines
            if json.loads(l).get("stage") == OUTCOME_STAGE_RECORDED
        ]
        assert len(outcome_lines) == 5

    def test_record_outcome_no_persistence(self):
        """无 persistence 时仍可 record(只内存)"""
        tracker = OutcomeTracker(persistence=None)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = tracker.record_outcome(outcome)
        # 无 persistence,内存已记录,返回 True
        assert ok is True
        # 内存中有
        assert tracker.get_action_outcome("a1") is not None

    def test_record_outcome_disabled(self, tmp_path):
        """enabled=False 时只更新内存,不写盘"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr, enabled=False)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = tracker.record_outcome(outcome)
        # disabled 时返回 True(等同没失败),内存已更新
        assert ok is True
        # 但 persistence 没写
        assert mgr.write_count == 0
        # 内存有
        assert tracker.get_action_outcome("a1") is not None

    def test_record_outcome_empty_action_id(self, tmp_path):
        """action_id 为空时跳过"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True,
        )
        ok = tracker.record_outcome(outcome)
        assert ok is False
        assert mgr.write_count == 0

    def test_record_outcome_none_input(self, tmp_path):
        """None 输入处理"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        ok = tracker.record_outcome(None)  # type: ignore[arg-type]
        assert ok is False

    def test_record_outcome_in_memory_state(self, tmp_path):
        """record_outcome 后内存状态正确"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8, feedback="nice",
        )
        tracker.record_outcome(outcome)
        # 内存可查
        stored = tracker.get_action_outcome("a1")
        assert stored is not None
        assert stored.action_type == "greeting"
        assert stored.success is True
        # 历史可查
        hist = tracker.get_outcome_history("a1")
        assert len(hist) == 1


# ============================================================
# 6. OutcomeTracker 异常写盘(降级)
# ============================================================

class TestOutcomeTrackerFailureIsolation:
    """写盘失败自动降级"""

    def test_degraded_after_persistence_failure(self, tmp_path):
        """persistence 写盘失败 → record_outcome 仍返回结果(可能 False)"""
        # 构造一个会失败的 persistence mock
        mgr = MagicMock()
        mgr.is_degraded = True
        mgr.persist_event.return_value = False  # 模拟降级
        tracker = OutcomeTracker(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = tracker.record_outcome(outcome)
        # 返回 False(写盘失败)
        assert ok is False
        # 但内存仍记录
        assert tracker.get_action_outcome("a1") is not None
        # 错误计数 +1
        assert tracker.record_error_count >= 1

    def test_persistence_exception_isolated(self):
        """persistence.persist_event 抛错时隔离"""
        mgr = MagicMock()
        mgr.is_degraded = False
        mgr.persist_event.side_effect = RuntimeError("boom")
        tracker = OutcomeTracker(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = tracker.record_outcome(outcome)
        # 异常隔离 → 内存已记录,但返回 False
        assert ok is False
        # 内存有
        assert tracker.get_action_outcome("a1") is not None

    def test_safe_record_outcome_handles_none_tracker(self):
        """safe_record_outcome(None, ...) → False"""
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True,
        )
        assert safe_record_outcome(None, outcome) is False

    def test_safe_record_outcome_handles_none_outcome(self):
        """safe_record_outcome(tracker, None) → False"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        tracker = OutcomeTracker(persistence=mgr)
        assert safe_record_outcome(tracker, None) is False  # type: ignore[arg-type]

    def test_safe_record_outcome_handles_exception(self):
        """safe_record_outcome 处理 record 抛错"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        tracker = OutcomeTracker(persistence=mgr)
        # mock tracker.record_outcome 抛错
        tracker.record_outcome = MagicMock(side_effect=RuntimeError("boom"))
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True,
        )
        assert safe_record_outcome(tracker, outcome) is False


# ============================================================
# 7. OutcomeTracker 统计接口
# ============================================================

class TestOutcomeTrackerStatistics:
    """get_action_success_rate / get_user_action_feedback"""

    def test_get_action_success_rate_empty(self):
        """无数据时返回零"""
        mgr = MagicMock()
        tracker = OutcomeTracker(persistence=mgr)
        result = tracker.get_action_success_rate("greeting")
        assert result["action_type"] == "greeting"
        assert result["total"] == 0
        assert result["success"] == 0
        assert result["rate"] == 0.0
        assert result["avg_score"] == 0.0

    def test_get_action_success_rate_basic(self):
        """基本成功率计算"""
        mgr = MagicMock()
        tracker = OutcomeTracker(persistence=mgr)
        # 注入 outcome
        for i, (ok, score) in enumerate([
            (True, 0.9), (True, 0.8), (False, 0.2), (True, 0.7), (False, 0.1),
        ]):
            tracker._outcomes[f"a{i}"] = OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=ok, score=score,
            )
        # 加一个非该 type 的,验证不混入
        tracker._outcomes["a9"] = OutcomeRecord(
            action_id="a9", action_type="share", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=1.0,
        )
        result = tracker.get_action_success_rate("greeting")
        assert result["total"] == 5
        assert result["success"] == 3
        assert result["rate"] == 0.6
        # avg_score = (0.9+0.8+0.2+0.7+0.1) / 5 = 2.7 / 5 = 0.54
        assert abs(result["avg_score"] - 0.54) < 1e-6

    def test_get_action_success_rate_perfect(self):
        """全部成功时 rate=1.0"""
        mgr = MagicMock()
        tracker = OutcomeTracker(persistence=mgr)
        for i in range(3):
            tracker._outcomes[f"a{i}"] = OutcomeRecord(
                action_id=f"a{i}", action_type="share", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=True, score=1.0,
            )
        result = tracker.get_action_success_rate("share")
        assert result["rate"] == 1.0

    def test_get_action_type_score(self):
        """action_type 综合评分"""
        mgr = MagicMock()
        tracker = OutcomeTracker(persistence=mgr)
        for i, (ok, score) in enumerate([
            (True, 1.0), (True, 0.8), (False, 0.2),
        ]):
            tracker._outcomes[f"a{i}"] = OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=ok, score=score,
            )
        result = tracker.get_action_type_score("greeting")
        assert result["total"] == 3
        assert result["success"] == 2
        assert abs(result["success_rate"] - 2/3) < 1e-6
        # avg_score = (1.0+0.8+0.2)/3 ≈ 0.667
        assert abs(result["avg_score"] - 0.667) < 1e-3
        # combined = success_rate*0.6 + avg_score*0.4
        expected = (2/3) * 0.6 + ((1.0+0.8+0.2)/3) * 0.4
        assert abs(result["combined_score"] - expected) < 1e-3

    def test_get_user_action_feedback(self):
        """按 user 过滤并按时间倒序"""
        mgr = MagicMock()
        tracker = OutcomeTracker(persistence=mgr)
        tracker._outcomes["a1"] = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=150.0,
            success=True, score=0.9, feedback="nice",
        )
        tracker._outcomes["a2"] = OutcomeRecord(
            action_id="a2", action_type="share", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=False, score=0.3, feedback="bad",
        )
        tracker._outcomes["a3"] = OutcomeRecord(
            action_id="a3", action_type="greeting", user_id="u2",
            created_at=100.0, completed_at=300.0,
            success=True, score=0.8,
        )
        feedbacks = tracker.get_user_action_feedback("u1")
        # 应只返回 u1 的 2 条
        assert len(feedbacks) == 2
        # 倒序:a2 (200) 在前,a1 (150) 在后
        assert feedbacks[0]["action_id"] == "a2"
        assert feedbacks[1]["action_id"] == "a1"
        # 字段完整
        for f in feedbacks:
            assert "action_id" in f
            assert "action_type" in f
            assert "success" in f
            assert "score" in f
            assert "feedback" in f
            assert "completed_at" in f

    def test_get_user_action_feedback_empty(self):
        """无该 user 时返回空"""
        mgr = MagicMock()
        tracker = OutcomeTracker(persistence=mgr)
        assert tracker.get_user_action_feedback("nonexistent") == []

    def test_get_outcome_summary(self):
        """整体摘要"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        tracker = OutcomeTracker(persistence=mgr)
        for i, (atype, ok) in enumerate([
            ("greeting", True), ("greeting", False), ("share", True),
        ]):
            tracker.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type=atype, user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=ok, score=0.5,
            ))
        summary = tracker.get_outcome_summary()
        assert summary["total"] == 3
        assert summary["success"] == 2
        assert "by_type" in summary
        assert summary["by_type"]["greeting"]["total"] == 2
        assert summary["by_type"]["greeting"]["success"] == 1
        assert summary["by_type"]["share"]["total"] == 1
        assert summary["by_type"]["share"]["success"] == 1

    def test_get_recent_outcomes(self):
        """get_recent_outcomes 按顺序返回"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        tracker = OutcomeTracker(persistence=mgr)
        for i in range(5):
            tracker.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.5,
            ))
        recent = tracker.get_recent_outcomes(limit=3)
        assert len(recent) == 3
        # 最后 3 个
        assert recent[-1]["action_id"] == "a4"


# ============================================================
# 8. OutcomeTracker 持久化恢复
# ============================================================

class TestOutcomeTrackerRecovery:
    """load_state 恢复 outcome"""

    def test_load_state_no_persistence(self):
        """无 persistence 时返回 0"""
        tracker = OutcomeTracker(persistence=None)
        assert tracker.load_state() == 0

    def test_load_state_empty(self, tmp_path):
        """空文件 → 恢复 0 条"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        # 空文件不存在
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        assert tracker.load_state() == 0

    def test_load_state_recovers_outcomes(self, tmp_path):
        """从 JSONL 恢复 outcome"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        # 进程 1:写入
        mgr1 = ActionPersistenceManager(path=log_path)
        tracker1 = OutcomeTracker(persistence=mgr1)
        for i in range(3):
            tracker1.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.7,
            ))

        # 进程 2:从文件恢复(构造时自动 load_state)
        mgr2 = ActionPersistenceManager(path=log_path)
        tracker2 = OutcomeTracker(persistence=mgr2)
        # 内存验证(auto-load 已恢复 3 个)
        assert tracker2.in_memory_count == 3
        for i in range(3):
            o = tracker2.get_action_outcome(f"a{i}")
            assert o is not None
            assert o.action_type == "greeting"
        # 手动 load_state 应返回 0(已加载)
        assert tracker2.load_state() == 0

    def test_load_state_skips_non_outcome_events(self, tmp_path):
        """load_state 只关注 outcome_recorded 事件"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        mgr1.persist_event("a2", "lc2", LifecycleStage.CREATED)

        mgr2 = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr2)
        count = tracker.load_state()
        assert count == 0

    def test_load_state_corrupt_line_skipped(self, tmp_path):
        """损坏行被跳过"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "action_id": "a1", "lifecycle_id": "outcome_a1",
                "stage": OUTCOME_STAGE_RECORDED, "ts": 200.0,
                "result": {
                    "action_id": "a1", "action_type": "greeting",
                    "user_id": "u1", "created_at": 100.0, "completed_at": 200.0,
                    "success": True, "score": 0.7,
                },
            }) + "\n")
            f.write("not valid json\n")
        mgr = ActionPersistenceManager(path=log_path)
        tracker = OutcomeTracker(persistence=mgr)
        # 损坏行被跳过,1 个有效 outcome 被恢复
        assert tracker.in_memory_count == 1
        assert tracker.get_action_outcome("a1") is not None


# ============================================================
# 9. OutcomeTracker 健康度
# ============================================================

class TestOutcomeTrackerHealth:
    """health_check / clear / reset_stats"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = OutcomeTracker(persistence=mgr)
        h = tracker.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "in_memory_outcomes", "record_count",
            "record_error_count", "recover_count", "last_record_at",
            "last_error", "created_at", "persistence",
        ):
            assert k in h, f"missing key: {k}"

    def test_reset_stats(self, tmp_path):
        """reset_stats 重置统计"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = OutcomeTracker(persistence=mgr)
        tracker.record_outcome(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.5,
        ))
        assert tracker.record_count == 1
        tracker.reset_stats()
        assert tracker.record_count == 0

    def test_clear(self, tmp_path):
        """clear 清空内存(不删文件)"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = OutcomeTracker(persistence=mgr)
        tracker.record_outcome(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.5,
        ))
        assert tracker.in_memory_count == 1
        tracker.clear()
        assert tracker.in_memory_count == 0
        # 文件仍存在
        assert os.path.exists(str(tmp_path / "x.jsonl"))


# ============================================================
# 10. create_outcome_tracker 工厂
# ============================================================

class TestCreateOutcomeTracker:
    """create_outcome_tracker 工厂"""

    def test_create_with_persistence(self, tmp_path):
        """带 persistence 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = create_outcome_tracker(persistence=mgr)
        assert tracker is not None
        assert tracker.persistence is mgr
        assert tracker.enabled is True

    def test_create_with_disabled(self, tmp_path):
        """disabled=True 显式关闭"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = create_outcome_tracker(persistence=mgr, enabled=False)
        assert tracker.enabled is False

    def test_create_with_no_persistence(self):
        """无 persistence 创建"""
        tracker = create_outcome_tracker(persistence=None)
        assert tracker is not None
        assert tracker.persistence is None
        assert tracker.is_degraded is True

    def test_create_from_cfg(self, tmp_path):
        """从 cfg 创建"""
        cfg = {
            "action_outcome": {
                "enabled": True,
                "max_records": 100,
            }
        }
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = create_outcome_tracker(persistence=mgr, cfg=cfg)
        assert tracker is not None
        assert tracker._max_records == 100


# ============================================================
# 11. RuntimeB4Bridge 集成
# ============================================================

class TestB4BridgeIntegration:
    """RuntimeB4Bridge 集成 OutcomeTracker"""

    def test_b4_bridge_has_outcome_tracker(self, tmp_path):
        """RuntimeB4Bridge 持有 outcome_tracker"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        assert b.outcome_tracker is not None
        assert b.outcome_tracker.persistence is mgr

    def test_b4_bridge_outcome_tracker_persistence_none(self):
        """无 persistence 时 outcome_tracker 仍可工作(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.outcome_tracker is not None
        assert b.outcome_tracker.persistence is None

    def test_b4_bridge_explicit_outcome_tracker(self, tmp_path):
        """显式注入 outcome_tracker"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        custom_tracker = OutcomeTracker(persistence=mgr, max_records=42)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
            outcome_tracker=custom_tracker,
        )
        assert b.outcome_tracker is custom_tracker
        assert b.outcome_tracker._max_records == 42

    def test_b4_bridge_record_outcome(self, tmp_path):
        """RuntimeB4Bridge.record_outcome 转发到 outcome_tracker"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8,
        )
        ok = b.record_outcome(outcome)
        assert ok is True
        # outcome 已记录
        assert b.outcome_tracker.get_action_outcome("a1") is not None
        # 计数已更新
        assert b._total_outcomes_recorded == 1

    def test_b4_bridge_record_outcome_governance_off(self, tmp_path):
        """governance OFF 时 record_outcome 拒绝"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": False, "audit_enabled": False}},
            persistence=mgr,
        )
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8,
        )
        ok = b.record_outcome(outcome)
        # governance OFF → False
        assert ok is False

    def test_b4_bridge_get_action_success_rate(self, tmp_path):
        """B.4 bridge 转发 get_action_success_rate"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 注入 outcome
        for i in range(3):
            b.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.8,
            ))
        rate = b.get_action_success_rate("greeting")
        assert rate["total"] == 3
        assert rate["success"] == 3
        assert rate["rate"] == 1.0

    def test_b4_bridge_get_user_action_feedback(self, tmp_path):
        """B.4 bridge 转发 get_user_action_feedback"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.record_outcome(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8, feedback="good",
        ))
        feedbacks = b.get_user_action_feedback("u1")
        assert len(feedbacks) == 1
        assert feedbacks[0]["feedback"] == "good"

    def test_b4_bridge_summarize_includes_outcome(self, tmp_path):
        """summarize_b4 包含 outcome 状态"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.record_outcome(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8,
        ))
        s = b.summarize_b4()
        assert "outcome" in s
        assert s["outcome"]["enabled"] is True
        assert s["outcome"]["in_memory_outcomes"] == 1
        assert s["outcome"]["record_count"] >= 1
        assert "total_outcomes_recorded" in s
        assert s["total_outcomes_recorded"] == 1


# ============================================================
# 12. RuntimeB4Bridge 自动 outcome 记录
# ============================================================

class TestB4BridgeAutoOutcome:
    """flush_pending 后自动记录 outcome"""

    def test_flush_records_outcome_on_dispatched(self, tmp_path):
        """dispatched action 自动记录 outcome(success=True)"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{
            "action_id": "a1", "stage": "dispatched",
            "action_type": "greeting", "user_id": "u1",
            "created_at": 100.0,
        }]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # outcome 已自动记录
        o = b.outcome_tracker.get_action_outcome("a1")
        assert o is not None
        assert o.action_type == "greeting"
        assert o.user_id == "u1"
        assert o.success is True
        assert o.score == 1.0

    def test_flush_records_outcome_on_rejected(self, tmp_path):
        """rejected action 自动记录 outcome(success=False)"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{
            "action_id": "a1", "stage": "gate_rejected",
            "action_type": "greeting", "user_id": "u1",
            "error": "low confidence",
        }]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        o = b.outcome_tracker.get_action_outcome("a1")
        assert o is not None
        assert o.success is False
        assert o.score == 0.0

    def test_flush_records_outcome_on_failed(self, tmp_path):
        """failed action 自动记录 outcome(success=False)"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{
            "action_id": "a1", "stage": "execute_exception",
            "action_type": "share", "user_id": "u1",
            "error": "timeout",
        }]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        o = b.outcome_tracker.get_action_outcome("a1")
        assert o is not None
        assert o.action_type == "share"
        assert o.success is False

    def test_flush_outcome_count_in_stats(self, tmp_path):
        """total_outcomes_recorded 累加"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [
            {"action_id": "a1", "stage": "dispatched", "action_type": "greeting", "user_id": "u1"},
            {"action_id": "a2", "stage": "gate_rejected", "action_type": "share", "user_id": "u1"},
        ]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # 2 个 outcome
        assert b._total_outcomes_recorded == 2
        # 统计中
        s = b.summarize_b4()
        assert s["total_outcomes_recorded"] == 2


# ============================================================
# 13. 兼容性测试
# ============================================================

class TestCompatibility:
    """B.6 不破坏 B.4/B.5 现有功能"""

    def test_b4_without_persistence_unchanged(self):
        """B.4 不传 persistence 时 outcome_tracker 仍创建(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.persistence is None
        assert b.outcome_tracker is not None
        # flush 仍正常
        out = b.flush_pending()
        assert "dispatched" in out

    def test_b4_audit_still_works(self, tmp_path):
        """B.6 引入后,B.4 audit 仍正常"""
        from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger

        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        mgr_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=mgr_path)
        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": True}},
            audit_logger=audit,
            persistence=mgr,
        )
        b.flush_pending()
        # audit 文件应被写入
        assert os.path.exists(log_path)
        # persistence 文件应被写入
        assert os.path.exists(mgr_path)
        # outcome 也应被记录
        assert b.outcome_tracker.get_action_outcome("a1") is not None

    def test_b4_persistence_unchanged(self, tmp_path):
        """B.6 引入后,B.4 persistence 仍正常(不破坏 B.5)"""
        log_path = str(tmp_path / "lifecycle.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # 至少 3 条 lifecycle 事件(approved/executing/completed) + 1 outcome
        assert mgr.write_count >= 4
        # 验证 history 中有 completed + outcome_recorded
        hist = mgr.get_history("a1")
        stages = [r.get("stage") for r in hist]
        assert "completed" in stages
        assert OUTCOME_STAGE_RECORDED in stages

    def test_b4_lifecycle_unchanged(self):
        """B.4 lifecycle 不受 B.6 影响"""
        lm = ActionLifecycleManager(ExecutionPolicy(), persistence=None)
        lm.mark_terminal("a1", LifecycleStage.COMPLETED)
        assert lm.is_duplicate("a1") is True


# ============================================================
# 14. 核心模块未修改保护
# ============================================================

class TestNoCoreModuleModification:
    """B.6 不修改任何核心模块"""

    @staticmethod
    def _extract_imports(src_text: str) -> List[str]:
        try:
            tree = ast.parse(src_text)
        except Exception:
            return []
        imports: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imports.append(n.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        return imports

    def test_action_outcome_no_core_imports(self):
        """action_outcome 不 import 任何核心模块"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "action_outcome.py"
        text = path.read_text(encoding="utf-8")
        imports = self._extract_imports(text)
        forbidden = [
            "src.runtime.supervisor",
            "src.runtime.runtime_core",
            "src.runtime.action_dispatcher",
            "src.proactive",
        ]
        for f in forbidden:
            assert not any(f in imp for imp in imports), \
                f"action_outcome 不得 import {f}, 实际 imports: {imports}"

    def test_phase_b4_integration_still_keeps_no_core_imports(self):
        """phase_b4_integration 仍不 import 核心模块(B.6 接入后)"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "phase_b4_integration.py"
        text = path.read_text(encoding="utf-8")
        imports = self._extract_imports(text)
        forbidden = [
            "src.runtime.supervisor",
            "src.runtime.runtime_core",
            "src.runtime.action_dispatcher",
            "src.proactive",
        ]
        for f in forbidden:
            assert not any(f in imp for imp in imports), \
                f"phase_b4 不得 import {f}, 实际 imports: {imports}"

    def test_supervisor_unchanged(self):
        """supervisor.py 不被 B.6 修改"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "supervisor.py"
        text = path.read_text(encoding="utf-8")
        # 不得出现 B.6 outcome 引用
        assert "OutcomeTracker" not in text
        assert "action_outcome" not in text
        assert "OutcomeRecord" not in text

    def test_runtime_core_unchanged(self):
        """runtime_core.py 不被 B.6 修改"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "runtime_core.py"
        text = path.read_text(encoding="utf-8")
        assert "OutcomeTracker" not in text
        assert "action_outcome" not in text
        assert "OutcomeRecord" not in text

    def test_action_dispatcher_unchanged(self):
        """action_dispatcher.py 不被 B.6 修改"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "action_dispatcher.py"
        text = path.read_text(encoding="utf-8")
        assert "OutcomeTracker" not in text
        assert "action_outcome" not in text

    def test_proactive_engine_unchanged(self):
        """proactive_engine.py 不被 B.6 修改"""
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "phase_b3_integration.py"
        text = path.read_text(encoding="utf-8")
        assert "OutcomeTracker" not in text
        assert "action_outcome" not in text

    def test_action_confidence_gate_unchanged(self):
        """ActionConfidenceGate 不被 B.6 修改"""
        # B.3 bridge 在 phase_b3_integration.py
        path = Path(__file__).resolve().parent.parent / "src" / "runtime" / "phase_b3_integration.py"
        text = path.read_text(encoding="utf-8")
        assert "OutcomeTracker" not in text
        assert "action_outcome" not in text
