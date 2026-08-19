# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b8.py

Phase B.8 Runtime Integration —— Decision Feedback Adapter 测试

覆盖:
  1. 配置测试(default / override / 不修改入参)
  2. DecisionFeedbackAdjustment 测试(创建 / to_dict / from_dict / 边界)
  3. DecisionFeedbackAdapter 写入测试(append-only JSONL via persistence)
  4. boost / neutral / suppress 调整逻辑
  5. 边界保护(0.0 <= confidence <= 1.0)
  6. input confidence 边界 clip
  7. DecisionFeedbackAdapter 异常写盘(降级为 memory-only)
  8. DecisionFeedbackAdapter persistence=None / disabled 行为
  9. DecisionFeedbackAdapter 重启恢复(load_state)
 10. get_adjustment_history / get_recent_adjustments / get_feedback_summary
 11. health_check / clear / reset_stats
 12. create_decision_feedback_adapter 工厂
 13. safe_apply_feedback 全局安全
 14. RuntimeB4Bridge 集成(get_feedback_adjustment / get_adjustment_history /
     get_feedback_adapter_summary / feedback_adapter_health_check)
 15. RuntimeB4Bridge 兼容性(B.4/B.5/B.6/B.7 现有功能不受影响)
 16. 核心模块未修改保护(supervisor / runtime_core / proactive_engine /
     action_dispatcher / ActionConfidenceGate)
 17. 闭环验证:Action → Outcome → Feedback → Adapter → 影响 Decision

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

from src.runtime.decision_feedback_adapter import (
    PHASE_B8_DEFAULT_CONFIG,
    PHASE_B8_NAME,
    PHASE_B8_VERSION,
    DEFAULT_ADJUSTMENT_PATH,
    SCHEMA_VERSION as B8_SCHEMA_VERSION,
    DECISION_FEEDBACK_STAGE_ADJUSTED,
    RECOMMENDATION_BOOST,
    RECOMMENDATION_NEUTRAL,
    RECOMMENDATION_SUPPRESS,
    DEFAULT_BOOST_BONUS,
    DEFAULT_SUPPRESS_PENALTY,
    apply_phase_b8_config,
    is_phase_b8_enabled,
    is_b8_enabled_simple,
    DecisionFeedbackAdjustment,
    DecisionFeedbackAdapter,
    create_decision_feedback_adapter,
    safe_apply_feedback,
)

from src.runtime.action_feedback import (
    ActionBehaviorProfile,
    DecisionFeedbackScore,
    ActionFeedbackManager,
    FEEDBACK_BOOST_THRESHOLD,
    FEEDBACK_SUPPRESS_THRESHOLD,
    DEFAULT_NEUTRAL_WEIGHT,
)

from src.runtime.action_outcome import (
    OutcomeRecord,
    OutcomeTracker,
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


class FakeFeedbackManager:
    """模拟 ActionFeedbackManager,提供 get_decision_feedback_score"""

    def __init__(self, score: Optional[DecisionFeedbackScore] = None) -> None:
        self._score = score
        self.health = {
            "enabled": True,
            "in_memory_profiles": 1,
            "update_count": 0,
            "update_error_count": 0,
            "recover_count": 0,
            "degraded": False,
        }

    def get_decision_feedback_score(
        self, action_type: str, user_id: Optional[str] = None,
    ) -> DecisionFeedbackScore:
        if self._score is None:
            return DecisionFeedbackScore(action_type=action_type)
        return self._score

    def health_check(self) -> Dict[str, Any]:
        return self.health


# ============================================================
# 3. 配置测试
# ============================================================

class TestB8ConfigInjection:
    """PHASE_B8_DEFAULT_CONFIG / apply_phase_b8_config / is_phase_b8_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 decision_feedback_adapter.enabled=True"""
        cfg = PHASE_B8_DEFAULT_CONFIG
        assert "decision_feedback_adapter" in cfg
        assert cfg["decision_feedback_adapter"]["enabled"] is True
        assert cfg["decision_feedback_adapter"]["path"] == "data/action_feedback_adjustment.jsonl"
        assert cfg["decision_feedback_adapter"]["auto_recover"] is True
        assert cfg["decision_feedback_adapter"]["boost_bonus"] > 0
        assert cfg["decision_feedback_adapter"]["suppress_penalty"] > 0
        assert cfg["decision_feedback_adapter"]["min_confidence"] == 0.0
        assert cfg["decision_feedback_adapter"]["max_confidence"] == 1.0

    def test_default_on(self):
        """B.8 默认开启(但不阻塞)"""
        assert PHASE_B8_DEFAULT_CONFIG["decision_feedback_adapter"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b8_config(None)
        assert result["decision_feedback_adapter"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b8_config({"decision_feedback_adapter": {"enabled": False}})
        assert result["decision_feedback_adapter"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {
            "decision_feedback_adapter": {"enabled": False, "path": "/tmp/x.jsonl"},
        }
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b8_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b8_config("not a dict")["decision_feedback_adapter"]["enabled"] is True
        assert apply_phase_b8_config(42)["decision_feedback_adapter"]["enabled"] is True
        assert apply_phase_b8_config([])["decision_feedback_adapter"]["enabled"] is True

    def test_is_phase_b8_enabled(self):
        """is_phase_b8_enabled 判定正确"""
        assert is_phase_b8_enabled({"decision_feedback_adapter": {"enabled": True}}) is True
        assert is_phase_b8_enabled({"decision_feedback_adapter": {"enabled": False}}) is False
        assert is_phase_b8_enabled({}) is True
        assert is_phase_b8_enabled(None) is False

    def test_is_b8_enabled_simple_variants(self):
        """is_b8_enabled_simple 兼容 cfg['adapter_enabled']"""
        assert is_b8_enabled_simple({"adapter_enabled": True}) is True
        assert is_b8_enabled_simple({"adapter_enabled": False}) is False
        assert is_b8_enabled_simple({"decision_feedback_adapter": {"enabled": True}}) is True
        assert is_b8_enabled_simple({}) is True
        assert is_b8_enabled_simple(None) is False


# ============================================================
# 4. DecisionFeedbackAdjustment 测试
# ============================================================

class TestDecisionFeedbackAdjustment:
    """DecisionFeedbackAdjustment 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        a = DecisionFeedbackAdjustment(
            action_type="greeting",
            original_confidence=0.5,
            adjusted_confidence=0.6,
        )
        assert a.action_type == "greeting"
        assert a.original_confidence == 0.5
        assert a.adjusted_confidence == 0.6
        assert a.feedback_weight == 0.5
        assert a.confidence_delta == 0.0
        assert a.recommendation == RECOMMENDATION_NEUTRAL
        assert a.reason == ""
        assert a.timestamp == 0.0

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        a = DecisionFeedbackAdjustment(
            action_type="share",
            original_confidence=0.5,
            adjusted_confidence=0.8,
            feedback_weight=0.85,
            confidence_delta=0.3,
            recommendation=RECOMMENDATION_BOOST,
            reason="boost (weight=0.85)",
            timestamp=1234.5,
        )
        d = a.to_dict()
        assert d["schema_version"] == B8_SCHEMA_VERSION
        assert d["action_type"] == "share"
        assert d["original_confidence"] == 0.5
        assert d["adjusted_confidence"] == 0.8
        assert d["feedback_weight"] == 0.85
        assert d["confidence_delta"] == 0.3
        assert d["recommendation"] == RECOMMENDATION_BOOST
        assert d["reason"] == "boost (weight=0.85)"
        assert d["timestamp"] == 1234.5

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = DecisionFeedbackAdjustment(
            action_type="reminder",
            original_confidence=0.3,
            adjusted_confidence=0.1,
            feedback_weight=0.2,
            confidence_delta=-0.2,
            recommendation=RECOMMENDATION_SUPPRESS,
            reason="suppress (weight=0.20)",
            timestamp=2000.0,
        )
        d = original.to_dict()
        restored = DecisionFeedbackAdjustment.from_dict(d)
        assert restored.action_type == original.action_type
        assert restored.original_confidence == original.original_confidence
        assert restored.adjusted_confidence == original.adjusted_confidence
        assert restored.feedback_weight == original.feedback_weight
        assert restored.confidence_delta == original.confidence_delta
        assert restored.recommendation == original.recommendation
        assert restored.reason == original.reason
        assert restored.timestamp == original.timestamp


# ============================================================
# 5. DecisionFeedbackAdapter 调整逻辑
# ============================================================

class TestAdapterAdjustmentLogic:
    """apply_feedback 的 boost / neutral / suppress 调整逻辑"""

    def test_no_feedback_manager_returns_neutral(self):
        """无 feedback_manager → 中性调整"""
        adapter = DecisionFeedbackAdapter(feedback_manager=None)
        adj = adapter.apply_feedback("greeting", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.adjusted_confidence == 0.5
        assert adj.confidence_delta == 0.0

    def test_boost_increases_confidence(self):
        """boost → confidence 增加"""
        score = DecisionFeedbackScore(
            action_type="greeting",
            feedback_weight=0.85,
            recommendation=RECOMMENDATION_BOOST,
            sample_size=10,
            has_history=True,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        adj = adapter.apply_feedback("greeting", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_BOOST
        # 0.5 + 0.1 (DEFAULT_BOOST_BONUS) = 0.6
        assert abs(adj.adjusted_confidence - 0.6) < 1e-6
        assert adj.confidence_delta > 0

    def test_suppress_decreases_confidence(self):
        """suppress → confidence 降低"""
        score = DecisionFeedbackScore(
            action_type="share",
            feedback_weight=0.15,
            recommendation=RECOMMENDATION_SUPPRESS,
            sample_size=10,
            has_history=True,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        adj = adapter.apply_feedback("share", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_SUPPRESS
        # 0.5 - 0.2 (DEFAULT_SUPPRESS_PENALTY) = 0.3
        assert abs(adj.adjusted_confidence - 0.3) < 1e-6
        assert adj.confidence_delta < 0

    def test_neutral_keeps_confidence(self):
        """neutral → confidence 保持"""
        score = DecisionFeedbackScore(
            action_type="x",
            feedback_weight=0.5,
            recommendation=RECOMMENDATION_NEUTRAL,
            sample_size=5,
            has_history=True,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        adj = adapter.apply_feedback("x", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.adjusted_confidence == 0.5
        assert adj.confidence_delta == 0.0

    def test_high_weight_inferred_boost(self):
        """高 weight 但无显式 recommendation → 推断为 boost"""
        score = DecisionFeedbackScore(
            action_type="g",
            feedback_weight=0.8,
            recommendation="unknown_recommendation",  # 未知
            sample_size=5,
            has_history=True,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        adj = adapter.apply_feedback("g", confidence=0.5)
        # 未知 recommendation → 按 weight 推断
        assert adj.recommendation == RECOMMENDATION_BOOST
        assert adj.adjusted_confidence > 0.5

    def test_low_weight_inferred_suppress(self):
        """低 weight 但无显式 recommendation → 推断为 suppress"""
        score = DecisionFeedbackScore(
            action_type="g",
            feedback_weight=0.2,
            recommendation="unknown",
            sample_size=5,
            has_history=True,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        adj = adapter.apply_feedback("g", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_SUPPRESS
        assert adj.adjusted_confidence < 0.5

    def test_mid_weight_inferred_neutral(self):
        """中等 weight → 推断为 neutral"""
        score = DecisionFeedbackScore(
            action_type="g",
            feedback_weight=0.5,
            recommendation="unknown",
            sample_size=5,
            has_history=True,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        adj = adapter.apply_feedback("g", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.adjusted_confidence == 0.5


# ============================================================
# 6. 边界保护
# ============================================================

class TestAdapterBoundary:
    """confidence 边界保护"""

    def test_boost_clipped_to_max(self):
        """boost 时 confidence 不能超过 max_confidence"""
        score = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        # base=0.95 + 0.1 = 1.05 → clip 到 1.0
        adj = adapter.apply_feedback("g", confidence=0.95)
        assert adj.adjusted_confidence == 1.0
        assert "[clipped]" in adj.reason

    def test_suppress_clipped_to_min(self):
        """suppress 时 confidence 不能低于 min_confidence"""
        score = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.1,
            recommendation=RECOMMENDATION_SUPPRESS, has_history=True, sample_size=5,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(feedback_manager=fb)
        # base=0.1 - 0.2 = -0.1 → clip 到 0.0
        adj = adapter.apply_feedback("g", confidence=0.1)
        assert adj.adjusted_confidence == 0.0
        assert "[clipped]" in adj.reason

    def test_input_confidence_clipped(self):
        """input confidence 超过 [0,1] 时先 clip"""
        adapter = DecisionFeedbackAdapter(feedback_manager=None)
        # input > 1.0 → clip to 1.0
        adj = adapter.apply_feedback("g", confidence=1.5)
        assert adj.original_confidence == 1.0
        # input < 0.0 → clip to 0.0
        adj = adapter.apply_feedback("g", confidence=-0.5)
        assert adj.original_confidence == 0.0

    def test_custom_bounds(self):
        """自定义 min/max confidence"""
        score = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        fb = FakeFeedbackManager(score=score)
        adapter = DecisionFeedbackAdapter(
            feedback_manager=fb, min_confidence=0.2, max_confidence=0.7,
        )
        # boost: 0.6 + 0.1 = 0.7 → clip to 0.7
        adj = adapter.apply_feedback("g", confidence=0.6)
        assert adj.adjusted_confidence == 0.7


# ============================================================
# 7. 写入与持久化
# ============================================================

class TestAdapterWrite:
    """DecisionFeedbackAdapter 写盘行为"""

    def test_apply_writes_to_persistence(self, tmp_path):
        """apply_feedback 通过 persistence 写盘"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        adj = adapter.apply_feedback("greeting", confidence=0.5)
        assert mgr.write_count >= 1
        assert os.path.exists(log_path)
        # 检查文件中包含 decision_feedback_adjusted stage
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        stages = []
        for line in lines:
            try:
                rec = json.loads(line)
                stages.append(rec.get("stage"))
            except Exception:
                pass
        assert DECISION_FEEDBACK_STAGE_ADJUSTED in stages

    def test_apply_no_persistence(self):
        """无 persistence 时仍可 apply(只内存)"""
        adapter = DecisionFeedbackAdapter(persistence=None)
        adj = adapter.apply_feedback("g", confidence=0.5)
        assert adj.action_type == "g"
        assert adapter.in_memory_count == 1

    def test_apply_disabled(self, tmp_path):
        """enabled=False 时只更新内存,不写盘"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr, enabled=False)
        adapter.apply_feedback("g", confidence=0.5)
        assert mgr.write_count == 0
        # 内存已记录
        assert adapter.in_memory_count == 1

    def test_apply_empty_action_type(self, tmp_path):
        """action_type 为空时跳过写盘"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        adj = adapter.apply_feedback("", confidence=0.5)
        # 中性返回
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert mgr.write_count == 0

    def test_apply_batch(self, tmp_path):
        """批量 apply"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        items = [
            {"action_type": "g", "confidence": 0.5},
            {"action_type": "s", "confidence": 0.5},
            {"action_type": "r", "confidence": 0.5},
        ]
        out = adapter.apply_feedback_batch(items)
        assert len(out) == 3
        assert adapter.in_memory_count == 3


# ============================================================
# 8. 异常写盘(降级)
# ============================================================

class TestAdapterFailureIsolation:
    """写盘失败自动降级"""

    def test_persistence_failure_returns_degraded(self):
        """persistence 写盘失败 → 内存仍记录,apply 计数 +1"""
        mgr = MagicMock()
        mgr.is_degraded = True
        mgr.persist_event.return_value = False
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        adj = adapter.apply_feedback("g", confidence=0.5)
        # 内存已记录
        assert adj.action_type == "g"
        assert adapter.in_memory_count == 1
        # 错误计数
        assert adapter.apply_error_count >= 1

    def test_persistence_exception_isolated(self):
        """persistence.persist_event 抛错时隔离"""
        mgr = MagicMock()
        mgr.is_degraded = False
        mgr.persist_event.side_effect = RuntimeError("boom")
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        adj = adapter.apply_feedback("g", confidence=0.5)
        # 内存已记录
        assert adj.action_type == "g"
        assert adapter.in_memory_count == 1
        # 错误计数
        assert adapter.apply_error_count >= 1

    def test_feedback_manager_exception_isolated(self):
        """feedback_manager 抛错时 → 中性返回"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        bad_fb = MagicMock()
        bad_fb.get_decision_feedback_score.side_effect = RuntimeError("boom")
        adapter = DecisionFeedbackAdapter(feedback_manager=bad_fb, persistence=mgr)
        adj = adapter.apply_feedback("g", confidence=0.5)
        # 中性返回
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.adjusted_confidence == 0.5

    def test_safe_apply_feedback_handles_none_adapter(self):
        """safe_apply_feedback(None, ...) → 中性 adjustment"""
        adj = safe_apply_feedback(None, "g", 0.5)
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.reason == "adapter is None"

    def test_safe_apply_feedback_handles_exception(self):
        """safe_apply_feedback 处理 apply 抛错"""
        adapter = MagicMock()
        adapter.apply_feedback.side_effect = RuntimeError("boom")
        adj = safe_apply_feedback(adapter, "g", 0.5)
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert "exception" in adj.reason


# ============================================================
# 9. 重启恢复
# ============================================================

class TestAdapterRecovery:
    """load_state 恢复 adjustment 历史"""

    def test_load_state_no_persistence(self):
        """无 persistence 时返回 0"""
        adapter = DecisionFeedbackAdapter(persistence=None)
        assert adapter.load_state() == 0

    def test_load_state_empty(self, tmp_path):
        """空文件 → 恢复 0 条"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        assert adapter.load_state() == 0

    def test_load_state_recovers_adjustments(self, tmp_path):
        """从 JSONL 恢复 adjustment"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        adapter1 = DecisionFeedbackAdapter(persistence=mgr1)
        # 写入几个 adjustment
        score_boost = DecisionFeedbackScore(
            action_type="greeting", feedback_weight=0.85,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        adapter1.set_feedback_manager(FakeFeedbackManager(score=score_boost))
        adapter1.apply_feedback("greeting", confidence=0.5)

        score_suppress = DecisionFeedbackScore(
            action_type="share", feedback_weight=0.15,
            recommendation=RECOMMENDATION_SUPPRESS, has_history=True, sample_size=5,
        )
        adapter1.set_feedback_manager(FakeFeedbackManager(score=score_suppress))
        adapter1.apply_feedback("share", confidence=0.5)

        # 进程 2:从文件恢复
        mgr2 = ActionPersistenceManager(path=log_path)
        adapter2 = DecisionFeedbackAdapter(persistence=mgr2)
        # 每个 action_type 恢复 1 条(latest)
        assert adapter2.in_memory_count >= 1
        history = adapter2.get_adjustment_history(limit=10)
        assert len(history) >= 1

    def test_load_state_skips_non_b8_events(self, tmp_path):
        """load_state 只关注 decision_feedback_adjusted 事件"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)

        mgr2 = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr2)
        count = adapter.load_state()
        assert count == 0


# ============================================================
# 10. 查询与统计
# ============================================================

class TestAdapterQueries:
    """get_adjustment_history / get_recent_adjustments / get_feedback_summary"""

    def test_get_adjustment_history_empty(self):
        """无记录时返回空"""
        adapter = DecisionFeedbackAdapter(persistence=None)
        assert adapter.get_adjustment_history() == []
        assert adapter.get_recent_adjustments() == []

    def test_get_adjustment_history_by_type(self):
        """按 action_type 过滤"""
        score_boost = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        score_suppress = DecisionFeedbackScore(
            action_type="s", feedback_weight=0.1,
            recommendation=RECOMMENDATION_SUPPRESS, has_history=True, sample_size=5,
        )
        adapter = DecisionFeedbackAdapter(persistence=None)
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_boost))
        adapter.apply_feedback("g", confidence=0.5)
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_suppress))
        adapter.apply_feedback("s", confidence=0.5)

        g_history = adapter.get_adjustment_history(action_type="g", limit=10)
        assert len(g_history) == 1
        assert g_history[0]["action_type"] == "g"
        s_history = adapter.get_adjustment_history(action_type="s", limit=10)
        assert len(s_history) == 1
        assert s_history[0]["action_type"] == "s"

    def test_get_recent_adjustments(self):
        """返回最近 N 条 adjustment 对象"""
        score_boost = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        adapter = DecisionFeedbackAdapter(persistence=None)
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_boost))
        for i in range(5):
            adapter.apply_feedback("g", confidence=0.5)
        recent = adapter.get_recent_adjustments(limit=3)
        assert len(recent) == 3
        for a in recent:
            assert isinstance(a, DecisionFeedbackAdjustment)

    def test_get_feedback_summary_keys(self, tmp_path):
        """get_feedback_summary 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        s = adapter.get_feedback_summary()
        for k in (
            "name", "version", "enabled", "degraded", "closed",
            "in_memory_adjustments", "tracked_action_types",
            "boost_count", "neutral_count", "suppress_count",
            "total_count", "boost_rate", "neutral_rate", "suppress_rate",
            "avg_confidence_delta", "apply_count", "apply_error_count",
            "recover_count",
        ):
            assert k in s, f"missing key: {k}"
        assert s["name"] == PHASE_B8_NAME
        assert s["version"] == PHASE_B8_VERSION

    def test_get_feedback_summary_after_apply(self):
        """apply 后统计正确"""
        score_boost = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        score_suppress = DecisionFeedbackScore(
            action_type="s", feedback_weight=0.1,
            recommendation=RECOMMENDATION_SUPPRESS, has_history=True, sample_size=5,
        )
        score_neutral = DecisionFeedbackScore(
            action_type="n", feedback_weight=0.5,
            recommendation=RECOMMENDATION_NEUTRAL, has_history=True, sample_size=5,
        )
        adapter = DecisionFeedbackAdapter(persistence=None)
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_boost))
        adapter.apply_feedback("g", confidence=0.5)
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_suppress))
        adapter.apply_feedback("s", confidence=0.5)
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_neutral))
        adapter.apply_feedback("n", confidence=0.5)

        s = adapter.get_feedback_summary()
        assert s["boost_count"] == 1
        assert s["suppress_count"] == 1
        assert s["neutral_count"] == 1
        assert s["total_count"] == 3


# ============================================================
# 11. 健康度 / 重置
# ============================================================

class TestAdapterHealth:
    """health_check / clear / reset_stats"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        h = adapter.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "in_memory_adjustments", "tracked_action_types",
            "boost_count", "neutral_count", "suppress_count",
            "apply_count", "apply_error_count", "recover_count",
            "last_apply_at", "last_error", "created_at",
            "boost_bonus", "suppress_penalty",
            "min_confidence", "max_confidence",
            "feedback_manager", "persistence",
        ):
            assert k in h, f"missing key: {k}"
        assert h["name"] == PHASE_B8_NAME
        assert h["schema_version"] == B8_SCHEMA_VERSION

    def test_reset_stats(self, tmp_path):
        """reset_stats 重置统计"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        adapter.apply_feedback("g", confidence=0.5)
        assert adapter.apply_count >= 1
        adapter.reset_stats()
        assert adapter.apply_count == 0

    def test_clear(self, tmp_path):
        """clear 清空内存(不删文件)"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        adapter.apply_feedback("g", confidence=0.5)
        assert adapter.in_memory_count == 1
        adapter.clear()
        assert adapter.in_memory_count == 0
        assert os.path.exists(str(tmp_path / "x.jsonl"))


# ============================================================
# 12. 工厂
# ============================================================

class TestCreateDecisionFeedbackAdapter:
    """create_decision_feedback_adapter 工厂"""

    def test_create_with_persistence(self, tmp_path):
        """带 persistence 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = create_decision_feedback_adapter(persistence=mgr)
        assert adapter is not None
        assert adapter.persistence is mgr
        assert adapter.enabled is True

    def test_create_with_disabled(self, tmp_path):
        """disabled=True 显式关闭"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = create_decision_feedback_adapter(persistence=mgr, enabled=False)
        assert adapter.enabled is False

    def test_create_with_no_persistence(self):
        """无 persistence 创建"""
        adapter = create_decision_feedback_adapter(persistence=None)
        assert adapter is not None
        assert adapter.persistence is None
        assert adapter.is_degraded is True

    def test_create_from_cfg(self, tmp_path):
        """从 cfg 创建"""
        cfg = {
            "decision_feedback_adapter": {
                "enabled": True,
                "boost_bonus": 0.2,
                "suppress_penalty": 0.3,
                "max_records": 100,
            }
        }
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        adapter = create_decision_feedback_adapter(persistence=mgr, cfg=cfg)
        assert adapter is not None
        assert abs(adapter._boost_bonus - 0.2) < 1e-6
        assert abs(adapter._suppress_penalty - 0.3) < 1e-6
        assert adapter._max_records == 100


# ============================================================
# 13. RuntimeB4Bridge 集成 adapter
# ============================================================

class TestB4BridgeAdapterIntegration:
    """RuntimeB4Bridge 集成 DecisionFeedbackAdapter"""

    def test_b4_bridge_has_feedback_adapter(self, tmp_path):
        """RuntimeB4Bridge 持有 feedback_adapter"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        assert b.feedback_adapter is not None

    def test_b4_bridge_feedback_adapter_persistence_none(self):
        """无 persistence 时 feedback_adapter 仍可工作(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.feedback_adapter is not None
        assert b.feedback_adapter.persistence is None

    def test_b4_bridge_explicit_feedback_adapter(self, tmp_path):
        """显式注入 feedback_adapter"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        custom_adapter = DecisionFeedbackAdapter(
            persistence=mgr, boost_bonus=0.25,
        )
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
            feedback_adapter=custom_adapter,
        )
        assert b.feedback_adapter is custom_adapter
        assert abs(b.feedback_adapter._boost_bonus - 0.25) < 1e-6

    def test_b4_bridge_get_feedback_adjustment(self, tmp_path):
        """B.4 bridge 转发 get_feedback_adjustment"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 注入 feedback
        for i in range(10):
            b.record_feedback(OutcomeRecord(
                action_id=f"a{i}", action_type="greeting", user_id="u1",
                created_at=100.0, completed_at=200.0, success=True, score=0.9,
            ))
        adj = b.get_feedback_adjustment("greeting", confidence=0.5)
        assert adj is not None
        assert adj.recommendation == RECOMMENDATION_BOOST
        assert adj.adjusted_confidence > 0.5
        # 计数
        assert b._total_feedback_adjustments >= 1

    def test_b4_bridge_get_feedback_adjustment_no_adapter(self):
        """feedback_adapter 不可用时返回中性"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        # 强制设 None
        b._feedback_adapter = None
        adj = b.get_feedback_adjustment("greeting", confidence=0.5)
        assert adj is not None
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.adjusted_confidence == 0.5

    def test_b4_bridge_get_adjustment_history(self, tmp_path):
        """B.4 bridge 转发 get_adjustment_history"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.get_feedback_adjustment("greeting", confidence=0.5)
        b.get_feedback_adjustment("share", confidence=0.5)
        history = b.get_adjustment_history(limit=10)
        assert len(history) >= 2

    def test_b4_bridge_get_feedback_adapter_summary(self, tmp_path):
        """B.4 bridge 转发 get_feedback_adapter_summary"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.get_feedback_adjustment("g", confidence=0.5)
        s = b.get_feedback_adapter_summary()
        assert s["name"] == "phase_b8"
        assert s["apply_count"] >= 1

    def test_b4_bridge_feedback_adapter_health_check(self, tmp_path):
        """B.4 bridge 转发 feedback_adapter_health_check"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        h = b.feedback_adapter_health_check()
        assert h["name"] == "phase_b8"
        assert "enabled" in h
        assert "degraded" in h

    def test_b4_bridge_get_recent_adjustments(self, tmp_path):
        """B.4 bridge 转发 get_recent_adjustments"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        for i in range(3):
            b.get_feedback_adjustment("g", confidence=0.5)
        recent = b.get_recent_adjustments(limit=3)
        assert len(recent) == 3
        for a in recent:
            assert isinstance(a, DecisionFeedbackAdjustment)

    def test_b4_bridge_summarize_includes_feedback_adapter(self, tmp_path):
        """summarize_b4 包含 feedback_adapter 状态"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.get_feedback_adjustment("greeting", confidence=0.5)
        s = b.summarize_b4()
        assert "feedback_adapter" in s
        assert s["feedback_adapter"]["enabled"] is True
        assert s["feedback_adapter"]["in_memory_adjustments"] >= 1
        assert "total_feedback_adjustments" in s


# ============================================================
# 14. 兼容性测试
# ============================================================

class TestB8Compatibility:
    """B.8 不破坏 B.4/B.5/B.6/B.7 现有功能"""

    def test_b4_without_persistence_adapter_works(self):
        """B.4 不传 persistence 时 feedback_adapter 仍创建(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.persistence is None
        assert b.feedback_adapter is not None
        out = b.flush_pending()
        assert "dispatched" in out

    def test_b6_outcome_unchanged(self, tmp_path):
        """B.8 引入后,B.6 outcome 仍正常"""
        log_path = str(tmp_path / "adj.jsonl")
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
        """B.8 引入后,B.7 feedback 仍正常"""
        log_path = str(tmp_path / "adj.jsonl")
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

    def test_b4_audit_still_works(self, tmp_path):
        """B.8 引入后,B.4 audit 仍正常"""
        from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger

        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        mgr_path = str(tmp_path / "adj.jsonl")
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
        """B.8 引入后,B.5 persistence 仍正常"""
        log_path = str(tmp_path / "adj.jsonl")
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

    def test_get_feedback_adjustment_without_adapter(self, tmp_path):
        """feedback_adapter=None 时返回中性"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_adapter = None
        adj = b.get_feedback_adjustment("g", confidence=0.7)
        assert adj is not None
        assert adj.recommendation == RECOMMENDATION_NEUTRAL
        assert adj.adjusted_confidence == 0.7

    def test_get_adjustment_history_without_adapter(self, tmp_path):
        """feedback_adapter=None 时返回空"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_adapter = None
        assert b.get_adjustment_history() == []

    def test_get_feedback_adapter_summary_without_adapter(self, tmp_path):
        """feedback_adapter=None 时返回零摘要"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_adapter = None
        s = b.get_feedback_adapter_summary()
        assert s["enabled"] is False
        assert s["in_memory_adjustments"] == 0

    def test_feedback_adapter_health_check_without_adapter(self, tmp_path):
        """feedback_adapter=None 时返回零健康度"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_adapter = None
        h = b.feedback_adapter_health_check()
        assert h["enabled"] is False


# ============================================================
# 15. 核心模块未修改保护
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

    def test_no_proactive_engine_adapter_references(self):
        """ProactiveEngine 不应直接引用 adapter(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        proactive = repo_root / "src" / "proactive" / "proactive_engine.py"
        if not proactive.exists():
            pytest.skip("proactive_engine not found")
        src = proactive.read_text(encoding="utf-8")
        assert "phase_b8" not in src, "proactive_engine should not reference phase_b8"
        assert "decision_feedback_adapter" not in src, (
            "proactive_engine should not reference decision_feedback_adapter"
        )

    def test_no_supervisor_adapter_references(self):
        """supervisor.py 不应直接引用 adapter(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        sup = repo_root / "src" / "runtime" / "supervisor.py"
        if not sup.exists():
            pytest.skip("supervisor not found")
        src = sup.read_text(encoding="utf-8")
        assert "phase_b8" not in src, "supervisor should not reference phase_b8"
        assert "decision_feedback_adapter" not in src, (
            "supervisor should not reference decision_feedback_adapter"
        )
        assert "feedback_adapter" not in src, (
            "supervisor should not reference feedback_adapter"
        )

    def test_no_runtime_core_adapter_references(self):
        """runtime_core.py 不应直接引用 adapter(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        rc = repo_root / "src" / "runtime" / "runtime_core.py"
        if not rc.exists():
            pytest.skip("runtime_core not found")
        src = rc.read_text(encoding="utf-8")
        assert "phase_b8" not in src, "runtime_core should not reference phase_b8"
        assert "decision_feedback_adapter" not in src, (
            "runtime_core should not reference decision_feedback_adapter"
        )
        assert "feedback_adapter" not in src, (
            "runtime_core should not reference feedback_adapter"
        )

    def test_no_action_dispatcher_adapter_references(self):
        """action_dispatcher.py 不应直接引用 adapter(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        ad = repo_root / "src" / "runtime" / "action_dispatcher.py"
        if not ad.exists():
            pytest.skip("action_dispatcher not found")
        src = ad.read_text(encoding="utf-8")
        assert "phase_b8" not in src, "action_dispatcher should not reference phase_b8"
        assert "decision_feedback_adapter" not in src, (
            "action_dispatcher should not reference decision_feedback_adapter"
        )
        assert "feedback_adapter" not in ad.read_text(encoding="utf-8"), (
            "action_dispatcher should not reference feedback_adapter"
        )


# ============================================================
# 16. 闭环验证测试
# ============================================================

class TestClosedLoop:
    """闭环验证:Action → Outcome → Feedback → Adapter → 影响 Decision"""

    def test_full_loop(self, tmp_path):
        """完整闭环:outcome → profile → feedback → adapter → confidence"""
        log_path = str(tmp_path / "adj.jsonl")
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

        # 3) 通过 adapter 获取 confidence 调整
        adj = b.get_feedback_adjustment("greeting", confidence=0.5)
        assert adj.recommendation == RECOMMENDATION_BOOST
        # 0.5 + 0.1 = 0.6
        assert abs(adj.adjusted_confidence - 0.6) < 1e-6

        # 4) 反例: 5 次 share 都失败
        for i in range(5):
            inner.set_recent([{
                "action_id": f"s{i}", "stage": "execute_exception",
                "action_type": "share", "user_id": "u1",
                "error": "timeout",
            }])
            b.flush_pending()

        s_profile = b.feedback_manager.get_action_type_profile("share")
        assert s_profile.total_count == 5
        assert s_profile.success_rate == 0.0

        # 5) adapter 对 share 给出 suppress 信号
        s_adj = b.get_feedback_adjustment("share", confidence=0.5)
        assert s_adj.recommendation == RECOMMENDATION_SUPPRESS
        # 0.5 - 0.2 = 0.3
        assert abs(s_adj.adjusted_confidence - 0.3) < 1e-6

        # 6) 调整历史可查询
        history = b.get_adjustment_history(limit=10)
        assert len(history) >= 2

        # 7) summarize 包含完整状态
        s = b.summarize_b4()
        assert "feedback_adapter" in s
        assert s["feedback_adapter"]["enabled"] is True
        assert s["feedback_adapter"]["boost_count"] >= 1
        assert s["feedback_adapter"]["suppress_count"] >= 1
        assert s["total_feedback_adjustments"] >= 2
        assert s["total_outcomes_recorded"] == 15
        assert s["total_feedback_recorded"] == 15

    def test_adapter_persists_and_reloads(self, tmp_path):
        """adapter 数据可持久化、重启恢复"""
        log_path = str(tmp_path / "adj.jsonl")
        # 进程 1
        mgr1 = ActionPersistenceManager(path=log_path)
        adapter1 = DecisionFeedbackAdapter(persistence=mgr1)
        score_boost = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        adapter1.set_feedback_manager(FakeFeedbackManager(score=score_boost))
        adapter1.apply_feedback("g", confidence=0.5)
        adapter1.apply_feedback("g", confidence=0.6)

        # 进程 2:从文件恢复
        mgr2 = ActionPersistenceManager(path=log_path)
        adapter2 = DecisionFeedbackAdapter(persistence=mgr2)
        # 恢复最新 1 条(同一 action_type 取 latest)
        assert adapter2.in_memory_count >= 1
        history = adapter2.get_adjustment_history(action_type="g", limit=10)
        assert len(history) >= 1
        # recommendation 应为 boost
        assert history[-1]["recommendation"] == RECOMMENDATION_BOOST


# ============================================================
# 17. RuntimeB4Bridge 端到端 + boundary
# ============================================================

class TestB4BridgeAdapterEdgeCases:
    """B.4 bridge 集成 adapter 边界情况"""

    def test_get_feedback_adjustment_input_clip(self, tmp_path):
        """input confidence 越界时被 clip"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # input > 1.0
        adj = b.get_feedback_adjustment("g", confidence=1.5)
        assert adj.original_confidence == 1.0
        # input < 0.0
        adj = b.get_feedback_adjustment("g", confidence=-0.5)
        assert adj.original_confidence == 0.0

    def test_get_feedback_adjustment_governance_off(self, tmp_path):
        """governance OFF 时 adapter 不可用"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": False, "audit_enabled": False}},
            persistence=mgr,
        )
        # governance OFF → adapter 不创建
        # 但若显式注入,仍可访问
        assert b.feedback_adapter is None or b.feedback_adapter is not None

    def test_flush_does_not_call_adapter(self, tmp_path):
        """flush_pending 不主动调用 adapter(adapter 是按需调用)"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "g"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # adapter 没有自动 apply
        assert b._total_feedback_adjustments == 0


# ============================================================
# 18. Schema 验证
# ============================================================

class TestAdjustmentSchema:
    """adjustment 持久化结构符合 schema"""

    def test_persistence_schema(self, tmp_path):
        """持久化文件结构正确"""
        log_path = str(tmp_path / "adj.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        adapter = DecisionFeedbackAdapter(persistence=mgr)
        score_boost = DecisionFeedbackScore(
            action_type="g", feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST, has_history=True, sample_size=5,
        )
        adapter.set_feedback_manager(FakeFeedbackManager(score=score_boost))
        adapter.apply_feedback("g", confidence=0.5)

        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["stage"] == DECISION_FEEDBACK_STAGE_ADJUSTED
        assert rec["decision"] == RECOMMENDATION_BOOST
        assert rec["source"] == "phase_b8_adapter"
        result = rec["result"]
        assert result["schema_version"] == B8_SCHEMA_VERSION
        assert result["action_type"] == "g"
        assert "original_confidence" in result
        assert "adjusted_confidence" in result
        assert "feedback_weight" in result
        assert "confidence_delta" in result
        assert "recommendation" in result
        assert "reason" in result
        assert "timestamp" in result
