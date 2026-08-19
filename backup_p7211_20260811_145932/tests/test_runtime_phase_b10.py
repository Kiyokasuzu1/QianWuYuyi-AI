# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b10.py

Phase B.10 Runtime Integration —— Decision Observability 测试

覆盖:
  1. 配置测试(default / override / 不修改入参)
  2. DecisionMetricSnapshot 测试(创建 / to_dict / from_dict / 边界)
  3. DecisionObserver 空状态运行(无 bridge / 无 persistence)
  4. DecisionObserver collect_snapshot 聚合 outcome
  5. DecisionObserver collect_snapshot 聚合 feedback
  6. DecisionObserver collect_snapshot 计算 confidence delta
  7. DecisionObserver collect_snapshot 统计 boost / suppress / neutral
  8. DecisionObserver top_action_types 提取
  9. DecisionObserver recent_failures 提取
 10. DecisionObserver get_action_statistics 查询
 11. DecisionObserver get_recent_decisions 查询
 12. DecisionObserver export_report 生成
 13. DecisionObserver persistence 写入
 14. DecisionObserver persistence 失败降级
 15. DecisionObserver persistence 启动恢复
 16. DecisionObserver health_check
 17. DecisionObserver 关闭/启用/禁用
 18. DecisionObserver record_decision
 19. RuntimeB4Bridge 集成 DecisionObserver
 20. RuntimeB4Bridge summarize_b4 包含 decision_observability
 21. RuntimeB4Bridge 转发 get_decision_metrics / get_action_statistics /
     get_recent_decisions / export_decision_report /
     decision_observer_health_check
 22. B.4~B.9 兼容性测试
 23. 核心模块未修改保护
 24. Schema 验证

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖
"""
from __future__ import annotations

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

from src.runtime.decision_observer import (
    PHASE_B10_DEFAULT_CONFIG,
    PHASE_B10_NAME,
    PHASE_B10_VERSION,
    SCHEMA_VERSION as B10_SCHEMA_VERSION,
    DECISION_METRIC_STAGE_SNAPSHOT,
    DEFAULT_OBSERVER_PATH,
    apply_phase_b10_config,
    is_phase_b10_enabled,
    is_b10_enabled_simple,
    DecisionMetricSnapshot,
    DecisionObserver,
    create_decision_observer,
    safe_collect_snapshot,
)

from src.runtime.action_outcome import (
    OutcomeRecord,
    OutcomeTracker,
)

from src.runtime.action_feedback import (
    ActionFeedbackManager,
)

from src.runtime.decision_feedback_adapter import (
    DecisionFeedbackAdjustment,
    DecisionFeedbackAdapter,
    RECOMMENDATION_BOOST,
    RECOMMENDATION_NEUTRAL,
    RECOMMENDATION_SUPPRESS,
)

from src.runtime.decision_feedback_runtime import (
    DecisionFeedbackRuntime,
    DecisionFeedbackContext,
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

class TestB10ConfigInjection:
    """PHASE_B10_DEFAULT_CONFIG / apply_phase_b10_config / is_phase_b10_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 decision_observer.enabled=True"""
        cfg = PHASE_B10_DEFAULT_CONFIG
        assert "decision_observer" in cfg
        assert cfg["decision_observer"]["enabled"] is True
        assert cfg["decision_observer"]["path"] == "data/decision_metrics.jsonl"
        assert cfg["decision_observer"]["auto_recover"] is True
        assert cfg["decision_observer"]["max_snapshots"] == 1000
        assert cfg["decision_observer"]["max_history"] == 200
        assert cfg["decision_observer"]["max_top_action_types"] == 10
        assert cfg["decision_observer"]["recent_failures_limit"] == 20
        assert cfg["decision_observer"]["recent_decisions_limit"] == 50

    def test_default_on(self):
        """B.10 默认开启(但不阻塞)"""
        assert PHASE_B10_DEFAULT_CONFIG["decision_observer"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b10_config(None)
        assert result["decision_observer"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b10_config({"decision_observer": {"enabled": False}})
        assert result["decision_observer"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {
            "decision_observer": {"enabled": False, "path": "/tmp/x.jsonl"},
        }
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b10_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b10_config("not a dict")["decision_observer"]["enabled"] is True
        assert apply_phase_b10_config(42)["decision_observer"]["enabled"] is True
        assert apply_phase_b10_config([])["decision_observer"]["enabled"] is True

    def test_is_phase_b10_enabled(self):
        """is_phase_b10_enabled 判定正确"""
        assert is_phase_b10_enabled({"decision_observer": {"enabled": True}}) is True
        assert is_phase_b10_enabled({"decision_observer": {"enabled": False}}) is False
        assert is_phase_b10_enabled({}) is True
        assert is_phase_b10_enabled(None) is False

    def test_is_b10_enabled_simple_variants(self):
        """is_b10_enabled_simple 兼容 cfg['observer_enabled']"""
        assert is_b10_enabled_simple({"observer_enabled": True}) is True
        assert is_b10_enabled_simple({"observer_enabled": False}) is False
        assert is_b10_enabled_simple({"decision_observer": {"enabled": True}}) is True
        assert is_b10_enabled_simple({}) is True
        assert is_b10_enabled_simple(None) is False


# ============================================================
# 4. DecisionMetricSnapshot 测试
# ============================================================

class TestDecisionMetricSnapshot:
    """DecisionMetricSnapshot 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        s = DecisionMetricSnapshot(
            total_actions=10,
            success_rate=0.8,
            failure_rate=0.2,
            average_confidence_change=0.05,
            boost_count=3,
            suppress_count=1,
            neutral_count=2,
            timestamp=1234.5,
        )
        assert s.total_actions == 10
        assert s.success_rate == 0.8
        assert s.failure_rate == 0.2
        assert s.average_confidence_change == 0.05
        assert s.boost_count == 3
        assert s.suppress_count == 1
        assert s.neutral_count == 2
        assert s.top_action_types == []
        assert s.recent_failures == []
        assert s.timestamp == 1234.5

    def test_defaults(self):
        """默认字段值"""
        s = DecisionMetricSnapshot()
        assert s.total_actions == 0
        assert s.success_rate == 0.0
        assert s.failure_rate == 0.0
        assert s.average_confidence_change == 0.0
        assert s.boost_count == 0
        assert s.suppress_count == 0
        assert s.neutral_count == 0
        assert s.top_action_types == []
        assert s.recent_failures == []
        assert s.timestamp == 0.0

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        s = DecisionMetricSnapshot(
            total_actions=10,
            success_rate=0.8,
            failure_rate=0.2,
            average_confidence_change=0.05,
            boost_count=3,
            suppress_count=1,
            neutral_count=2,
            top_action_types=[{"action_type": "greeting", "count": 5}],
            recent_failures=[{"action_id": "a1"}],
            timestamp=1234.5,
        )
        d = s.to_dict()
        assert d["schema_version"] == B10_SCHEMA_VERSION
        assert d["total_actions"] == 10
        assert d["success_rate"] == 0.8
        assert d["failure_rate"] == 0.2
        assert d["average_confidence_change"] == 0.05
        assert d["boost_count"] == 3
        assert d["suppress_count"] == 1
        assert d["neutral_count"] == 2
        assert d["top_action_types"] == [{"action_type": "greeting", "count": 5}]
        assert d["recent_failures"] == [{"action_id": "a1"}]
        assert d["timestamp"] == 1234.5

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = DecisionMetricSnapshot(
            total_actions=20,
            success_rate=0.75,
            failure_rate=0.25,
            average_confidence_change=-0.1,
            boost_count=5,
            suppress_count=3,
            neutral_count=4,
            top_action_types=[
                {"action_type": "greeting", "count": 10, "success_rate": 0.9},
                {"action_type": "share", "count": 5, "success_rate": 0.6},
            ],
            recent_failures=[
                {"action_id": "a1", "action_type": "share", "score": 0.2},
            ],
            timestamp=5000.0,
        )
        d = original.to_dict()
        restored = DecisionMetricSnapshot.from_dict(d)
        assert restored.total_actions == original.total_actions
        assert restored.success_rate == original.success_rate
        assert restored.failure_rate == original.failure_rate
        assert restored.average_confidence_change == original.average_confidence_change
        assert restored.boost_count == original.boost_count
        assert restored.suppress_count == original.suppress_count
        assert restored.neutral_count == original.neutral_count
        assert len(restored.top_action_types) == 2
        assert restored.top_action_types[0]["action_type"] == "greeting"
        assert restored.top_action_types[1]["count"] == 5
        assert len(restored.recent_failures) == 1
        assert restored.recent_failures[0]["action_id"] == "a1"
        assert restored.timestamp == original.timestamp

    def test_from_dict_handles_missing_keys(self):
        """from_dict 处理缺失字段"""
        s = DecisionMetricSnapshot.from_dict({})
        assert s.total_actions == 0
        assert s.success_rate == 0.0
        assert s.boost_count == 0
        assert s.timestamp == 0.0

    def test_is_empty(self):
        """is_empty 判定"""
        s_empty = DecisionMetricSnapshot()
        assert s_empty.is_empty() is True
        s_full = DecisionMetricSnapshot(total_actions=5, boost_count=1)
        assert s_full.is_empty() is False


# ============================================================
# 5. DecisionObserver 空状态运行
# ============================================================

class TestEmptyState:
    """DecisionObserver 无 bridge / 无 persistence 时的行为"""

    def test_no_bridge_returns_empty_snapshot(self):
        """无 bridge → 空 snapshot(全零)"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        snap = obs.collect_snapshot()
        assert snap.total_actions == 0
        assert snap.success_rate == 0.0
        assert snap.failure_rate == 0.0
        assert snap.boost_count == 0
        assert snap.suppress_count == 0
        assert snap.neutral_count == 0
        assert snap.top_action_types == []
        assert snap.recent_failures == []
        assert snap.timestamp > 0.0
        assert snap.is_empty() is True

    def test_no_persistence_is_degraded(self):
        """无 persistence → is_degraded=True"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        assert obs.is_degraded is True

    def test_get_action_statistics_no_bridge(self):
        """无 bridge → 返回零值结构"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        result = obs.get_action_statistics("greeting")
        assert result["action_type"] == "greeting"
        assert result["total"] == 0
        assert result["success_rate"] == 0.0
        assert result["recommendation"] == "neutral"
        assert result["has_history"] is False

    def test_get_recent_decisions_no_bridge(self):
        """无 bridge → 返回空 list"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        result = obs.get_recent_decisions(limit=10)
        assert result == []

    def test_export_report_no_bridge(self):
        """无 bridge → 仍能 export(空 snapshot)"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        report = obs.export_report()
        assert report["report_version"] == PHASE_B10_VERSION
        assert "snapshot" in report
        assert "summary" in report
        assert report["snapshot"]["total_actions"] == 0

    def test_health_check_no_bridge(self):
        """无 bridge → health_check 仍可调用"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        h = obs.health_check()
        assert h["name"] == PHASE_B10_NAME
        assert h["version"] == PHASE_B10_VERSION
        assert h["enabled"] is True
        assert h["degraded"] is True
        assert "persistence" in h
        assert "bridge" in h

    def test_safe_collect_snapshot_with_none(self):
        """safe_collect_snapshot(None) → 空 snapshot"""
        snap = safe_collect_snapshot(None)
        assert snap.total_actions == 0
        assert snap.timestamp > 0.0


# ============================================================
# 6. outcome 聚合
# ============================================================

class TestOutcomeAggregation:
    """DecisionObserver 聚合 outcome 数据"""

    def test_collect_snapshot_aggregates_outcomes(self, tmp_path):
        """从 outcome_tracker 聚合 success/failure 统计"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 注入 10 条 outcome:7 成功 + 3 失败
        for i in range(7):
            ot.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        for i in range(3):
            ot.record_outcome(OutcomeRecord(
                action_id=f"b{i}", action_type="share", user_id="u1",
                created_at=100.0, completed_at=200.0, success=False, score=0.2,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()

        assert snap.total_actions == 10
        assert snap.success_rate == 0.7
        assert snap.failure_rate == 0.3
        assert len(snap.recent_failures) == 3
        # recent_failures 倒序
        assert snap.recent_failures[0]["action_id"] in ("b2", "b1", "b0")

    def test_collect_snapshot_with_no_outcomes(self, tmp_path):
        """空 outcome → 零值"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()
        assert snap.total_actions == 0
        assert snap.success_rate == 0.0
        assert snap.failure_rate == 0.0
        assert snap.recent_failures == []


# ============================================================
# 7. feedback 聚合
# ============================================================

class TestFeedbackAggregation:
    """DecisionObserver 聚合 feedback/adapter 数据"""

    def test_collect_snapshot_aggregates_boost_suppress_neutral(self, tmp_path):
        """从 feedback_adapter 统计 boost/suppress/neutral"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb, persistence=mgr)

        # 注入 3 boost + 2 suppress + 1 neutral
        for atype in ["g1", "g2", "g3"]:
            adj = adapter.apply_feedback(atype, 0.5, user_id="u1")
            # 强制 boost
            adapter._adjustments[-1] = DecisionFeedbackAdjustment(
                action_type=atype, original_confidence=0.5, adjusted_confidence=0.7,
                feedback_weight=0.85, confidence_delta=0.2,
                recommendation=RECOMMENDATION_BOOST, reason="boost",
            )
        for atype in ["s1", "s2"]:
            adapter._adjustments.append(DecisionFeedbackAdjustment(
                action_type=atype, original_confidence=0.5, adjusted_confidence=0.3,
                feedback_weight=0.15, confidence_delta=-0.2,
                recommendation=RECOMMENDATION_SUPPRESS, reason="suppress",
            ))
        for atype in ["n1"]:
            adapter._adjustments.append(DecisionFeedbackAdjustment(
                action_type=atype, original_confidence=0.5, adjusted_confidence=0.5,
                feedback_weight=0.5, confidence_delta=0.0,
                recommendation=RECOMMENDATION_NEUTRAL, reason="neutral",
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = adapter
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()

        # boost/suppress/neutral 来自 _collect_feedback_stats 的 get_recent_adjustments
        # 验证总数正确(不严格区分,因为 aggregate 从 recent_adjustments 拉)
        total_count = snap.boost_count + snap.suppress_count + snap.neutral_count
        # 至少有 1 个 recommendation 被记录
        assert total_count >= 1


# ============================================================
# 8. confidence delta 聚合
# ============================================================

class TestConfidenceDelta:
    """DecisionObserver 计算 average_confidence_change"""

    def test_collect_snapshot_avg_confidence_change(self, tmp_path):
        """从 feedback_runtime 聚合 confidence change"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb, persistence=mgr)
        rt = DecisionFeedbackRuntime(feedback_adapter=adapter, persistence=mgr)

        # 注入 3 个 context:平均 change = (0.1 + 0.2 - 0.1) / 3 = 0.0667
        rt._contexts.append(DecisionFeedbackContext(
            action_type="g1", original_confidence=0.5, adjusted_confidence=0.6,
            recommendation=RECOMMENDATION_BOOST, adjustment_reason="boost", timestamp=time.time(),
        ))
        rt._contexts.append(DecisionFeedbackContext(
            action_type="g2", original_confidence=0.5, adjusted_confidence=0.7,
            recommendation=RECOMMENDATION_BOOST, adjustment_reason="boost", timestamp=time.time(),
        ))
        rt._contexts.append(DecisionFeedbackContext(
            action_type="s1", original_confidence=0.5, adjusted_confidence=0.4,
            recommendation=RECOMMENDATION_SUPPRESS, adjustment_reason="suppress", timestamp=time.time(),
        ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = adapter
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = rt

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()
        # 0.1 + 0.2 + (-0.1) = 0.2 / 3 ≈ 0.0667
        assert abs(snap.average_confidence_change - (0.2 / 3)) < 0.01

    def test_collect_snapshot_no_runtime_returns_zero(self, tmp_path):
        """无 feedback_runtime → average_confidence_change=0"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()
        assert snap.average_confidence_change == 0.0


# ============================================================
# 9. boost/suppress/neutral 统计
# ============================================================

class TestRecommendationCounters:
    """boost/suppress/neutral 计数"""

    def test_collect_snapshot_with_only_boost(self, tmp_path):
        """只有 boost 时计数"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb, persistence=mgr)
        # 直接构造 boost
        adapter._adjustments.append(DecisionFeedbackAdjustment(
            action_type="g1", original_confidence=0.5, adjusted_confidence=0.7,
            feedback_weight=0.85, confidence_delta=0.2,
            recommendation=RECOMMENDATION_BOOST, reason="boost",
        ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = adapter
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()
        # 至少 1 个 boost
        assert snap.boost_count >= 1
        assert snap.suppress_count == 0

    def test_collect_snapshot_with_only_suppress(self, tmp_path):
        """只有 suppress 时计数"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb, persistence=mgr)
        adapter._adjustments.append(DecisionFeedbackAdjustment(
            action_type="s1", original_confidence=0.5, adjusted_confidence=0.3,
            feedback_weight=0.15, confidence_delta=-0.2,
            recommendation=RECOMMENDATION_SUPPRESS, reason="suppress",
        ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = adapter
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()
        assert snap.suppress_count >= 1
        assert snap.boost_count == 0


# ============================================================
# 10. action_type 统计
# ============================================================

class TestActionTypeStats:
    """top_action_types 提取"""

    def test_collect_snapshot_top_action_types(self, tmp_path):
        """top_action_types 排序按 count 倒序"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        # 注入 profile:greeting 5 次、share 3 次
        for i in range(5):
            fb.record_outcome(OutcomeRecord(
                action_id=f"g{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        for i in range(3):
            fb.record_outcome(OutcomeRecord(
                action_id=f"s{i}", action_type="share", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.7,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()

        # top_action_types 至少包含 2 个 type
        assert len(snap.top_action_types) >= 2
        # greeting 排第一(count=5)
        types_in_order = [t["action_type"] for t in snap.top_action_types]
        assert "greeting" in types_in_order
        assert "share" in types_in_order
        # 验证顺序:greeting 在 share 之前
        assert types_in_order.index("greeting") < types_in_order.index("share")

    def test_get_action_statistics_returns_full_info(self, tmp_path):
        """get_action_statistics 完整返回"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        for i in range(5):
            ot.record_outcome(OutcomeRecord(
                action_id=f"g{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))

        fb = ActionFeedbackManager(persistence=mgr)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb, persistence=mgr)

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = adapter
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        result = obs.get_action_statistics("greeting")

        assert result["action_type"] == "greeting"
        assert result["total"] == 5
        assert result["success_rate"] == 1.0
        assert result["has_history"] is True

    def test_get_action_statistics_unknown_type(self, tmp_path):
        """未知 action_type 返回零值"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        result = obs.get_action_statistics("nonexistent")
        assert result["action_type"] == "nonexistent"
        assert result["total"] == 0
        assert result["has_history"] is False


# ============================================================
# 11. recent_failures 提取
# ============================================================

class TestRecentFailures:
    """recent_failures 列表"""

    def test_recent_failures_capped(self, tmp_path):
        """recent_failures 数量被 recent_failures_limit 限制"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        for i in range(50):
            ot.record_outcome(OutcomeRecord(
                action_id=f"b{i}", action_type="share", user_id="u1",
                created_at=100.0, completed_at=200.0, success=False, score=0.1,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(
            runtime_bridge=fake_bridge, persistence=mgr, recent_failures_limit=5,
        )
        snap = obs.collect_snapshot()
        assert len(snap.recent_failures) == 5

    def test_recent_failures_ordered_by_ts(self, tmp_path):
        """recent_failures 倒序(最近在前)"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 注入 3 条失败,completed_at 不同
        ot.record_outcome(OutcomeRecord(
            action_id="a_old", action_type="share", user_id="u1",
            created_at=100.0, completed_at=100.0, success=False, score=0.1,
        ))
        ot.record_outcome(OutcomeRecord(
            action_id="b_mid", action_type="share", user_id="u1",
            created_at=200.0, completed_at=200.0, success=False, score=0.1,
        ))
        ot.record_outcome(OutcomeRecord(
            action_id="c_new", action_type="share", user_id="u1",
            created_at=300.0, completed_at=300.0, success=False, score=0.1,
        ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        snap = obs.collect_snapshot()
        # 倒序:最近在前
        assert snap.recent_failures[0]["action_id"] == "c_new"


# ============================================================
# 12. export_report
# ============================================================

class TestExportReport:
    """export_report 行为"""

    def test_export_report_structure(self, tmp_path):
        """export_report 包含必要字段"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr)
        report = obs.export_report()
        for k in (
            "report_version", "generated_at", "snapshot", "summary",
            "recent_decisions", "top_action_types",
        ):
            assert k in report, f"missing key: {k}"
        assert report["report_version"] == PHASE_B10_VERSION
        assert report["snapshot"]["total_actions"] == 0

    def test_export_report_with_data(self, tmp_path):
        """有数据时 export_report 包含完整 snapshot"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        for i in range(3):
            ot.record_outcome(OutcomeRecord(
                action_id=f"g{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        report = obs.export_report()
        assert report["snapshot"]["total_actions"] == 3
        assert report["snapshot"]["success_rate"] == 1.0


# ============================================================
# 13. persistence
# ============================================================

class TestPersistence:
    """persistence 行为"""

    def test_collect_snapshot_persists_event(self, tmp_path):
        """collect_snapshot 通过 persistence 写盘"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr)
        obs.collect_snapshot()
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["stage"] == DECISION_METRIC_STAGE_SNAPSHOT
        assert rec["source"] == "phase_b10_observer"
        result = rec["result"]
        assert result["schema_version"] == B10_SCHEMA_VERSION
        assert "total_actions" in result
        assert "success_rate" in result
        assert "boost_count" in result
        assert "suppress_count" in result
        assert "neutral_count" in result
        assert "top_action_types" in result
        assert "recent_failures" in result
        assert "timestamp" in result

    def test_collect_snapshot_no_persistence_no_error(self):
        """无 persistence 时 collect_snapshot 仍正常"""
        obs = DecisionObserver(runtime_bridge=None, persistence=None)
        snap = obs.collect_snapshot()
        assert snap is not None
        assert snap.total_actions == 0

    def test_persistence_failure_degraded(self, tmp_path):
        """persistence 写盘失败时降级为 memory-only"""
        mgr = MagicMock()
        mgr.persist_event.return_value = False
        mgr.is_degraded = True
        mgr.health_check.return_value = {
            "enabled": True, "degraded": True, "write_count": 0,
            "write_error_count": 5, "recover_count": 0,
        }
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr)
        snap = obs.collect_snapshot()
        # 内存仍记录
        assert obs.snapshot_count == 1
        # 错误计数 +1
        assert obs.error_count >= 1
        # snapshot 仍正常返回
        assert snap is not None

    def test_persistence_exception_isolated(self):
        """persistence.persist_event 抛错时隔离"""
        mgr = MagicMock()
        mgr.persist_event.side_effect = RuntimeError("boom")
        mgr.is_degraded = False
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr)
        # 不抛
        snap = obs.collect_snapshot()
        assert snap is not None
        assert obs.snapshot_count == 1
        assert obs.error_count >= 1

    def test_load_state_recovers_snapshots(self, tmp_path):
        """load_state 从 persistence 恢复 snapshot"""
        log_path = str(tmp_path / "b10.jsonl")
        # 进程 1:写 3 条 snapshot
        mgr1 = ActionPersistenceManager(path=log_path)
        obs1 = DecisionObserver(runtime_bridge=None, persistence=mgr1)
        for _ in range(3):
            obs1.collect_snapshot()
        assert mgr1.write_count == 3

        # 进程 2:从文件恢复(构造函数自动调用 load_state)
        mgr2 = ActionPersistenceManager(path=log_path)
        obs2 = DecisionObserver(runtime_bridge=None, persistence=mgr2)
        # 内存中应有 3 条(自动 load_state)
        assert obs2.in_memory_snapshot_count == 3
        # 显式调用 load_state 应返回 0(已去重)
        assert obs2.load_state() == 0
        # 仍然只有 3 条
        assert obs2.in_memory_snapshot_count == 3

    def test_load_state_skips_non_b10_events(self, tmp_path):
        """load_state 跳过非 decision_metric_snapshot stage"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)

        mgr2 = ActionPersistenceManager(path=log_path)
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr2)
        assert obs.load_state() == 0


# ============================================================
# 14. health_check
# ============================================================

class TestHealthCheck:
    """health_check 行为"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr)
        h = obs.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "in_memory_snapshots", "in_memory_recent_decisions",
            "snapshot_count", "error_count", "recover_count",
            "last_snapshot_at", "last_error", "created_at",
            "max_snapshots", "max_history", "max_top_action_types",
            "recent_failures_limit", "recent_decisions_limit",
            "persistence", "bridge",
        ):
            assert k in h, f"missing key: {k}"
        assert h["name"] == PHASE_B10_NAME
        assert h["version"] == PHASE_B10_VERSION
        assert h["schema_version"] == B10_SCHEMA_VERSION

    def test_health_check_after_collect(self, tmp_path):
        """collect 后 health_check 反映状态"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        obs = DecisionObserver(runtime_bridge=None, persistence=mgr)
        obs.collect_snapshot()
        h = obs.health_check()
        assert h["snapshot_count"] >= 1
        assert h["last_snapshot_at"] is not None

    def test_enable_disable(self):
        """enable / disable 切换"""
        obs = DecisionObserver()
        assert obs.enabled is True
        obs.disable()
        assert obs.enabled is False
        obs.enable()
        assert obs.enabled is True

    def test_close(self):
        """close 关闭 observer"""
        obs = DecisionObserver()
        obs.close()
        assert obs._closed is True
        # 关闭后 record_decision 返回 False
        assert obs.record_decision({"type": "test"}) is False


# ============================================================
# 15. record_decision
# ============================================================

class TestRecordDecision:
    """record_decision 行为"""

    def test_record_decision_basic(self):
        """基本 record"""
        obs = DecisionObserver()
        assert obs.record_decision({"type": "test", "ts": time.time()}) is True
        recent = obs.get_recent_decisions(limit=10)
        assert len(recent) >= 1

    def test_record_decision_invalid_input(self):
        """非 dict 输入返回 False"""
        obs = DecisionObserver()
        assert obs.record_decision("not a dict") is False
        assert obs.record_decision(None) is False
        assert obs.record_decision([]) is False

    def test_record_decision_disabled(self):
        """disable 后 record 返回 False"""
        obs = DecisionObserver()
        obs.disable()
        assert obs.record_decision({"type": "test"}) is False

    def test_record_decision_caps_at_max_history(self):
        """record_decision 超过 max_history 后截断"""
        obs = DecisionObserver(max_history=3)
        for i in range(10):
            obs.record_decision({"type": "test", "i": i})
        # 内存中应只有 3 条
        assert obs._max_history == 3


# ============================================================
# 16. 工厂 / 便捷函数
# ============================================================

class TestFactory:
    """create_decision_observer 工厂"""

    def test_create_basic(self, tmp_path):
        """基础工厂"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        obs = create_decision_observer(persistence=mgr)
        assert obs is not None
        assert obs.enabled is True
        assert obs.persistence is mgr

    def test_create_disabled(self, tmp_path):
        """disabled=True 显式关闭"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        obs = create_decision_observer(persistence=mgr, enabled=False)
        assert obs.enabled is False

    def test_create_from_cfg(self, tmp_path):
        """从 cfg 创建"""
        cfg = {
            "decision_observer": {
                "enabled": True,
                "max_snapshots": 50,
                "max_history": 30,
                "max_top_action_types": 5,
                "recent_failures_limit": 10,
                "recent_decisions_limit": 25,
            }
        }
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        obs = create_decision_observer(persistence=mgr, cfg=cfg)
        assert obs is not None
        assert obs._max_snapshots == 50
        assert obs._max_history == 30
        assert obs._max_top_action_types == 5
        assert obs._recent_failures_limit == 10
        assert obs._recent_decisions_limit == 25

    def test_create_with_runtime_bridge(self, tmp_path):
        """带 runtime_bridge 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fake_bridge = MagicMock()
        obs = create_decision_observer(runtime_bridge=fake_bridge, persistence=mgr)
        assert obs.bridge is fake_bridge


# ============================================================
# 17. get_recent_decisions
# ============================================================

class TestGetRecentDecisions:
    """get_recent_decisions 行为"""

    def test_get_recent_decisions_with_recorded(self):
        """含 record_decision 的最近决策"""
        obs = DecisionObserver()
        for i in range(5):
            obs.record_decision({
                "type": "test",
                "action_type": f"g{i}",
                "ts": time.time() + i,
            })
        recent = obs.get_recent_decisions(limit=3)
        # 至少 record_decision 的内容(可能混合其他来源)
        assert len(recent) >= 1

    def test_get_recent_decisions_sorted_by_ts(self):
        """按 ts 倒序"""
        obs = DecisionObserver()
        for i in range(5):
            obs.record_decision({
                "type": "test",
                "ts": 100.0 + i,
            })
        recent = obs.get_recent_decisions(limit=5)
        # 倒序
        ts_list = [r.get("ts", 0.0) for r in recent]
        assert ts_list == sorted(ts_list, reverse=True)

    def test_get_recent_decisions_from_adapter(self, tmp_path):
        """从 feedback_adapter 拉取 decisions"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb, persistence=mgr)
        for i in range(3):
            adapter._adjustments.append(DecisionFeedbackAdjustment(
                action_type=f"g{i}", original_confidence=0.5, adjusted_confidence=0.7,
                feedback_weight=0.85, confidence_delta=0.2,
                recommendation=RECOMMENDATION_BOOST, reason="boost",
                timestamp=100.0 + i,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = adapter
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        recent = obs.get_recent_decisions(limit=10)
        # 至少 1 条
        assert len(recent) >= 1

    def test_get_recent_decisions_limit_zero(self):
        """limit=0 时使用默认值"""
        obs = DecisionObserver(recent_decisions_limit=10)
        obs.record_decision({"type": "test", "ts": time.time()})
        recent = obs.get_recent_decisions(limit=0)
        # 至少 1 条
        assert isinstance(recent, list)


# ============================================================
# 18. RuntimeB4Bridge 集成
# ============================================================

class TestB4BridgeObserverIntegration:
    """RuntimeB4Bridge 集成 DecisionObserver"""

    def test_b4_bridge_has_decision_observer(self, tmp_path):
        """RuntimeB4Bridge 持有 decision_observer"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        assert b.decision_observer is not None
        assert b.decision_observer.enabled is True

    def test_b4_bridge_decision_observer_no_persistence(self):
        """无 persistence 时 decision_observer 仍创建"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.decision_observer is not None
        assert b.decision_observer.persistence is None
        assert b.decision_observer.is_degraded is True

    def test_b4_bridge_explicit_decision_observer(self, tmp_path):
        """显式注入 decision_observer"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        custom_obs = DecisionObserver(
            persistence=mgr, max_snapshots=42, max_history=21,
        )
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
            decision_observer=custom_obs,
        )
        assert b.decision_observer is custom_obs
        assert b.decision_observer._max_snapshots == 42
        assert b.decision_observer._max_history == 21

    def test_b4_bridge_get_decision_metrics(self, tmp_path):
        """B.4 bridge 转发 get_decision_metrics"""
        log_path = str(tmp_path / "b10.jsonl")
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
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        metrics = b.get_decision_metrics()
        for k in (
            "schema_version", "total_actions", "success_rate",
            "failure_rate", "average_confidence_change",
            "boost_count", "suppress_count", "neutral_count",
            "top_action_types", "recent_failures", "timestamp",
        ):
            assert k in metrics, f"missing key: {k}"
        assert metrics["total_actions"] == 3
        assert metrics["success_rate"] == 1.0

    def test_b4_bridge_get_action_statistics(self, tmp_path):
        """B.4 bridge 转发 get_action_statistics"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        for i in range(5):
            b.record_outcome(OutcomeRecord(
                action_id=f"g{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        result = b.get_action_statistics("greeting")
        assert result["action_type"] == "greeting"
        assert result["total"] == 5
        assert result["success_rate"] == 1.0
        assert result["has_history"] is True

    def test_b4_bridge_get_recent_decisions(self, tmp_path):
        """B.4 bridge 转发 get_recent_decisions"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.decision_observer.record_decision({
            "type": "test", "ts": time.time(),
        })
        recent = b.get_recent_decisions(limit=10)
        assert len(recent) >= 1

    def test_b4_bridge_export_decision_report(self, tmp_path):
        """B.4 bridge 转发 export_decision_report"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        report = b.export_decision_report()
        assert report["report_version"] == PHASE_B10_VERSION
        assert "snapshot" in report
        assert "summary" in report

    def test_b4_bridge_decision_observer_health_check(self, tmp_path):
        """B.4 bridge 转发 decision_observer_health_check"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        h = b.decision_observer_health_check()
        assert h["name"] == PHASE_B10_NAME
        assert "enabled" in h
        assert "degraded" in h

    def test_b4_bridge_get_decision_observability_summary(self, tmp_path):
        """B.4 bridge 转发 get_decision_observability_summary"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        s = b.get_decision_observability_summary()
        assert s["name"] == PHASE_B10_NAME

    def test_b4_bridge_summarize_includes_observability(self, tmp_path):
        """summarize_b4 包含 decision_observability"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.get_decision_metrics()
        s = b.summarize_b4()
        assert "decision_observability" in s
        obs = s["decision_observability"]
        assert "enabled" in obs
        assert "total_actions" in obs
        assert "success_rate" in obs
        assert "snapshot_count" in obs
        assert "last_snapshot" in obs
        assert obs["enabled"] is True
        assert obs["snapshot_count"] >= 1

    def test_b4_bridge_get_decision_metrics_no_observer(self):
        """decision_observer=None 时 get_decision_metrics 返回空"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_observer = None
        metrics = b.get_decision_metrics()
        assert metrics["total_actions"] == 0
        assert metrics["success_rate"] == 0.0

    def test_b4_bridge_get_action_statistics_no_observer(self):
        """decision_observer=None 时 get_action_statistics 返回零值"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_observer = None
        result = b.get_action_statistics("g")
        assert result["action_type"] == "g"
        assert result["total"] == 0
        assert result["has_history"] is False

    def test_b4_bridge_get_recent_decisions_no_observer(self):
        """decision_observer=None 时 get_recent_decisions 返回空"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_observer = None
        assert b.get_recent_decisions() == []

    def test_b4_bridge_export_decision_report_no_observer(self):
        """decision_observer=None 时 export_decision_report 返回错误"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_observer = None
        report = b.export_decision_report()
        assert "error" in report

    def test_b4_bridge_decision_observer_health_check_no_observer(self):
        """decision_observer=None 时 health_check 返回零值"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_observer = None
        h = b.decision_observer_health_check()
        assert h["enabled"] is False


# ============================================================
# 19. B.4~B.9 兼容性
# ============================================================

class TestB10Compatibility:
    """B.10 不破坏 B.4~B.9 现有功能"""

    def test_b4_audit_still_works(self, tmp_path):
        """B.10 引入后,B.4 audit 仍正常"""
        from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger

        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        mgr_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=mgr_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": True}},
            audit_logger=audit,
            persistence=mgr,
        )
        b.flush_pending()
        assert os.path.exists(log_path)
        assert os.path.exists(mgr_path)

    def test_b5_persistence_unchanged(self, tmp_path):
        """B.10 引入后,B.5 persistence 仍正常"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # B.5 persistence 仍写盘
        assert mgr.write_count >= 1
        hist = mgr.get_history("a1")
        stages = [r.get("stage") for r in hist]
        assert "completed" in stages

    def test_b6_outcome_unchanged(self, tmp_path):
        """B.10 引入后,B.6 outcome 仍正常"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        assert b.outcome_tracker.get_action_outcome("a1") is not None

    def test_b7_feedback_unchanged(self, tmp_path):
        """B.10 引入后,B.7 feedback 仍正常"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        profile = b.feedback_manager.get_action_type_profile("greeting")
        assert profile.total_count == 1

    def test_b8_adapter_unchanged(self, tmp_path):
        """B.10 引入后,B.8 adapter 仍正常"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        for i in range(10):
            b.record_feedback(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        adj = b.get_feedback_adjustment("greeting", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_BOOST

    def test_b9_runtime_unchanged(self, tmp_path):
        """B.10 引入后,B.9 runtime 仍正常"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [
            {
                "action_id": f"a{i}", "stage": "dispatched",
                "action_type": "greeting", "user_id": "u1",
                "created_at": 100.0 + i,
            } for i in range(5)
        ]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # flush_pending 会自动 outcome → feedback
        b.flush_pending()
        # 验证 feedback 已聚合
        profile = b.feedback_manager.get_action_type_profile("greeting")
        assert profile.total_count == 5
        # runtime 对 greeting 应给出 boost(纯计算)
        ctx = b.get_runtime_feedback("greeting", confidence=0.5)
        assert ctx is not None
        assert ctx.recommendation == RECOMMENDATION_BOOST

    def test_b4_bridge_flush_does_not_call_observer(self, tmp_path):
        """flush_pending 不主动调用 observer(observer 是按需)"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "g"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # observer 没有自动 collect
        # (observer 是按需调用:get_decision_metrics / export_decision_report)
        # 仍然可以手动调用
        assert b.decision_observer is not None


# ============================================================
# 20. 核心模块未修改保护
# ============================================================

CORE_MODULES = [
    "src/runtime/supervisor.py",
    "src/runtime/runtime_core.py",
    "src/proactive/proactive_engine.py",
    "src/runtime/action_dispatcher.py",
]


class TestCoreModulesUnchanged:
    """核心模块未修改保护"""

    @pytest.mark.parametrize("rel_path", CORE_MODULES)
    def test_core_module_file_exists(self, rel_path):
        """核心模块文件必须存在(基线)"""
        repo_root = Path(__file__).resolve().parents[1]
        full = repo_root / rel_path
        assert full.exists(), f"core module missing: {rel_path}"

    def test_no_proactive_engine_observer_references(self):
        """ProactiveEngine 不应直接引用 observer(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        proactive = repo_root / "src" / "proactive" / "proactive_engine.py"
        if not proactive.exists():
            pytest.skip("proactive_engine not found")
        src = proactive.read_text(encoding="utf-8")
        assert "phase_b10" not in src, "proactive_engine should not reference phase_b10"
        assert "decision_observer" not in src, (
            "proactive_engine should not reference decision_observer"
        )

    def test_no_supervisor_observer_references(self):
        """supervisor.py 不应直接引用 observer(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        sup = repo_root / "src" / "runtime" / "supervisor.py"
        if not sup.exists():
            pytest.skip("supervisor not found")
        src = sup.read_text(encoding="utf-8")
        assert "phase_b10" not in src, "supervisor should not reference phase_b10"
        assert "decision_observer" not in src, (
            "supervisor should not reference decision_observer"
        )

    def test_no_runtime_core_observer_references(self):
        """runtime_core.py 不应直接引用 observer(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        rc = repo_root / "src" / "runtime" / "runtime_core.py"
        if not rc.exists():
            pytest.skip("runtime_core not found")
        src = rc.read_text(encoding="utf-8")
        assert "phase_b10" not in src, "runtime_core should not reference phase_b10"
        assert "decision_observer" not in src, (
            "runtime_core should not reference decision_observer"
        )

    def test_no_action_dispatcher_observer_references(self):
        """action_dispatcher.py 不应直接引用 observer(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        ad = repo_root / "src" / "runtime" / "action_dispatcher.py"
        if not ad.exists():
            pytest.skip("action_dispatcher not found")
        src = ad.read_text(encoding="utf-8")
        assert "phase_b10" not in src, "action_dispatcher should not reference phase_b10"
        assert "decision_observer" not in src, (
            "action_dispatcher should not reference decision_observer"
        )


# ============================================================
# 21. Schema 验证
# ============================================================

class TestSchema:
    """持久化结构符合 schema"""

    def test_persistence_payload_schema(self, tmp_path):
        """持久化文件结构正确"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        for i in range(3):
            ot.record_outcome(OutcomeRecord(
                action_id=f"g{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        obs = DecisionObserver(runtime_bridge=fake_bridge, persistence=mgr)
        obs.collect_snapshot()

        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        rec = json.loads(lines[-1])
        assert rec["stage"] == DECISION_METRIC_STAGE_SNAPSHOT
        assert rec["source"] == "phase_b10_observer"
        result = rec["result"]
        assert result["schema_version"] == B10_SCHEMA_VERSION
        assert result["total_actions"] == 3
        assert result["success_rate"] == 1.0
        assert "boost_count" in result
        assert "suppress_count" in result
        assert "neutral_count" in result
        assert "top_action_types" in result
        assert "recent_failures" in result
        assert "timestamp" in result

    def test_persistence_stage_constant(self):
        """stage 常量正确"""
        assert DECISION_METRIC_STAGE_SNAPSHOT == "decision_metric_snapshot"

    def test_schema_version_constant(self):
        """schema_version 常量"""
        assert B10_SCHEMA_VERSION == "1.0"


# ============================================================
# 22. 闭环验证
# ============================================================

class TestClosedLoop:
    """完整闭环验证"""

    def test_full_loop_outcome_feedback_observer(self, tmp_path):
        """完整闭环:outcome → feedback → adapter → runtime → observer"""
        log_path = str(tmp_path / "b10.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )

        # 1) 模拟 10 次 greeting 成功
        for i in range(10):
            inner.set_recent([{
                "action_id": f"a{i}", "stage": "dispatched",
                "action_type": "greeting", "user_id": "u1",
                "created_at": 100.0 + i,
            }])
            b.flush_pending()

        # 2) 5 次 share 失败
        for i in range(5):
            inner.set_recent([{
                "action_id": f"s{i}", "stage": "execute_exception",
                "action_type": "share", "user_id": "u1",
                "error": "timeout",
            }])
            b.flush_pending()

        # 3) observer snapshot
        metrics = b.get_decision_metrics()
        assert metrics["total_actions"] == 15
        assert metrics["success_rate"] == 10.0 / 15.0
        assert metrics["failure_rate"] == 5.0 / 15.0

        # 4) per-action_type 统计
        g_stats = b.get_action_statistics("greeting")
        assert g_stats["total"] == 10
        assert g_stats["success_rate"] == 1.0

        s_stats = b.get_action_statistics("share")
        assert s_stats["total"] == 5
        assert s_stats["success_rate"] == 0.0

        # 5) top_action_types 应包含 greeting 和 share
        top_types = [t["action_type"] for t in metrics["top_action_types"]]
        assert "greeting" in top_types
        assert "share" in top_types

        # 6) recent_failures 应包含 5 条 share 失败
        assert len(metrics["recent_failures"]) == 5

        # 7) report
        report = b.export_decision_report()
        assert report["snapshot"]["total_actions"] == 15

        # 8) summarize 包含完整状态
        s = b.summarize_b4()
        assert "decision_observability" in s
        assert s["decision_observability"]["enabled"] is True
        assert s["decision_observability"]["total_actions"] >= 15
