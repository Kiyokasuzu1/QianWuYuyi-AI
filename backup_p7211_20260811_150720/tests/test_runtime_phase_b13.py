# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b13.py

Phase B.13 Runtime Integration —— Decision Governance Executor 测试

覆盖 12 类:
  1. ExecutionResult schema
  2. Policy validation
  3. 低 confidence 拒绝
  4. 未批准 proposal 拒绝
  5. approved proposal 执行
  6. MockAdapter 成功
  7. MockAdapter 失败
  8. Persistence trace
  9. Rollback 接口
 10. Bridge 集成
 11. Health check
 12. B.4-B.12 regression(summarize_b4 包含 decision_execution)

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖
"""
from __future__ import annotations

import json
import os
import time
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest


# ============================================================
# 1. 测试目标导入
# ============================================================

from src.runtime.decision_executor import (
    PHASE_B13_DEFAULT_CONFIG,
    PHASE_B13_NAME,
    PHASE_B13_VERSION,
    SCHEMA_VERSION,
    EXEC_STATUS_PENDING,
    EXEC_STATUS_STARTED,
    EXEC_STATUS_COMPLETED,
    EXEC_STATUS_FAILED,
    EXEC_STATUS_REJECTED,
    ALL_EXEC_STATUSES,
    EXEC_TERMINAL_STATUSES,
    REJECT_NOT_APPROVED,
    REJECT_LOW_CONFIDENCE,
    REJECT_TYPE_NOT_ALLOWED,
    REJECT_TYPE_BLOCKED,
    REJECT_MONITOR_NOT_EXECUTABLE,
    REJECT_AUTO_APPLY_DISABLED,
    REJECT_INVALID_PROPOSAL,
    REJECT_NO_ADAPTER,
    REJECT_VALIDATION_FAILED,
    REJECT_EXECUTION_FAILED,
    STAGE_EXEC_STARTED,
    STAGE_EXEC_COMPLETED,
    STAGE_EXEC_FAILED,
    STAGE_EXEC_REVERTED,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MAX_EXECUTIONS,
    apply_phase_b13_config,
    is_phase_b13_enabled,
    ExecutionResult,
    DecisionGovernancePolicy,
    DecisionAdapter,
    MockAdapter,
    DecisionExecutor,
    DecisionExecutionRuntime,
    create_decision_execution_runtime,
    safe_get_execution_summary,
    _ExecutionIndex,
)
from src.runtime.decision_proposal import (
    PROPOSAL_TYPE_BOOST,
    PROPOSAL_TYPE_SUPPRESS,
    PROPOSAL_TYPE_MONITOR,
    STATUS_PENDING,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_APPLIED,
    DecisionProposal,
    DecisionEvolutionRuntime,
    create_decision_evolution_runtime,
)
from src.runtime.phase_b4_integration import (
    RuntimeB4Bridge,
    PHASE_B13_NAME as BRIDGE_PHASE_B13_NAME,
)
from src.runtime.action_persistence import (
    B13_STAGE_EXEC_STARTED,
    B13_STAGE_EXEC_COMPLETED,
    B13_STAGE_EXEC_FAILED,
    B13_STAGE_EXEC_REVERTED,
    ALL_B13_STAGES,
)


# ============================================================
# 2. 工具
# ============================================================

class _MemoryPersistence:
    """最小可用的 memory persistence,支持 B.13 全部接口"""
    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self._terminal: Dict[str, tuple] = {}
        self.write_count = 0
        self.write_error_count = 0
        self.recover_count = 0
        self.is_degraded = False
        self.enabled = True
        self._closed = False

    def persist_event(self, **kwargs: Any) -> bool:
        if self._closed:
            return False
        try:
            rec = {
                "action_id": str(kwargs.get("action_id", "") or ""),
                "lifecycle_id": str(kwargs.get("lifecycle_id", "") or ""),
                "stage": str(kwargs.get("stage", "") or ""),
                "decision": str(kwargs.get("decision", "") or ""),
                "ts": float(kwargs.get("ts", 0.0) or 0.0),
                "result": kwargs.get("result", None),
                "error": kwargs.get("error", None),
                "source": str(kwargs.get("source", "test") or "test"),
            }
            self._records.append(rec)
            stage = rec["stage"]
            if stage in (
                "completed", "failed", "rejected",
                STAGE_EXEC_COMPLETED, STAGE_EXEC_FAILED, STAGE_EXEC_REVERTED,
            ):
                self._terminal[rec["action_id"]] = (stage, rec["ts"])
            self.write_count += 1
            return True
        except Exception:
            self.write_error_count += 1
            return False

    def get_recent_actions(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self._records[-limit:])

    def get_terminal_actions(self) -> Dict[str, tuple]:
        return dict(self._terminal)

    def health_check(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "degraded": self.is_degraded,
            "write_count": self.write_count,
            "write_error_count": self.write_error_count,
            "recover_count": self.recover_count,
        }

    def load_state(self) -> int:
        # memory persistence: 自身不需恢复,返回 0
        return 0


def _make_approved_proposal(
    proposal_id: str = "p_test_001",
    proposal_type: str = PROPOSAL_TYPE_BOOST,
    target_action_type: str = "greeting",
    confidence: float = 0.9,
    reason: str = "test reason",
    suggested_change: str = "test change",
) -> DecisionProposal:
    """构造一个已 approved 的 proposal"""
    return DecisionProposal(
        proposal_id=proposal_id,
        proposal_type=proposal_type,
        target_action_type=target_action_type,
        suggested_change=suggested_change,
        reason=reason,
        evidence=[],
        confidence=confidence,
        status=STATUS_APPROVED,
    )


def _make_pending_proposal(
    proposal_id: str = "p_test_002",
    proposal_type: str = PROPOSAL_TYPE_BOOST,
    confidence: float = 0.9,
) -> DecisionProposal:
    return DecisionProposal(
        proposal_id=proposal_id,
        proposal_type=proposal_type,
        target_action_type="greeting",
        suggested_change="x",
        reason="x",
        evidence=[],
        confidence=confidence,
        status=STATUS_PENDING,
    )


# ============================================================
# 3. Test 1: ExecutionResult schema
# ============================================================

class TestExecutionResultSchema:
    """ExecutionResult schema 测试"""

    def test_default_construction(self):
        r = ExecutionResult()
        assert r.status == EXEC_STATUS_PENDING
        assert r.proposal_id == ""
        assert r.success is False
        assert r.error == ""
        assert isinstance(r.applied_change, dict)
        assert isinstance(r.metadata, dict)
        assert r.execution_id.startswith("dexec_")

    def test_construction_with_args(self):
        r = ExecutionResult(
            proposal_id="p1",
            status=EXEC_STATUS_COMPLETED,
            success=True,
            error="",
            adapter_name="mock_adapter",
            reject_reason="",
            applied_change={"k": "v"},
            metadata={"caller_id": "tester"},
        )
        assert r.proposal_id == "p1"
        assert r.status == EXEC_STATUS_COMPLETED
        assert r.success is True
        assert r.adapter_name == "mock_adapter"
        assert r.applied_change == {"k": "v"}
        assert r.metadata.get("caller_id") == "tester"

    def test_to_dict(self):
        r = ExecutionResult(
            proposal_id="p1",
            status=EXEC_STATUS_COMPLETED,
            success=True,
        )
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["proposal_id"] == "p1"
        assert d["status"] == EXEC_STATUS_COMPLETED
        assert d["success"] is True
        assert d["schema_version"] == SCHEMA_VERSION
        assert "execution_id" in d

    def test_from_dict_roundtrip(self):
        r1 = ExecutionResult(
            proposal_id="p1",
            status=EXEC_STATUS_COMPLETED,
            success=True,
        )
        d = r1.to_dict()
        r2 = ExecutionResult.from_dict(d)
        assert r2.proposal_id == r1.proposal_id
        assert r2.status == r1.status
        assert r2.success == r1.success
        assert r2.execution_id == r1.execution_id

    def test_is_terminal(self):
        assert ExecutionResult(status=EXEC_STATUS_PENDING).is_terminal() is False
        assert ExecutionResult(status=EXEC_STATUS_STARTED).is_terminal() is False
        assert ExecutionResult(status=EXEC_STATUS_COMPLETED).is_terminal() is True
        assert ExecutionResult(status=EXEC_STATUS_FAILED).is_terminal() is True
        assert ExecutionResult(status=EXEC_STATUS_REJECTED).is_terminal() is True

    def test_all_status_constants(self):
        assert set(ALL_EXEC_STATUSES) == {
            EXEC_STATUS_PENDING,
            EXEC_STATUS_STARTED,
            EXEC_STATUS_COMPLETED,
            EXEC_STATUS_FAILED,
            EXEC_STATUS_REJECTED,
        }
        assert set(EXEC_TERMINAL_STATUSES) == {
            EXEC_STATUS_COMPLETED,
            EXEC_STATUS_FAILED,
            EXEC_STATUS_REJECTED,
        }


# ============================================================
# 4. Test 2: Policy validation
# ============================================================

class TestPolicyValidation:
    """Policy validation 测试"""

    def test_default_policy_auto_apply_false(self):
        p = DecisionGovernancePolicy()
        # B.13 硬约束:auto_apply_enabled 必须为 False
        assert p.auto_apply_enabled is False
        assert p.min_confidence == DEFAULT_MIN_CONFIDENCE

    def test_policy_construction_force_disabled(self):
        # 即使外部传 True,也会被强制为 False
        p = DecisionGovernancePolicy(auto_apply_enabled=True)
        assert p.auto_apply_enabled is False

    def test_min_confidence_clip(self):
        p = DecisionGovernancePolicy(min_confidence=2.0)
        assert p.min_confidence == 1.0
        p2 = DecisionGovernancePolicy(min_confidence=-0.5)
        assert p2.min_confidence == 0.0

    def test_can_execute_approved_high_confidence(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        proposal = _make_approved_proposal(confidence=0.9)
        ok, reason = p.can_execute(proposal)
        assert ok is True
        assert reason == ""

    def test_can_execute_rejects_none_proposal(self):
        p = DecisionGovernancePolicy()
        ok, reason = p.can_execute(None)
        assert ok is False
        assert reason == REJECT_INVALID_PROPOSAL

    def test_can_execute_blocks_monitor_by_default(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        proposal = _make_approved_proposal(
            proposal_type=PROPOSAL_TYPE_MONITOR, confidence=0.95,
        )
        ok, reason = p.can_execute(proposal)
        assert ok is False
        assert reason == REJECT_MONITOR_NOT_EXECUTABLE

    def test_can_execute_allows_monitor_when_explicit(self):
        p = DecisionGovernancePolicy(
            min_confidence=0.5, allow_monitor_execution=True,
        )
        proposal = _make_approved_proposal(
            proposal_type=PROPOSAL_TYPE_MONITOR, confidence=0.95,
        )
        ok, reason = p.can_execute(proposal)
        assert ok is True

    def test_can_execute_blocked_type(self):
        p = DecisionGovernancePolicy(
            min_confidence=0.5, blocked_types=[PROPOSAL_TYPE_SUPPRESS],
        )
        proposal = _make_approved_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS, confidence=0.95,
        )
        ok, reason = p.can_execute(proposal)
        assert ok is False
        assert reason == REJECT_TYPE_BLOCKED

    def test_can_execute_allowed_type_filter(self):
        p = DecisionGovernancePolicy(
            min_confidence=0.5, allowed_types=[PROPOSAL_TYPE_BOOST],
        )
        # 允许的类型
        p1 = _make_approved_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST, confidence=0.95,
        )
        ok, _ = p.can_execute(p1)
        assert ok is True
        # 非允许的类型
        p2 = _make_approved_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS, confidence=0.95,
        )
        ok, reason = p.can_execute(p2)
        assert ok is False
        assert reason == REJECT_TYPE_NOT_ALLOWED

    def test_can_execute_auto_apply_disabled(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        proposal = _make_approved_proposal(confidence=0.95)
        # auto_apply=True 但 policy.auto_apply_enabled=False
        ok, reason = p.can_execute(proposal, auto_apply=True)
        assert ok is False
        assert reason == REJECT_AUTO_APPLY_DISABLED

    def test_to_dict(self):
        p = DecisionGovernancePolicy(
            min_confidence=0.7,
            allowed_types=["boost"],
            blocked_types=["monitor"],
        )
        d = p.to_dict()
        assert d["min_confidence"] == 0.7
        assert d["allowed_types"] == ["boost"]
        assert d["blocked_types"] == ["monitor"]
        assert d["auto_apply_enabled"] is False

    def test_from_config(self):
        p = DecisionGovernancePolicy.from_config({
            "min_confidence": 0.6,
            "allowed_types": ["boost", "suppress"],
            "require_approved_status": False,
        })
        assert p.min_confidence == 0.6
        assert p.allowed_types == ["boost", "suppress"]
        assert p.require_approved_status is False
        assert p.auto_apply_enabled is False  # 永远 False


# ============================================================
# 5. Test 3: 低 confidence 拒绝
# ============================================================

class TestLowConfidenceRejection:
    """低 confidence 拒绝测试"""

    def test_low_confidence_rejected(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        proposal = _make_approved_proposal(confidence=0.5)
        ok, reason = p.can_execute(proposal)
        assert ok is False
        assert reason == REJECT_LOW_CONFIDENCE

    def test_executor_rejects_low_confidence(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.3)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_REJECTED
        assert result.reject_reason == REJECT_LOW_CONFIDENCE
        assert result.success is False

    def test_executor_accepts_exact_threshold(self):
        # 边界:confidence == min_confidence 应该通过
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.5)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_COMPLETED
        assert result.success is True


# ============================================================
# 6. Test 4: 未批准 proposal 拒绝
# ============================================================

class TestUnapprovedRejection:
    """未批准 proposal 拒绝测试"""

    def test_pending_proposal_rejected_by_policy(self):
        p = DecisionGovernancePolicy(min_confidence=0.5, require_approved_status=True)
        proposal = _make_pending_proposal(confidence=0.95)
        ok, reason = p.can_execute(proposal)
        assert ok is False
        assert reason == REJECT_NOT_APPROVED

    def test_executor_rejects_pending_proposal(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_pending_proposal(confidence=0.95)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_REJECTED
        assert result.reject_reason == REJECT_NOT_APPROVED

    def test_can_disable_approved_requirement(self):
        p = DecisionGovernancePolicy(
            min_confidence=0.5, require_approved_status=False,
        )
        proposal = _make_pending_proposal(confidence=0.95)
        ok, reason = p.can_execute(proposal)
        assert ok is True

    def test_rejected_status_proposal_rejected(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        proposal = DecisionProposal(
            proposal_id="p_rej",
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            confidence=0.95,
            status=STATUS_REJECTED,
        )
        ok, reason = p.can_execute(proposal)
        assert ok is False
        assert reason == REJECT_NOT_APPROVED


# ============================================================
# 7. Test 5: approved proposal 执行
# ============================================================

class TestApprovedExecution:
    """approved proposal 执行测试"""

    def test_approved_proposal_executed(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_COMPLETED
        assert result.success is True
        assert result.proposal_id == proposal.proposal_id
        assert result.applied_change.get("proposal_id") == proposal.proposal_id

    def test_execution_records_timestamps(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.started_at != ""
        assert result.completed_at != ""
        assert result.started_at <= result.completed_at

    def test_execution_with_caller_id(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal, caller_id="test_admin")
        assert result.metadata.get("caller_id") == "test_admin"

    def test_can_execute_method(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        assert executor.can_execute(proposal) is True

        bad_proposal = _make_approved_proposal(confidence=0.3)
        assert executor.can_execute(bad_proposal) is False


# ============================================================
# 8. Test 6: MockAdapter 成功
# ============================================================

class TestMockAdapterSuccess:
    """MockAdapter 成功测试"""

    def test_mock_adapter_default(self):
        adapter = MockAdapter()
        assert adapter.name == "mock_adapter"

    def test_mock_adapter_validate_change(self):
        adapter = MockAdapter()
        proposal = _make_approved_proposal()
        ok, reason = adapter.validate_change(proposal)
        assert ok is True
        assert reason == ""

    def test_mock_adapter_validate_none(self):
        adapter = MockAdapter()
        ok, reason = adapter.validate_change(None)
        assert ok is False
        assert "None" in reason or "proposal" in reason

    def test_mock_adapter_apply_change(self):
        adapter = MockAdapter()
        proposal = _make_approved_proposal(confidence=0.9)
        applied = adapter.apply_change(proposal)
        assert isinstance(applied, dict)
        assert applied.get("proposal_id") == proposal.proposal_id
        assert applied.get("proposal_type") == proposal.proposal_type
        assert applied.get("target_action_type") == proposal.target_action_type
        assert applied.get("suggested_change") == proposal.suggested_change
        assert applied.get("applied_at") != ""

    def test_mock_adapter_applied_history(self):
        adapter = MockAdapter()
        for i in range(3):
            adapter.apply_change(_make_approved_proposal(
                proposal_id=f"p_{i}", confidence=0.9,
            ))
        history = adapter.get_applied_history()
        assert len(history) == 3
        assert history[0]["proposal_id"] == "p_0"
        assert history[2]["proposal_id"] == "p_2"

    def test_mock_adapter_clear(self):
        adapter = MockAdapter()
        adapter.apply_change(_make_approved_proposal(confidence=0.9))
        adapter.clear()
        assert len(adapter.get_applied_history()) == 0


# ============================================================
# 9. Test 7: MockAdapter 失败
# ============================================================

class TestMockAdapterFailure:
    """MockAdapter 失败测试"""

    def test_validate_failure_propagates(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        adapter.set_should_fail(validate=True)
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_REJECTED
        assert "validate" in (result.reject_reason or "").lower() or \
               "mock_validate" in (result.reject_reason or "")

    def test_apply_failure_marks_failed(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        adapter.set_should_fail(apply=True)
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_FAILED
        assert result.success is False
        assert "mock_apply_failed" in (result.error or "")

    def test_rollback_failure_returns_false(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_COMPLETED
        # 设置 rollback 失败
        adapter.set_should_fail(rollback=True)
        ok = executor.rollback(result.execution_id)
        assert ok is False


# ============================================================
# 10. Test 8: Persistence trace
# ============================================================

class TestPersistenceTrace:
    """Persistence trace 测试"""

    def test_execution_persists_started_event(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        persistence = _MemoryPersistence()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(), persistence=persistence,
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        # 应该至少有 started 和 completed
        stages = [r.get("stage") for r in persistence._records]
        assert STAGE_EXEC_STARTED in stages
        assert STAGE_EXEC_COMPLETED in stages

    def test_execution_persists_failed_event(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        adapter.set_should_fail(apply=True)
        persistence = _MemoryPersistence()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(), persistence=persistence,
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_FAILED
        stages = [r.get("stage") for r in persistence._records]
        assert STAGE_EXEC_FAILED in stages

    def test_execution_persists_rejected_event(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        persistence = _MemoryPersistence()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(), persistence=persistence,
        )
        proposal = _make_approved_proposal(confidence=0.3)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_REJECTED
        stages = [r.get("stage") for r in persistence._records]
        assert STAGE_EXEC_FAILED in stages  # rejected 也走 failed 通道

    def test_rollback_persists_reverted_event(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        persistence = _MemoryPersistence()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(), persistence=persistence,
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        ok = executor.rollback(result.execution_id)
        assert ok is True
        stages = [r.get("stage") for r in persistence._records]
        assert STAGE_EXEC_REVERTED in stages

    def test_persistence_with_duck_typed_object(self):
        # 不是 _MemoryPersistence,只是有 persist_event 方法的对象
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()

        class _DuckPersistence:
            def __init__(self):
                self.records = []

            def persist_event(self, **kwargs):
                self.records.append(kwargs)
                return True

        duck = _DuckPersistence()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(), persistence=duck,
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_COMPLETED
        assert len(duck.records) >= 2  # 至少 started + completed

    def test_persistence_failure_does_not_crash(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()

        class _BrokenPersistence:
            def persist_event(self, **kwargs):
                raise RuntimeError("broken")

        broken = _BrokenPersistence()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(), persistence=broken,
        )
        proposal = _make_approved_proposal(confidence=0.9)
        # 不应该抛
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_COMPLETED
        assert result.success is True

    def test_b13_stage_constants_in_action_persistence(self):
        # 确认 stage 常量在 action_persistence.py 中已注册
        assert B13_STAGE_EXEC_STARTED == "decision_execution_started"
        assert B13_STAGE_EXEC_COMPLETED == "decision_execution_completed"
        assert B13_STAGE_EXEC_FAILED == "decision_execution_failed"
        assert B13_STAGE_EXEC_REVERTED == "decision_execution_reverted"
        assert B13_STAGE_EXEC_STARTED in ALL_B13_STAGES
        assert len(ALL_B13_STAGES) == 4


# ============================================================
# 11. Test 9: Rollback 接口
# ============================================================

class TestRollback:
    """Rollback 接口测试"""

    def test_rollback_completed_execution(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_COMPLETED
        ok = executor.rollback(result.execution_id)
        assert ok is True

    def test_rollback_nonexistent_execution(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        ok = executor.rollback("nonexistent_id")
        assert ok is False

    def test_rollback_rejected_execution_fails(self):
        p = DecisionGovernancePolicy(min_confidence=0.85)
        adapter = MockAdapter()
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        # rejected 不是 completed
        proposal = _make_approved_proposal(confidence=0.3)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_REJECTED
        ok = executor.rollback(result.execution_id)
        assert ok is False

    def test_rollback_failed_execution_fails(self):
        p = DecisionGovernancePolicy(min_confidence=0.5)
        adapter = MockAdapter()
        adapter.set_should_fail(apply=True)
        executor = DecisionExecutor(
            policy=p, adapter=adapter, store=_ExecutionIndex(),
        )
        proposal = _make_approved_proposal(confidence=0.9)
        result = executor.execute(proposal)
        assert result.status == EXEC_STATUS_FAILED
        # failed 不能 rollback
        ok = executor.rollback(result.execution_id)
        assert ok is False


# ============================================================
# 12. Test 10: Bridge 集成
# ============================================================

class TestBridgeIntegration:
    """Bridge 集成测试"""

    def _make_bridge(self, **kwargs) -> RuntimeB4Bridge:
        """构造一个最小可用的 RuntimeB4Bridge"""
        inner = MagicMock()
        inner.flush_pending.return_value = {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        inner.get_recent_results.return_value = []
        return RuntimeB4Bridge(
            inner_bridge=inner,
            use_governance=True,
            **kwargs,
        )

    def test_bridge_has_execution_runtime(self):
        bridge = self._make_bridge()
        assert hasattr(bridge, "decision_execution_runtime")
        # 默认会自动创建
        assert bridge.decision_execution_runtime is not None

    def test_bridge_execute_proposal_interface(self):
        bridge = self._make_bridge()
        # 未 approved 提案 → rejected
        proposal = _make_pending_proposal(confidence=0.95)
        result = bridge.execute_proposal(proposal, caller_id="tester")
        assert isinstance(result, dict)
        assert result.get("status") == EXEC_STATUS_REJECTED
        assert result.get("success") is False

    def test_bridge_execute_approved_proposal(self):
        bridge = self._make_bridge()
        proposal = _make_approved_proposal(confidence=0.95)
        result = bridge.execute_proposal(proposal, caller_id="tester")
        assert isinstance(result, dict)
        assert result.get("status") == EXEC_STATUS_COMPLETED
        assert result.get("success") is True

    def test_bridge_approve_and_execute(self):
        bridge = self._make_bridge()
        proposal = _make_pending_proposal(confidence=0.95)
        # 先调用 approve_and_execute
        result = bridge.approve_and_execute(
            proposal, reviewer_id="admin", comment="ok",
            caller_id="admin",
        )
        assert isinstance(result, dict)
        assert result.get("status") == EXEC_STATUS_COMPLETED
        assert result.get("success") is True

    def test_bridge_validate_proposal(self):
        bridge = self._make_bridge()
        approved = _make_approved_proposal(confidence=0.95)
        r1 = bridge.validate_proposal(approved)
        assert r1.get("ok") is True
        # pending 失败
        pending = _make_pending_proposal(confidence=0.95)
        r2 = bridge.validate_proposal(pending)
        assert r2.get("ok") is False
        # 低 confidence
        low_conf = _make_approved_proposal(confidence=0.1)
        r3 = bridge.validate_proposal(low_conf)
        assert r3.get("ok") is False

    def test_bridge_get_execution_history(self):
        bridge = self._make_bridge()
        proposal = _make_approved_proposal(confidence=0.95)
        bridge.execute_proposal(proposal, caller_id="tester")
        history = bridge.get_execution_history(limit=10)
        assert isinstance(history, list)
        assert len(history) >= 1
        assert history[0].get("proposal_id") == proposal.proposal_id

    def test_bridge_get_execution_summary(self):
        bridge = self._make_bridge()
        summary = bridge.get_execution_summary()
        assert isinstance(summary, dict)
        assert summary.get("name") == PHASE_B13_NAME
        assert "execute_count" in summary
        assert "auto_apply_enabled" in summary
        assert summary.get("auto_apply_enabled") is False

    def test_bridge_summarize_b4_contains_execution(self):
        bridge = self._make_bridge()
        s = bridge.summarize_b4()
        assert "decision_execution" in s
        de = s["decision_execution"]
        assert isinstance(de, dict)
        assert "enabled" in de
        assert "total_count" in de
        assert "success_count" in de
        assert "failure_count" in de
        assert "pending_count" in de
        assert de.get("auto_apply_enabled") is False

    def test_bridge_evolution_proposal_aware_execution(self):
        # 测试 B.12 + B.13 联动
        bridge = self._make_bridge()
        # 1. 通过 B.12 创建一个 proposal
        proposal_dict = bridge.create_proposal(
            proposal_type=PROPOSAL_TYPE_BOOST,
            target_action_type="greeting",
            suggested_change="increase frequency",
            reason="high success rate",
            confidence=0.95,
        )
        assert proposal_dict is not None
        proposal_id = proposal_dict.get("proposal_id", "")
        assert proposal_id != ""
        # 2. 审批
        ok = bridge.approve_proposal(proposal_id, reviewer_id="admin", comment="ok")
        assert ok is True
        # 3. 通过 B.13 执行
        # 重新构造 proposal 状态(已 approved)
        approved_proposal = DecisionProposal(
            proposal_id=proposal_id,
            proposal_type=proposal_dict.get("proposal_type", PROPOSAL_TYPE_BOOST),
            target_action_type=proposal_dict.get("target_action_type", "greeting"),
            suggested_change=proposal_dict.get("suggested_change", "x"),
            reason=proposal_dict.get("reason", "x"),
            confidence=proposal_dict.get("confidence", 0.95),
            status=STATUS_APPROVED,
        )
        result = bridge.execute_proposal(approved_proposal, caller_id="admin")
        assert result.get("status") == EXEC_STATUS_COMPLETED
        assert result.get("success") is True

    def test_bridge_with_no_evolution_runtime_fallback(self):
        # 没有 decision_evolution_runtime 时,execute_proposal 仍可用
        inner = MagicMock()
        inner.flush_pending.return_value = {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        inner.get_recent_results.return_value = []
        bridge = RuntimeB4Bridge(
            inner_bridge=inner,
            use_governance=True,
            decision_evolution_runtime=None,
        )
        assert bridge.decision_execution_runtime is not None
        proposal = _make_approved_proposal(confidence=0.95)
        result = bridge.execute_proposal(proposal, caller_id="tester")
        assert result.get("status") == EXEC_STATUS_COMPLETED


# ============================================================
# 13. Test 11: Health check
# ============================================================

class TestHealthCheck:
    """Health check 测试"""

    def test_executor_health_check(self):
        runtime = DecisionExecutionRuntime()
        hc = runtime.health_check()
        assert isinstance(hc, dict)
        assert hc.get("name") == PHASE_B13_NAME
        assert "enabled" in hc
        assert "degraded" in hc
        assert "execute_count" in hc
        assert "auto_apply_enabled" in hc
        assert hc.get("auto_apply_enabled") is False

    def test_bridge_health_check(self):
        inner = MagicMock()
        inner.flush_pending.return_value = {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        inner.get_recent_results.return_value = []
        bridge = RuntimeB4Bridge(
            inner_bridge=inner, use_governance=True,
        )
        hc = bridge.decision_executor_health_check()
        assert isinstance(hc, dict)
        assert hc.get("name") == PHASE_B13_NAME
        assert "execute_count" in hc
        assert "rollback_count" in hc
        assert "error_count" in hc

    def test_runtime_summary(self):
        runtime = DecisionExecutionRuntime()
        s = runtime.get_execution_summary()
        assert s.get("enabled") is True
        assert s.get("auto_apply_enabled") is False
        assert s.get("min_confidence") == DEFAULT_MIN_CONFIDENCE
        assert s.get("require_approved_status") is True
        assert s.get("allow_monitor_execution") is False

    def test_safe_summary_with_none(self):
        s = safe_get_execution_summary(None)
        assert isinstance(s, dict)
        assert s.get("enabled") is False
        assert s.get("auto_apply_enabled") is False

    def test_safe_summary_with_runtime(self):
        runtime = DecisionExecutionRuntime()
        s = safe_get_execution_summary(runtime)
        assert s.get("enabled") is True

    def test_apply_phase_b13_config(self):
        cfg = apply_phase_b13_config({})
        assert "decision_execution" in cfg
        de = cfg["decision_execution"]
        assert de.get("enabled") is True
        assert de.get("auto_apply_enabled") is False
        assert de.get("min_confidence") == DEFAULT_MIN_CONFIDENCE

    def test_is_phase_b13_enabled(self):
        assert is_phase_b13_enabled({}) is False  # 默认 cfg 无 decision_execution
        assert is_phase_b13_enabled({"decision_execution": {}}) is True  # 空 dict 也算 enabled
        assert is_phase_b13_enabled({"decision_execution": {"enabled": False}}) is False
        assert is_phase_b13_enabled(None) is False


# ============================================================
# 14. Test 12: B.4-B.12 regression
# ============================================================

class TestB4ToB12Regression:
    """B.4-B.12 regression 测试(summarize_b4 包含决策层全链路)"""

    def _make_full_bridge(self) -> RuntimeB4Bridge:
        inner = MagicMock()
        inner.flush_pending.return_value = {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        inner.get_recent_results.return_value = []
        return RuntimeB4Bridge(
            inner_bridge=inner,
            use_governance=True,
        )

    def test_summarize_b4_includes_all_phases(self):
        bridge = self._make_full_bridge()
        s = bridge.summarize_b4()
        # B.4-B.13 所有 phase 字段都应在
        assert "governance" not in s  # 改名为 phase_b4_enabled
        assert "policy" in s
        assert "persistence" in s
        # B.6
        assert "outcome" in s
        # B.7
        assert "feedback" in s
        # B.8
        assert "feedback_adapter" in s
        # B.9
        assert "decision_feedback_runtime" in s
        # B.10
        assert "decision_observability" in s
        # B.11
        assert "decision_intelligence" in s
        # B.12
        assert "decision_evolution" in s
        # B.13
        assert "decision_execution" in s

    def test_summarize_b4_decision_evolution_intact(self):
        # 确保 B.13 集成没有破坏 B.12
        bridge = self._make_full_bridge()
        s = bridge.summarize_b4()
        de = s.get("decision_evolution", {})
        assert "pending_count" in de
        assert "approved_count" in de
        assert de.get("auto_apply") is False

    def test_summarize_b4_decision_execution_intact(self):
        # B.13 字段
        bridge = self._make_full_bridge()
        s = bridge.summarize_b4()
        de = s.get("decision_execution", {})
        assert de.get("auto_apply_enabled") is False
        assert "pending_count" in de
        assert "success_count" in de
        assert "failure_count" in de

    def test_total_evolution_proposals_field_preserved(self):
        # 累加字段应保留
        bridge = self._make_full_bridge()
        s = bridge.summarize_b4()
        assert "total_evolution_proposals" in s
        assert "total_executions" in s
        assert "total_approved_executed" in s
        assert "total_executions_rejected" in s
        assert "total_executions_failed" in s
        assert "total_executions_reverted" in s

    def test_b13_bridge_evolution_counters(self):
        # 验证 B.13 累加计数
        inner = MagicMock()
        inner.flush_pending.return_value = {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        inner.get_recent_results.return_value = []
        bridge = RuntimeB4Bridge(
            inner_bridge=inner, use_governance=True,
        )
        # 执行 3 个 approved
        for i in range(3):
            bridge.execute_proposal(
                _make_approved_proposal(
                    proposal_id=f"p_{i}", confidence=0.95,
                ),
                caller_id="tester",
            )
        # 1 个低 confidence
        bridge.execute_proposal(
            _make_approved_proposal(
                proposal_id="p_low", confidence=0.1,
            ),
            caller_id="tester",
        )
        s = bridge.summarize_b4()
        de = s.get("decision_execution", {})
        # total_count: 4 (3 completed + 1 rejected)
        assert de.get("total_count") == 4
        # success_count: 3
        assert de.get("success_count") == 3
        # rejected_count: 1
        assert de.get("rejected_count") == 1
        # bridge 累加字段
        assert s.get("total_executions") == 4
        assert s.get("total_approved_executed") == 3
        assert s.get("total_executions_rejected") == 1

    def test_b12_create_proposal_still_works(self):
        # B.12 接口仍可用
        bridge = self._make_full_bridge()
        p = bridge.create_proposal(
            proposal_type=PROPOSAL_TYPE_SUPPRESS,
            target_action_type="x",
            suggested_change="decrease",
            reason="low success",
            confidence=0.85,
        )
        assert p is not None
        assert p.get("proposal_type") == PROPOSAL_TYPE_SUPPRESS

    def test_b11_intelligence_still_works(self):
        # B.11 接口仍可用
        bridge = self._make_full_bridge()
        h = bridge.compute_health_score()
        assert isinstance(h, dict)
        assert "risk_level" in h

    def test_b10_observer_still_works(self):
        # B.10 接口仍可用
        bridge = self._make_full_bridge()
        m = bridge.get_decision_metrics()
        assert isinstance(m, dict)
        assert "total_actions" in m

    def test_b4_governance_intact(self):
        # B.4 治理仍可用
        bridge = self._make_full_bridge()
        s = bridge.summarize_b4()
        assert s.get("phase_b4_enabled") is True
        assert s.get("policy", {}).get("max_retry") == 0
        assert s.get("ledger_count") == 0

    def test_flush_pending_still_works(self):
        # B.4 flush_pending 不受影响
        bridge = self._make_full_bridge()
        result = bridge.flush_pending()
        assert isinstance(result, dict)
        assert "processed" in result
        assert "dispatched" in result
        assert "governed_audited" in result
        assert "governed_duplicates" in result


# ============================================================
# 15. Extra: create_decision_execution_runtime 工厂
# ============================================================

class TestFactory:
    """工厂函数测试"""

    def test_create_factory_default(self):
        runtime = create_decision_execution_runtime()
        assert runtime is not None
        assert runtime.enabled is True
        assert runtime.auto_apply_enabled is False
        assert runtime.min_confidence == DEFAULT_MIN_CONFIDENCE

    def test_create_factory_with_bridge(self):
        inner = MagicMock()
        inner.flush_pending.return_value = {
            "processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0,
        }
        inner.get_recent_results.return_value = []
        bridge = RuntimeB4Bridge(
            inner_bridge=inner, use_governance=True,
        )
        runtime = create_decision_execution_runtime(bridge=bridge)
        assert runtime is not None
        # 应该自动注入 persistence + evolution
        assert runtime is not None

    def test_create_factory_with_cfg(self):
        cfg = {
            "decision_execution": {
                "enabled": True,
                "min_confidence": 0.7,
                "max_executions": 100,
            },
        }
        runtime = create_decision_execution_runtime(cfg=cfg)
        assert runtime.min_confidence == 0.7
        assert runtime._max_executions == 100

    def test_create_factory_disabled(self):
        runtime = create_decision_execution_runtime(enabled=False)
        assert runtime.enabled is False

    def test_create_factory_auto_apply_forced_false(self):
        # 即使 cfg 设了 True 也会被强制为 False
        cfg = {
            "decision_execution": {
                "auto_apply_enabled": True,  # 尝试打开
            },
        }
        runtime = create_decision_execution_runtime(cfg=cfg)
        assert runtime.auto_apply_enabled is False


# ============================================================
# 16. Extra: DecisionExecutionRuntime 核心流程
# ============================================================

class TestRuntimeCore:
    """DecisionExecutionRuntime 核心流程测试"""

    def test_approve_and_execute_2_step(self):
        # 模拟 B.12 evolution 提供 get_proposal / approve_proposal
        runtime = DecisionExecutionRuntime()

        class _MockEvolution:
            def __init__(self):
                self.approved = False

            def approve_proposal(self, proposal_id, reviewer_id, comment):
                self.approved = True
                return True

            def get_proposal(self, proposal_id):
                p = _make_pending_proposal(
                    proposal_id=proposal_id, confidence=0.95,
                )
                if self.approved:
                    p.status = STATUS_APPROVED
                return p

        evolution = _MockEvolution()
        runtime.set_evolution_runtime(evolution)
        # 构造一个 pending proposal
        proposal = _make_pending_proposal(confidence=0.95)
        result = runtime.approve_and_execute(
            proposal, reviewer_id="admin", comment="ok",
        )
        assert result.status == EXEC_STATUS_COMPLETED
        assert result.success is True

    def test_rollback_execution(self):
        runtime = DecisionExecutionRuntime()
        proposal = _make_approved_proposal(confidence=0.95)
        result = runtime.execute_proposal(proposal, caller_id="tester")
        assert result.status == EXEC_STATUS_COMPLETED
        ok = runtime.rollback_execution(result.execution_id)
        assert ok is True

    def test_rollback_unknown_execution(self):
        runtime = DecisionExecutionRuntime()
        ok = runtime.rollback_execution("nonexistent")
        assert ok is False

    def test_close(self):
        runtime = DecisionExecutionRuntime()
        runtime.close()
        assert runtime._closed is True

    def test_disable_enable(self):
        runtime = DecisionExecutionRuntime()
        assert runtime.enabled is True
        runtime.disable()
        assert runtime.enabled is False
        runtime.enable()
        assert runtime.enabled is True

    def test_clear(self):
        runtime = DecisionExecutionRuntime()
        proposal = _make_approved_proposal(confidence=0.95)
        runtime.execute_proposal(proposal, caller_id="tester")
        runtime.clear()
        assert runtime.execute_count == 0
        assert runtime.get_execution_history() == []

    def test_get_execution_by_id(self):
        runtime = DecisionExecutionRuntime()
        proposal = _make_approved_proposal(confidence=0.95)
        result = runtime.execute_proposal(proposal, caller_id="tester")
        eid = result.execution_id
        fetched = runtime.get_execution(eid)
        assert fetched is not None
        assert fetched.execution_id == eid

    def test_set_adapter(self):
        runtime = DecisionExecutionRuntime()
        new_adapter = MockAdapter(name="custom_adapter")
        runtime.set_adapter(new_adapter)
        assert runtime.adapter.name == "custom_adapter"

    def test_set_policy(self):
        runtime = DecisionExecutionRuntime()
        new_policy = DecisionGovernancePolicy(min_confidence=0.5)
        runtime.set_policy(new_policy)
        assert runtime.min_confidence == 0.5

    def test_set_policy_force_auto_apply_false(self):
        runtime = DecisionExecutionRuntime()
        new_policy = DecisionGovernancePolicy(min_confidence=0.5)
        new_policy.auto_apply_enabled = True  # 尝试打开
        runtime.set_policy(new_policy)
        # 应被强制为 False
        assert runtime.policy.auto_apply_enabled is False

    def test_set_persistence(self):
        runtime = DecisionExecutionRuntime()
        persistence = _MemoryPersistence()
        runtime.set_persistence(persistence)
        # 再次执行,应该走新 persistence
        proposal = _make_approved_proposal(confidence=0.95)
        runtime.execute_proposal(proposal, caller_id="tester")
        assert persistence.write_count > 0
