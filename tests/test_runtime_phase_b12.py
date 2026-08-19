# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b12.py

Phase B.12 Runtime Integration —— Decision Evolution Loop 测试

覆盖:
  1. Proposal schema (DecisionProposal 数据类)
  2. Generator 基础能力(空 report / 普通 report)
  3. boost proposal 生成
  4. suppress proposal 生成
  5. monitor proposal 生成
  6. Approval lifecycle(approve/reject/expire/mark_applied)
  7. Persistence(各 stage 写盘 + 失败降级)
  8. Recovery(load_state 恢复)
  9. Bridge integration(RuntimeB4Bridge 集成 + 5 个新接口)
 10. B.4-B.11 regression(summarize_b4 包含 decision_evolution)

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

from src.runtime.decision_proposal import (
    PHASE_B12_DEFAULT_CONFIG,
    PHASE_B12_NAME,
    PHASE_B12_VERSION,
    SCHEMA_VERSION,
    PROPOSAL_TYPE_BOOST,
    PROPOSAL_TYPE_SUPPRESS,
    PROPOSAL_TYPE_MONITOR,
    ALL_PROPOSAL_TYPES,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_APPLIED,
    STATUS_EXPIRED,
    ALL_STATUSES,
    TERMINAL_STATUSES,
    STAGE_PROPOSAL_CREATED,
    STAGE_PROPOSAL_APPROVED,
    STAGE_PROPOSAL_REJECTED,
    STAGE_PROPOSAL_APPLIED,
    STAGE_PROPOSAL_EXPIRED,
    apply_phase_b12_config,
    is_phase_b12_enabled,
    DecisionProposal,
    DecisionProposalGenerator,
    DecisionApprovalManager,
    DecisionEvolutionRuntime,
    create_decision_evolution_runtime,
    safe_get_evolution_summary,
)

from src.runtime.action_persistence import (
    ActionPersistenceManager,
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

class FakePersistence:
    """模拟 ActionPersistenceManager,记录所有 persist_event 调用"""

    def __init__(self, fail: bool = False) -> None:
        self.events: List[Dict[str, Any]] = []
        self._fail = fail
        self._write_count = 0
        self._write_error_count = 0
        self._is_degraded = False
        self._lock = type("L", (), {"__enter__": lambda s: s, "__exit__": lambda s, a, b, c: None})()

    def persist_event(
        self,
        action_id: str,
        lifecycle_id: str,
        stage: str,
        decision: str = "",
        ts: Optional[float] = None,
        result: Any = None,
        error: Optional[str] = None,
        source: str = "unknown",
    ) -> bool:
        if self._fail:
            self._write_error_count += 1
            return False
        self.events.append({
            "action_id": action_id,
            "lifecycle_id": lifecycle_id,
            "stage": stage,
            "decision": decision,
            "ts": ts or time.time(),
            "result": result,
            "error": error,
            "source": source,
        })
        self._write_count += 1
        return True

    def get_recent_actions(self, limit: int = 100) -> List[Dict[str, Any]]:
        return list(self.events[-limit:])

    def get_terminal_actions(self) -> Dict[str, Any]:
        return {}

    def health_check(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "degraded": self._is_degraded,
            "write_count": self._write_count,
            "write_error_count": self._write_error_count,
        }

    @property
    def write_count(self) -> int:
        return self._write_count

    @property
    def write_error_count(self) -> int:
        return self._write_error_count

    @property
    def is_degraded(self) -> bool:
        return self._is_degraded

    @property
    def enabled(self) -> bool:
        return True

    @property
    def recover_count(self) -> int:
        return 0


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


def make_intelligence_report(
    recommendations: Optional[List[Dict[str, Any]]] = None,
    drifts: Optional[List[Dict[str, Any]]] = None,
    patterns: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """构造 B.11 intelligence report"""
    return {
        "report_version": "1.0.0",
        "generated_at": "2026-08-03T00:00:00Z",
        "health_score": {
            "overall_score": 0.7,
            "risk_level": "low",
        },
        "trends": [],
        "feedback_drifts": drifts or [],
        "failure_patterns": patterns or [],
        "strategy_recommendations": recommendations or [],
        "summary": {},
    }


# ============================================================
# 3. TestPhaseB12Config —— 配置测试
# ============================================================

class TestPhaseB12Config:
    """配置测试"""

    def test_default_config_present(self):
        cfg = PHASE_B12_DEFAULT_CONFIG
        assert "decision_evolution" in cfg
        evo = cfg["decision_evolution"]
        assert evo["enabled"] is True
        assert evo["auto_apply"] is False
        assert evo["min_confidence"] == 0.3
        assert evo["boost_confidence_threshold"] == 0.7
        assert evo["suppress_confidence_threshold"] == 0.6
        assert evo["monitor_drift_threshold"] == 0.15

    def test_apply_phase_b12_config_does_not_modify_input(self):
        user_cfg: Dict[str, Any] = {
            "decision_evolution": {
                "enabled": False,
                "min_confidence": 0.5,
            }
        }
        original = json.dumps(user_cfg, sort_keys=True)
        merged = apply_phase_b12_config(user_cfg)
        # 不修改原 dict
        assert json.dumps(user_cfg, sort_keys=True) == original
        # user 字段优先级最高
        assert merged["decision_evolution"]["enabled"] is False
        assert merged["decision_evolution"]["min_confidence"] == 0.5
        # 缺失字段用 default
        assert merged["decision_evolution"]["max_proposals"] == 200

    def test_apply_phase_b12_config_none_input(self):
        merged = apply_phase_b12_config(None)
        assert "decision_evolution" in merged
        assert merged["decision_evolution"]["enabled"] is True

    def test_is_phase_b12_enabled(self):
        assert is_phase_b12_enabled({"decision_evolution": {"enabled": True}}) is True
        assert is_phase_b12_enabled({"decision_evolution": {"enabled": False}}) is False
        assert is_phase_b12_enabled({}) is True  # default
        assert is_phase_b12_enabled(None) is False


# ============================================================
# 4. TestDecisionProposalSchema —— Proposal schema 测试
# ============================================================

class TestDecisionProposalSchema:
    """DecisionProposal 数据类测试"""

    def test_default_construction(self):
        p = DecisionProposal()
        assert p.proposal_id.startswith("dprop_")
        assert p.proposal_type == PROPOSAL_TYPE_MONITOR
        assert p.target_action_type == ""
        assert p.confidence == 0.5
        assert p.status == STATUS_PENDING
        assert p.created_at != ""
        assert p.evidence == []
        assert p.metadata == {}

    def test_to_dict_and_from_dict_roundtrip(self):
        p = DecisionProposal(
            proposal_id="dprop_test123",
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            current_state={"k": "v"},
            suggested_change="increase confidence",
            reason="success rate high",
            evidence=[{"type": "test", "score": 0.9}],
            confidence=0.85,
            status=STATUS_PENDING,
            source_report_hash="abc123",
        )
        d = p.to_dict()
        assert d["proposal_id"] == "dprop_test123"
        assert d["proposal_type"] == PROPOSAL_TYPE_BOOST
        assert d["target_action_type"] == "greeting"
        assert d["confidence"] == 0.85
        assert d["status"] == STATUS_PENDING
        assert d["source_report_hash"] == "abc123"
        assert d["schema_version"] == SCHEMA_VERSION

        # roundtrip
        p2 = DecisionProposal.from_dict(d)
        assert p2.proposal_id == p.proposal_id
        assert p2.proposal_type == p.proposal_type
        assert p2.target_action_type == p.target_action_type
        assert p2.confidence == p.confidence
        assert p2.status == p.status
        assert p2.evidence == p.evidence

    def test_is_terminal(self):
        p_pending = DecisionProposal(status=STATUS_PENDING)
        assert p_pending.is_terminal() is False
        p_approved = DecisionProposal(status=STATUS_APPROVED)
        assert p_approved.is_terminal() is True
        p_rejected = DecisionProposal(status=STATUS_REJECTED)
        assert p_rejected.is_terminal() is True
        p_applied = DecisionProposal(status=STATUS_APPLIED)
        assert p_applied.is_terminal() is True
        p_expired = DecisionProposal(status=STATUS_EXPIRED)
        assert p_expired.is_terminal() is True

    def test_is_expired(self):
        # 无 expires_at → False
        p = DecisionProposal()
        assert p.is_expired() is False
        # 过去的时间 → True
        p2 = DecisionProposal(expires_at="2020-01-01T00:00:00Z")
        assert p2.is_expired() is True
        # 未来的时间 → False
        p3 = DecisionProposal(expires_at="2099-01-01T00:00:00Z")
        assert p3.is_expired() is False
        # 非法格式 → False
        p4 = DecisionProposal(expires_at="not-a-date")
        assert p4.is_expired() is False

    def test_confidence_clipped_in_dict(self):
        p = DecisionProposal(confidence=1.5)
        d = p.to_dict()
        assert d["confidence"] == 1.0
        p2 = DecisionProposal(confidence=-0.5)
        d2 = p2.to_dict()
        assert d2["confidence"] == 0.0


# ============================================================
# 5. TestDecisionProposalGenerator —— Generator 测试
# ============================================================

class TestDecisionProposalGenerator:
    """DecisionProposalGenerator 测试"""

    def test_empty_report(self):
        gen = DecisionProposalGenerator()
        proposals = gen.generate(None)
        assert proposals == []
        proposals2 = gen.generate({})
        assert proposals2 == []

    def test_generate_boost_proposal(self):
        gen = DecisionProposalGenerator()
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "greeting",
                    "action": "boost",
                    "reason": "high success rate",
                    "confidence": 0.85,
                    "supporting_metrics": {"success_rate": 0.92},
                }
            ]
        )
        proposals = gen.generate(report)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.proposal_type == PROPOSAL_TYPE_BOOST
        assert p.target_action_type == "greeting"
        assert p.confidence == 0.85
        assert p.status == STATUS_PENDING
        assert p.suggested_change == "increase confidence / frequency"
        assert len(p.evidence) >= 1

    def test_generate_suppress_proposal(self):
        gen = DecisionProposalGenerator()
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "reminder",
                    "action": "suppress",
                    "reason": "low success rate",
                    "confidence": 0.78,
                    "supporting_metrics": {"success_rate": 0.2},
                }
            ]
        )
        proposals = gen.generate(report)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.proposal_type == PROPOSAL_TYPE_SUPPRESS
        assert p.target_action_type == "reminder"
        assert p.suggested_change == "reduce frequency / suppress"

    def test_generate_monitor_proposal_from_recommendation(self):
        gen = DecisionProposalGenerator()
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "tip",
                    "action": "monitor",
                    "reason": "insufficient data",
                    "confidence": 0.5,
                }
            ]
        )
        proposals = gen.generate(report)
        assert len(proposals) == 1
        p = proposals[0]
        assert p.proposal_type == PROPOSAL_TYPE_MONITOR
        assert p.target_action_type == "tip"

    def test_generate_monitor_proposal_from_drift(self):
        gen = DecisionProposalGenerator(monitor_drift_threshold=0.1)
        report = make_intelligence_report(
            drifts=[
                {
                    "action_type": "question",
                    "current_weight": 0.7,
                    "historical_avg_weight": 0.4,
                    "drift_magnitude": 0.3,
                    "direction": "boosting",
                    "is_anomalous": True,
                }
            ]
        )
        proposals = gen.generate(report)
        # 应该有 1 个 monitor proposal(从 drift 来)
        monitor_proposals = [p for p in proposals if p.proposal_type == PROPOSAL_TYPE_MONITOR]
        assert len(monitor_proposals) == 1
        assert monitor_proposals[0].target_action_type == "question"

    def test_low_confidence_filtered(self):
        gen = DecisionProposalGenerator(min_confidence=0.5)
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "greeting",
                    "action": "boost",
                    "reason": "test",
                    "confidence": 0.3,  # 低于 min_confidence
                }
            ]
        )
        proposals = gen.generate(report)
        assert proposals == []

    def test_boost_below_threshold_degrades_to_monitor(self):
        gen = DecisionProposalGenerator(boost_confidence_threshold=0.8)
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "greeting",
                    "action": "boost",
                    "reason": "test",
                    "confidence": 0.6,  # 低于 boost threshold
                }
            ]
        )
        proposals = gen.generate(report)
        # 因为低于 boost threshold,降级为 monitor
        assert len(proposals) == 1
        assert proposals[0].proposal_type == PROPOSAL_TYPE_MONITOR


# ============================================================
# 6. TestDecisionEvolutionRuntime —— Runtime 测试
# ============================================================

class TestDecisionEvolutionRuntime:
    """DecisionEvolutionRuntime 测试"""

    def test_create_proposal_boost(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            suggested_change="increase frequency",
            reason="success rate 0.92",
            confidence=0.87,
        )
        assert p is not None
        assert p.proposal_type == PROPOSAL_TYPE_BOOST
        assert p.target_action_type == "greeting"
        assert p.confidence == 0.87
        assert p.status == STATUS_PENDING

    def test_create_proposal_suppress(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="reminder",
            suggested_change="reduce frequency",
            reason="failure burst detected",
            confidence=0.8,
        )
        assert p is not None
        assert p.proposal_type == PROPOSAL_TYPE_SUPPRESS

    def test_create_proposal_monitor(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_MONITOR,
            target_action_type="tip",
            suggested_change="observe",
            reason="feedback drift detected",
            confidence=0.65,
        )
        assert p is not None
        assert p.proposal_type == PROPOSAL_TYPE_MONITOR

    def test_create_proposal_low_confidence_rejected(self):
        evo = DecisionEvolutionRuntime(enabled=True, min_confidence=0.5)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.2,
        )
        assert p is None

    def test_create_proposal_disabled(self):
        evo = DecisionEvolutionRuntime(enabled=False)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.8,
        )
        assert p is None

    def test_approve_proposal_lifecycle(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            suggested_change="increase",
            confidence=0.85,
        )
        assert p is not None
        assert evo.approve_proposal(p.proposal_id, reviewer_id="admin", comment="ok") is True
        # 再次 approve 应该失败(已 terminal)
        assert evo.approve_proposal(p.proposal_id, reviewer_id="admin") is False
        # 状态已变更
        p2 = evo.get_proposal(p.proposal_id)
        assert p2.status == STATUS_APPROVED
        assert p2.reviewer_id == "admin"
        assert p2.review_comment == "ok"

    def test_reject_proposal_lifecycle(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="reminder",
            suggested_change="reduce",
            confidence=0.75,
        )
        assert p is not None
        assert evo.reject_proposal(p.proposal_id, reviewer_id="admin", comment="no") is True
        p2 = evo.get_proposal(p.proposal_id)
        assert p2.status == STATUS_REJECTED

    def test_mark_applied_only_for_approved(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.85,
        )
        # 未 approved 时 mark_applied 应失败
        assert evo.mark_proposal_applied(p.proposal_id) is False
        # 批准后再 mark_applied
        evo.approve_proposal(p.proposal_id, reviewer_id="admin")
        assert evo.mark_proposal_applied(p.proposal_id, applied_by="B13") is True
        p2 = evo.get_proposal(p.proposal_id)
        assert p2.status == STATUS_APPLIED
        assert p2.applied_by == "B13"

    def test_auto_apply_always_false(self):
        # 即使传 auto_apply=True,内部也强制为 False
        evo = DecisionEvolutionRuntime(enabled=True, auto_apply=True)
        assert evo.auto_apply is False
        evo2 = DecisionEvolutionRuntime(enabled=True, auto_apply=False)
        assert evo2.auto_apply is False

    def test_get_pending_proposals(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="a",
            suggested_change="x",
            confidence=0.85,
        )
        evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="b",
            suggested_change="y",
            confidence=0.7,
        )
        pendings = evo.get_pending_proposals(limit=10)
        assert len(pendings) == 2

    def test_get_evolution_summary(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="a",
            suggested_change="x",
            confidence=0.85,
        )
        s = evo.get_evolution_summary()
        assert s["name"] == PHASE_B12_NAME
        assert s["enabled"] is True
        assert s["auto_apply"] is False
        assert s["pending_count"] == 1
        assert s["total_count"] == 1
        assert s["approved_count"] == 0

    def test_generate_from_intelligence(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "greeting",
                    "action": "boost",
                    "reason": "high success",
                    "confidence": 0.85,
                }
            ]
        )
        proposals = evo.generate_from_intelligence(report)
        assert len(proposals) == 1
        assert proposals[0].proposal_type == PROPOSAL_TYPE_BOOST
        # 已写入 store
        assert evo.get_evolution_summary()["pending_count"] == 1
        assert evo.get_evolution_summary()["total_count"] == 1

    def test_generate_from_empty_report(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        proposals = evo.generate_from_intelligence(None)
        assert proposals == []
        proposals2 = evo.generate_from_intelligence({})
        assert proposals2 == []

    def test_get_proposals_by_type(self):
        evo = DecisionEvolutionRuntime(enabled=True)
        evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="a",
            suggested_change="x",
            confidence=0.85,
        )
        evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="b",
            suggested_change="y",
            confidence=0.75,
        )
        boosts = evo.get_proposals_by_type(PROPOSAL_TYPE_BOOST, limit=10)
        assert len(boosts) == 1
        assert boosts[0].target_action_type == "a"


# ============================================================
# 7. TestPersistence —— 持久化测试
# ============================================================

class TestPersistence:
    """持久化测试"""

    def test_persistence_stages(self):
        pers = FakePersistence()
        evo = DecisionEvolutionRuntime(enabled=True, persistence=pers)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.85,
        )
        assert p is not None
        # 应该有 STAGE_PROPOSAL_CREATED 事件
        created_events = [e for e in pers.events if e["stage"] == STAGE_PROPOSAL_CREATED]
        assert len(created_events) == 1

        # 批准
        evo.approve_proposal(p.proposal_id, reviewer_id="admin")
        approved_events = [e for e in pers.events if e["stage"] == STAGE_PROPOSAL_APPROVED]
        assert len(approved_events) == 1

        # 标记应用
        evo.mark_proposal_applied(p.proposal_id, applied_by="B13")
        applied_events = [e for e in pers.events if e["stage"] == STAGE_PROPOSAL_APPLIED]
        assert len(applied_events) == 1

    def test_persistence_reject(self):
        pers = FakePersistence()
        evo = DecisionEvolutionRuntime(enabled=True, persistence=pers)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="x",
            suggested_change="y",
            confidence=0.8,
        )
        evo.reject_proposal(p.proposal_id, reviewer_id="admin")
        rejected_events = [e for e in pers.events if e["stage"] == STAGE_PROPOSAL_REJECTED]
        assert len(rejected_events) == 1

    def test_persistence_fail_soft(self):
        # 写盘失败时,Runtime 不抛异常
        pers = FakePersistence(fail=True)
        evo = DecisionEvolutionRuntime(enabled=True, persistence=pers)
        # 不应抛异常
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.85,
        )
        # proposal 仍能创建(in-memory)
        assert p is not None
        # 也能审批
        assert evo.approve_proposal(p.proposal_id, reviewer_id="admin") is True

    def test_no_persistence_works(self):
        # 完全无 persistence,Runtime 仍可用
        evo = DecisionEvolutionRuntime(enabled=True, persistence=None)
        p = evo.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.85,
        )
        assert p is not None
        assert evo.approve_proposal(p.proposal_id, reviewer_id="admin") is True

    def test_recovery_from_persistence(self):
        pers = FakePersistence()
        evo1 = DecisionEvolutionRuntime(enabled=True, persistence=pers)
        p1 = evo1.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.85,
        )
        assert p1 is not None
        evo1.approve_proposal(p1.proposal_id, reviewer_id="admin")

        # 模拟重启:新 Runtime,从同一 persistence 恢复
        evo2 = DecisionEvolutionRuntime(enabled=True, persistence=pers)
        # 恢复后应能找到 approved proposal
        p2 = evo2.get_proposal(p1.proposal_id)
        assert p2 is not None
        # 状态应是 approved(latest-wins)
        assert p2.status == STATUS_APPROVED


# ============================================================
# 8. TestBridgeIntegration —— RuntimeB4Bridge 集成测试
# ============================================================

class TestBridgeIntegration:
    """RuntimeB4Bridge 集成测试"""

    def test_bridge_has_decision_evolution_runtime(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        # 应有 decision_evolution_runtime 属性
        assert hasattr(bridge, "decision_evolution_runtime")
        evo = bridge.decision_evolution_runtime
        assert evo is not None

    def test_bridge_create_proposal(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        d = bridge.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            suggested_change="increase frequency",
            reason="success rate high",
            confidence=0.85,
        )
        assert d is not None
        assert d["proposal_type"] == PROPOSAL_TYPE_BOOST
        assert d["target_action_type"] == "greeting"
        assert d["status"] == STATUS_PENDING

    def test_bridge_approve_proposal(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        d = bridge.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            suggested_change="x",
            confidence=0.85,
        )
        assert d is not None
        assert bridge.approve_proposal(d["proposal_id"], reviewer_id="admin") is True
        pendings = bridge.get_pending_proposals(limit=10)
        assert all(p["proposal_id"] != d["proposal_id"] for p in pendings)

    def test_bridge_reject_proposal(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        d = bridge.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="reminder",
            suggested_change="x",
            confidence=0.75,
        )
        assert d is not None
        assert bridge.reject_proposal(d["proposal_id"], reviewer_id="admin", comment="no") is True

    def test_bridge_get_pending_proposals(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        for i in range(3):
            bridge.create_proposal(
                proposal_type=PROPOSAL_TYPE_BOOST,
                target_action_type=f"action_{i}",
                suggested_change="x",
                confidence=0.85,
            )
        pendings = bridge.get_pending_proposals(limit=10)
        assert len(pendings) == 3

    def test_bridge_get_evolution_summary(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.get_evolution_summary()
        assert s["enabled"] is True
        assert s["auto_apply"] is False
        assert s["pending_count"] == 0
        assert "create_count" in s
        assert "generate_count" in s

    def test_bridge_generate_proposals_from_intelligence(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        report = make_intelligence_report(
            recommendations=[
                {
                    "target_action_type": "greeting",
                    "action": "boost",
                    "reason": "high success",
                    "confidence": 0.85,
                }
            ]
        )
        proposals = bridge.generate_proposals_from_intelligence(report)
        assert len(proposals) == 1
        assert proposals[0]["proposal_type"] == PROPOSAL_TYPE_BOOST

    def test_summarize_b4_contains_decision_evolution(self):
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.summarize_b4()
        assert "decision_evolution" in s
        de = s["decision_evolution"]
        assert de["enabled"] is True
        assert de["auto_apply"] is False
        assert "pending_count" in de
        assert "approved_count" in de
        assert "applied_count" in de

    def test_bridge_no_evolution_fallback(self):
        # 即使显式传 None,RuntimeB4Bridge 也会自动创建 evolution
        # (这是 B.12 的设计:始终可用,失败降级)
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(
            inner_bridge=b3,
            use_governance=True,
            decision_evolution_runtime=None,
        )
        # 此时 _decision_evolution_runtime 不应为 None(自动创建了)
        assert bridge.decision_evolution_runtime is not None
        # 创建 proposal 应正常工作
        d = bridge.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="x",
            suggested_change="y",
            confidence=0.85,
        )
        assert d is not None
        assert d["proposal_type"] == PROPOSAL_TYPE_BOOST
        # get_pending_proposals / get_evolution_summary 也能工作
        assert len(bridge.get_pending_proposals(limit=10)) >= 1
        s = bridge.get_evolution_summary()
        assert s["enabled"] is True
        assert s["auto_apply"] is False


# ============================================================
# 9. TestSafeHelpers —— 全局安全包装
# ============================================================

class TestSafeHelpers:
    """安全包装测试"""

    def test_safe_get_evolution_summary_none(self):
        s = safe_get_evolution_summary(None)
        assert s["enabled"] is False
        assert s["pending_count"] == 0

    def test_safe_get_evolution_summary_exception(self):
        # 传入一个会抛异常的 fake object
        class BadEvo:
            def get_evolution_summary(self):
                raise RuntimeError("boom")

        s = safe_get_evolution_summary(BadEvo())
        assert s["enabled"] is False


# ============================================================
# 10. TestRegression —— B.4-B.11 回归测试
# ============================================================

class TestRegression:
    """B.4-B.11 兼容性回归测试"""

    def test_b4_governance_still_works(self):
        """B.4 governance 仍然可用"""
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        # 应该有 ledger / persistence
        s = bridge.summarize_b4()
        assert s["available"] is True
        assert s["enabled"] is True
        assert s["governance_on"] is True

    def test_b6_outcome_still_works(self):
        """B.6 outcome 仍集成"""
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.summarize_b4()
        assert "outcome" in s

    def test_b7_feedback_still_works(self):
        """B.7 feedback 仍集成"""
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.summarize_b4()
        assert "feedback" in s

    def test_b10_observer_still_works(self):
        """B.10 observer 仍集成"""
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.summarize_b4()
        assert "decision_observability" in s

    def test_b11_intelligence_still_works(self):
        """B.11 intelligence 仍集成"""
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.summarize_b4()
        assert "decision_intelligence" in s
        # 还能调用 health_score
        hs = bridge.compute_health_score()
        assert "overall_score" in hs
        assert "risk_level" in hs

    def test_b12_does_not_break_b4(self):
        """B.12 不破坏 B.4 flush 流程"""
        b3 = FakeB3Bridge(
            next_result={"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}
        )
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        result = bridge.flush_pending()
        # 不应抛异常
        assert "processed" in result
        assert "dispatched" in result
        assert "governed_audited" in result

    def test_create_b4_bridge_factory(self):
        """create_b4_bridge 工厂支持 B.12 参数"""
        b3 = FakeB3Bridge()
        bridge = create_b4_bridge(
            inner_bridge=b3,
            cfg={"governance": {"enabled": True}},
        )
        assert bridge.decision_evolution_runtime is not None
        s = bridge.summarize_b4()
        assert "decision_evolution" in s


# ============================================================
# 11. TestCoreModuleNotModified —— 核心模块保护
# ============================================================

class TestCoreModuleNotModified:
    """核心模块未修改保护"""

    def test_evolution_does_not_modify_intelligence(self):
        """B.12 不修改 B.11 intelligence"""
        from src.runtime import decision_intelligence

        # B.11 的核心 API 仍存在
        assert hasattr(decision_intelligence, "DecisionIntelligence")
        assert hasattr(decision_intelligence, "StrategyRecommendation")
        assert hasattr(decision_intelligence, "create_decision_intelligence")

    def test_evolution_does_not_modify_bridge_core(self):
        """B.12 不修改 B.4 bridge 核心方法签名"""
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)

        # 关键方法签名保持
        assert hasattr(bridge, "flush_pending")
        assert hasattr(bridge, "summarize_b4")
        assert hasattr(bridge, "is_governance_enabled")
        # B.4-B.11 关键属性仍在
        assert hasattr(bridge, "_inner")
        assert hasattr(bridge, "_lifecycle")
        assert hasattr(bridge, "_persistence")
        assert hasattr(bridge, "_outcome_tracker")
        assert hasattr(bridge, "_feedback_manager")
        assert hasattr(bridge, "_feedback_adapter")
        assert hasattr(bridge, "_feedback_runtime")
        assert hasattr(bridge, "_decision_observer")
        assert hasattr(bridge, "_decision_intelligence")
        # B.12 新增
        assert hasattr(bridge, "_decision_evolution_runtime")

    def test_evolution_auto_apply_strict_false(self):
        """B.12 硬约束:auto_apply 始终 False"""
        # 即使传 True,内部也强制为 False
        evo = DecisionEvolutionRuntime(enabled=True, auto_apply=True)
        assert evo.auto_apply is False
        # bridge 摘要也应是 False
        b3 = FakeB3Bridge()
        bridge = RuntimeB4Bridge(inner_bridge=b3, use_governance=True)
        s = bridge.summarize_b4()
        assert s["decision_evolution"]["auto_apply"] is False


# ============================================================
# Pytest 主入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
