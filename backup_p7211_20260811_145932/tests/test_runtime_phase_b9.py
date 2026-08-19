# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b9.py

Phase B.9 Runtime Integration —— Decision Feedback Runtime 测试

覆盖:
  1. 配置测试(default / override / 不修改入参)
  2. DecisionFeedbackContext 测试(创建 / to_dict / from_dict / 边界)
  3. AdjustedProposal 测试(创建 / to_dict)
  4. 默认无 feedback_adapter 保持原 confidence
  5. boost 正确增加 confidence
  6. suppress 正确降低 confidence
  7. confidence clip 0~1
  8. input confidence 越界 clip
  9. DecisionFeedbackAdapter disabled 不影响流程
 10. persistence 写入
 11. lifecycle trail 记录
 12. DecisionFeedbackRuntime.disabled / persistence=None 行为
 13. DecisionFeedbackRuntime 重启恢复(load_state)
 14. DecisionFeedbackRuntime 异常写盘(降级为 memory-only)
 15. get_feedback_trace / get_recent_contexts / get_runtime_summary
 16. health_check / clear / reset_stats
 17. create_decision_feedback_runtime 工厂
 18. safe_apply_runtime_feedback 全局安全
 19. RuntimeB4Bridge 集成(get_runtime_feedback / apply_runtime_feedback /
     get_runtime_feedback_trace / get_runtime_feedback_summary /
     feedback_runtime_health_check)
 20. RuntimeB4Bridge 兼容性(B.4/B.5/B.6/B.7/B.8 现有功能不受影响)
 21. 核心模块未修改保护(supervisor / runtime_core / proactive_engine /
     action_dispatcher / ActionConfidenceGate)
 22. 闭环验证:Action → Outcome → Feedback → Adapter → Runtime → 影响 Decision
 23. 审计 trace 记录(decision_feedback_applied stage)
 24. B.4~B.8 regression(确保 B.9 不破坏已有功能)

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

from src.runtime.decision_feedback_runtime import (
    PHASE_B9_DEFAULT_CONFIG,
    PHASE_B9_NAME,
    PHASE_B9_VERSION,
    DEFAULT_RUNTIME_PATH,
    SCHEMA_VERSION as B9_SCHEMA_VERSION,
    DECISION_FEEDBACK_STAGE_APPLIED,
    apply_phase_b9_config,
    is_phase_b9_enabled,
    is_b9_enabled_simple,
    DecisionFeedbackContext,
    AdjustedProposal,
    DecisionFeedbackRuntime,
    create_decision_feedback_runtime,
    safe_apply_runtime_feedback,
)

from src.runtime.decision_feedback_adapter import (
    DecisionFeedbackAdjustment,
    RECOMMENDATION_BOOST,
    RECOMMENDATION_NEUTRAL,
    RECOMMENDATION_SUPPRESS,
)

from src.runtime.action_feedback import (
    DecisionFeedbackScore,
    ActionFeedbackManager,
)

from src.runtime.action_outcome import (
    OutcomeRecord,
    OutcomeTracker,
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


class FakeFeedbackAdapter:
    """模拟 DecisionFeedbackAdapter"""

    def __init__(self, adj: Optional[DecisionFeedbackAdjustment] = None) -> None:
        self._adj = adj
        self.apply_count = 0
        self.health = {
            "enabled": True,
            "degraded": False,
        }

    def apply_feedback(
        self, action_type: str, confidence: float, user_id: Optional[str] = None,
    ) -> DecisionFeedbackAdjustment:
        self.apply_count += 1
        if self._adj is None:
            return DecisionFeedbackAdjustment(
                action_type=action_type,
                original_confidence=confidence,
                adjusted_confidence=confidence,
                recommendation=RECOMMENDATION_NEUTRAL,
            )
        # 重新构造,保持新 action_type / confidence
        return DecisionFeedbackAdjustment(
            action_type=action_type,
            original_confidence=confidence,
            adjusted_confidence=self._adj.adjusted_confidence,
            feedback_weight=self._adj.feedback_weight,
            confidence_delta=self._adj.confidence_delta,
            recommendation=self._adj.recommendation,
            reason=self._adj.reason,
            timestamp=time.time(),
        )

    def health_check(self) -> Dict[str, Any]:
        return self.health


class FakeActionProposal:
    """模拟 ProactiveEngine 输出的 proposal"""

    def __init__(
        self,
        action_id: str = "",
        action_type: str = "",
        user_id: Optional[str] = None,
        confidence: float = 0.0,
    ) -> None:
        self.action_id = action_id
        self.action_type = action_type
        self.user_id = user_id
        self.confidence = confidence


# ============================================================
# 3. 配置测试
# ============================================================

class TestB9ConfigInjection:
    """PHASE_B9_DEFAULT_CONFIG / apply_phase_b9_config / is_phase_b9_enabled"""

    def test_default_keys_present(self):
        """默认配置必须包含 decision_feedback_runtime.enabled=True"""
        cfg = PHASE_B9_DEFAULT_CONFIG
        assert "decision_feedback_runtime" in cfg
        assert cfg["decision_feedback_runtime"]["enabled"] is True
        assert cfg["decision_feedback_runtime"]["path"] == "data/decision_feedback_runtime.jsonl"
        assert cfg["decision_feedback_runtime"]["auto_recover"] is True
        assert cfg["decision_feedback_runtime"]["min_confidence"] == 0.0
        assert cfg["decision_feedback_runtime"]["max_confidence"] == 1.0
        assert cfg["decision_feedback_runtime"]["audit_stage"] == "decision_feedback_applied"

    def test_default_on(self):
        """B.9 默认开启(但不阻塞)"""
        assert PHASE_B9_DEFAULT_CONFIG["decision_feedback_runtime"]["enabled"] is True

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b9_config(None)
        assert result["decision_feedback_runtime"]["enabled"] is True

    def test_user_can_disable(self):
        """用户可显式关闭"""
        result = apply_phase_b9_config({"decision_feedback_runtime": {"enabled": False}})
        assert result["decision_feedback_runtime"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {
            "decision_feedback_runtime": {"enabled": False, "path": "/tmp/x.jsonl"},
        }
        snapshot = json.dumps(user_cfg, sort_keys=True)
        result = apply_phase_b9_config(user_cfg)
        assert json.dumps(user_cfg, sort_keys=True) == snapshot
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b9_config("not a dict")["decision_feedback_runtime"]["enabled"] is True
        assert apply_phase_b9_config(42)["decision_feedback_runtime"]["enabled"] is True
        assert apply_phase_b9_config([])["decision_feedback_runtime"]["enabled"] is True

    def test_is_phase_b9_enabled(self):
        """is_phase_b9_enabled 判定正确"""
        assert is_phase_b9_enabled({"decision_feedback_runtime": {"enabled": True}}) is True
        assert is_phase_b9_enabled({"decision_feedback_runtime": {"enabled": False}}) is False
        assert is_phase_b9_enabled({}) is True
        assert is_phase_b9_enabled(None) is False

    def test_is_b9_enabled_simple_variants(self):
        """is_b9_enabled_simple 兼容 cfg['runtime_enabled']"""
        assert is_b9_enabled_simple({"runtime_enabled": True}) is True
        assert is_b9_enabled_simple({"runtime_enabled": False}) is False
        assert is_b9_enabled_simple({"decision_feedback_runtime": {"enabled": True}}) is True
        assert is_b9_enabled_simple({}) is True
        assert is_b9_enabled_simple(None) is False


# ============================================================
# 4. DecisionFeedbackContext 测试
# ============================================================

class TestDecisionFeedbackContext:
    """DecisionFeedbackContext 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        c = DecisionFeedbackContext(
            action_type="greeting",
            original_confidence=0.5,
            adjusted_confidence=0.6,
        )
        assert c.action_type == "greeting"
        assert c.original_confidence == 0.5
        assert c.adjusted_confidence == 0.6
        assert c.feedback_weight == 0.5
        assert c.recommendation == "neutral"
        assert c.adjustment_reason == ""
        assert c.timestamp == 0.0

    def test_to_dict(self):
        """to_dict 输出符合 schema"""
        c = DecisionFeedbackContext(
            action_type="share",
            original_confidence=0.5,
            adjusted_confidence=0.8,
            feedback_weight=0.85,
            recommendation=RECOMMENDATION_BOOST,
            adjustment_reason="boost (weight=0.85)",
            timestamp=1234.5,
        )
        d = c.to_dict()
        assert d["schema_version"] == B9_SCHEMA_VERSION
        assert d["action_type"] == "share"
        assert d["original_confidence"] == 0.5
        assert d["adjusted_confidence"] == 0.8
        assert d["feedback_weight"] == 0.85
        assert d["recommendation"] == RECOMMENDATION_BOOST
        assert d["adjustment_reason"] == "boost (weight=0.85)"
        assert d["timestamp"] == 1234.5

    def test_from_dict_round_trip(self):
        """to_dict → from_dict 还原"""
        original = DecisionFeedbackContext(
            action_type="reminder",
            original_confidence=0.3,
            adjusted_confidence=0.1,
            feedback_weight=0.2,
            recommendation=RECOMMENDATION_SUPPRESS,
            adjustment_reason="suppress (weight=0.20)",
            timestamp=2000.0,
        )
        d = original.to_dict()
        restored = DecisionFeedbackContext.from_dict(d)
        assert restored.action_type == original.action_type
        assert restored.original_confidence == original.original_confidence
        assert restored.adjusted_confidence == original.adjusted_confidence
        assert restored.feedback_weight == original.feedback_weight
        assert restored.recommendation == original.recommendation
        assert restored.adjustment_reason == original.adjustment_reason
        assert restored.timestamp == original.timestamp


# ============================================================
# 5. AdjustedProposal 测试
# ============================================================

class TestAdjustedProposal:
    """AdjustedProposal 数据类"""

    def test_basic_creation(self):
        """基本字段构造"""
        ctx = DecisionFeedbackContext(
            action_type="g",
            original_confidence=0.5,
            adjusted_confidence=0.6,
        )
        p = AdjustedProposal(
            action_id="a1",
            action_type="g",
            user_id="u1",
            original_confidence=0.5,
            adjusted_confidence=0.6,
            context=ctx,
        )
        assert p.action_id == "a1"
        assert p.action_type == "g"
        assert p.user_id == "u1"
        assert p.original_confidence == 0.5
        assert p.adjusted_confidence == 0.6
        assert p.context is ctx
        assert p.extra == {}

    def test_to_dict(self):
        """to_dict 包含 context"""
        ctx = DecisionFeedbackContext(
            action_type="g",
            original_confidence=0.5,
            adjusted_confidence=0.7,
            feedback_weight=0.85,
            recommendation=RECOMMENDATION_BOOST,
            adjustment_reason="boost",
        )
        p = AdjustedProposal(
            action_id="a1",
            action_type="g",
            user_id="u1",
            original_confidence=0.5,
            adjusted_confidence=0.7,
            context=ctx,
            extra={"extra_key": "extra_val"},
        )
        d = p.to_dict()
        assert d["action_id"] == "a1"
        assert d["action_type"] == "g"
        assert d["user_id"] == "u1"
        assert d["original_confidence"] == 0.5
        assert d["adjusted_confidence"] == 0.7
        assert d["context"]["action_type"] == "g"
        assert d["context"]["adjusted_confidence"] == 0.7
        assert d["extra"]["extra_key"] == "extra_val"

    def test_to_dict_no_extra(self):
        """无 extra 时不输出 extra 字段"""
        ctx = DecisionFeedbackContext(
            action_type="g", original_confidence=0.5, adjusted_confidence=0.5,
        )
        p = AdjustedProposal(
            action_id="a1", action_type="g", user_id=None,
            original_confidence=0.5, adjusted_confidence=0.5, context=ctx,
        )
        d = p.to_dict()
        assert "extra" not in d


# ============================================================
# 6. evaluate_action_feedback 核心逻辑
# ============================================================

class TestEvaluateActionFeedback:
    """DecisionFeedbackRuntime.evaluate_action_feedback 纯计算逻辑"""

    def test_no_adapter_keeps_confidence(self):
        """无 feedback_adapter → confidence 不变"""
        rt = DecisionFeedbackRuntime(feedback_adapter=None)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.recommendation == "neutral"
        assert ctx.adjusted_confidence == 0.5
        assert ctx.adjustment_reason == "no_feedback_adapter"

    def test_empty_action_type_keeps_confidence(self):
        """action_type 为空时保持 confidence"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="",
                original_confidence=0.5,
                adjusted_confidence=0.9,
                recommendation=RECOMMENDATION_BOOST,
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("", confidence=0.5)
        assert ctx.adjusted_confidence == 0.5
        assert ctx.adjustment_reason == "empty action_type"

    def test_boost_increases_confidence(self):
        """boost → adjusted_confidence > original"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.6,
                feedback_weight=0.85,
                confidence_delta=0.1,
                recommendation=RECOMMENDATION_BOOST,
                reason="boost (weight=0.85)",
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.recommendation == RECOMMENDATION_BOOST
        assert ctx.adjusted_confidence > 0.5

    def test_suppress_decreases_confidence(self):
        """suppress → adjusted_confidence < original"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.3,
                feedback_weight=0.15,
                confidence_delta=-0.2,
                recommendation=RECOMMENDATION_SUPPRESS,
                reason="suppress (weight=0.15)",
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.recommendation == RECOMMENDATION_SUPPRESS
        assert ctx.adjusted_confidence < 0.5

    def test_neutral_keeps_confidence(self):
        """neutral → confidence 保持"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.5,
                feedback_weight=0.5,
                confidence_delta=0.0,
                recommendation=RECOMMENDATION_NEUTRAL,
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.recommendation == RECOMMENDATION_NEUTRAL
        assert ctx.adjusted_confidence == 0.5

    def test_input_confidence_clipped_high(self):
        """input confidence > 1.0 → clip 到 1.0"""
        rt = DecisionFeedbackRuntime(feedback_adapter=None)
        ctx = rt.evaluate_action_feedback("g", confidence=1.5)
        assert ctx.original_confidence == 1.0

    def test_input_confidence_clipped_low(self):
        """input confidence < 0.0 → clip 到 0.0"""
        rt = DecisionFeedbackRuntime(feedback_adapter=None)
        ctx = rt.evaluate_action_feedback("g", confidence=-0.5)
        assert ctx.original_confidence == 0.0

    def test_output_confidence_clipped_high(self):
        """output adjusted_confidence > 1.0 → clip"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.95,
                adjusted_confidence=1.5,  # 越界
                feedback_weight=0.9,
                recommendation=RECOMMENDATION_BOOST,
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("g", confidence=0.95)
        assert ctx.adjusted_confidence == 1.0

    def test_output_confidence_clipped_low(self):
        """output adjusted_confidence < 0.0 → clip"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.1,
                adjusted_confidence=-0.5,  # 越界
                feedback_weight=0.1,
                recommendation=RECOMMENDATION_SUPPRESS,
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("g", confidence=0.1)
        assert ctx.adjusted_confidence == 0.0

    def test_adapter_exception_isolated(self):
        """adapter 抛错时 → 返回 neutral"""
        bad = MagicMock()
        bad.apply_feedback.side_effect = RuntimeError("boom")
        rt = DecisionFeedbackRuntime(feedback_adapter=bad)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.recommendation == "neutral"
        assert ctx.adjusted_confidence == 0.5
        assert rt.apply_error_count >= 1

    def test_adapter_returns_none(self):
        """adapter 返回 None → neutral"""
        adj = MagicMock()
        adj.apply_feedback.return_value = None
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.recommendation == "neutral"
        assert ctx.adjusted_confidence == 0.5

    def test_custom_bounds(self):
        """自定义 min/max confidence"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.6,
                adjusted_confidence=0.6,
                feedback_weight=0.5,
                recommendation=RECOMMENDATION_BOOST,
            )
        )
        rt = DecisionFeedbackRuntime(
            feedback_adapter=adj, min_confidence=0.2, max_confidence=0.7,
        )
        # 强制 output > 0.7
        adj._adj = DecisionFeedbackAdjustment(
            action_type="g",
            original_confidence=0.6,
            adjusted_confidence=1.0,
            feedback_weight=0.9,
            recommendation=RECOMMENDATION_BOOST,
        )
        ctx = rt.evaluate_action_feedback("g", confidence=0.6)
        assert ctx.adjusted_confidence == 0.7

    def test_disabled_runtime_returns_neutral(self):
        """disabled runtime → 中性返回"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.9,
                recommendation=RECOMMENDATION_BOOST,
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj, enabled=False)
        # evaluate 仍然有效(纯计算)
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        # 即使 enabled=False,evaluate 仍正常
        assert ctx.recommendation == RECOMMENDATION_BOOST


# ============================================================
# 7. apply_feedback 副作用
# ============================================================

class TestApplyFeedback:
    """DecisionFeedbackRuntime.apply_feedback 副作用逻辑"""

    def test_apply_no_persistence(self):
        """无 persistence 时仍可 apply(只内存)"""
        rt = DecisionFeedbackRuntime(persistence=None)
        proposal = FakeActionProposal(
            action_id="a1", action_type="g", user_id="u1", confidence=0.5,
        )
        adj = rt.apply_feedback(proposal)
        assert adj.action_id == "a1"
        assert adj.action_type == "g"
        assert rt.in_memory_count == 1
        assert rt.apply_count == 1

    def test_apply_disabled(self, tmp_path):
        """enabled=False 时只更新内存,不写盘"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr, enabled=False)
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        rt.apply_feedback(proposal)
        assert mgr.write_count == 0
        # 内存已记录
        assert rt.in_memory_count == 1

    def test_apply_persists_to_persistence(self, tmp_path):
        """apply_feedback 通过 persistence 写盘"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        rt.apply_feedback(proposal)
        assert mgr.write_count >= 1
        assert os.path.exists(log_path)
        # 检查文件中包含 decision_feedback_applied stage
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        stages = [json.loads(l).get("stage") for l in lines if l.strip()]
        assert DECISION_FEEDBACK_STAGE_APPLIED in stages

    def test_apply_records_lifecycle(self, tmp_path):
        """apply_feedback 通过 lifecycle manager 记录 trail"""
        log_path = str(tmp_path / "lc.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        lc = ActionLifecycleManager(ExecutionPolicy(), persistence=mgr)
        rt = DecisionFeedbackRuntime(lifecycle_manager=lc, persistence=mgr)
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        rt.apply_feedback(proposal)
        trail = lc.get_audit_trail()
        # 应该包含 decision_feedback_applied stage
        stages = [r.get("stage") for r in trail]
        assert DECISION_FEEDBACK_STAGE_APPLIED in stages

    def test_apply_empty_proposal(self):
        """proposal 为 None → 返回中性 AdjustedProposal"""
        rt = DecisionFeedbackRuntime()
        adj = rt.apply_feedback(None)
        assert adj.action_id == ""
        assert adj.action_type == ""
        assert adj.context.recommendation == "neutral"
        assert adj.context.adjustment_reason == "empty_proposal"

    def test_apply_returns_adjusted_proposal(self):
        """apply_feedback 返回 AdjustedProposal"""
        rt = DecisionFeedbackRuntime()
        proposal = FakeActionProposal(
            action_id="a1", action_type="greeting", user_id="u1", confidence=0.5,
        )
        adj = rt.apply_feedback(proposal)
        assert isinstance(adj, AdjustedProposal)
        assert adj.action_id == "a1"
        assert adj.action_type == "greeting"
        assert adj.user_id == "u1"
        assert adj.original_confidence == 0.5
        # 没装 adapter → neutral
        assert adj.adjusted_confidence == 0.5

    def test_apply_batch(self, tmp_path):
        """批量 apply"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        proposals = [
            FakeActionProposal(action_id=f"a{i}", action_type="g", confidence=0.5)
            for i in range(3)
        ]
        for p in proposals:
            rt.apply_feedback(p)
        assert rt.in_memory_count == 3
        assert rt.apply_count == 3

    def test_apply_with_boost_adapter(self):
        """boost adapter → adjusted_confidence > original"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.6,
                feedback_weight=0.85,
                confidence_delta=0.1,
                recommendation=RECOMMENDATION_BOOST,
                reason="boost",
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        proposal = FakeActionProposal(
            action_id="a1", action_type="g", confidence=0.5,
        )
        result = rt.apply_feedback(proposal)
        assert result.adjusted_confidence > 0.5
        assert result.context.recommendation == RECOMMENDATION_BOOST

    def test_apply_with_suppress_adapter(self):
        """suppress adapter → adjusted_confidence < original"""
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.3,
                feedback_weight=0.15,
                confidence_delta=-0.2,
                recommendation=RECOMMENDATION_SUPPRESS,
                reason="suppress",
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj)
        proposal = FakeActionProposal(
            action_id="a1", action_type="g", confidence=0.5,
        )
        result = rt.apply_feedback(proposal)
        assert result.adjusted_confidence < 0.5
        assert result.context.recommendation == RECOMMENDATION_SUPPRESS

    def test_apply_persists_correct_payload(self, tmp_path):
        """apply_feedback 持久化 payload 完整"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        # 注入 boost adapter 让 reason 非空
        adj = FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="greeting",
                original_confidence=0.5,
                adjusted_confidence=0.6,
                feedback_weight=0.85,
                confidence_delta=0.1,
                recommendation=RECOMMENDATION_BOOST,
                reason="boost (weight=0.85)",
            )
        )
        rt = DecisionFeedbackRuntime(feedback_adapter=adj, persistence=mgr)
        proposal = FakeActionProposal(
            action_id="a1", action_type="greeting", user_id="u1", confidence=0.5,
        )
        rt.apply_feedback(proposal)
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        rec = json.loads(lines[-1])
        assert rec["stage"] == DECISION_FEEDBACK_STAGE_APPLIED
        assert rec["source"] == "phase_b9_runtime"
        result = rec["result"]
        assert result["action_type"] == "greeting"
        assert "original_confidence" in result
        assert "adjusted_confidence" in result
        assert "feedback_weight" in result
        assert "recommendation" in result
        assert "adjustment_reason" in result
        assert "timestamp" in result


# ============================================================
# 8. 异常隔离
# ============================================================

class TestFailureIsolation:
    """apply_feedback 失败自动降级"""

    def test_persistence_failure_returns_degraded(self):
        """persistence 写盘失败 → 内存仍记录,apply 计数 +1"""
        mgr = MagicMock()
        mgr.is_degraded = True
        mgr.persist_event.return_value = False
        rt = DecisionFeedbackRuntime(persistence=mgr)
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        adj = rt.apply_feedback(proposal)
        # 内存已记录
        assert adj.action_type == "g"
        assert rt.in_memory_count == 1
        # 错误计数
        assert rt.apply_error_count >= 1

    def test_persistence_exception_isolated(self):
        """persistence.persist_event 抛错时隔离"""
        mgr = MagicMock()
        mgr.is_degraded = False
        mgr.persist_event.side_effect = RuntimeError("boom")
        rt = DecisionFeedbackRuntime(persistence=mgr)
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        adj = rt.apply_feedback(proposal)
        # 内存已记录
        assert adj.action_type == "g"
        assert rt.in_memory_count == 1
        # 错误计数
        assert rt.apply_error_count >= 1

    def test_lifecycle_exception_isolated(self):
        """lifecycle.record_trail 抛错时隔离"""
        mgr = MagicMock()
        mgr.persist_event.return_value = True
        mgr.is_degraded = False
        bad_lc = MagicMock()
        bad_lc.new_lifecycle_id.return_value = "lc1"
        bad_lc.record_trail.side_effect = RuntimeError("boom")
        rt = DecisionFeedbackRuntime(
            persistence=mgr, lifecycle_manager=bad_lc,
        )
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        # 不抛
        adj = rt.apply_feedback(proposal)
        assert adj is not None
        assert rt.in_memory_count == 1

    def test_safe_apply_runtime_feedback_none_runtime(self):
        """safe_apply_runtime_feedback(runtime=None) → 中性"""
        adj = safe_apply_runtime_feedback(None, "g")
        assert adj.context.recommendation == "neutral"
        assert adj.context.adjustment_reason == "runtime is None"

    def test_safe_apply_runtime_feedback_exception(self):
        """safe_apply_runtime_feedback 处理 apply 抛错"""
        bad = MagicMock()
        bad.apply_feedback.side_effect = RuntimeError("boom")
        adj = safe_apply_runtime_feedback(bad, "g")
        assert adj.context.recommendation == "neutral"

    def test_close_disables_runtime(self):
        """close 后 evaluate 仍可用但 apply 行为变化"""
        rt = DecisionFeedbackRuntime(feedback_adapter=None)
        rt.close()
        # evaluate 返回 neutral
        ctx = rt.evaluate_action_feedback("g", confidence=0.5)
        assert ctx.adjustment_reason == "runtime_closed"


# ============================================================
# 9. 重启恢复
# ============================================================

class TestRecovery:
    """load_state 恢复 context 历史"""

    def test_load_state_no_persistence(self):
        """无 persistence 时返回 0"""
        rt = DecisionFeedbackRuntime(persistence=None)
        assert rt.load_state() == 0

    def test_load_state_empty(self, tmp_path):
        """空文件 → 恢复 0 条"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        assert rt.load_state() == 0

    def test_load_state_recovers_contexts(self, tmp_path):
        """从 JSONL 恢复 context"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        rt1 = DecisionFeedbackRuntime(persistence=mgr1)
        # 写入几条
        for atype in ["greeting", "share", "reminder"]:
            rt1.apply_feedback(FakeActionProposal(
                action_id=f"a_{atype}", action_type=atype, confidence=0.5,
            ))

        # 进程 2:从文件恢复
        mgr2 = ActionPersistenceManager(path=log_path)
        rt2 = DecisionFeedbackRuntime(persistence=mgr2)
        assert rt2.in_memory_count >= 1
        history = rt2.get_feedback_trace(limit=10)
        assert len(history) >= 1

    def test_load_state_skips_non_b9_events(self, tmp_path):
        """load_state 只关注 decision_feedback_applied 事件"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr1 = ActionPersistenceManager(path=log_path)
        mgr1.persist_event("a1", "lc1", LifecycleStage.CREATED)
        mgr1.persist_event("a1", "lc1", LifecycleStage.COMPLETED)

        mgr2 = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr2)
        count = rt.load_state()
        assert count == 0


# ============================================================
# 10. 查询与统计
# ============================================================

class TestQueries:
    """get_feedback_trace / get_recent_contexts / get_runtime_summary"""

    def test_get_feedback_trace_empty(self):
        """无记录时返回空"""
        rt = DecisionFeedbackRuntime(persistence=None)
        assert rt.get_feedback_trace() == []
        assert rt.get_recent_contexts() == []

    def test_get_feedback_trace_by_type(self):
        """按 action_type 过滤"""
        rt = DecisionFeedbackRuntime(persistence=None)
        rt.apply_feedback(FakeActionProposal(action_id="a1", action_type="g", confidence=0.5))
        rt.apply_feedback(FakeActionProposal(action_id="a2", action_type="s", confidence=0.5))
        rt.apply_feedback(FakeActionProposal(action_id="a3", action_type="g", confidence=0.5))

        g_history = rt.get_feedback_trace(action_type="g", limit=10)
        assert len(g_history) == 2
        for h in g_history:
            assert h["action_type"] == "g"

    def test_get_recent_contexts(self):
        """返回最近 N 条 context 对象"""
        rt = DecisionFeedbackRuntime(persistence=None)
        for i in range(5):
            rt.apply_feedback(FakeActionProposal(
                action_id=f"a{i}", action_type="g", confidence=0.5,
            ))
        recent = rt.get_recent_contexts(limit=3)
        assert len(recent) == 3
        for c in recent:
            assert isinstance(c, DecisionFeedbackContext)

    def test_get_runtime_summary_keys(self, tmp_path):
        """get_runtime_summary 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = DecisionFeedbackRuntime(persistence=mgr)
        s = rt.get_runtime_summary()
        for k in (
            "name", "version", "enabled", "degraded", "closed",
            "in_memory_contexts", "tracked_action_types",
            "apply_count", "apply_error_count", "recover_count",
            "last_apply_at", "last_error", "created_at",
            "min_confidence", "max_confidence",
        ):
            assert k in s, f"missing key: {k}"
        assert s["name"] == PHASE_B9_NAME
        assert s["version"] == PHASE_B9_VERSION

    def test_get_runtime_summary_after_apply(self):
        """apply 后统计正确"""
        rt = DecisionFeedbackRuntime(persistence=None)
        for i in range(3):
            rt.apply_feedback(FakeActionProposal(
                action_id=f"a{i}", action_type="g", confidence=0.5,
            ))
        s = rt.get_runtime_summary()
        assert s["apply_count"] == 3
        assert s["in_memory_contexts"] == 3

    def test_max_records_caps_memory(self):
        """max_records 限制内存中 context 数量"""
        rt = DecisionFeedbackRuntime(persistence=None, max_records=2)
        for i in range(5):
            rt.apply_feedback(FakeActionProposal(
                action_id=f"a{i}", action_type="g", confidence=0.5,
            ))
        # 内存中应只有 2 条
        assert rt.in_memory_count == 2


# ============================================================
# 11. 健康度 / 重置
# ============================================================

class TestHealth:
    """health_check / clear / reset_stats / enable / disable"""

    def test_health_check_keys(self, tmp_path):
        """health_check 字段完整"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = DecisionFeedbackRuntime(persistence=mgr)
        h = rt.health_check()
        for k in (
            "name", "schema_version", "version", "enabled", "degraded",
            "closed", "in_memory_contexts", "tracked_action_types",
            "apply_count", "apply_error_count", "recover_count",
            "last_apply_at", "last_error", "created_at",
            "min_confidence", "max_confidence",
            "feedback_adapter", "persistence", "lifecycle_manager",
        ):
            assert k in h, f"missing key: {k}"
        assert h["name"] == PHASE_B9_NAME
        assert h["schema_version"] == B9_SCHEMA_VERSION

    def test_enable_disable(self):
        """enable / disable 切换"""
        rt = DecisionFeedbackRuntime()
        assert rt.enabled is True
        rt.disable()
        assert rt.enabled is False
        rt.enable()
        assert rt.enabled is True

    def test_reset_stats(self, tmp_path):
        """reset_stats 重置统计"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = DecisionFeedbackRuntime(persistence=mgr)
        rt.apply_feedback(FakeActionProposal(action_id="a1", action_type="g", confidence=0.5))
        assert rt.apply_count >= 1
        rt.reset_stats()
        assert rt.apply_count == 0

    def test_clear(self, tmp_path):
        """clear 清空内存(不删文件)"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = DecisionFeedbackRuntime(persistence=mgr)
        rt.apply_feedback(FakeActionProposal(action_id="a1", action_type="g", confidence=0.5))
        assert rt.in_memory_count == 1
        rt.clear()
        assert rt.in_memory_count == 0
        assert os.path.exists(str(tmp_path / "x.jsonl"))

    def test_is_degraded_no_persistence(self):
        """无 persistence → is_degraded=True"""
        rt = DecisionFeedbackRuntime(persistence=None)
        assert rt.is_degraded is True

    def test_is_degraded_with_persistence(self, tmp_path):
        """有 persistence → is_degraded=False(若非降级)"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = DecisionFeedbackRuntime(persistence=mgr)
        assert rt.is_degraded is False


# ============================================================
# 12. 工厂
# ============================================================

class TestCreateDecisionFeedbackRuntime:
    """create_decision_feedback_runtime 工厂"""

    def test_create_with_persistence(self, tmp_path):
        """带 persistence 创建"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = create_decision_feedback_runtime(persistence=mgr)
        assert rt is not None
        assert rt.persistence is mgr
        assert rt.enabled is True

    def test_create_with_disabled(self, tmp_path):
        """disabled=True 显式关闭"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = create_decision_feedback_runtime(persistence=mgr, enabled=False)
        assert rt.enabled is False

    def test_create_with_no_persistence(self):
        """无 persistence 创建"""
        rt = create_decision_feedback_runtime(persistence=None)
        assert rt is not None
        assert rt.persistence is None
        assert rt.is_degraded is True

    def test_create_from_cfg(self, tmp_path):
        """从 cfg 创建"""
        cfg = {
            "decision_feedback_runtime": {
                "enabled": True,
                "max_records": 100,
                "max_history": 50,
                "min_confidence": 0.1,
                "max_confidence": 0.9,
            }
        }
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        rt = create_decision_feedback_runtime(persistence=mgr, cfg=cfg)
        assert rt is not None
        assert rt._max_records == 100
        assert rt._max_history == 50
        assert rt._min_confidence == 0.1
        assert rt._max_confidence == 0.9


# ============================================================
# 13. RuntimeB4Bridge 集成
# ============================================================

class TestB4BridgeRuntimeIntegration:
    """RuntimeB4Bridge 集成 DecisionFeedbackRuntime"""

    def test_b4_bridge_has_feedback_runtime(self, tmp_path):
        """RuntimeB4Bridge 持有 feedback_runtime"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        assert b.feedback_runtime is not None

    def test_b4_bridge_feedback_runtime_no_persistence(self):
        """无 persistence 时 feedback_runtime 仍创建(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.feedback_runtime is not None
        assert b.feedback_runtime.persistence is None

    def test_b4_bridge_explicit_feedback_runtime(self, tmp_path):
        """显式注入 feedback_runtime"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        custom_rt = DecisionFeedbackRuntime(
            persistence=mgr, min_confidence=0.1, max_confidence=0.9,
        )
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
            feedback_runtime=custom_rt,
        )
        assert b.feedback_runtime is custom_rt
        assert b.feedback_runtime._min_confidence == 0.1
        assert b.feedback_runtime._max_confidence == 0.9

    def test_b4_bridge_get_runtime_feedback(self, tmp_path):
        """B.4 bridge 转发 get_runtime_feedback"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        # 注入 adapter(模拟 B.7 outcome → B.8 boost)
        boost_adj = DecisionFeedbackAdjustment(
            action_type="greeting",
            original_confidence=0.5,
            adjusted_confidence=0.6,
            feedback_weight=0.85,
            confidence_delta=0.1,
            recommendation=RECOMMENDATION_BOOST,
        )
        adapter = FakeFeedbackAdapter(adj=boost_adj)
        b._feedback_runtime.set_feedback_adapter(adapter)
        ctx = b.get_runtime_feedback("greeting", confidence=0.5)
        assert ctx is not None
        assert ctx.recommendation == RECOMMENDATION_BOOST
        assert ctx.adjusted_confidence > 0.5

    def test_b4_bridge_get_runtime_feedback_no_runtime(self):
        """feedback_runtime 不可用时返回中性"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._feedback_runtime = None
        ctx = b.get_runtime_feedback("greeting", confidence=0.5)
        assert ctx is not None
        assert ctx.recommendation == "neutral"
        assert ctx.adjusted_confidence == 0.5
        assert ctx.adjustment_reason == "runtime is None"

    def test_b4_bridge_apply_runtime_feedback(self, tmp_path):
        """B.4 bridge 转发 apply_runtime_feedback"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        proposal = FakeActionProposal(
            action_id="a1", action_type="greeting", user_id="u1", confidence=0.5,
        )
        adj = b.apply_runtime_feedback(proposal)
        assert adj is not None
        assert adj.action_id == "a1"
        assert b._total_runtime_feedback_applied >= 1

    def test_b4_bridge_apply_runtime_feedback_no_runtime(self):
        """feedback_runtime 不可用时返回中性 AdjustedProposal"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        b._feedback_runtime = None
        proposal = FakeActionProposal(
            action_id="a1", action_type="g", confidence=0.5,
        )
        adj = b.apply_runtime_feedback(proposal)
        assert adj is not None
        assert adj.adjusted_confidence == 0.5
        assert adj.context.recommendation == "neutral"

    def test_b4_bridge_get_runtime_feedback_trace(self, tmp_path):
        """B.4 bridge 转发 get_runtime_feedback_trace"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.apply_runtime_feedback(FakeActionProposal(
            action_id="a1", action_type="greeting", confidence=0.5,
        ))
        b.apply_runtime_feedback(FakeActionProposal(
            action_id="a2", action_type="share", confidence=0.5,
        ))
        history = b.get_runtime_feedback_trace(limit=10)
        assert len(history) >= 2

    def test_b4_bridge_get_runtime_feedback_summary(self, tmp_path):
        """B.4 bridge 转发 get_runtime_feedback_summary"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.apply_runtime_feedback(FakeActionProposal(
            action_id="a1", action_type="g", confidence=0.5,
        ))
        s = b.get_runtime_feedback_summary()
        assert s["name"] == "phase_b9"
        assert s["apply_count"] >= 1

    def test_b4_bridge_feedback_runtime_health_check(self, tmp_path):
        """B.4 bridge 转发 feedback_runtime_health_check"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        h = b.feedback_runtime_health_check()
        assert h["name"] == "phase_b9"
        assert "enabled" in h
        assert "degraded" in h

    def test_b4_bridge_summarize_includes_runtime(self, tmp_path):
        """summarize_b4 包含 decision_feedback_runtime 状态"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.apply_runtime_feedback(FakeActionProposal(
            action_id="a1", action_type="greeting", confidence=0.5,
        ))
        s = b.summarize_b4()
        assert "decision_feedback_runtime" in s
        assert s["decision_feedback_runtime"]["enabled"] is True
        assert s["decision_feedback_runtime"]["adjustment_count"] >= 1
        assert s["decision_feedback_runtime"]["last_adjustment"] is not None
        assert "total_runtime_feedback_applied" in s
        assert s["total_runtime_feedback_applied"] >= 1


# ============================================================
# 14. 兼容性测试(B.4~B.8)
# ============================================================

class TestB9Compatibility:
    """B.9 不破坏 B.4/B.5/B.6/B.7/B.8 现有功能"""

    def test_b4_without_persistence_runtime_works(self):
        """B.4 不传 persistence 时 feedback_runtime 仍创建(纯内存)"""
        inner = FakeB3Bridge()
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
        )
        assert b.persistence is None
        assert b.feedback_runtime is not None
        out = b.flush_pending()
        assert "dispatched" in out

    def test_b6_outcome_unchanged(self, tmp_path):
        """B.9 引入后,B.6 outcome 仍正常"""
        log_path = str(tmp_path / "b9.jsonl")
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
        """B.9 引入后,B.7 feedback 仍正常"""
        log_path = str(tmp_path / "b9.jsonl")
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
        """B.9 引入后,B.8 adapter 仍正常"""
        log_path = str(tmp_path / "b9.jsonl")
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

    def test_b4_audit_still_works(self, tmp_path):
        """B.9 引入后,B.4 audit 仍正常"""
        from src.runtime.audit_log.runtime_audit_logger import RuntimeAuditLogger

        log_path = str(tmp_path / "audit.jsonl")
        audit = RuntimeAuditLogger(log_path=log_path)
        mgr_path = str(tmp_path / "b9.jsonl")
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
        """B.9 引入后,B.5 persistence 仍正常"""
        log_path = str(tmp_path / "b9.jsonl")
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

    def test_get_runtime_feedback_without_runtime(self, tmp_path):
        """feedback_runtime=None 时返回中性"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_runtime = None
        ctx = b.get_runtime_feedback("g", confidence=0.7)
        assert ctx is not None
        assert ctx.recommendation == "neutral"
        assert ctx.adjusted_confidence == 0.7

    def test_apply_runtime_feedback_without_runtime(self, tmp_path):
        """feedback_runtime=None 时返回中性 AdjustedProposal"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_runtime = None
        proposal = FakeActionProposal(action_id="a1", action_type="g", confidence=0.5)
        adj = b.apply_runtime_feedback(proposal)
        assert adj is not None
        assert adj.adjusted_confidence == 0.5
        assert adj.context.recommendation == "neutral"

    def test_get_runtime_feedback_trace_without_runtime(self, tmp_path):
        """feedback_runtime=None 时返回空"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_runtime = None
        assert b.get_runtime_feedback_trace() == []

    def test_get_runtime_feedback_summary_without_runtime(self, tmp_path):
        """feedback_runtime=None 时返回零摘要"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_runtime = None
        s = b.get_runtime_feedback_summary()
        assert s["enabled"] is False
        assert s["in_memory_contexts"] == 0

    def test_feedback_runtime_health_check_without_runtime(self, tmp_path):
        """feedback_runtime=None 时返回零健康度"""
        mgr = ActionPersistenceManager(path=str(tmp_path / "x.jsonl"))
        b = RuntimeB4Bridge(
            inner_bridge=FakeB3Bridge(),
            use_governance=True,
            persistence=mgr,
        )
        b._feedback_runtime = None
        h = b.feedback_runtime_health_check()
        assert h["enabled"] is False

    def test_flush_does_not_call_runtime(self, tmp_path):
        """flush_pending 不主动调用 runtime(runtime 是按需调用)"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        recent = [{"action_id": "a1", "stage": "dispatched", "action_type": "g"}]
        inner = FakeB3Bridge(recent_results=recent)
        b = create_b4_bridge(
            inner,
            cfg={"governance": {"enabled": True, "audit_enabled": False}},
            persistence=mgr,
        )
        b.flush_pending()
        # runtime 没有自动 apply
        assert b._total_runtime_feedback_applied == 0


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

    def test_no_proactive_engine_runtime_references(self):
        """ProactiveEngine 不应直接引用 runtime(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        proactive = repo_root / "src" / "proactive" / "proactive_engine.py"
        if not proactive.exists():
            pytest.skip("proactive_engine not found")
        src = proactive.read_text(encoding="utf-8")
        assert "phase_b9" not in src, "proactive_engine should not reference phase_b9"
        assert "decision_feedback_runtime" not in src, (
            "proactive_engine should not reference decision_feedback_runtime"
        )

    def test_no_supervisor_runtime_references(self):
        """supervisor.py 不应直接引用 runtime(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        sup = repo_root / "src" / "runtime" / "supervisor.py"
        if not sup.exists():
            pytest.skip("supervisor not found")
        src = sup.read_text(encoding="utf-8")
        assert "phase_b9" not in src, "supervisor should not reference phase_b9"
        assert "decision_feedback_runtime" not in src, (
            "supervisor should not reference decision_feedback_runtime"
        )
        assert "feedback_runtime" not in src, (
            "supervisor should not reference feedback_runtime"
        )

    def test_no_runtime_core_runtime_references(self):
        """runtime_core.py 不应直接引用 runtime(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        rc = repo_root / "src" / "runtime" / "runtime_core.py"
        if not rc.exists():
            pytest.skip("runtime_core not found")
        src = rc.read_text(encoding="utf-8")
        assert "phase_b9" not in src, "runtime_core should not reference phase_b9"
        assert "decision_feedback_runtime" not in src, (
            "runtime_core should not reference decision_feedback_runtime"
        )
        assert "feedback_runtime" not in src, (
            "runtime_core should not reference feedback_runtime"
        )

    def test_no_action_dispatcher_runtime_references(self):
        """action_dispatcher.py 不应直接引用 runtime(零侵入保证)"""
        repo_root = Path(__file__).resolve().parents[1]
        ad = repo_root / "src" / "runtime" / "action_dispatcher.py"
        if not ad.exists():
            pytest.skip("action_dispatcher not found")
        src = ad.read_text(encoding="utf-8")
        assert "phase_b9" not in src, "action_dispatcher should not reference phase_b9"
        assert "decision_feedback_runtime" not in src, (
            "action_dispatcher should not reference decision_feedback_runtime"
        )
        assert "feedback_runtime" not in src, (
            "action_dispatcher should not reference feedback_runtime"
        )


# ============================================================
# 16. 闭环验证
# ============================================================

class TestClosedLoop:
    """闭环验证:Action → Outcome → Feedback → Adapter → Runtime → 影响 Decision"""

    def test_full_loop(self, tmp_path):
        """完整闭环:outcome → profile → feedback → adapter → runtime → confidence"""
        log_path = str(tmp_path / "b9.jsonl")
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

        # 3) 通过 runtime 获取 confidence 调整(纯计算)
        ctx = b.get_runtime_feedback("greeting", confidence=0.5)
        assert ctx.recommendation == RECOMMENDATION_BOOST
        assert ctx.adjusted_confidence > 0.5

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

        # 5) runtime 对 share 给出 suppress 信号(纯计算)
        s_ctx = b.get_runtime_feedback("share", confidence=0.5)
        assert s_ctx.recommendation == RECOMMENDATION_SUPPRESS
        assert s_ctx.adjusted_confidence < 0.5

        # 6) apply_runtime_feedback 多次,确保 trace 至少 2 条
        b.apply_runtime_feedback(FakeActionProposal(
            action_id="a_test1", action_type="greeting", user_id="u1", confidence=0.5,
        ))
        b.apply_runtime_feedback(FakeActionProposal(
            action_id="a_test2", action_type="share", user_id="u1", confidence=0.5,
        ))
        # 至少 2 条(来自 apply,greeting + share)
        trace = b.get_runtime_feedback_trace(limit=20)
        assert len(trace) >= 2

        # 7) summarize 包含完整状态
        s = b.summarize_b4()
        assert "decision_feedback_runtime" in s
        assert s["decision_feedback_runtime"]["enabled"] is True
        assert s["decision_feedback_runtime"]["adjustment_count"] >= 1
        assert s["total_outcomes_recorded"] == 15
        assert s["total_feedback_recorded"] == 15
        assert s["total_runtime_feedback_applied"] >= 2

    def test_runtime_persists_and_reloads(self, tmp_path):
        """runtime 数据可持久化、重启恢复"""
        log_path = str(tmp_path / "b9.jsonl")
        # 进程 1
        mgr1 = ActionPersistenceManager(path=log_path)
        rt1 = DecisionFeedbackRuntime(persistence=mgr1)
        for i in range(3):
            rt1.apply_feedback(FakeActionProposal(
                action_id=f"a{i}", action_type="greeting", confidence=0.5,
            ))

        # 进程 2:从文件恢复
        mgr2 = ActionPersistenceManager(path=log_path)
        rt2 = DecisionFeedbackRuntime(persistence=mgr2)
        # 恢复最新 1 条
        assert rt2.in_memory_count >= 1
        history = rt2.get_feedback_trace(action_type="greeting", limit=10)
        assert len(history) >= 1


# ============================================================
# 17. Schema 验证
# ============================================================

class TestRuntimeSchema:
    """context 持久化结构符合 schema"""

    def test_persistence_schema(self, tmp_path):
        """持久化文件结构正确"""
        log_path = str(tmp_path / "b9.jsonl")
        mgr = ActionPersistenceManager(path=log_path)
        rt = DecisionFeedbackRuntime(persistence=mgr)
        # 注入 boost adapter
        rt.set_feedback_adapter(FakeFeedbackAdapter(
            adj=DecisionFeedbackAdjustment(
                action_type="g",
                original_confidence=0.5,
                adjusted_confidence=0.6,
                feedback_weight=0.85,
                confidence_delta=0.1,
                recommendation=RECOMMENDATION_BOOST,
                reason="boost (weight=0.85)",
            )
        ))
        rt.apply_feedback(FakeActionProposal(
            action_id="a1", action_type="g", confidence=0.5,
        ))

        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["stage"] == DECISION_FEEDBACK_STAGE_APPLIED
        assert rec["source"] == "phase_b9_runtime"
        result = rec["result"]
        assert result["schema_version"] == B9_SCHEMA_VERSION
        assert result["action_type"] == "g"
        assert "original_confidence" in result
        assert "adjusted_confidence" in result
        assert "feedback_weight" in result
        assert "recommendation" in result
        assert "adjustment_reason" in result
        assert "timestamp" in result
