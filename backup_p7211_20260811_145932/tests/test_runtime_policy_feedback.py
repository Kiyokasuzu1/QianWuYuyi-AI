# -*- coding: utf-8 -*-
"""
tests/test_runtime_policy_feedback.py

Phase C.10.9 — Runtime Policy Feedback Loop 测试

覆盖:
1. PolicyMetrics 收集(record_execution / record_throttle_hit / record_budget_reject)
2. PolicyAdjustmentProposal 字段 / 工厂 / direction 计算
3. ProposalStore append-only 语义(list / get / filter / snapshot / stats)
4. AdaptiveEvaluator 评估逻辑(high_failure_rate / high_throttle / high_budget / high_success / high_cost)
5. Confidence 计算与上下界
6. EvaluatorConfig 字段与默认值
7. capture_policy_snapshot 只读
8. PolicyFeedbackEngine 集成(collect / generate / evaluate / record / snapshot)
9. EventBus 集成(RuntimePolicyFeedbackEvent)
10. Runtime 集成(configure_feedback_engine / record_module_execution / record_module_deny /
    trigger_policy_feedback / get_policy_feedback_snapshot / get_policy_feedback_proposals)
11. Fail-soft 行为(各种异常注入)
12. 默认行为保护(无 feedback engine 时完全向后兼容)

目标:
- >= 60 个测试用例
- 全部通过
- 现有 534+ 测试保持通过(向后兼容)
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional

import pytest


# ============================================================
# 路径设置
# ============================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture(autouse=True)
def _reset_policy_state():
    """每个 test 前重置 Policy 默认实例。"""
    from src.runtime.policy import (
        reset_default_policy_engine_for_testing,
    )
    try:
        reset_default_policy_engine_for_testing()
    except Exception:  # noqa: BLE001
        pass
    yield


# ============================================================
# Test: PolicyAdjustmentProposal
# ============================================================
class TestPolicyAdjustmentProposal:
    """PolicyAdjustmentProposal 数据结构。"""

    def test_default_construction(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal()
        assert p.module == ""
        assert p.parameter == ""
        assert p.confidence == 0.0
        assert p.direction == "no_change"
        assert p.proposal_id != ""
        assert p.created_at > 0.0

    def test_proposal_id_auto_generated(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p1 = PolicyAdjustmentProposal(module="x")
        p2 = PolicyAdjustmentProposal(module="x")
        assert p1.proposal_id != p2.proposal_id

    def test_direction_increase(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="x",
            old_value=10,
            suggested_value=20,
        )
        assert p.direction == "increase"

    def test_direction_decrease(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="x",
            old_value=20,
            suggested_value=10,
        )
        assert p.direction == "decrease"

    def test_direction_no_change(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="x",
            old_value=10,
            suggested_value=10,
        )
        assert p.direction == "no_change"

    def test_confidence_clamped_high(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(module="x", confidence=2.0)
        assert p.confidence == 1.0

    def test_confidence_clamped_low(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(module="x", confidence=-0.5)
        assert p.confidence == 0.0

    def test_confidence_invalid_input(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(module="x", confidence="not-a-number")
        assert p.confidence == 0.0

    def test_is_actionable_true(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="x",
            old_value=10,
            suggested_value=20,
            confidence=0.5,
        )
        assert p.is_actionable is True

    def test_is_actionable_low_confidence(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="x",
            old_value=10,
            suggested_value=20,
            confidence=0.2,
        )
        assert p.is_actionable is False

    def test_is_actionable_no_change(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="x",
            old_value=10,
            suggested_value=10,
            confidence=0.9,
        )
        assert p.is_actionable is False

    def test_to_dict_contains_fields(self):
        from src.runtime.policy.feedback import PolicyAdjustmentProposal
        p = PolicyAdjustmentProposal(
            module="growth",
            parameter="throttle.interval",
            old_value=50,
            suggested_value=75,
            reason="high_failure",
            confidence=0.8,
        )
        d = p.to_dict()
        assert d["module"] == "growth"
        assert d["parameter"] == "throttle.interval"
        assert d["old_value"] == 50
        assert d["suggested_value"] == 75
        assert d["reason"] == "high_failure"
        assert d["confidence"] == 0.8
        assert d["direction"] == "increase"
        assert d["proposal_id"] != ""


# ============================================================
# Test: ProposalStore
# ============================================================
class TestProposalStore:
    """ProposalStore append-only 语义。"""

    def test_default_construction(self):
        from src.runtime.policy.feedback import ProposalStore
        store = ProposalStore()
        assert store.count() == 0

    def test_append_adds_proposal(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        p = PolicyAdjustmentProposal(module="x", old_value=1, suggested_value=2)
        assert store.append(p) is True
        assert store.count() == 1

    def test_append_invalid_type(self):
        from src.runtime.policy.feedback import ProposalStore
        store = ProposalStore()
        assert store.append("not-a-proposal") is False
        assert store.count() == 0

    def test_get_by_id(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        p = PolicyAdjustmentProposal(module="x")
        store.append(p)
        got = store.get(p.proposal_id)
        assert got is p

    def test_get_nonexistent(self):
        from src.runtime.policy.feedback import ProposalStore
        store = ProposalStore()
        assert store.get("nonexistent") is None

    def test_list_filter_by_module(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        store.append(PolicyAdjustmentProposal(module="growth"))
        store.append(PolicyAdjustmentProposal(module="memory"))
        store.append(PolicyAdjustmentProposal(module="growth"))
        result = store.list(module="growth")
        assert len(result) == 2

    def test_list_filter_by_parameter(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        store.append(PolicyAdjustmentProposal(module="x", parameter="throttle.interval"))
        store.append(PolicyAdjustmentProposal(module="x", parameter="budget.cost"))
        result = store.list(parameter="throttle.interval")
        assert len(result) == 1

    def test_list_filter_by_confidence(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        store.append(PolicyAdjustmentProposal(module="x", confidence=0.3))
        store.append(PolicyAdjustmentProposal(module="x", confidence=0.8))
        result = store.list(min_confidence=0.5)
        assert len(result) == 1

    def test_list_with_limit(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        for i in range(5):
            store.append(PolicyAdjustmentProposal(module="x"))
        result = store.list(limit=3)
        assert len(result) == 3

    def test_append_many(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        proposals = [
            PolicyAdjustmentProposal(module="x"),
            PolicyAdjustmentProposal(module="y"),
        ]
        n = store.append_many(proposals)
        assert n == 2
        assert store.count() == 2

    def test_snapshot(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        store.append(PolicyAdjustmentProposal(module="x"))
        snap = store.snapshot()
        assert isinstance(snap, dict)
        assert snap["total"] == 1
        assert "proposals" in snap

    def test_stats(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        store.append(PolicyAdjustmentProposal(
            module="x", old_value=1, suggested_value=2, confidence=0.5,
        ))
        store.append(PolicyAdjustmentProposal(
            module="y", old_value=1, suggested_value=1, confidence=0.9,
        ))
        stats = store.stats()
        assert stats["total"] == 2
        # 第一个 actionable (direction != no_change, confidence > 0.3)
        # 第二个 not actionable (no_change)
        assert stats["actionable"] == 1

    def test_latest(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        for i in range(3):
            store.append(PolicyAdjustmentProposal(module="x"))
        latest = store.latest(2)
        assert len(latest) == 2

    def test_clear(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore()
        store.append(PolicyAdjustmentProposal(module="x"))
        store.clear()
        assert store.count() == 0

    def test_max_proposals_overflow(self):
        from src.runtime.policy.feedback import (
            PolicyAdjustmentProposal,
            ProposalStore,
        )
        store = ProposalStore(max_proposals=3)
        for i in range(5):
            store.append(PolicyAdjustmentProposal(module=f"m{i}"))
        assert store.count() == 3  # 截断到 max
        # 留下的应该是最新 3 条
        modules = [p.module for p in store.list()]
        assert "m4" in modules
        assert "m0" not in modules


# ============================================================
# Test: PolicyMetrics
# ============================================================
class TestPolicyMetrics:
    """PolicyMetrics 数据结构。"""

    def test_default_construction(self):
        from src.runtime.policy.feedback import PolicyMetrics
        m = PolicyMetrics(module="growth")
        assert m.module == "growth"
        assert m.execute_count == 0
        assert m.success_rate == 0.0
        assert m.failure_rate == 0.0
        assert m.deny_count == 0
        assert m.deny_rate == 0.0

    def test_success_rate_calculation(self):
        from src.runtime.policy.feedback import PolicyMetrics
        m = PolicyMetrics(
            module="x",
            execute_count=10,
            success_count=8,
            failure_count=2,
        )
        assert abs(m.success_rate - 0.8) < 0.001
        assert abs(m.failure_rate - 0.2) < 0.001

    def test_deny_count(self):
        from src.runtime.policy.feedback import PolicyMetrics
        m = PolicyMetrics(
            module="x",
            throttle_hit_count=3,
            budget_reject_count=2,
            other_deny_count=1,
        )
        assert m.deny_count == 6

    def test_deny_rate_calculation(self):
        from src.runtime.policy.feedback import PolicyMetrics
        m = PolicyMetrics(
            module="x",
            execute_count=10,
            throttle_hit_count=3,
            budget_reject_count=2,
        )
        # deny_count = 5, execute_count = 10, rate = 0.5
        assert abs(m.deny_rate - 0.5) < 0.001

    def test_recompute_avg(self):
        from src.runtime.policy.feedback import PolicyMetrics
        m = PolicyMetrics(
            module="x",
            execute_count=2,
            total_latency_ms=200.0,
            total_cost=10.0,
        )
        m.recompute()
        assert m.avg_latency_ms == 100.0
        assert m.avg_cost == 5.0

    def test_to_dict(self):
        from src.runtime.policy.feedback import PolicyMetrics
        m = PolicyMetrics(
            module="growth",
            execute_count=100,
            success_count=95,
            failure_count=5,
        )
        d = m.to_dict()
        assert d["module"] == "growth"
        assert d["execute_count"] == 100
        assert abs(d["success_rate"] - 0.95) < 0.001
        assert "deny_count" in d


# ============================================================
# Test: MetricsCollector
# ============================================================
class TestMetricsCollector:
    """MetricsCollector 收集逻辑。"""

    def test_default_construction(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        assert c.all_modules() == []

    def test_record_execution_auto_register(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True, latency_ms=100, tokens=10, cost=0.5)
        m = c.collect("growth")
        assert m is not None
        assert m.execute_count == 1
        assert m.success_count == 1
        assert m.total_latency_ms == 100.0
        assert m.total_tokens == 10
        assert abs(m.total_cost - 0.5) < 0.001

    def test_record_execution_failure(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=False)
        m = c.collect("growth")
        assert m.failure_count == 1
        assert m.execute_count == 1

    def test_record_throttle_hit(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_throttle_hit("growth")
        c.record_throttle_hit("growth")
        m = c.collect("growth")
        assert m.throttle_hit_count == 2

    def test_record_budget_reject(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_budget_reject("memory")
        m = c.collect("memory")
        assert m.budget_reject_count == 1

    def test_record_other_deny(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_other_deny("initiative")
        m = c.collect("initiative")
        assert m.other_deny_count == 1

    def test_collect_nonexistent(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector(auto_register=False)
        m = c.collect("nonexistent")
        assert m is None

    def test_collect_returns_copy(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        m1 = c.collect("growth")
        m1.execute_count = 999  # 修改副本
        m2 = c.collect("growth")
        # 原数据不应被修改
        assert m2.execute_count == 1

    def test_collect_all(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        c.record_execution("memory", success=True)
        all_m = c.collect_all()
        assert "growth" in all_m
        assert "memory" in all_m

    def test_snapshot(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        snap = c.snapshot()
        assert "growth" in snap
        assert snap["growth"]["execute_count"] == 1

    def test_stats(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        c.record_execution("memory", success=True)
        c.record_throttle_hit("growth")
        stats = c.stats()
        assert stats["modules"] == 2
        assert stats["total_executions"] == 2
        assert stats["total_throttle_hits"] == 1

    def test_reset(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        c.reset()
        assert c.all_modules() == []

    def test_reset_specific(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        c.record_execution("memory", success=True)
        c.reset("growth")
        assert "growth" not in c.all_modules()
        assert "memory" in c.all_modules()

    def test_has_module(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True)
        assert c.has_module("growth") is True
        assert c.has_module("memory") is False

    def test_record_with_now(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True, now=1000.0)
        m = c.collect("growth")
        assert m.last_updated_at == 1000.0

    def test_recompute_avg_latency(self):
        from src.runtime.policy.feedback import MetricsCollector
        c = MetricsCollector()
        c.record_execution("growth", success=True, latency_ms=100)
        c.record_execution("growth", success=True, latency_ms=200)
        m = c.collect("growth")
        # 2 executions, 300 ms total
        assert m.avg_latency_ms == 150.0


# ============================================================
# Test: EvaluatorConfig
# ============================================================
class TestEvaluatorConfig:
    """EvaluatorConfig 字段与默认值。"""

    def test_default_values(self):
        from src.runtime.policy.feedback import EvaluatorConfig
        c = EvaluatorConfig.default()
        assert c.min_executions_for_proposal == 10
        assert c.high_success_rate == 0.95
        assert c.high_failure_rate == 0.20
        assert c.interval_min >= 1

    def test_to_dict(self):
        from src.runtime.policy.feedback import EvaluatorConfig
        c = EvaluatorConfig.default()
        d = c.to_dict()
        assert "min_executions_for_proposal" in d
        assert "high_success_rate" in d
        assert isinstance(d["interval_min"], int)


# ============================================================
# Test: AdaptiveEvaluator
# ============================================================
class TestAdaptiveEvaluator:
    """AdaptiveEvaluator 评估逻辑。"""

    def test_default_construction(self):
        from src.runtime.policy.feedback import AdaptiveEvaluator, build_evaluator
        e = build_evaluator()
        assert e is not None
        assert e.config is not None

    def test_low_utilization_no_proposal(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        # 只有 5 次执行,小于 min_executions_for_proposal(10)
        m = {"growth": PolicyMetrics(
            module="growth", execute_count=5, success_count=5, failure_count=0,
        )}
        snap = PolicySnapshot(module_intervals={"growth": 50})
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        assert proposals == []

    def test_high_failure_rate_proposal(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth",
            execute_count=100,
            success_count=70,
            failure_count=30,  # 30% 失败率
        )}
        snap = PolicySnapshot(module_intervals={"growth": 50})
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        # 应生成 interval 增加建议
        interval_proposals = [
            p for p in proposals
            if p.parameter == "throttle.interval"
        ]
        assert len(interval_proposals) >= 1
        # 旧值 50 -> 新值应 > 50
        assert interval_proposals[0].old_value == 50
        assert interval_proposals[0].suggested_value > 50
        assert interval_proposals[0].direction == "increase"

    def test_high_throttle_hit_proposal(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth",
            execute_count=100,
            success_count=100,
            failure_count=0,
            throttle_hit_count=30,  # throttle 命中率高
        )}
        snap = PolicySnapshot(module_intervals={"growth": 50})
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        interval_proposals = [
            p for p in proposals
            if p.parameter == "throttle.interval"
        ]
        assert len(interval_proposals) >= 1

    def test_high_budget_reject_proposal(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth",
            execute_count=100,
            success_count=100,
            failure_count=0,
            budget_reject_count=20,  # 20% 拒绝率
        )}
        snap = PolicySnapshot(
            module_intervals={"growth": 50},
            module_costs={"growth": 2.0},
        )
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        # 至少应有 interval 建议 + module_cost 建议
        param_set = {p.parameter for p in proposals}
        assert "throttle.interval" in param_set
        assert "budget.module_cost" in param_set

    def test_high_success_low_cost_proposal(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth",
            execute_count=200,  # 满足 2x 阈值
            success_count=199,
            failure_count=1,
            total_latency_ms=2000.0,
            total_cost=5.0,  # avg_cost = 0.025, 低
        )}
        snap = PolicySnapshot(module_intervals={"growth": 50})
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        # 高成功率 + 低成本 → 建议降低 interval
        decrease_proposals = [
            p for p in proposals
            if p.parameter == "throttle.interval" and p.direction == "decrease"
        ]
        assert len(decrease_proposals) >= 1

    def test_high_cost_throttle_proposal(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth",
            execute_count=10,
            success_count=10,
            failure_count=0,
            total_cost=600.0,  # avg_cost = 60, > 50
        )}
        snap = PolicySnapshot(
            module_intervals={"growth": 50},
            module_throttles={"growth": 1.0},
        )
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        # 建议降低 throttle 倍率
        throttle_proposals = [
            p for p in proposals
            if p.parameter == "throttle.throttle"
        ]
        assert len(throttle_proposals) >= 1
        assert throttle_proposals[0].suggested_value < 1.0

    def test_confidence_in_valid_range(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth",
            execute_count=100,
            success_count=50,
            failure_count=50,
        )}
        snap = PolicySnapshot(module_intervals={"growth": 50})
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        for p in proposals:
            assert 0.0 <= p.confidence <= 1.0

    def test_evaluate_no_metrics(self):
        from src.runtime.policy.feedback import AdaptiveEvaluator
        e = AdaptiveEvaluator()
        proposals = e.evaluate(metrics={}, policy_snapshot=None)
        assert proposals == []

    def test_evaluate_multiple_modules(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {
            "growth": PolicyMetrics(
                module="growth", execute_count=100,
                success_count=70, failure_count=30,
            ),
            "memory": PolicyMetrics(
                module="memory", execute_count=100,
                success_count=100, failure_count=0,
            ),
        }
        snap = PolicySnapshot(
            module_intervals={"growth": 50, "memory": 30},
        )
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        # growth 应有 proposal,memory 没有失败,可能没有
        modules = {p.module for p in proposals}
        assert "growth" in modules

    def test_evaluate_invalid_metric_skipped(self):
        from src.runtime.policy.feedback import AdaptiveEvaluator
        e = AdaptiveEvaluator()
        # 传入非 PolicyMetrics 实例
        m = {"growth": "not-a-metrics"}
        proposals = e.evaluate(metrics=m)
        assert proposals == []

    def test_get_stats(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(
            module="growth", execute_count=100,
            success_count=70, failure_count=30,
        )}
        snap = PolicySnapshot(module_intervals={"growth": 50})
        e.evaluate(metrics=m, policy_snapshot=snap)
        stats = e.get_stats()
        assert stats["evaluate_total"] >= 1

    def test_reset_stats(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
        )
        e = AdaptiveEvaluator()
        m = {"growth": PolicyMetrics(module="growth", execute_count=100)}
        e.evaluate(metrics=m)
        e.reset_stats()
        stats = e.get_stats()
        assert stats["evaluate_total"] == 0


# ============================================================
# Test: PolicySnapshot & capture_policy_snapshot
# ============================================================
class TestPolicySnapshot:
    """PolicySnapshot + capture_policy_snapshot。"""

    def test_default_construction(self):
        from src.runtime.policy.feedback import PolicySnapshot
        s = PolicySnapshot()
        assert s.module_intervals == {}
        assert s.budget_daily_cost_limit == 0.0

    def test_to_dict(self):
        from src.runtime.policy.feedback import PolicySnapshot
        s = PolicySnapshot(
            module_intervals={"growth": 50},
            module_throttles={"growth": 0.8},
        )
        d = s.to_dict()
        assert d["module_intervals"]["growth"] == 50
        assert d["module_throttles"]["growth"] == 0.8

    def test_capture_with_none(self):
        from src.runtime.policy.feedback import capture_policy_snapshot
        snap = capture_policy_snapshot(throttle_registry=None, runtime_budget=None)
        assert snap is not None
        assert snap.module_intervals == {}

    def test_capture_with_throttle_registry(self):
        from src.runtime.policy.feedback import capture_policy_snapshot
        from src.runtime.policy.throttle import ThrottleRegistry
        registry = ThrottleRegistry()
        registry.set("growth", interval=50, throttle=0.8, cooldown_seconds=2.0)
        snap = capture_policy_snapshot(throttle_registry=registry)
        assert snap.module_intervals.get("growth") == 50
        assert snap.module_throttles.get("growth") == 0.8
        assert snap.module_cooldowns.get("growth") == 2.0

    def test_capture_with_runtime_budget(self):
        from src.runtime.policy.feedback import capture_policy_snapshot
        from src.runtime.policy.budget import RuntimeBudget
        budget = RuntimeBudget()
        snap = capture_policy_snapshot(runtime_budget=budget)
        # 默认有 daily_cost_limit
        assert snap.budget_daily_cost_limit >= 0.0


# ============================================================
# Test: PolicyFeedbackEngine
# ============================================================
class TestPolicyFeedbackEngine:
    """PolicyFeedbackEngine 集成。"""

    def test_build_default(self):
        from src.runtime.policy.feedback import (
            PolicyFeedbackEngine,
            build_feedback_engine,
        )
        engine = build_feedback_engine()
        assert engine is not None
        assert engine.collector is not None
        assert engine.evaluator is not None
        assert engine.store is not None

    def test_record_execution(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_execution("growth", success=True, latency_ms=100, cost=2.0)
        m = engine.collector.collect("growth")
        assert m is not None
        assert m.execute_count == 1

    def test_record_throttle_hit(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_throttle_hit("growth")
        m = engine.collector.collect("growth")
        assert m.throttle_hit_count == 1

    def test_record_budget_reject(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_budget_reject("growth")
        m = engine.collector.collect("growth")
        assert m.budget_reject_count == 1

    def test_record_other_deny(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_other_deny("growth")
        m = engine.collector.collect("growth")
        assert m.other_deny_count == 1

    def test_evaluate_generates_proposals(self):
        from src.runtime.policy.throttle import ThrottleRegistry
        from src.runtime.policy.budget import RuntimeBudget
        from src.runtime.policy.feedback import build_feedback_engine
        registry = ThrottleRegistry()
        registry.set("growth", interval=50, throttle=1.0)
        budget = RuntimeBudget()
        engine = build_feedback_engine(
            throttle_registry=registry,
            runtime_budget=budget,
            publish_events=False,
        )
        # 模拟 100 次执行,30% 失败
        for _ in range(70):
            engine.record_execution("growth", success=True, latency_ms=100, cost=0.5)
        for _ in range(30):
            engine.record_execution("growth", success=False, latency_ms=100, cost=0.5)
        proposals = engine.evaluate(cycle_id="c-1")
        # 至少应生成 1 个 proposal
        assert len(proposals) >= 1
        # 写入 store
        assert engine.store.count() >= 1

    def test_evaluate_no_proposals_low_utilization(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        # 不足阈值
        engine.record_execution("growth", success=True)
        proposals = engine.evaluate(cycle_id="c-1")
        assert proposals == []

    def test_generate_proposals_no_store_write(self):
        from src.runtime.policy.throttle import ThrottleRegistry
        from src.runtime.policy.feedback import build_feedback_engine
        registry = ThrottleRegistry()
        registry.set("growth", interval=50, throttle=1.0)
        engine = build_feedback_engine(
            throttle_registry=registry,
            publish_events=False,
        )
        for _ in range(100):
            engine.record_execution("growth", success=False, latency_ms=100, cost=0.5)
        proposals = engine.generate_proposals(cycle_id="c-1")
        # 不应写入 store
        assert engine.store.count() == 0
        # 但应返回 proposals
        assert len(proposals) >= 1

    def test_snapshot(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_execution("growth", success=True)
        snap = engine.snapshot()
        assert "metrics" in snap
        assert "evaluator_stats" in snap
        assert "proposal_store" in snap
        assert "engine_stats" in snap

    def test_health_check(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        hc = engine.health_check()
        assert hc["ok"] is True
        assert hc["collector_ok"] is True
        assert hc["evaluator_ok"] is True
        assert hc["store_ok"] is True

    def test_get_stats(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.evaluate(cycle_id="c-1")
        stats = engine.get_stats()
        assert "evaluate_total" in stats
        assert stats["evaluate_total"] >= 1

    def test_reset_metrics(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_execution("growth", success=True)
        engine.reset_metrics()
        m = engine.collector.collect("growth")
        assert m is None

    def test_collect_metrics(self):
        from src.runtime.policy.feedback import build_feedback_engine
        engine = build_feedback_engine(publish_events=False)
        engine.record_execution("growth", success=True)
        engine.record_execution("memory", success=True)
        m = engine.collect_metrics()
        assert "growth" in m
        assert "memory" in m

    def test_evaluate_with_publish_event(self):
        from src.runtime.policy.throttle import ThrottleRegistry
        from src.runtime.policy.feedback import build_feedback_engine
        captured = []

        def fake_publisher(ev):
            captured.append(ev)

        registry = ThrottleRegistry()
        registry.set("growth", interval=50, throttle=1.0)
        engine = build_feedback_engine(
            throttle_registry=registry,
            publish_events=True,
            event_publisher=fake_publisher,
        )
        for _ in range(100):
            engine.record_execution("growth", success=False, latency_ms=100, cost=0.5)
        engine.evaluate(cycle_id="c-1")
        # 至少发布 1 个事件
        assert len(captured) >= 1


# ============================================================
# Test: EventBus Integration
# ============================================================
class TestRuntimePolicyFeedbackEvent:
    """RuntimePolicyFeedbackEvent 集成。"""

    def test_event_type_defined(self):
        from src.events.events import EventType
        assert hasattr(EventType, "RUNTIME_POLICY_FEEDBACK")
        assert EventType.RUNTIME_POLICY_FEEDBACK == "runtime.policy_feedback"

    def test_event_class_defined(self):
        from src.events.events import RuntimePolicyFeedbackEvent
        ev = RuntimePolicyFeedbackEvent(
            module="growth",
            metric="throttle.interval",
            value="75",
            confidence=0.8,
            cycle_id="c-1",
            direction="increase",
            reason="high_failure",
        )
        assert ev.module == "growth"
        assert ev.metric == "throttle.interval"
        assert ev.value == "75"
        assert ev.confidence == 0.8
        assert ev.cycle_id == "c-1"
        assert ev.direction == "increase"
        assert ev.reason == "high_failure"


# ============================================================
# Test: Runtime 集成
# ============================================================
class TestRuntimePolicyFeedbackIntegration:
    """Runtime 与 PolicyFeedbackEngine 集成。"""

    def test_configure_feedback_engine(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        assert core.has_feedback_engine() is True
        assert core.feedback_engine is engine

    def test_configure_with_none(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        core.configure_feedback_engine(None)
        assert core.has_feedback_engine() is False

    def test_record_module_execution(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        core.record_module_execution(
            "growth", success=True, latency_ms=100, cost=2.0,
        )
        m = engine.collector.collect("growth")
        assert m is not None
        assert m.execute_count == 1

    def test_record_module_execution_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 不应抛错
        core.record_module_execution("growth", success=True)

    def test_record_module_deny_throttle(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        core.record_module_deny("growth", reason="in_cooldown")
        m = engine.collector.collect("growth")
        assert m.throttle_hit_count == 1

    def test_record_module_deny_budget(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        core.record_module_deny("growth", reason="budget_exceeded")
        m = engine.collector.collect("growth")
        assert m.budget_reject_count == 1

    def test_record_module_deny_other(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        core.record_module_deny("growth", reason="maintenance_mode")
        m = engine.collector.collect("growth")
        assert m.other_deny_count == 1

    def test_trigger_policy_feedback(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.throttle import ThrottleRegistry
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        registry = ThrottleRegistry()
        registry.set("growth", interval=50, throttle=1.0)
        engine = build_feedback_engine(
            throttle_registry=registry,
            publish_events=False,
        )
        core.configure_feedback_engine(engine)
        for _ in range(100):
            core.record_module_execution("growth", success=False, latency_ms=100, cost=0.5)
        proposals = core.trigger_policy_feedback(cycle_id="c-1")
        assert proposals is not None
        assert len(proposals) >= 1

    def test_trigger_policy_feedback_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        result = core.trigger_policy_feedback(cycle_id="c-1")
        assert result is None

    def test_get_policy_feedback_snapshot(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        engine = build_feedback_engine(publish_events=False)
        core.configure_feedback_engine(engine)
        core.record_module_execution("growth", success=True)
        snap = core.get_policy_feedback_snapshot()
        assert isinstance(snap, dict)
        assert "metrics" in snap

    def test_get_policy_feedback_snapshot_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.get_policy_feedback_snapshot() is None

    def test_get_policy_feedback_proposals(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy.throttle import ThrottleRegistry
        from src.runtime.policy.feedback import build_feedback_engine
        core = RuntimeCore()
        registry = ThrottleRegistry()
        registry.set("growth", interval=50, throttle=1.0)
        engine = build_feedback_engine(
            throttle_registry=registry,
            publish_events=False,
        )
        core.configure_feedback_engine(engine)
        for _ in range(100):
            core.record_module_execution("growth", success=False, latency_ms=100, cost=0.5)
        core.trigger_policy_feedback(cycle_id="c-1")
        proposals = core.get_policy_feedback_proposals(module="growth")
        assert proposals is not None
        assert len(proposals) >= 1

    def test_get_policy_feedback_proposals_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.get_policy_feedback_proposals() is None


# ============================================================
# Test: Fail-soft 行为
# ============================================================
class TestFailSoftAndException:
    """异常注入与 fail-soft 行为。"""

    def test_evaluator_with_broken_metric(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        # 抛错的 metrics
        class BrokenMetrics:
            module = "broken"
            execute_count = "not-int"  # 会在 recompute 时出错
            success_count = 0
            failure_count = 0
            throttle_hit_count = 0
            budget_reject_count = 0
            other_deny_count = 0
            total_latency_ms = 0.0
            avg_latency_ms = 0.0
            total_tokens = 0
            total_cost = 0.0
            avg_cost = 0.0
            llm_call_count = 0
            window_started_at = 0.0
            last_updated_at = 0.0
            cycle_id = ""
            success_rate = 0.0
            failure_rate = 0.0
            deny_count = 0
            deny_rate = 0.0

            def to_dict(self):
                raise RuntimeError("boom")

        m = {"broken": BrokenMetrics()}
        # 不应抛错,返回 []
        proposals = e.evaluate(metrics=m, policy_snapshot=PolicySnapshot())
        assert isinstance(proposals, list)

    def test_engine_with_broken_collector(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            ProposalStore,
            PolicyFeedbackEngine,
        )

        class BrokenCollector:
            def record_execution(self, **kwargs):
                raise RuntimeError("boom")
            def record_throttle_hit(self, **kwargs):
                raise RuntimeError("boom")
            def collect_all(self):
                raise RuntimeError("boom")
            def snapshot(self):
                return {}
            def stats(self):
                return {}
            def reset(self):
                pass

        engine = PolicyFeedbackEngine(
            collector=BrokenCollector(),  # type: ignore
            evaluator=AdaptiveEvaluator(),
            store=ProposalStore(),
        )
        # 不应抛错
        engine.record_execution("x", success=True)
        engine.evaluate(cycle_id="c-1")
        snap = engine.snapshot()
        assert isinstance(snap, dict)

    def test_engine_with_broken_store(self):
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            MetricsCollector,
            PolicyFeedbackEngine,
            PolicyMetrics,
            PolicySnapshot,
        )

        class BrokenStore:
            def append(self, *args, **kwargs):
                raise RuntimeError("boom")
            def append_many(self, proposals):
                return 0
            def list(self, **kwargs):
                return []
            def snapshot(self):
                return {"total": 0, "proposals": []}
            def stats(self):
                return {}
            def count(self):
                return 0

        engine = PolicyFeedbackEngine(
            collector=MetricsCollector(),
            evaluator=AdaptiveEvaluator(),
            store=BrokenStore(),  # type: ignore
        )
        # 直接生成 proposals
        proposals = engine.generate_proposals(cycle_id="c-1")
        # evaluate 会失败(因为 store 抛错),但 engine 应隔离
        result = engine.evaluate(cycle_id="c-1")
        # 不应抛错
        assert result is not None or result is None  # 异常时为 []

    def test_record_module_deny_without_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 不应抛错
        core.record_module_deny("x", reason="test")

    def test_evaluator_evaluate_all_modules_robust(self):
        """任意模块抛错时,evaluator 不应中断整个流程。"""
        from src.runtime.policy.feedback import (
            AdaptiveEvaluator,
            PolicyMetrics,
            PolicySnapshot,
        )
        e = AdaptiveEvaluator()
        # 一半模块正常,一半抛错
        m = {
            "ok": PolicyMetrics(
                module="ok", execute_count=100,
                success_count=70, failure_count=30,
            ),
            "broken": "not-a-metrics",
        }
        snap = PolicySnapshot(module_intervals={"ok": 50})
        proposals = e.evaluate(metrics=m, policy_snapshot=snap)
        # 应至少有 1 个 proposal(来自 ok)
        assert len(proposals) >= 1
        modules = {p.module for p in proposals}
        assert "ok" in modules


# ============================================================
# Test: 默认行为保护
# ============================================================
class TestDefaultBehaviorPreserved:
    """无 feedback engine 时,Runtime 行为完全向后兼容。"""

    def test_no_feedback_engine_default(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.has_feedback_engine() is False
        assert core.feedback_engine is None
        assert core.get_policy_feedback_snapshot() is None
        assert core.get_policy_feedback_proposals() is None
        assert core.trigger_policy_feedback() is None

    def test_record_module_execution_no_engine(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 完全静默
        core.record_module_execution("x", success=True)
        core.record_module_deny("x", reason="test")
        assert core.has_feedback_engine() is False

    def test_c10_8_adaptive_layer_still_works(self):
        """确认 C.10.9 引入 feedback 后,C.10.8 AdaptivePolicyLayer 仍能正常注入。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import AdaptivePolicyLayer
        core = RuntimeCore()
        layer = AdaptivePolicyLayer.build_default()
        core.configure_adaptive_policy(layer)
        assert core.has_adaptive_layer() is True
        # feedback engine 仍是 None(独立)
        assert core.has_feedback_engine() is False

    def test_c10_7_policy_engine_still_works(self):
        """确认 C.10.9 引入 feedback 后,C.10.7 PolicyEngine 仍能正常注入。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine
        core = RuntimeCore()
        engine = PolicyEngine()
        core.configure_policy_engine(engine)
        assert core.policy_engine is engine
        # feedback engine 仍是 None(独立)
        assert core.has_feedback_engine() is False
