# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b11.py

Phase B.11 Runtime Integration —— Decision Intelligence Layer 测试

覆盖:
  1. 配置测试(default / override / 不修改入参 / 边界)
  2. DecisionHealthScore 数据类(创建 / to_dict / from_dict)
  3. TrendPoint 数据类(创建 / to_dict / from_dict)
  4. FeedbackDrift 数据类
  5. FailurePattern 数据类
  6. StrategyRecommendation 数据类
  7. DecisionIntelligence 空状态运行(无 bridge / 无 persistence)
  8. DecisionIntelligence compute_health_score 多维计算
  9. DecisionIntelligence analyze_trends 多 metric 分析
 10. DecisionIntelligence detect_feedback_drift 漂移检测
 11. DecisionIntelligence detect_failure_patterns 四类模式
 12. DecisionIntelligence recommend_strategy 策略建议
 13. DecisionIntelligence export_intelligence_report 综合报告
 14. DecisionIntelligence health_check
 15. DecisionIntelligence persistence 写盘
 16. DecisionIntelligence persistence 失败降级
 17. DecisionIntelligence load_state 恢复
 18. DecisionIntelligence 关闭/启用/禁用
 19. DecisionIntelligence 异常隔离
 20. 工厂 / 便捷函数(create_decision_intelligence / safe_compute_health_score)
 21. RuntimeB4Bridge 集成 DecisionIntelligence
 22. RuntimeB4Bridge 转发 B.11 便捷接口
 23. RuntimeB4Bridge summarize_b4 包含 decision_intelligence
 24. B.4-B.10 兼容性测试
 25. 核心模块未修改保护

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

from src.runtime.decision_intelligence import (
    PHASE_B11_DEFAULT_CONFIG,
    PHASE_B11_NAME,
    PHASE_B11_VERSION,
    SCHEMA_VERSION as B11_SCHEMA_VERSION,
    INTELLIGENCE_STAGE_ANALYSIS,
    DEFAULT_INTELLIGENCE_PATH,
    RISK_LEVEL_LOW,
    RISK_LEVEL_MEDIUM,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_CRITICAL,
    TREND_RISING,
    TREND_FALLING,
    TREND_STABLE,
    TREND_UNKNOWN,
    DRIFT_BOOSTING,
    DRIFT_SUPPRESSING,
    DRIFT_STABLE,
    PATTERN_CONSECUTIVE,
    PATTERN_BURST,
    PATTERN_TYPE_CONCENTRATION,
    PATTERN_TIME_CLUSTERING,
    SEVERITY_WARNING,
    SEVERITY_ALERT,
    SEVERITY_CRITICAL,
    STRATEGY_BOOST,
    STRATEGY_SUPPRESS,
    STRATEGY_NEUTRAL,
    STRATEGY_MONITOR,
    apply_phase_b11_config,
    is_phase_b11_enabled,
    is_b11_enabled_simple,
    DecisionHealthScore,
    TrendPoint,
    FeedbackDrift,
    FailurePattern,
    StrategyRecommendation,
    DecisionIntelligence,
    create_decision_intelligence,
    safe_compute_health_score,
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

from src.runtime.decision_observer import (
    DecisionObserver,
    DecisionMetricSnapshot,
)

from src.runtime.phase_b4_integration import (
    PHASE_B4_DEFAULT_CONFIG,
    LifecycleStage,
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

class TestB11ConfigInjection:
    """PHASE_B11_DEFAULT_CONFIG / apply_phase_b11_config / is_phase_b11_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 decision_intelligence.enabled=True"""
        cfg = PHASE_B11_DEFAULT_CONFIG
        assert "decision_intelligence" in cfg
        assert cfg["decision_intelligence"]["enabled"] is True
        assert cfg["decision_intelligence"]["path"] == "data/decision_intelligence.jsonl"
        assert cfg["decision_intelligence"]["max_history"] == 500
        assert cfg["decision_intelligence"]["trend_window_seconds"] == 3600.0
        assert cfg["decision_intelligence"]["trend_default_limit"] == 50
        assert cfg["decision_intelligence"]["drift_window"] == 20
        assert cfg["decision_intelligence"]["drift_anomaly_threshold"] == 0.15
        assert cfg["decision_intelligence"]["drift_stable_threshold"] == 0.05
        assert cfg["decision_intelligence"]["failure_window"] == 50
        assert cfg["decision_intelligence"]["consecutive_failure_threshold"] == 3
        assert cfg["decision_intelligence"]["burst_failure_rate_threshold"] == 0.5
        assert cfg["decision_intelligence"]["type_concentration_threshold"] == 0.6
        assert cfg["decision_intelligence"]["time_cluster_window_seconds"] == 60.0
        assert cfg["decision_intelligence"]["time_cluster_min_failures"] == 3
        assert cfg["decision_intelligence"]["auto_recover"] is True
        assert cfg["decision_intelligence"]["max_top_action_types"] == 20

    def test_default_on(self):
        """B.11 默认开启(但不阻塞)"""
        assert PHASE_B11_DEFAULT_CONFIG["decision_intelligence"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b11_config(None)
        assert result["decision_intelligence"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b11_config({"decision_intelligence": {"enabled": False}})
        assert result["decision_intelligence"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {
            "decision_intelligence": {"enabled": False, "path": "/tmp/x.jsonl"},
        }
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b11_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b11_config("not a dict")["decision_intelligence"]["enabled"] is True
        assert apply_phase_b11_config(42)["decision_intelligence"]["enabled"] is True
        assert apply_phase_b11_config([])["decision_intelligence"]["enabled"] is True

    def test_is_phase_b11_enabled(self):
        """is_phase_b11_enabled 判定正确"""
        assert is_phase_b11_enabled({"decision_intelligence": {"enabled": True}}) is True
        assert is_phase_b11_enabled({"decision_intelligence": {"enabled": False}}) is False
        assert is_phase_b11_enabled({}) is True
        assert is_phase_b11_enabled(None) is False

    def test_is_b11_enabled_simple_variants(self):
        """is_b11_enabled_simple 兼容 cfg['intelligence_enabled']"""
        assert is_b11_enabled_simple({"intelligence_enabled": True}) is True
        assert is_b11_enabled_simple({"intelligence_enabled": False}) is False
        assert is_b11_enabled_simple({"decision_intelligence": {"enabled": True}}) is True
        assert is_b11_enabled_simple({}) is True
        assert is_b11_enabled_simple(None) is False


# ============================================================
# 4. DecisionHealthScore 测试
# ============================================================

class TestDecisionHealthScore:
    """DecisionHealthScore 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        s = DecisionHealthScore(
            overall_score=0.85,
            success_score=0.9,
            stability_score=0.8,
            confidence_score=0.85,
            recovery_score=0.85,
            risk_level=RISK_LEVEL_LOW,
            timestamp=1234.5,
        )
        assert s.overall_score == 0.85
        assert s.success_score == 0.9
        assert s.stability_score == 0.8
        assert s.confidence_score == 0.85
        assert s.recovery_score == 0.85
        assert s.risk_level == "low"
        assert s.timestamp == 1234.5

    def test_defaults(self):
        """默认字段值"""
        s = DecisionHealthScore()
        assert s.overall_score == 0.5
        assert s.success_score == 0.5
        assert s.stability_score == 0.5
        assert s.confidence_score == 0.5
        assert s.recovery_score == 0.5
        assert s.risk_level == "medium"
        assert s.timestamp == 0.0

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        s = DecisionHealthScore(
            overall_score=0.85,
            success_score=0.9,
            stability_score=0.8,
            confidence_score=0.85,
            recovery_score=0.85,
            risk_level=RISK_LEVEL_LOW,
            timestamp=1234.5,
        )
        d = s.to_dict()
        assert d["schema_version"] == B11_SCHEMA_VERSION
        assert d["overall_score"] == 0.85
        assert d["success_score"] == 0.9
        assert d["stability_score"] == 0.8
        assert d["confidence_score"] == 0.85
        assert d["recovery_score"] == 0.85
        assert d["risk_level"] == "low"
        assert d["timestamp"] == 1234.5

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = DecisionHealthScore(
            overall_score=0.72,
            success_score=0.85,
            stability_score=0.6,
            confidence_score=0.7,
            recovery_score=0.8,
            risk_level=RISK_LEVEL_MEDIUM,
            timestamp=5000.0,
        )
        d = original.to_dict()
        restored = DecisionHealthScore.from_dict(d)
        assert restored.overall_score == original.overall_score
        assert restored.success_score == original.success_score
        assert restored.stability_score == original.stability_score
        assert restored.confidence_score == original.confidence_score
        assert restored.recovery_score == original.recovery_score
        assert restored.risk_level == original.risk_level
        assert restored.timestamp == original.timestamp

    def test_from_dict_handles_missing_keys(self):
        """from_dict 处理缺失字段"""
        s = DecisionHealthScore.from_dict({})
        assert s.overall_score == 0.5
        assert s.risk_level == "medium"
        assert s.timestamp == 0.0


# ============================================================
# 5. TrendPoint 测试
# ============================================================

class TestTrendPoint:
    """TrendPoint 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        tp = TrendPoint(
            metric="success_rate",
            action_type="greeting",
            values=[0.5, 0.6, 0.7, 0.8],
            direction=TREND_RISING,
            delta=0.3,
            sample_size=4,
            timestamp=time.time(),
        )
        assert tp.metric == "success_rate"
        assert tp.action_type == "greeting"
        assert tp.values == [0.5, 0.6, 0.7, 0.8]
        assert tp.direction == "rising"
        assert tp.delta == 0.3
        assert tp.sample_size == 4

    def test_defaults(self):
        """默认字段值"""
        tp = TrendPoint(metric="success_rate")
        assert tp.metric == "success_rate"
        assert tp.action_type is None
        assert tp.values == []
        assert tp.direction == TREND_UNKNOWN
        assert tp.delta == 0.0
        assert tp.sample_size == 0

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        tp = TrendPoint(
            metric="feedback_weight",
            action_type="share",
            values=[0.5, 0.6, 0.7],
            direction=TREND_RISING,
            delta=0.2,
            sample_size=3,
            timestamp=1000.0,
        )
        d = tp.to_dict()
        assert d["schema_version"] == B11_SCHEMA_VERSION
        assert d["metric"] == "feedback_weight"
        assert d["action_type"] == "share"
        assert d["values"] == [0.5, 0.6, 0.7]
        assert d["direction"] == "rising"
        assert d["delta"] == 0.2
        assert d["sample_size"] == 3
        assert d["timestamp"] == 1000.0

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = TrendPoint(
            metric="failure_rate",
            action_type=None,
            values=[0.1, 0.2, 0.3, 0.4, 0.5],
            direction=TREND_RISING,
            delta=0.4,
            sample_size=5,
            timestamp=2000.0,
        )
        d = original.to_dict()
        restored = TrendPoint.from_dict(d)
        assert restored.metric == original.metric
        assert restored.action_type == original.action_type
        assert restored.values == original.values
        assert restored.direction == original.direction
        assert restored.delta == original.delta
        assert restored.sample_size == original.sample_size
        assert restored.timestamp == original.timestamp


# ============================================================
# 6. FeedbackDrift 测试
# ============================================================

class TestFeedbackDrift:
    """FeedbackDrift 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        fd = FeedbackDrift(
            action_type="greeting",
            current_weight=0.8,
            historical_avg_weight=0.5,
            drift_magnitude=0.3,
            direction=DRIFT_BOOSTING,
            consecutive_count=4,
            is_anomalous=True,
            timestamp=time.time(),
        )
        assert fd.action_type == "greeting"
        assert fd.current_weight == 0.8
        assert fd.historical_avg_weight == 0.5
        assert fd.drift_magnitude == 0.3
        assert fd.direction == "boosting"
        assert fd.consecutive_count == 4
        assert fd.is_anomalous is True

    def test_defaults(self):
        """默认字段值"""
        fd = FeedbackDrift(action_type="test")
        assert fd.action_type == "test"
        assert fd.current_weight == 0.5
        assert fd.historical_avg_weight == 0.5
        assert fd.drift_magnitude == 0.0
        assert fd.direction == DRIFT_STABLE
        assert fd.consecutive_count == 0
        assert fd.is_anomalous is False

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        fd = FeedbackDrift(
            action_type="g1",
            current_weight=0.7,
            historical_avg_weight=0.5,
            drift_magnitude=0.2,
            direction=DRIFT_BOOSTING,
            consecutive_count=3,
            is_anomalous=True,
            timestamp=3000.0,
        )
        d = fd.to_dict()
        assert d["schema_version"] == B11_SCHEMA_VERSION
        assert d["action_type"] == "g1"
        assert d["current_weight"] == 0.7
        assert d["historical_avg_weight"] == 0.5
        assert d["drift_magnitude"] == 0.2
        assert d["direction"] == "boosting"
        assert d["consecutive_count"] == 3
        assert d["is_anomalous"] is True
        assert d["timestamp"] == 3000.0

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = FeedbackDrift(
            action_type="x",
            current_weight=0.6,
            historical_avg_weight=0.4,
            drift_magnitude=0.2,
            direction=DRIFT_BOOSTING,
            consecutive_count=2,
            is_anomalous=True,
            timestamp=4000.0,
        )
        d = original.to_dict()
        restored = FeedbackDrift.from_dict(d)
        assert restored.action_type == "x"
        assert restored.current_weight == 0.6
        assert restored.drift_magnitude == 0.2
        assert restored.is_anomalous is True
        assert restored.timestamp == 4000.0


# ============================================================
# 7. FailurePattern 测试
# ============================================================

class TestFailurePattern:
    """FailurePattern 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        fp = FailurePattern(
            pattern_type=PATTERN_CONSECUTIVE,
            action_type="share",
            severity=SEVERITY_CRITICAL,
            details={"consecutive_count": 5},
            evidence_count=5,
            timestamp=time.time(),
        )
        assert fp.pattern_type == "consecutive"
        assert fp.action_type == "share"
        assert fp.severity == "critical"
        assert fp.details == {"consecutive_count": 5}
        assert fp.evidence_count == 5

    def test_defaults(self):
        """默认字段值"""
        fp = FailurePattern(pattern_type=PATTERN_BURST)
        assert fp.pattern_type == "burst"
        assert fp.action_type is None
        assert fp.severity == SEVERITY_WARNING
        assert fp.details == {}
        assert fp.evidence_count == 0

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        fp = FailurePattern(
            pattern_type=PATTERN_TYPE_CONCENTRATION,
            action_type="share",
            severity=SEVERITY_ALERT,
            details={"concentration": 0.7, "failure_count": 5},
            evidence_count=5,
            timestamp=5000.0,
        )
        d = fp.to_dict()
        assert d["schema_version"] == B11_SCHEMA_VERSION
        assert d["pattern_type"] == "type_concentration"
        assert d["action_type"] == "share"
        assert d["severity"] == "alert"
        assert d["details"]["concentration"] == 0.7
        assert d["evidence_count"] == 5
        assert d["timestamp"] == 5000.0

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = FailurePattern(
            pattern_type=PATTERN_TIME_CLUSTERING,
            action_type="g1",
            severity=SEVERITY_WARNING,
            details={"cluster_size": 4},
            evidence_count=4,
            timestamp=6000.0,
        )
        d = original.to_dict()
        restored = FailurePattern.from_dict(d)
        assert restored.pattern_type == "time_clustering"
        assert restored.action_type == "g1"
        assert restored.severity == "warning"
        assert restored.details == {"cluster_size": 4}
        assert restored.evidence_count == 4
        assert restored.timestamp == 6000.0


# ============================================================
# 8. StrategyRecommendation 测试
# ============================================================

class TestStrategyRecommendation:
    """StrategyRecommendation 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        sr = StrategyRecommendation(
            target_action_type="greeting",
            action=STRATEGY_BOOST,
            reason="high success rate",
            confidence=0.8,
            supporting_metrics={"success_rate": 0.9},
            timestamp=time.time(),
        )
        assert sr.target_action_type == "greeting"
        assert sr.action == "boost"
        assert sr.reason == "high success rate"
        assert sr.confidence == 0.8
        assert sr.supporting_metrics == {"success_rate": 0.9}

    def test_defaults(self):
        """默认字段值"""
        sr = StrategyRecommendation(target_action_type="test")
        assert sr.target_action_type == "test"
        assert sr.action == STRATEGY_NEUTRAL
        assert sr.reason == ""
        assert sr.confidence == 0.5
        assert sr.supporting_metrics == {}

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        sr = StrategyRecommendation(
            target_action_type="share",
            action=STRATEGY_SUPPRESS,
            reason="critical failure",
            confidence=0.85,
            supporting_metrics={"critical_pattern": "consecutive"},
            timestamp=7000.0,
        )
        d = sr.to_dict()
        assert d["schema_version"] == B11_SCHEMA_VERSION
        assert d["target_action_type"] == "share"
        assert d["action"] == "suppress"
        assert d["reason"] == "critical failure"
        assert d["confidence"] == 0.85
        assert d["supporting_metrics"]["critical_pattern"] == "consecutive"
        assert d["timestamp"] == 7000.0

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = StrategyRecommendation(
            target_action_type="x",
            action=STRATEGY_MONITOR,
            reason="drift detected",
            confidence=0.6,
            supporting_metrics={"drift_magnitude": 0.2},
            timestamp=8000.0,
        )
        d = original.to_dict()
        restored = StrategyRecommendation.from_dict(d)
        assert restored.target_action_type == "x"
        assert restored.action == "monitor"
        assert restored.reason == "drift detected"
        assert restored.confidence == 0.6
        assert restored.supporting_metrics == {"drift_magnitude": 0.2}
        assert restored.timestamp == 8000.0


# ============================================================
# 9. DecisionIntelligence 空状态运行
# ============================================================

class TestEmptyState:
    """DecisionIntelligence 无 bridge / 无 persistence 时的行为"""

    def test_no_bridge_returns_neutral_health(self):
        """无 bridge → 中性 health score"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        health = intel.compute_health_score()
        assert health.overall_score == 0.5
        assert health.success_score == 0.5
        assert health.stability_score == 0.5
        assert health.confidence_score == 0.5
        assert health.recovery_score == 0.5
        assert health.risk_level == RISK_LEVEL_MEDIUM
        assert health.timestamp > 0.0

    def test_no_persistence_is_degraded(self):
        """无 persistence → is_degraded=True"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        assert intel.is_degraded is True

    def test_analyze_trends_no_bridge(self):
        """无 bridge → 返回空 list"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        trends = intel.analyze_trends(metric="success_rate")
        assert trends == []

    def test_detect_feedback_drift_no_bridge(self):
        """无 bridge → 返回空 list"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        drifts = intel.detect_feedback_drift()
        assert drifts == []

    def test_detect_failure_patterns_no_bridge(self):
        """无 bridge → 返回空 list"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        patterns = intel.detect_failure_patterns()
        assert patterns == []

    def test_recommend_strategy_no_bridge(self):
        """无 bridge → 返回空 list"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        recs = intel.recommend_strategy()
        assert recs == []

    def test_export_report_no_bridge(self):
        """无 bridge → 仍能 export(空 report)"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        report = intel.export_intelligence_report()
        assert report["report_version"] == PHASE_B11_VERSION
        assert "health_score" in report
        assert report["health_score"]["overall_score"] == 0.5

    def test_health_check_no_bridge(self):
        """无 bridge → health_check 仍可调用"""
        intel = DecisionIntelligence(runtime_bridge=None, persistence=None)
        h = intel.health_check()
        assert h["name"] == PHASE_B11_NAME
        assert h["version"] == PHASE_B11_VERSION
        assert h["enabled"] is True
        assert h["degraded"] is True
        assert "persistence" in h
        assert "bridge" in h

    def test_safe_compute_health_score_with_none(self):
        """safe_compute_health_score(None) → 空 health score"""
        h = safe_compute_health_score(None)
        assert h.overall_score == 0.5
        assert h.risk_level == RISK_LEVEL_MEDIUM
        assert h.timestamp > 0.0


# ============================================================
# 10. compute_health_score 测试
# ============================================================

class TestComputeHealthScore:
    """compute_health_score 多维计算"""

    def test_compute_with_outcomes(self, tmp_path):
        """基于 outcome 数据计算 health score"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 注入 7 成功 + 3 失败
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
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        health = intel.compute_health_score()

        # success_score = 0.7
        assert abs(health.success_score - 0.7) < 0.01
        # overall 在 [0,1] 区间
        assert 0.0 <= health.overall_score <= 1.0
        # risk_level 是合法值
        assert health.risk_level in (
            RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH, RISK_LEVEL_CRITICAL,
        )
        assert health.timestamp > 0.0

    def test_compute_with_no_data(self, tmp_path):
        """空数据时 health score 为中性"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        health = intel.compute_health_score()
        assert health.overall_score == 0.5
        assert health.risk_level == RISK_LEVEL_MEDIUM

    def test_health_score_persists(self, tmp_path):
        """health score 持久化到 JSONL"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        intel.compute_health_score()
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["stage"] == INTELLIGENCE_STAGE_ANALYSIS
        assert rec["source"] == "phase_b11_intelligence"


# ============================================================
# 11. analyze_trends 测试
# ============================================================

class TestAnalyzeTrends:
    """analyze_trends 多 metric 分析"""

    def test_analyze_success_rate_trend(self, tmp_path):
        """从 observer 拉 success_rate 趋势"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)

        # 构造 observer with 5 snapshots
        observer = DecisionObserver(persistence=mgr)
        for i in range(5):
            snap = DecisionMetricSnapshot(
                total_actions=10,
                success_rate=0.5 + i * 0.05,
                failure_rate=0.5 - i * 0.05,
                average_confidence_change=0.0,
                boost_count=0,
                suppress_count=0,
                neutral_count=0,
                timestamp=time.time() - (5 - i) * 10.0,  # 5s 间隔,确保都在窗口内
            )
            observer._snapshots.append(snap)

        fake_bridge = MagicMock()
        fake_bridge._decision_observer = observer
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
            trend_window_seconds=3600.0,
        )
        trends = intel.analyze_trends(
            metric="success_rate", window_seconds=3600.0, limit=10,
        )
        # 至少 1 个 TrendPoint
        assert len(trends) >= 1
        tp = trends[0]
        assert tp.metric == "success_rate"
        # values 数量 = 5
        assert tp.sample_size == 5
        # trend 方向:delta = 0.5+0.2 - 0.5 = 0.2 > 0.05 → rising
        assert tp.direction == TREND_RISING
        assert tp.delta > 0.0

    def test_analyze_trend_empty_data(self, tmp_path):
        """无数据时返回空 list"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        observer = DecisionObserver(persistence=mgr)

        fake_bridge = MagicMock()
        fake_bridge._decision_observer = observer
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        trends = intel.analyze_trends(metric="success_rate")
        assert trends == []

    def test_analyze_trend_stable(self, tmp_path):
        """stable 趋势(波动小)"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        observer = DecisionObserver(persistence=mgr)
        for i in range(5):
            snap = DecisionMetricSnapshot(
                total_actions=10,
                success_rate=0.7 + (i % 2) * 0.01,  # 0.7 或 0.71,变化微小
                failure_rate=0.3,
                timestamp=time.time() - (5 - i) * 10.0,
            )
            observer._snapshots.append(snap)

        fake_bridge = MagicMock()
        fake_bridge._decision_observer = observer
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        trends = intel.analyze_trends(
            metric="success_rate", window_seconds=3600.0, limit=10,
        )
        assert len(trends) >= 1
        # delta 微小,应为 stable
        assert trends[0].direction == TREND_STABLE

    def test_analyze_trend_falling(self, tmp_path):
        """falling 趋势(下降)"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        observer = DecisionObserver(persistence=mgr)
        for i in range(5):
            snap = DecisionMetricSnapshot(
                total_actions=10,
                success_rate=0.9 - i * 0.1,  # 0.9 → 0.5
                failure_rate=0.1 + i * 0.1,
                timestamp=time.time() - (5 - i) * 10.0,
            )
            observer._snapshots.append(snap)

        fake_bridge = MagicMock()
        fake_bridge._decision_observer = observer
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        trends = intel.analyze_trends(
            metric="success_rate", window_seconds=3600.0, limit=10,
        )
        assert len(trends) >= 1
        assert trends[0].direction == TREND_FALLING
        assert trends[0].delta < 0.0


# ============================================================
# 12. detect_feedback_drift 测试
# ============================================================

class TestDetectFeedbackDrift:
    """detect_feedback_drift 漂移检测"""

    def test_detect_drift_boosting(self, tmp_path):
        """detect boosting drift"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        # 历史 weight 0.4,当前 0.8 → boosting
        ts_base = time.time()
        for i, w in enumerate([0.3, 0.4, 0.4, 0.5, 0.4, 0.8]):
            rt._contexts.append(DecisionFeedbackContext(
                action_type="g1", original_confidence=0.5, adjusted_confidence=0.5 + w * 0.2,
                recommendation=RECOMMENDATION_BOOST,
                adjustment_reason="test", timestamp=ts_base - (6 - i) * 10.0,
            ))

        fake_bridge = MagicMock()
        fake_bridge._feedback_runtime = rt
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
            drift_window=10,
        )
        drifts = intel.detect_feedback_drift(action_type="g1", window=10)
        assert len(drifts) >= 1
        d = drifts[0]
        # current 0.8 > avg 0.4 → boosting
        assert d.direction == DRIFT_BOOSTING
        assert d.is_anomalous is True
        assert d.drift_magnitude > 0.15

    def test_detect_drift_suppressing(self, tmp_path):
        """detect suppressing drift"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        ts_base = time.time()
        for i, w in enumerate([0.6, 0.7, 0.6, 0.5, 0.6, 0.2]):
            rt._contexts.append(DecisionFeedbackContext(
                action_type="s1", original_confidence=0.5, adjusted_confidence=0.5 - w * 0.2,
                recommendation=RECOMMENDATION_SUPPRESS,
                adjustment_reason="test", timestamp=ts_base - (6 - i) * 10.0,
            ))

        fake_bridge = MagicMock()
        fake_bridge._feedback_runtime = rt
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr, drift_window=10,
        )
        drifts = intel.detect_feedback_drift(action_type="s1", window=10)
        assert len(drifts) >= 1
        assert drifts[0].direction == DRIFT_SUPPRESSING

    def test_detect_drift_no_runtime(self, tmp_path):
        """无 feedback_runtime → 空 list"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fake_bridge = MagicMock()
        fake_bridge._feedback_runtime = None
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        drifts = intel.detect_feedback_drift(action_type="g1")
        assert drifts == []

    def test_detect_drift_stable(self, tmp_path):
        """stable drift(波动小)"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        ts_base = time.time()
        for i, w in enumerate([0.5, 0.51, 0.49, 0.5, 0.52, 0.5]):
            rt._contexts.append(DecisionFeedbackContext(
                action_type="g2", original_confidence=0.5, adjusted_confidence=0.5,
                recommendation=RECOMMENDATION_NEUTRAL,
                adjustment_reason="test", timestamp=ts_base - (6 - i) * 10.0,
            ))

        fake_bridge = MagicMock()
        fake_bridge._feedback_runtime = rt
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr, drift_window=10,
        )
        drifts = intel.detect_feedback_drift(action_type="g2", window=10)
        assert len(drifts) >= 1
        # 0.5 附近波动 → stable
        assert drifts[0].direction == DRIFT_STABLE
        assert drifts[0].is_anomalous is False


# ============================================================
# 13. detect_failure_patterns 测试
# ============================================================

class TestDetectFailurePatterns:
    """detect_failure_patterns 四类模式"""

    def test_detect_consecutive(self, tmp_path):
        """detect consecutive failures"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 注入 4 连失败
        for i in range(4):
            ot._outcomes[f"f{i}"] = OutcomeRecord(
                action_id=f"f{i}", action_type="share", user_id="u1",
                created_at=100.0 + i, completed_at=200.0 + i,
                success=False, score=0.1,
            )

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
            failure_window=10, consecutive_failure_threshold=3,
        )
        patterns = intel.detect_failure_patterns(window=10)
        # 至少包含 consecutive
        pattern_types = [p.pattern_type for p in patterns]
        assert PATTERN_CONSECUTIVE in pattern_types

    def test_detect_burst(self, tmp_path):
        """detect burst failures(高失败率)"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 5/5 失败 = 100% 失败率
        for i in range(5):
            ot._outcomes[f"f{i}"] = OutcomeRecord(
                action_id=f"f{i}", action_type="g{i % 2}", user_id="u1",
                created_at=100.0 + i, completed_at=200.0 + i,
                success=False, score=0.1,
            )

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
            failure_window=10, burst_failure_rate_threshold=0.5,
        )
        patterns = intel.detect_failure_patterns(window=10)
        pattern_types = [p.pattern_type for p in patterns]
        assert PATTERN_BURST in pattern_types

    def test_detect_type_concentration(self, tmp_path):
        """detect type concentration(单类型失败集中)"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 3 share 失败 + 1 greeting 失败 → share 集中度 75%
        ot._outcomes["f1"] = OutcomeRecord(
            action_id="f1", action_type="share", user_id="u1",
            created_at=100.0, completed_at=200.0, success=False, score=0.1,
        )
        ot._outcomes["f2"] = OutcomeRecord(
            action_id="f2", action_type="share", user_id="u1",
            created_at=100.0, completed_at=200.0, success=False, score=0.1,
        )
        ot._outcomes["f3"] = OutcomeRecord(
            action_id="f3", action_type="share", user_id="u1",
            created_at=100.0, completed_at=200.0, success=False, score=0.1,
        )
        ot._outcomes["f4"] = OutcomeRecord(
            action_id="f4", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=False, score=0.1,
        )

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
            failure_window=10,
            type_concentration_threshold=0.6,
            consecutive_failure_threshold=3,
        )
        patterns = intel.detect_failure_patterns(window=10)
        pattern_types = [p.pattern_type for p in patterns]
        assert PATTERN_TYPE_CONCENTRATION in pattern_types

    def test_detect_time_clustering(self, tmp_path):
        """detect time clustering(时间聚集失败)"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        # 3 个失败集中在 10s 内
        ts_base = time.time()
        for i in range(3):
            ot._outcomes[f"f{i}"] = OutcomeRecord(
                action_id=f"f{i}", action_type="share", user_id="u1",
                created_at=ts_base + i, completed_at=ts_base + i + 0.1,
                success=False, score=0.1,
            )

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
            failure_window=10,
            time_cluster_window_seconds=60.0,
            time_cluster_min_failures=3,
        )
        patterns = intel.detect_failure_patterns(window=10)
        pattern_types = [p.pattern_type for p in patterns]
        assert PATTERN_TIME_CLUSTERING in pattern_types

    def test_no_failures(self, tmp_path):
        """无失败时无 pattern"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        for i in range(5):
            ot._outcomes[f"s{i}"] = OutcomeRecord(
                action_id=f"s{i}", action_type="greeting", user_id="u1",
                created_at=100.0 + i, completed_at=200.0 + i,
                success=True, score=0.9,
            )

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_manager = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        patterns = intel.detect_failure_patterns(window=10)
        # 所有失败 pattern 都不应被检测到
        pattern_types = [p.pattern_type for p in patterns]
        assert PATTERN_CONSECUTIVE not in pattern_types
        assert PATTERN_BURST not in pattern_types


# ============================================================
# 14. recommend_strategy 测试
# ============================================================

class TestRecommendStrategy:
    """recommend_strategy 策略建议"""

    def test_recommend_boost(self, tmp_path):
        """高 success rate → boost"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))

        fake_bridge = MagicMock()
        fake_bridge._feedback_manager = fb
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        recs = intel.recommend_strategy(action_types=["greeting"])
        assert len(recs) >= 1
        target = next((r for r in recs if r.target_action_type == "greeting"), None)
        assert target is not None
        # 10/10 成功 → boost
        assert target.action == STRATEGY_BOOST

    def test_recommend_suppress(self, tmp_path):
        """低 success rate → suppress"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb.record_outcome(OutcomeRecord(
                action_id=f"b{i}", action_type="share", user_id="u1",
                created_at=100.0, completed_at=200.0, success=False, score=0.1,
            ))

        fake_bridge = MagicMock()
        fake_bridge._feedback_manager = fb
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        recs = intel.recommend_strategy(action_types=["share"])
        assert len(recs) >= 1
        target = next((r for r in recs if r.target_action_type == "share"), None)
        assert target is not None
        # 全失败 → suppress
        assert target.action == STRATEGY_SUPPRESS

    def test_recommend_no_data(self, tmp_path):
        """无数据 → neutral"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)

        fake_bridge = MagicMock()
        fake_bridge._feedback_manager = fb
        fake_bridge._outcome_tracker = None
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        recs = intel.recommend_strategy(action_types=["newtype"])
        assert len(recs) >= 1
        # 无样本 → neutral
        target = next((r for r in recs if r.target_action_type == "newtype"), None)
        assert target is not None
        assert target.action == STRATEGY_NEUTRAL


# ============================================================
# 15. export_intelligence_report 测试
# ============================================================

class TestExportIntelligenceReport:
    """export_intelligence_report 综合报告"""

    def test_export_report_structure(self, tmp_path):
        """export_report 包含必要字段"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        report = intel.export_intelligence_report()
        for k in (
            "report_version", "generated_at", "health_score",
            "trends", "feedback_drifts", "failure_patterns",
            "strategy_recommendations", "summary",
        ):
            assert k in report, f"missing key: {k}"
        assert report["report_version"] == PHASE_B11_VERSION
        assert report["health_score"]["overall_score"] == 0.5

    def test_export_report_with_data(self, tmp_path):
        """有数据时 export_report 包含完整分析"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        ot = OutcomeTracker(persistence=mgr)
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(5):
            ot.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))

        fake_bridge = MagicMock()
        fake_bridge._outcome_tracker = ot
        fake_bridge._feedback_manager = fb
        fake_bridge._feedback_adapter = None
        fake_bridge._feedback_runtime = None
        fake_bridge._decision_observer = None

        intel = DecisionIntelligence(runtime_bridge=fake_bridge, persistence=mgr)
        report = intel.export_intelligence_report()
        assert report["health_score"]["success_score"] > 0.0
        # 至少有 analysis
        assert intel.analysis_count >= 1


# ============================================================
# 16. health_check 测试
# ============================================================

class TestHealthCheck:
    """health_check 行为"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        h = intel.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "analysis_count", "error_count", "recover_count",
            "in_memory_history", "last_analysis_at", "last_error",
            "created_at", "max_history", "trend_window_seconds",
            "drift_window", "failure_window", "persistence", "bridge",
        ):
            assert k in h, f"missing key: {k}"
        assert h["name"] == PHASE_B11_NAME
        assert h["version"] == PHASE_B11_VERSION
        assert h["schema_version"] == B11_SCHEMA_VERSION

    def test_health_check_after_analysis(self, tmp_path):
        """analysis 后 health_check 反映状态"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        intel.compute_health_score()
        h = intel.health_check()
        assert h["analysis_count"] >= 1
        assert h["last_analysis_at"] is not None

    def test_enable_disable(self):
        """enable / disable 切换"""
        intel = DecisionIntelligence()
        assert intel.enabled is True
        intel.disable()
        assert intel.enabled is False
        intel.enable()
        assert intel.enabled is True

    def test_close(self):
        """close 关闭 intelligence"""
        intel = DecisionIntelligence()
        intel.close()
        assert intel._closed is True


# ============================================================
# 17. persistence 测试
# ============================================================

class TestPersistence:
    """persistence 行为"""

    def test_persist_event_writes(self, tmp_path):
        """compute_health_score 通过 persistence 写盘"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        intel.compute_health_score()
        assert os.path.exists(log_path)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["stage"] == INTELLIGENCE_STAGE_ANALYSIS
        assert rec["source"] == "phase_b11_intelligence"
        result = rec["result"]
        assert result["schema_version"] == B11_SCHEMA_VERSION
        assert "overall_score" in result
        assert "risk_level" in result

    def test_persist_failure_degraded(self, tmp_path):
        """persistence 写盘失败时降级为 memory-only"""
        mgr = MagicMock()
        mgr.persist_event.return_value = False
        mgr.is_degraded = True
        mgr.health_check.return_value = {
            "enabled": True, "degraded": True, "write_count": 0,
            "write_error_count": 5, "recover_count": 0,
        }
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        intel.compute_health_score()
        # 内存仍记录
        assert intel.analysis_count >= 1
        # 错误计数 +1
        assert intel.error_count >= 1

    def test_persist_exception_isolated(self):
        """persistence.persist_event 抛错时隔离"""
        mgr = MagicMock()
        mgr.persist_event.side_effect = RuntimeError("boom")
        mgr.is_degraded = False
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        # 不抛
        intel.compute_health_score()
        assert intel.analysis_count >= 1
        assert intel.error_count >= 1

    def test_load_state_recovers(self, tmp_path):
        """load_state 从 persistence 恢复 analysis"""
        log_path = str(tmp_path / "b11.jsonl")
        # 进程 1:写 3 条 analysis
        mgr1 = ActionPersistenceManager(path=log_path)
        intel1 = DecisionIntelligence(runtime_bridge=None, persistence=mgr1)
        for _ in range(3):
            intel1.compute_health_score()
        assert intel1.analysis_count == 3

        # 进程 2:从文件恢复(构造函数自动调用 load_state)
        mgr2 = ActionPersistenceManager(path=log_path)
        intel2 = DecisionIntelligence(runtime_bridge=None, persistence=mgr2)
        # 内存中应有 3 条
        assert intel2.in_memory_history_count == 3
        # 显式调用 load_state 应返回 0(已去重)
        assert intel2.load_state() == 0
        # 仍然只有 3 条
        assert intel2.in_memory_history_count == 3

    def test_load_state_skips_non_b11_events(self, tmp_path):
        """load_state 跳过非 decision_intelligence_analysis stage"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)

        mgr2 = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr2)
        assert intel.load_state() == 0


# ============================================================
# 18. 工厂 / 便捷函数测试
# ============================================================

class TestFactory:
    """create_decision_intelligence / safe_compute_health_score 工厂"""

    def test_create_basic(self, tmp_path):
        """基础工厂"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        intel = create_decision_intelligence(persistence=mgr)
        assert intel is not None
        assert intel.enabled is True
        assert intel.persistence is mgr

    def test_create_disabled(self, tmp_path):
        """disabled=True 显式关闭"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        intel = create_decision_intelligence(persistence=mgr, enabled=False)
        assert intel.enabled is False

    def test_create_from_cfg(self, tmp_path):
        """从 cfg 创建"""
        cfg = {
            "decision_intelligence": {
                "enabled": True,
                "max_history": 50,
                "trend_window_seconds": 1800.0,
                "drift_window": 10,
                "failure_window": 30,
                "consecutive_failure_threshold": 5,
            }
        }
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        intel = create_decision_intelligence(persistence=mgr, cfg=cfg)
        assert intel is not None
        assert intel._max_history == 50
        assert intel._trend_window_seconds == 1800.0
        assert intel._drift_window == 10
        assert intel._failure_window == 30
        assert intel._consecutive_failure_threshold == 5

    def test_create_with_runtime_bridge(self, tmp_path):
        """带 runtime_bridge 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fake_bridge = MagicMock()
        intel = create_decision_intelligence(
            runtime_bridge=fake_bridge, persistence=mgr,
        )
        assert intel.bridge is fake_bridge

    def test_safe_compute_health_score_with_intel(self, tmp_path):
        """safe_compute_health_score 正常调用"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        h = safe_compute_health_score(intel)
        assert h.overall_score == 0.5
        assert h.risk_level == RISK_LEVEL_MEDIUM


# ============================================================
# 19. 异常隔离测试
# ============================================================

class TestExceptionIsolation:
    """所有方法都被 try/except 隔离,绝不抛"""

    def test_compute_health_score_isolated(self, tmp_path):
        """compute_health_score 异常被隔离"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        # 构造一个 bridge,所有属性抛错
        bad_bridge = MagicMock()
        type(bad_bridge)._decision_observer = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        intel = DecisionIntelligence(runtime_bridge=bad_bridge, persistence=mgr)
        # 不抛
        h = intel.compute_health_score()
        # 返回中性值
        assert h.overall_score == 0.5

    def test_analyze_trends_isolated(self, tmp_path):
        """analyze_trends 异常被隔离"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        bad_bridge = MagicMock()
        type(bad_bridge)._decision_observer = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        intel = DecisionIntelligence(runtime_bridge=bad_bridge, persistence=mgr)
        # 不抛
        trends = intel.analyze_trends(metric="success_rate")
        assert trends == []

    def test_detect_feedback_drift_isolated(self, tmp_path):
        """detect_feedback_drift 异常被隔离"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        bad_bridge = MagicMock()
        type(bad_bridge)._feedback_runtime = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        intel = DecisionIntelligence(runtime_bridge=bad_bridge, persistence=mgr)
        drifts = intel.detect_feedback_drift()
        assert drifts == []

    def test_detect_failure_patterns_isolated(self, tmp_path):
        """detect_failure_patterns 异常被隔离"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        bad_bridge = MagicMock()
        type(bad_bridge)._outcome_tracker = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        intel = DecisionIntelligence(runtime_bridge=bad_bridge, persistence=mgr)
        patterns = intel.detect_failure_patterns()
        assert patterns == []

    def test_recommend_strategy_isolated(self, tmp_path):
        """recommend_strategy 异常被隔离"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        bad_bridge = MagicMock()
        type(bad_bridge)._feedback_manager = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        intel = DecisionIntelligence(runtime_bridge=bad_bridge, persistence=mgr)
        recs = intel.recommend_strategy()
        assert recs == []


# ============================================================
# 20. RuntimeB4Bridge 集成测试
# ============================================================

class TestB4BridgeIntelligenceIntegration:
    """RuntimeB4Bridge 集成 DecisionIntelligence"""

    def test_b4_bridge_has_decision_intelligence(self, tmp_path):
        """RuntimeB4Bridge 持有 decision_intelligence"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        assert b.decision_intelligence is not None
        assert b.decision_intelligence.enabled is True

    def test_b4_bridge_decision_intelligence_no_persistence(self):
        """无 persistence 时 decision_intelligence 仍创建"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.decision_intelligence is not None
        assert b.decision_intelligence.persistence is None
        assert b.decision_intelligence.is_degraded is True

    def test_b4_bridge_explicit_decision_intelligence(self, tmp_path):
        """显式注入 decision_intelligence"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        custom_intel = DecisionIntelligence(
            persistence=mgr, max_history=42, drift_window=5,
        )
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
            decision_intelligence=custom_intel,
        )
        assert b.decision_intelligence is custom_intel
        assert b.decision_intelligence._max_history == 42
        assert b.decision_intelligence._drift_window == 5

    def test_b4_bridge_compute_health_score(self, tmp_path):
        """B.4 bridge 转发 compute_health_score"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        for i in range(5):
            b.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        health = b.compute_health_score()
        for k in (
            "overall_score", "success_score", "stability_score",
            "confidence_score", "recovery_score", "risk_level", "timestamp",
        ):
            assert k in health, f"missing key: {k}"
        assert 0.0 <= health["overall_score"] <= 1.0

    def test_b4_bridge_analyze_trends(self, tmp_path):
        """B.4 bridge 转发 analyze_trends"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        trends = b.analyze_trends(metric="success_rate")
        assert isinstance(trends, list)

    def test_b4_bridge_detect_feedback_drift(self, tmp_path):
        """B.4 bridge 转发 detect_feedback_drift"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        drifts = b.detect_feedback_drift()
        assert isinstance(drifts, list)

    def test_b4_bridge_detect_failure_patterns(self, tmp_path):
        """B.4 bridge 转发 detect_failure_patterns"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        patterns = b.detect_failure_patterns()
        assert isinstance(patterns, list)

    def test_b4_bridge_recommend_strategy(self, tmp_path):
        """B.4 bridge 转发 recommend_strategy"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        recs = b.recommend_strategy()
        assert isinstance(recs, list)

    def test_b4_bridge_export_intelligence_report(self, tmp_path):
        """B.4 bridge 转发 export_intelligence_report"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        report = b.export_intelligence_report()
        for k in (
            "report_version", "generated_at", "health_score",
            "trends", "feedback_drifts", "failure_patterns",
            "strategy_recommendations", "summary",
        ):
            assert k in report, f"missing key: {k}"
        assert report["report_version"] == PHASE_B11_VERSION

    def test_b4_bridge_decision_intelligence_health_check(self, tmp_path):
        """B.4 bridge 转发 decision_intelligence_health_check"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        h = b.decision_intelligence_health_check()
        assert h["name"] == PHASE_B11_NAME
        assert "enabled" in h
        assert "degraded" in h

    def test_b4_bridge_get_intelligence_summary(self, tmp_path):
        """B.4 bridge 转发 get_intelligence_summary"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        s = b.get_intelligence_summary()
        assert s["name"] == PHASE_B11_NAME
        assert s["version"] == PHASE_B11_VERSION

    def test_b4_bridge_summarize_includes_intelligence(self, tmp_path):
        """summarize_b4 包含 decision_intelligence"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.compute_health_score()
        s = b.summarize_b4()
        assert "decision_intelligence" in s
        di = s["decision_intelligence"]
        assert "enabled" in di
        assert "analysis_count" in di
        assert di["enabled"] is True
        assert di["analysis_count"] >= 1

    def test_b4_bridge_compute_health_score_no_intel(self):
        """decision_intelligence=None 时 compute_health_score 返回中性"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        health = b.compute_health_score()
        assert health["overall_score"] == 0.5
        assert health["risk_level"] == RISK_LEVEL_MEDIUM

    def test_b4_bridge_analyze_trends_no_intel(self):
        """decision_intelligence=None 时 analyze_trends 返回空 list"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        trends = b.analyze_trends(metric="success_rate")
        assert trends == []

    def test_b4_bridge_detect_feedback_drift_no_intel(self):
        """decision_intelligence=None 时 detect_feedback_drift 返回空 list"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        drifts = b.detect_feedback_drift()
        assert drifts == []

    def test_b4_bridge_detect_failure_patterns_no_intel(self):
        """decision_intelligence=None 时 detect_failure_patterns 返回空 list"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        patterns = b.detect_failure_patterns()
        assert patterns == []

    def test_b4_bridge_recommend_strategy_no_intel(self):
        """decision_intelligence=None 时 recommend_strategy 返回空 list"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        recs = b.recommend_strategy()
        assert recs == []

    def test_b4_bridge_export_intelligence_report_no_intel(self):
        """decision_intelligence=None 时 export_intelligence_report 返回错误"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        report = b.export_intelligence_report()
        assert "error" in report

    def test_b4_bridge_decision_intelligence_health_check_no_intel(self):
        """decision_intelligence=None 时 health_check 返回零值"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._decision_intelligence = None
        h = b.decision_intelligence_health_check()
        assert h["enabled"] is False


# ============================================================
# 21. B.4-B.10 兼容性测试
# ============================================================

class TestB4ToB10Compatibility:
    """B.11 引入后,B.4-B.10 仍正常"""

    def test_b4_governance_unchanged(self, tmp_path):
        """B.11 引入后,B.4 governance 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        result = b.flush_pending()
        # B.4 governance 仍工作
        assert isinstance(result, dict)

    def test_b5_persistence_unchanged(self, tmp_path):
        """B.11 引入后,B.5 persistence 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        assert mgr.write_count >= 1

    def test_b6_outcome_unchanged(self, tmp_path):
        """B.11 引入后,B.6 outcome 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        assert b.outcome_tracker is not None

    def test_b7_feedback_unchanged(self, tmp_path):
        """B.11 引入后,B.7 feedback 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
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
        """B.11 引入后,B.8 adapter 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
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
        """B.11 引入后,B.9 runtime 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
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
        b.flush_pending()
        ctx = b.get_runtime_feedback("greeting", confidence=0.5)
        assert ctx is not None
        assert ctx.recommendation == RECOMMENDATION_BOOST

    def test_b10_observer_unchanged(self, tmp_path):
        """B.11 引入后,B.10 observer 仍正常"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        for i in range(3):
            b.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        metrics = b.get_decision_metrics()
        assert metrics["total_actions"] == 3
        assert metrics["success_rate"] == 1.0

    def test_b11_does_not_modify_b4_bridge_flush(self, tmp_path):
        """B.11 不修改 B.4 bridge 的 flush_pending 行为"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # flush_pending 不主动调用 intelligence(按需)
        b.flush_pending()
        # intelligence 没有自动 collect
        # (intelligence 是按需调用:compute_health_score / export_intelligence_report)
        assert b.decision_intelligence is not None


# ============================================================
# 22. 核心模块未修改保护
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

    def test_no_proactive_engine_intelligence_references(self):
        """ProactiveEngine 不应直接引用 intelligence(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        proactive = repo_root / "src" / "proactive" / "proactive_engine.py"
        if not proactive.exists():
            pytest.skip("proactive_engine not found")
        src = proactive.read_text(encoding="utf-8")
        assert "phase_b11" not in src, "proactive_engine should not reference phase_b11"
        assert "decision_intelligence" not in src, (
            "proactive_engine should not reference decision_intelligence"
        )

    def test_no_supervisor_intelligence_references(self):
        """supervisor.py 不应直接引用 intelligence(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        sup = repo_root / "src" / "runtime" / "supervisor.py"
        if not sup.exists():
            pytest.skip("supervisor not found")
        src = sup.read_text(encoding="utf-8")
        assert "phase_b11" not in src, "supervisor should not reference phase_b11"
        assert "decision_intelligence" not in src, (
            "supervisor should not reference decision_intelligence"
        )

    def test_no_runtime_core_intelligence_references(self):
        """runtime_core.py 不应直接引用 intelligence(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        rc = repo_root / "src" / "runtime" / "runtime_core.py"
        if not rc.exists():
            pytest.skip("runtime_core not found")
        src = rc.read_text(encoding="utf-8")
        assert "phase_b11" not in src, "runtime_core should not reference phase_b11"
        assert "decision_intelligence" not in src, (
            "runtime_core should not reference decision_intelligence"
        )

    def test_no_action_dispatcher_intelligence_references(self):
        """action_dispatcher.py 不应直接引用 intelligence(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        ad = repo_root / "src" / "runtime" / "action_dispatcher.py"
        if not ad.exists():
            pytest.skip("action_dispatcher not found")
        src = ad.read_text(encoding="utf-8")
        assert "phase_b11" not in src, "action_dispatcher should not reference phase_b11"
        assert "decision_intelligence" not in src, (
            "action_dispatcher should not reference decision_intelligence"
        )


# ============================================================
# 23. Schema 验证
# ============================================================

class TestSchema:
    """持久化结构符合 schema"""

    def test_persistence_payload_schema(self, tmp_path):
        """持久化文件结构正确"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        intel = DecisionIntelligence(runtime_bridge=None, persistence=mgr)
        intel.compute_health_score()

        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        rec = json.loads(lines[-1])
        assert rec["stage"] == INTELLIGENCE_STAGE_ANALYSIS
        assert rec["source"] == "phase_b11_intelligence"
        result = rec["result"]
        assert result["schema_version"] == B11_SCHEMA_VERSION
        assert "overall_score" in result
        assert "risk_level" in result
        assert "timestamp" in result

    def test_intelligence_stage_constant(self):
        """stage 常量正确"""
        assert INTELLIGENCE_STAGE_ANALYSIS == "decision_intelligence_analysis"

    def test_schema_version_constant(self):
        """schema_version 常量"""
        assert B11_SCHEMA_VERSION == "1.0"

    def test_persistence_version_constant(self):
        """PHASE_B11_VERSION"""
        assert PHASE_B11_VERSION == "1.0.0"

    def test_name_constant(self):
        """PHASE_B11_NAME"""
        assert PHASE_B11_NAME == "phase_b11"


# ============================================================
# 24. 闭环验证
# ============================================================

class TestClosedLoop:
    """完整闭环验证"""

    def test_full_loop_b4_to_b11(self, tmp_path):
        """完整闭环:outcome → feedback → adapter → runtime → observer → intelligence"""
        log_path = str(tmp_path / "b11.jsonl")
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

        # 3) B.11 health score
        health = b.compute_health_score()
        assert 0.0 <= health["overall_score"] <= 1.0
        assert health["risk_level"] in (
            RISK_LEVEL_LOW, RISK_LEVEL_MEDIUM, RISK_LEVEL_HIGH, RISK_LEVEL_CRITICAL,
        )

        # 4) B.11 trends
        trends = b.analyze_trends(metric="success_rate")
        assert isinstance(trends, list)

        # 5) B.11 failure patterns(5 失败 share → 至少 burst/consecutive)
        patterns = b.detect_failure_patterns()
        assert isinstance(patterns, list)

        # 6) B.11 strategy recommendations
        recs = b.recommend_strategy()
        assert isinstance(recs, list)

        # 7) B.11 export report
        report = b.export_intelligence_report()
        assert report["report_version"] == PHASE_B11_VERSION
        assert "health_score" in report

        # 8) summarize_b4 包含完整 B.11 状态
        s = b.summarize_b4()
        assert "decision_intelligence" in s
        di = s["decision_intelligence"]
        assert di["enabled"] is True
        assert di["analysis_count"] >= 1

    def test_b11_recommendations_aligned_with_data(self, tmp_path):
        """B.11 recommendations 与实际数据一致"""
        log_path = str(tmp_path / "b11.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )

        # 5 greeting 成功
        for i in range(5):
            inner.set_recent([{
                "action_id": f"a{i}", "stage": "dispatched",
                "action_type": "greeting", "user_id": "u1",
            }])
            b.flush_pending()

        recs = b.recommend_strategy(action_types=["greeting"])
        assert len(recs) >= 1
        target = next((r for r in recs if r["target_action_type"] == "greeting"), None)
        assert target is not None
        # 5/5 成功 → 应建议 boost
        assert target["action"] == STRATEGY_BOOST
