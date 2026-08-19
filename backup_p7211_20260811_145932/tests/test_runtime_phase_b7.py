# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b7.py

Phase B.7 Runtime Integration —— Action Decision Feedback Integration 测试

覆盖:
  1. 配置测试(default / override / 不修改入参)
  2. ActionBehaviorProfile 测试(创建 / update / to_dict / from_dict / is_reliable)
  3. DecisionFeedbackScore 测试(创建 / to_dict / from_dict)
  4. ActionFeedbackManager 写入测试(append-only JSONL via persistence)
  5. ActionFeedbackManager profile 统计测试
  6. ActionFeedbackManager 异常写盘(降级为 memory-only)
  7. ActionFeedbackManager persistence=None / disabled 行为
  8. ActionFeedbackManager 重启恢复(load_state)
  9. ActionFeedbackManager DecisionFeedbackScore 计算测试
 10. RuntimeB4Bridge 集成(record_feedback / get_decision_feedback_score /
     get_behavior_score / get_action_type_profile)
 11. RuntimeB4Bridge 自动 feedback 记录(dispatch 完成后同步)
 12. 兼容:B.6 / B.5 / B.4 现有功能不受影响
 13. 核心模块未修改保护(supervisor / runtime_core / proactive_engine /
     action_dispatcher / ActionConfidenceGate)

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

from src.runtime.action_feedback import (
    PHASE_B7_DEFAULT_CONFIG,
    PHASE_B7_NAME,
    PHASE_B7_VERSION,
    DEFAULT_FEEDBACK_PATH,
    SCHEMA_VERSION as FB_SCHEMA_VERSION,
    FEEDBACK_STAGE_UPDATED,
    FEEDBACK_BOOST_THRESHOLD,
    FEEDBACK_SUPPRESS_THRESHOLD,
    DEFAULT_NEUTRAL_WEIGHT,
    apply_phase_b7_config,
    is_phase_b7_enabled,
    is_b7_enabled_simple,
    ActionBehaviorProfile,
    DecisionFeedbackScore,
    ActionFeedbackManager,
    create_feedback_manager,
    safe_record_outcome_to_feedback,
    compute_decision_feedback_score,
)

from src.runtime.action_outcome import (
    OutcomeRecord,
    OutcomeTracker,
    create_outcome_tracker,
    SCHEMA_VERSION as OUTCOME_SCHEMA_VERSION,
    OUTCOME_STAGE_RECORDED,
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

class TestB7ConfigInjection:
    """PHASE_B7_DEFAULT_CONFIG / apply_phase_b7_config / is_phase_b7_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 action_feedback.enabled=True"""
        cfg = PHASE_B7_DEFAULT_CONFIG
        assert "action_feedback" in cfg
        assert cfg["action_feedback"]["enabled"] is True
        assert cfg["action_feedback"]["path"] == "data/action_feedback.jsonl"
        assert cfg["action_feedback"]["auto_recover"] is True
        assert cfg["action_feedback"]["recent_failure_window"] >= 1
        assert cfg["action_feedback"]["recency_failure_threshold"] >= 1

    def test_default_on(self):
        """B.7 默认开启(但不阻塞)"""
        assert PHASE_B7_DEFAULT_CONFIG["action_feedback"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b7_config(None)
        assert result["action_feedback"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b7_config({"action_feedback": {"enabled": False}})
        assert result["action_feedback"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {"action_feedback": {"enabled": False, "path": "/tmp/x.jsonl"}}
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b7_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b7_config("not a dict")["action_feedback"]["enabled"] is True
        assert apply_phase_b7_config(42)["action_feedback"]["enabled"] is True
        assert apply_phase_b7_config([])["action_feedback"]["enabled"] is True

    def test_is_phase_b7_enabled(self):
        """is_phase_b7_enabled 判定正确"""
        assert is_phase_b7_enabled({"action_feedback": {"enabled": True}}) is True
        assert is_phase_b7_enabled({"action_feedback": {"enabled": False}}) is False
        assert is_phase_b7_enabled({}) is True
        assert is_phase_b7_enabled(None) is False

    def test_is_b7_enabled_simple_variants(self):
        """is_b7_enabled_simple 兼容 cfg['feedback_enabled']"""
        assert is_b7_enabled_simple({"feedback_enabled": True}) is True
        assert is_b7_enabled_simple({"feedback_enabled": False}) is False
        assert is_b7_enabled_simple({"action_feedback": {"enabled": True}}) is True
        assert is_b7_enabled_simple({}) is True
        assert is_b7_enabled_simple(None) is False


# ============================================================
# 4. ActionBehaviorProfile 测试
# ============================================================

class TestActionBehaviorProfile:
    """ActionBehaviorProfile 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        p = ActionBehaviorProfile(action_type="greeting")
        assert p.action_type == "greeting"
        assert p.total_count == 0
        assert p.success_count == 0
        assert p.failure_count == 0
        assert p.success_rate == 0.0
        assert p.average_score == 0.0
        assert p.last_updated == 0.0

    def test_update_from_outcome_first_success(self):
        """第一条 success outcome"""
        p = ActionBehaviorProfile(action_type="greeting")
        p.update_from_outcome(success=True, score=0.8)
        assert p.total_count == 1
        assert p.success_count == 1
        assert p.failure_count == 0
        assert p.success_rate == 1.0
        assert abs(p.average_score - 0.8) < 1e-6
        assert p.last_updated > 0.0

    def test_update_from_outcome_first_failure(self):
        """第一条 failure outcome"""
        p = ActionBehaviorProfile(action_type="greeting")
        p.update_from_outcome(success=False, score=0.2)
        assert p.total_count == 1
        assert p.success_count == 0
        assert p.failure_count == 1
        assert p.success_rate == 0.0
        assert abs(p.average_score - 0.2) < 1e-6

    def test_update_from_outcome_running_average(self):
        """多次 update 维护 running average"""
        p = ActionBehaviorProfile(action_type="greeting")
        p.update_from_outcome(success=True, score=0.6)
        p.update_from_outcome(success=True, score=0.8)
        p.update_from_outcome(success=False, score=0.4)
        # total=3, success=2, failure=1, rate=2/3
        assert p.total_count == 3
        assert p.success_count == 2
        assert p.failure_count == 1
        assert abs(p.success_rate - 2/3) < 1e-6
        # avg = (0.6+0.8+0.4)/3 = 0.6
        assert abs(p.average_score - 0.6) < 1e-6

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        p = ActionBehaviorProfile(
            action_type="greeting",
            total_count=10,
            success_count=8,
            failure_count=2,
            success_rate=0.8,
            average_score=0.75,
            last_updated=1234.5,
        )
        d = p.to_dict()
        assert d["action_type"] == "greeting"
        assert d["total_count"] == 10
        assert d["success_count"] == 8
        assert d["failure_count"] == 2
        assert d["success_rate"] == 0.8
        assert d["average_score"] == 0.75
        assert d["last_updated"] == 1234.5

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = ActionBehaviorProfile(
            action_type="share",
            total_count=5,
            success_count=3,
            failure_count=2,
            success_rate=0.6,
            average_score=0.5,
            last_updated=1000.0,
        )
        d = original.to_dict()
        restored = ActionBehaviorProfile.from_dict(d)
        assert restored.action_type == original.action_type
        assert restored.total_count == original.total_count
        assert restored.success_count == original.success_count
        assert restored.failure_count == original.failure_count
        assert restored.success_rate == original.success_rate
        assert restored.average_score == original.average_score
        assert restored.last_updated == original.last_updated

    def test_is_reliable(self):
        """is_reliable 判定"""
        p = ActionBehaviorProfile(action_type="x")
        p.update_from_outcome(True, 0.5)
        p.update_from_outcome(True, 0.5)
        # total=2, 不可信
        assert p.is_reliable(min_count=3) is False
        p.update_from_outcome(False, 0.5)
        # total=3, 可信
        assert p.is_reliable(min_count=3) is True


# ============================================================
# 5. DecisionFeedbackScore 测试
# ============================================================

class TestDecisionFeedbackScore:
    """DecisionFeedbackScore 数据类"""

    def test_default_neutral(self):
        """无历史时返回中性默认"""
        s = DecisionFeedbackScore(action_type="greeting")
        assert s.action_type == "greeting"
        assert s.historical_success_rate == DEFAULT_NEUTRAL_WEIGHT
        assert s.historical_avg_score == DEFAULT_NEUTRAL_WEIGHT
        assert s.recent_failure_count == 0
        assert s.recent_total_count == 0
        assert s.feedback_weight == DEFAULT_NEUTRAL_WEIGHT
        assert s.confidence_penalty == 0.0
        assert s.recommendation == "neutral"
        assert s.sample_size == 0
        assert s.has_history is False

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        s = DecisionFeedbackScore(
            action_type="greeting",
            historical_success_rate=0.8,
            historical_avg_score=0.7,
            recent_failure_count=2,
            recent_total_count=10,
            feedback_weight=0.75,
            confidence_penalty=-0.1,
            recommendation="boost",
            sample_size=10,
            has_history=True,
        )
        d = s.to_dict()
        assert d["action_type"] == "greeting"
        assert d["historical_success_rate"] == 0.8
        assert d["historical_avg_score"] == 0.7
        assert d["recent_failure_count"] == 2
        assert d["recent_total_count"] == 10
        assert d["feedback_weight"] == 0.75
        assert d["confidence_penalty"] == -0.1
        assert d["recommendation"] == "boost"
        assert d["sample_size"] == 10
        assert d["has_history"] is True

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = DecisionFeedbackScore(
            action_type="share",
            historical_success_rate=0.5,
            historical_avg_score=0.4,
            recent_failure_count=3,
            recent_total_count=8,
            feedback_weight=0.45,
            confidence_penalty=0.0,
            recommendation="neutral",
            sample_size=8,
            has_history=True,
        )
        d = original.to_dict()
        restored = DecisionFeedbackScore.from_dict(d)
        assert restored.action_type == original.action_type
        assert restored.historical_success_rate == original.historical_success_rate
        assert restored.feedback_weight == original.feedback_weight
        assert restored.recommendation == original.recommendation


# ============================================================
# 6. ActionFeedbackManager 写入测试
# ============================================================

class TestFeedbackManagerWrite:
    """ActionFeedbackManager 写盘行为"""

    def test_record_outcome_writes_to_persistence(self, tmp_path):
        """record_outcome 通过 persistence 写盘"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = fb.record_outcome(outcome)
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
        assert rec["stage"] == FEEDBACK_STAGE_UPDATED
        assert rec["decision"] == "feedback_updated"
        assert rec["result"]["action_type"] == "greeting"
        assert rec["result"]["success"] is True

    def test_record_outcome_updates_profile(self, tmp_path):
        """record_outcome 更新内存 profile"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        for i, (ok, score) in enumerate([
            (True, 0.8), (True, 0.9), (False, 0.3),
        ]):
            fb.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=ok, score=score,
            ))
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 3
        assert profile.success_count == 2
        assert profile.failure_count == 1
        assert abs(profile.success_rate - 2/3) < 1e-6
        # avg = (0.8+0.9+0.3)/3 = 0.667
        assert abs(profile.average_score - (0.8+0.9+0.3)/3) < 1e-3

    def test_record_outcome_no_persistence(self):
        """无 persistence 时仍可 record(只内存)"""
        fb = ActionFeedbackManager(persistence=None)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = fb.record_outcome(outcome)
        # 无 persistence,内存已记录
        assert ok is True
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 1

    def test_record_outcome_disabled(self, tmp_path):
        """enabled=False 时只更新内存,不写盘"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr, enabled=False)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = fb.record_outcome(outcome)
        # disabled 时返回 True(等同没失败),内存已更新
        assert ok is True
        # 但 persistence 没写
        assert mgr.write_count == 0
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 1

    def test_record_outcome_empty_action_type(self, tmp_path):
        """action_type 为空时跳过"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True,
        )
        ok = fb.record_outcome(outcome)
        assert ok is False
        assert mgr.write_count == 0

    def test_record_outcome_none_input(self, tmp_path):
        """None 输入处理"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        ok = fb.record_outcome(None)  # type: ignore[arg-type]
        assert ok is False

    def test_record_outcomes_batch(self, tmp_path):
        """批量 record_outcome"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        outcomes = [
            OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=True, score=0.5,
            )
            for i in range(5)
        ]
        n = fb.record_outcomes_batch(outcomes)
        assert n == 5
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 5


# ============================================================
# 7. ActionFeedbackManager 异常写盘(降级)
# ============================================================

class TestFeedbackManagerFailureIsolation:
    """写盘失败自动降级"""

    def test_degraded_after_persistence_failure(self):
        """persistence 写盘失败 → record_outcome 返回 False(但内存仍记录)"""
        mgr = MagicMock()
        mgr.is_degraded = True
        mgr.persist_event.return_value = False  # 模拟降级
        fb = ActionFeedbackManager(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = fb.record_outcome(outcome)
        assert ok is False
        # 但内存仍记录
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 1
        # 错误计数 +1
        assert fb.update_error_count >= 1

    def test_persistence_exception_isolated(self):
        """persistence.persist_event 抛错时隔离"""
        mgr = MagicMock()
        mgr.is_degraded = False
        mgr.persist_event.side_effect = RuntimeError("boom")
        fb = ActionFeedbackManager(persistence=mgr)
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0,
            success=True, score=0.8,
        )
        ok = fb.record_outcome(outcome)
        # 异常隔离 → 内存已记录,但返回 False
        assert ok is False
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 1

    def test_safe_record_outcome_handles_none_manager(self):
        """safe_record_outcome_to_feedback(None, ...) → False"""
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True,
        )
        assert safe_record_outcome_to_feedback(None, outcome) is False

    def test_safe_record_outcome_handles_none_outcome(self):
        """safe_record_outcome_to_feedback(mgr, None) → False"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        fb = ActionFeedbackManager(persistence=mgr)
        assert safe_record_outcome_to_feedback(fb, None) is False  # type: ignore[arg-type]

    def test_safe_record_outcome_handles_exception(self):
        """safe_record_outcome_to_feedback 处理 record 抛错"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        fb = ActionFeedbackManager(persistence=mgr)
        # mock manager.record_outcome 抛错
        fb.record_outcome = MagicMock(side_effect=RuntimeError("boom"))
        outcome = OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True,
        )
        assert safe_record_outcome_to_feedback(fb, outcome) is False


# ============================================================
# 8. ActionFeedbackManager 行为评分 / profile 查询
# ============================================================

class TestFeedbackManagerBehaviorScore:
    """get_behavior_score / get_action_type_profile / get_all_profiles"""

    def test_get_behavior_score_no_history(self):
        """无历史时返回中性"""
        fb = ActionFeedbackManager(persistence=None)
        score = fb.get_behavior_score("greeting")
        assert score == DEFAULT_NEUTRAL_WEIGHT

    def test_get_behavior_score_basic(self):
        """混合 outcome 计算 behavior_score"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        # 注入 outcome
        for i, (ok, score) in enumerate([
            (True, 0.9), (True, 0.8), (False, 0.4),
        ]):
            fb._profiles.setdefault("greeting", ActionBehaviorProfile(action_type="greeting"))
            fb._profiles["greeting"].update_from_outcome(success=ok, score=score)
        # rate=2/3, avg=0.7
        # behavior_score = rate*0.6 + avg*0.4
        expected = (2/3) * 0.6 + 0.7 * 0.4
        score = fb.get_behavior_score("greeting")
        assert abs(score - expected) < 1e-3

    def test_get_action_type_profile_empty(self):
        """无历史时返回空 profile"""
        fb = ActionFeedbackManager(persistence=None)
        p = fb.get_action_type_profile("nonexistent")
        assert p.action_type == "nonexistent"
        assert p.total_count == 0

    def test_get_action_type_profile_returns_copy(self):
        """返回深拷贝,避免外部修改污染"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        fb._profiles["greeting"] = ActionBehaviorProfile(
            action_type="greeting", total_count=5, success_count=4,
        )
        p1 = fb.get_action_type_profile("greeting")
        p1.total_count = 999  # 修改副本
        p2 = fb.get_action_type_profile("greeting")
        assert p2.total_count == 5  # 内部未变

    def test_get_all_profiles(self):
        """get_all_profiles 返回所有 profile 快照"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        for at in ["greeting", "share"]:
            fb._profiles[at] = ActionBehaviorProfile(action_type=at, total_count=3)
        all_p = fb.get_all_profiles()
        assert "greeting" in all_p
        assert "share" in all_p
        assert all_p["greeting"].total_count == 3


# ============================================================
# 9. DecisionFeedbackScore 计算测试
# ============================================================

class TestDecisionFeedbackScoreComputation:
    """get_decision_feedback_score 的计算正确性"""

    def test_no_history_returns_neutral(self):
        """无历史 → 中性评分"""
        fb = ActionFeedbackManager(persistence=None)
        s = fb.get_decision_feedback_score("greeting")
        assert s.action_type == "greeting"
        assert s.feedback_weight == DEFAULT_NEUTRAL_WEIGHT
        assert s.recommendation == "neutral"
        assert s.has_history is False
        assert s.sample_size == 0

    def test_high_success_rate_boost(self):
        """高成功率 + 高 score + 无失败 → boost"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb._profiles.setdefault("greeting", ActionBehaviorProfile(action_type="greeting"))
            fb._profiles["greeting"].update_from_outcome(success=True, score=0.9)
        s = fb.get_decision_feedback_score("greeting")
        assert s.feedback_weight >= FEEDBACK_BOOST_THRESHOLD
        assert s.recommendation == "boost"
        assert s.confidence_penalty < 0
        assert s.has_history is True

    def test_low_success_rate_suppress(self):
        """低成功率 + 低 score → suppress"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb._profiles.setdefault("greeting", ActionBehaviorProfile(action_type="greeting"))
            fb._profiles["greeting"].update_from_outcome(success=False, score=0.1)
        s = fb.get_decision_feedback_score("greeting")
        assert s.feedback_weight <= FEEDBACK_SUPPRESS_THRESHOLD
        assert s.recommendation == "suppress"
        assert s.confidence_penalty > 0

    def test_mixed_neutral(self):
        """混合 outcomes → neutral"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb._profiles.setdefault("greeting", ActionBehaviorProfile(action_type="greeting"))
            fb._profiles["greeting"].update_from_outcome(success=(i % 2 == 0), score=0.5)
        s = fb.get_decision_feedback_score("greeting")
        # rate=0.5, avg=0.5, recency depends
        # weight = 0.4*0.5 + 0.4*0.5 + 0.2*recency
        # recency_factor: 10 outcomes, 5 fails, threshold=5, so recency = 0
        # weight = 0.2 + 0.2 + 0 = 0.4 (neutral)
        assert 0.3 <= s.feedback_weight <= 0.7
        assert s.recommendation in ("neutral", "suppress", "boost")

    def test_recent_failure_count(self):
        """recent_failure_count 正确"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr, recent_failure_window=5)
        for i in range(5):
            fb._recent_outcomes.setdefault("greeting", [])
            fb._recent_outcomes["greeting"].append({
                "success": False, "score": 0.2, "ts": 100.0 + i,
                "action_id": f"a{i}",
            })
        assert fb.get_recent_failure_count("greeting") == 5
        assert fb.get_recent_total_count("greeting") == 5

    def test_compute_decision_feedback_score_safe(self):
        """compute_decision_feedback_score 安全"""
        # manager=None → 返回中性
        s = compute_decision_feedback_score(None, "greeting")
        assert s.action_type == "greeting"
        assert s.feedback_weight == DEFAULT_NEUTRAL_WEIGHT

    def test_apply_feedback_to_confidence(self):
        """apply_feedback_to_confidence 应用 penalty"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb._profiles.setdefault("greeting", ActionBehaviorProfile(action_type="greeting"))
            fb._profiles["greeting"].update_from_outcome(success=True, score=0.9)
        # boost → penalty = -0.1
        adjusted = fb.apply_feedback_to_confidence("greeting", base_confidence=0.5)
        # 0.5 + (-0.1) = 0.4
        assert abs(adjusted - 0.4) < 1e-6

        for i in range(10):
            fb._profiles.setdefault("share", ActionBehaviorProfile(action_type="share"))
            fb._profiles["share"].update_from_outcome(success=False, score=0.1)
        # suppress → penalty = +0.3
        adjusted = fb.apply_feedback_to_confidence("share", base_confidence=0.5)
        # 0.5 + 0.3 = 0.8 (clipped to 1.0)
        assert abs(adjusted - 0.8) < 1e-6

    def test_apply_feedback_clipped(self):
        """apply_feedback_to_confidence 不超过 [0, 1]"""
        mgr = MagicMock()
        fb = ActionFeedbackManager(persistence=mgr)
        for i in range(10):
            fb._profiles.setdefault("greeting", ActionBehaviorProfile(action_type="greeting"))
            fb._profiles["greeting"].update_from_outcome(success=True, score=0.9)
        # boost → penalty = -0.1
        # base = 0.05 → 0.05 - 0.1 = -0.05 → clipped to 0
        adjusted = fb.apply_feedback_to_confidence("greeting", base_confidence=0.05)
        assert adjusted == 0.0


# ============================================================
# 10. ActionFeedbackManager 持久化恢复
# ============================================================

class TestFeedbackManagerRecovery:
    """load_state 恢复 profile"""

    def test_load_state_no_persistence(self):
        """无 persistence 时返回 0"""
        fb = ActionFeedbackManager(persistence=None)
        assert fb.load_state() == 0

    def test_load_state_empty(self, tmp_path):
        """空文件 → 恢复 0 条"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        assert fb.load_state() == 0

    def test_load_state_recovers_profiles(self, tmp_path):
        """从 JSONL 恢复 profile"""
        log_path = str(tmp_path / "feedback.jsonl")
        # 进程 1:写入多个 outcome
        mgr1 = ActionPersistenceManager(path=log_path)
        fb1 = ActionFeedbackManager(persistence=mgr1)
        for i in range(3):
            fb1.record_outcome(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0,
                success=(i % 2 == 0), score=0.7,
            ))

        # 进程 2:从文件恢复(构造时自动 load_state)
        mgr2 = ActionPersistenceManager(path=log_path)
        fb2 = ActionFeedbackManager(persistence=mgr2)
        # 内存验证:auto-load 已恢复
        assert fb2.in_memory_count == 1
        # greeting profile 应存在
        profile = fb2.get_action_type_profile("greeting")
        assert profile.total_count >= 2  # latest snapshot

    def test_load_state_skips_non_feedback_events(self, tmp_path):
        """load_state 只关注 feedback_updated 事件"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)
        mgr1.persist_event("a2", "lc2", LifecycleStage.CREATED)

        mgr2 = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr2)
        count = fb.load_state()
        assert count == 0

    def test_load_state_corrupt_line_skipped(self, tmp_path):
        """损坏行被跳过"""
        log_path = str(tmp_path / "feedback.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "action_id": "feedback_greeting", "lifecycle_id": "feedback_greeting_1",
                "stage": FEEDBACK_STAGE_UPDATED, "ts": 200.0,
                "result": {
                    "action_type": "greeting",
                    "success": True, "score": 0.7,
                    "ts": 200.0,
                    "profile_snapshot": {
                        "action_type": "greeting",
                        "total_count": 1, "success_count": 1,
                        "failure_count": 0, "success_rate": 1.0,
                        "average_score": 0.7, "last_updated": 200.0,
                    },
                },
            }) + "\n")
            f.write("not valid json\n")
        mgr = ActionPersistenceManager(path=log_path)
        fb = ActionFeedbackManager(persistence=mgr)
        # 损坏行被跳过,1 个有效 profile 被恢复
        assert fb.in_memory_count == 1
        profile = fb.get_action_type_profile("greeting")
        assert profile.total_count == 1


# ============================================================
# 11. ActionFeedbackManager 健康度
# ============================================================

class TestFeedbackManagerHealth:
    """health_check / clear / reset_stats"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fb = ActionFeedbackManager(persistence=mgr)
        h = fb.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "in_memory_profiles", "update_count",
            "update_error_count", "recover_count", "last_update_at",
            "last_error", "created_at", "persistence",
            "recent_failure_window", "recency_failure_threshold",
        ):
            assert k in h, f"missing key: {k}"
        assert h["name"] == PHASE_B7_NAME
        assert h["schema_version"] == FB_SCHEMA_VERSION

    def test_reset_stats(self, tmp_path):
        """reset_stats 重置统计"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fb = ActionFeedbackManager(persistence=mgr)
        fb.record_outcome(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.5,
        ))
        assert fb.update_count >= 1
        fb.reset_stats()
        assert fb.update_count == 0

    def test_clear(self, tmp_path):
        """clear 清空内存(不删文件)"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fb = ActionFeedbackManager(persistence=mgr)
        fb.record_outcome(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.5,
        ))
        assert fb.in_memory_count == 1
        fb.clear()
        assert fb.in_memory_count == 0
        # 文件仍存在
        assert os.path.exists(str(tmp_path / "x.jsonl"))


# ============================================================
# 12. create_feedback_manager 工厂
# ============================================================

class TestCreateFeedbackManager:
    """create_feedback_manager 工厂"""

    def test_create_with_persistence(self, tmp_path):
        """带 persistence 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fb = create_feedback_manager(persistence=mgr)
        assert fb is not None
        assert fb.persistence is mgr
        assert fb.enabled is True

    def test_create_with_disabled(self, tmp_path):
        """disabled=True 显式关闭"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fb = create_feedback_manager(persistence=mgr, enabled=False)
        assert fb.enabled is False

    def test_create_with_no_persistence(self):
        """无 persistence 创建"""
        fb = create_feedback_manager(persistence=None)
        assert fb is not None
        assert fb.persistence is None
        assert fb.is_degraded is True

    def test_create_from_cfg(self, tmp_path):
        """从 cfg 创建"""
        cfg = {
            "action_feedback": {
                "enabled": True,
                "max_records": 100,
                "recent_failure_window": 5,
                "recency_failure_threshold": 3,
            }
        }
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        fb = create_feedback_manager(persistence=mgr, cfg=cfg)
        assert fb is not None
        assert fb._max_records == 100
        assert fb._recent_failure_window == 5
        assert fb._recency_failure_threshold == 3

    def test_create_with_outcome_tracker(self, tmp_path):
        """带 outcome_tracker 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        tracker = OutcomeTracker(persistence=mgr)
        fb = create_feedback_manager(persistence=mgr, outcome_tracker=tracker)
        assert fb.outcome_tracker is tracker


# ============================================================
# 13. RuntimeB4Bridge 集成 feedback
# ============================================================

class TestB4BridgeFeedbackIntegration:
    """RuntimeB4Bridge 集成 ActionFeedbackManager"""

    def test_b4_bridge_has_feedback_manager(self, tmp_path):
        """RuntimeB4Bridge 持有 feedback_manager"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        assert b.feedback_manager is not None
        assert b.feedback_manager.persistence is mgr

    def test_b4_bridge_feedback_manager_persistence_none(self):
        """无 persistence 时 feedback_manager 仍可工作(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.feedback_manager is not None
        assert b.feedback_manager.persistence is None

    def test_b4_bridge_explicit_feedback_manager(self, tmp_path):
        """显式注入 feedback_manager"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        custom_fb = ActionFeedbackManager(persistence=mgr, max_records=42)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
            feedback_manager=custom_fb,
        )
        assert b.feedback_manager is custom_fb
        assert b.feedback_manager._max_records == 42

    def test_b4_bridge_record_feedback(self, tmp_path):
        """RuntimeB4Bridge.record_feedback 转发到 feedback_manager"""
        log_path = str(tmp_path / "feedback.jsonl")
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
        ok = b.record_feedback(outcome)
        assert ok is True
        # feedback profile 已更新
        profile = b.feedback_manager.get_action_type_profile("greeting")
        assert profile.total_count == 1
        # 计数已更新
        assert b._total_feedback_recorded == 1

    def test_b4_bridge_record_feedback_governance_off(self, tmp_path):
        """governance OFF 时 record_feedback 拒绝"""
        log_path = str(tmp_path / "feedback.jsonl")
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
        ok = b.record_feedback(outcome)
        # governance OFF → False
        assert ok is False

    def test_b4_bridge_get_decision_feedback_score(self, tmp_path):
        """B.4 bridge 转发 get_decision_feedback_score"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 注入 10 个成功 outcome
        for i in range(10):
            b.record_feedback(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        score = b.get_decision_feedback_score("greeting")
        assert score["action_type"] == "greeting"
        assert score["sample_size"] == 10
        assert score["feedback_weight"] >= FEEDBACK_BOOST_THRESHOLD
        assert score["recommendation"] == "boost"

    def test_b4_bridge_get_behavior_score(self, tmp_path):
        """B.4 bridge 转发 get_behavior_score"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 注入 outcome
        for i, (ok, sc) in enumerate([
            (True, 0.8), (True, 0.8), (False, 0.2),
        ]):
            b.record_feedback(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=ok, score=sc,
            ))
        score = b.get_behavior_score("greeting")
        assert 0.0 <= score <= 1.0

    def test_b4_bridge_get_action_type_profile(self, tmp_path):
        """B.4 bridge 转发 get_action_type_profile"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.record_feedback(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8,
        ))
        profile = b.get_action_type_profile("greeting")
        assert profile["action_type"] == "greeting"
        assert profile["total_count"] == 1
        assert profile["success_count"] == 1

    def test_b4_bridge_apply_feedback_to_confidence(self, tmp_path):
        """B.4 bridge 转发 apply_feedback_to_confidence"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 高成功率
        for i in range(10):
            b.record_feedback(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        adjusted = b.apply_feedback_to_confidence("greeting", base_confidence=0.5)
        # boost → penalty = -0.1 → 0.4
        assert abs(adjusted - 0.4) < 1e-6

    def test_b4_bridge_get_feedback_summary(self, tmp_path):
        """B.4 bridge 转发 get_feedback_summary"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.record_feedback(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8,
        ))
        summary = b.get_feedback_summary()
        assert summary["type_count"] == 1
        assert "greeting" in summary["profiles"]

    def test_b4_bridge_summarize_includes_feedback(self, tmp_path):
        """summarize_b4 包含 feedback 状态"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.record_feedback(OutcomeRecord(
            action_id="a1", action_type="greeting", user_id="u1",
            created_at=100.0, completed_at=200.0, success=True, score=0.8,
        ))
        s = b.summarize_b4()
        assert "feedback" in s
        assert s["feedback"]["enabled"] is True
        assert s["feedback"]["in_memory_profiles"] == 1
        assert s["feedback"]["update_count"] >= 1
        assert "total_feedback_recorded" in s
        assert s["total_feedback_recorded"] == 1


# ============================================================
# 14. RuntimeB4Bridge 自动 feedback 记录(在 outcome 记录后)
# ============================================================

class TestB4BridgeAutoFeedback:
    """flush_pending 后自动同步 outcome → feedback"""

    def test_flush_records_feedback_on_dispatched(self, tmp_path):
        """dispatched action 后 feedback profile 自动更新"""
        log_path = str(tmp_path / "feedback.jsonl")
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
        assert b.outcome_tracker.get_action_outcome("a1") is not None
        # feedback profile 已自动更新
        profile = b.feedback_manager.get_action_type_profile("greeting")
        assert profile.total_count == 1
        assert profile.success_count == 1
        assert profile.success_rate == 1.0
        # 计数已更新
        assert b._total_feedback_recorded == 1

    def test_flush_records_feedback_on_rejected(self, tmp_path):
        """rejected action 后 feedback profile 也更新(success=False)"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{
            "action_id": "a1", "stage": "gate_rejected",
            "action_type": "share", "user_id": "u1",
            "error": "low confidence",
        }]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # feedback profile 更新
        profile = b.feedback_manager.get_action_type_profile("share")
        assert profile.total_count == 1
        assert profile.success_count == 0
        assert profile.failure_count == 1

    def test_flush_records_feedback_on_failed(self, tmp_path):
        """failed action 后 feedback profile 更新"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{
            "action_id": "a1", "stage": "execute_exception",
            "action_type": "reminder", "user_id": "u1",
            "error": "timeout",
        }]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        profile = b.feedback_manager.get_action_type_profile("reminder")
        assert profile.total_count == 1
        assert profile.failure_count == 1

    def test_flush_aggregates_feedback(self, tmp_path):
        """多次 flush 后,feedback 聚合正确"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        # 3 个 greeting (成功), 2 个 share (失败)
        recent = [
            {"action_id": "g1", "stage": "dispatched", "action_type": "greeting", "user_id": "u1"},
            {"action_id": "g2", "stage": "dispatched", "action_type": "greeting", "user_id": "u1"},
            {"action_id": "g3", "stage": "dispatched", "action_type": "greeting", "user_id": "u1"},
            {"action_id": "s1", "stage": "execute_exception", "action_type": "share", "user_id": "u1"},
            {"action_id": "s2", "stage": "execute_exception", "action_type": "share", "user_id": "u1"},
        ]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # greeting: 3 成功
        g_profile = b.feedback_manager.get_action_type_profile("greeting")
        assert g_profile.total_count == 3
        assert g_profile.success_count == 3
        assert g_profile.success_rate == 1.0
        # share: 2 失败
        s_profile = b.feedback_manager.get_action_type_profile("share")
        assert s_profile.total_count == 2
        assert s_profile.failure_count == 2
        assert s_profile.success_rate == 0.0
        # 总计数
        assert b._total_feedback_recorded == 5
        # get_decision_feedback_score 反映 history
        g_score = b.get_decision_feedback_score("greeting")
        assert g_score["recommendation"] == "boost"
        s_score = b.get_decision_feedback_score("share")
        assert s_score["recommendation"] == "suppress"


# ============================================================
# 15. 兼容性测试
# ============================================================

class TestB7Compatibility:
    """B.7 不破坏 B.4/B.5/B.6 现有功能"""

    def test_b4_without_persistence_feedback_works(self):
        """B.4 不传 persistence 时 feedback_manager 仍创建(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.persistence is None
        assert b.feedback_manager is not None
        # flush 仍正常
        out = b.flush_pending()
        assert "dispatched" in out

    def test_b6_outcome_unchanged(self, tmp_path):
        """B.7 引入后,B.6 outcome 仍正常"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "greeting"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # B.6 outcome 仍记录
        assert b.outcome_tracker.get_action_outcome("a1") is not None
        # B.7 feedback 也记录
        profile = b.feedback_manager.get_action_type_profile("greeting")
        assert profile.total_count == 1

    def test_b4_audit_still_works(self, tmp_path):
        """B.7 引入后,B.4 audit 仍正常"""
        from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger

        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        mgr_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=mgr_path)
        recent = [{
            "action_id": "a1", "stage": "dispatched",
            "action_type": "greeting", "user_id": "u1",
        }]
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
        # outcome 仍记录
        assert b.outcome_tracker.get_action_outcome("a1") is not None
        # feedback 也记录
        assert b.feedback_manager.get_action_type_profile("greeting").total_count == 1

    def test_b5_persistence_unchanged(self, tmp_path):
        """B.7 引入后,B.5 persistence 仍正常"""
        log_path = str(tmp_path / "feedback.jsonl")
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
        assert mgr.write_count >= 3
        # history 中包含 completed 和 feedback_updated
        hist = mgr.get_history("a1") + mgr.get_history("feedback_greeting")
        stages = [r.get("stage") for r in hist]
        assert "completed" in stages
        assert FEEDBACK_STAGE_UPDATED in stages

    def test_get_decision_feedback_score_without_feedback_manager(self, tmp_path):
        """feedback_manager=None 时返回中性"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        # 构造一个 feedback_manager 不可用的 bridge
        from src.runtime.phase_b4_integration import RuntimeB4Bridge
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_manager = None  # 强制设 None
        score = b.get_decision_feedback_score("greeting")
        assert score["feedback_weight"] == 0.5
        assert score["recommendation"] == "neutral"

    def test_get_behavior_score_without_feedback_manager(self, tmp_path):
        """feedback_manager=None 时返回中性"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_manager = None
        score = b.get_behavior_score("greeting")
        assert score == 0.5

    def test_apply_feedback_to_confidence_without_feedback_manager(self, tmp_path):
        """feedback_manager=None 时返回 base_confidence"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_manager = None
        adjusted = b.apply_feedback_to_confidence("greeting", base_confidence=0.7)
        assert adjusted == 0.7

    def test_get_action_type_profile_without_feedback_manager(self, tmp_path):
        """feedback_manager=None 时返回零 profile"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_manager = None
        profile = b.get_action_type_profile("greeting")
        assert profile["total_count"] == 0
        assert profile["success_rate"] == 0.0

    def test_get_feedback_summary_without_feedback_manager(self, tmp_path):
        """feedback_manager=None 时返回零摘要"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_manager = None
        summary = b.get_feedback_summary()
        assert summary["type_count"] == 0
        assert summary["profiles"] == {}


# ============================================================
# 16. 核心模块未修改保护
# ============================================================

CORE_MODULES = [
    "src/runtime/supervisor.py",
    "src/runtime/runtime_core.py",
    "src/proactive/proactive_engine.py",
    "src/runtime/action_dispatcher.py",
]

# 注: ActionConfidenceGate 在 proactive_engine.py 内(暂不单独导入)


class TestCoreModulesUnchanged:
    """核心模块未修改保护"""

    @pytest.mark.parametrize("rel_path", CORE_MODULES)
    def test_core_module_file_exists(self, rel_path):
        """核心模块文件必须存在(基线)"""
        repo_root = Path(__file__).resolve().parents[1]
        full = repo_root / rel_path
        assert full.exists(), f"core module missing: {rel_path}"

    def test_no_proactive_engine_feedback_references(self):
        """ProactiveEngine 不应直接引用 feedback(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        proactive = repo_root / "src" / "proactive" / "proactive_engine.py"
        if not proactive.exists():
            pytest.skip("proactive_engine not found")
        src = proactive.read_text(encoding="utf-8")
        # 禁止直接引用 phase_b7 / action_feedback
        assert "phase_b7" not in src, "proactive_engine should not reference phase_b7"
        assert "action_feedback" not in src, "proactive_engine should not reference action_feedback"

    def test_no_supervisor_feedback_references(self):
        """supervisor.py 不应直接引用 feedback(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        sup = repo_root / "src" / "runtime" / "supervisor.py"
        if not sup.exists():
            pytest.skip("supervisor not found")
        src = sup.read_text(encoding="utf-8")
        assert "phase_b7" not in src, "supervisor should not reference phase_b7"
        assert "action_feedback" not in src, "supervisor should not reference action_feedback"
        assert "feedback_manager" not in src, "supervisor should not reference feedback_manager"

    def test_no_runtime_core_feedback_references(self):
        """runtime_core.py 不应直接引用 feedback(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        rc = repo_root / "src" / "runtime" / "runtime_core.py"
        if not rc.exists():
            pytest.skip("runtime_core not found")
        src = rc.read_text(encoding="utf-8")
        assert "phase_b7" not in src, "runtime_core should not reference phase_b7"
        assert "action_feedback" not in src, "runtime_core should not reference action_feedback"
        assert "feedback_manager" not in src, "runtime_core should not reference feedback_manager"

    def test_no_action_dispatcher_feedback_references(self):
        """action_dispatcher.py 不应直接引用 feedback(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        ad = repo_root / "src" / "runtime" / "action_dispatcher.py"
        if not ad.exists():
            pytest.skip("action_dispatcher not found")
        src = ad.read_text(encoding="utf-8")
        assert "phase_b7" not in src, "action_dispatcher should not reference phase_b7"
        assert "action_feedback" not in src, "action_dispatcher should not reference action_feedback"
        assert "feedback_manager" not in src, "action_dispatcher should not reference feedback_manager"


# ============================================================
# 17. 闭环验证测试
# ============================================================

class TestClosedLoop:
    """闭环验证:Action 提出 → Governance → 执行 → Outcome → Feedback → 影响 Decision"""

    def test_full_loop(self, tmp_path):
        """完整闭环:outcome → profile → feedback_weight → 影响 confidence"""
        log_path = str(tmp_path / "feedback.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )

        # 1) 模拟 10 次 greeting 都成功
        for i in range(10):
            inner.set_recent([{
                "action_id": f"a{i}", "stage": "dispatched",
                "action_type": "greeting", "user_id": "u1",
                "created_at": 100.0 + i,
            }])
            b.flush_pending()

        # 2) feedback 已聚合
        g_profile = b.feedback_manager.get_action_type_profile("greeting")
        assert g_profile.total_count == 10
        assert g_profile.success_rate == 1.0

        # 3) decision 引擎可调用 get_decision_feedback_score
        score = b.get_decision_feedback_score("greeting")
        assert score["recommendation"] == "boost"
        assert score["has_history"] is True
        assert score["feedback_weight"] >= FEEDBACK_BOOST_THRESHOLD

        # 4) 模拟新决策: 基础 confidence 0.5 + boost (-0.1) = 0.4
        adjusted = b.apply_feedback_to_confidence("greeting", base_confidence=0.5)
        assert abs(adjusted - 0.4) < 1e-6

        # 5) 反例: 10 次 share 都失败
        for i in range(10):
            inner.set_recent([{
                "action_id": f"s{i}", "stage": "execute_exception",
                "action_type": "share", "user_id": "u1",
                "error": "timeout",
            }])
            b.flush_pending()

        # 6) share profile 已聚合(失败)
        s_profile = b.feedback_manager.get_action_type_profile("share")
        assert s_profile.total_count == 10
        assert s_profile.success_rate == 0.0

        # 7) decision 引擎对 share 建议 suppress
        s_score = b.get_decision_feedback_score("share")
        assert s_score["recommendation"] == "suppress"
        assert s_score["feedback_weight"] <= FEEDBACK_SUPPRESS_THRESHOLD

        # 8) 应用 feedback: 0.5 + 0.3 = 0.8
        s_adjusted = b.apply_feedback_to_confidence("share", base_confidence=0.5)
        assert abs(s_adjusted - 0.8) < 1e-6

        # 9) summarize 包含完整状态
        s = b.summarize_b4()
        assert s["feedback"]["in_memory_profiles"] == 2  # greeting + share
        assert s["total_feedback_recorded"] == 20
        assert s["total_outcomes_recorded"] == 20
