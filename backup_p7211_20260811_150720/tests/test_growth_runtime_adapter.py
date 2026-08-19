# -*- coding: utf-8 -*-
"""
tests/test_growth_runtime_adapter.py

Phase C.6 Growth Runtime Integration —— GrowthRuntimeAdapter 测试

覆盖 6 类:
  1. TestProtocol       - CycleAdapter duck-type 兼容 / name / schema_version
  2. TestReadOnly       - 禁止调用写入接口(apply/save/update_metrics 等)
  3. TestProcessing     - 正常读取 / 写入 ctx.growth_output / 空状态 / 缺字段
  4. TestExceptionIsolation - state 异常 / store 异常 / 字段异常 → degraded
  5. TestHealthSnapshot - health_check / snapshot
  6. TestRuntimeIntegration - 注册到 RuntimeCycleOrchestrator / step 5

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化 GrowthState / ProposalStore
  - 不依赖 RuntimeCore 单例
"""
from __future__ import annotations

import sys
import os
import time
import threading
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.adapters.impl.growth_runtime_adapter import (
    GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION,
    PHASE_C6_NAME,
    PHASE_C6_VERSION,
    PHASE_C6_STAGE_GROWTH_READ,
    PHASE_C6_STAGE_GROWTH_DEGRADED,
    ALL_PHASE_C6_STAGES,
    SOURCE_NAME,
    DEFAULT_RECENT_RECORDS_LIMIT,
    DEFAULT_PENDING_PROPOSALS_LIMIT,
    DEFAULT_MILESTONES_LIMIT,
    DEFAULT_GROWTH_HISTORY_LIMIT,
    _GrowthOutputSnapshot,
    GrowthRuntimeAdapter,
    create_growth_runtime_adapter,
    safe_get_growth_adapter_summary,
)
from src.runtime.cycle_adapter import (
    is_cycle_adapter,
    STANDARD_ADAPTER_GROWTH,
)
from src.runtime.cycle_context import RuntimeCycleContext
from src.runtime.cycle_event import (
    CYCLE_EVENT_GROWTH_COMPLETED,
    CYCLE_EVENT_COMPLETED,
)
from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Helper:Mock GrowthState / ProposalStore
# ============================================================


def _make_growth_state(
    metrics: Optional[Dict[str, float]] = None,
    behaviors: Optional[Dict[str, bool]] = None,
    identities: Optional[List[str]] = None,
    milestones: Optional[List[Dict[str, Any]]] = None,
    growth_history: Optional[List[Dict[str, Any]]] = None,
) -> Any:
    """构造一个 mock GrowthState(只含 get() 与 get_metric())。"""

    class _MockGrowthState:
        def __init__(self):
            self._state = {
                "version": "0.2",
                "metrics": dict(metrics or {
                    "trust": 0.30,
                    "closeness": 0.20,
                    "safety": 0.30,
                    "self_awareness": 0.20,
                    "self_confidence": 0.10,
                }),
                "behaviors": dict(behaviors or {
                    "active_care": False,
                    "use_nickname": False,
                    "initiate_topic": False,
                }),
                "identities": list(identities or []),
                "milestones": list(milestones or []),
                "growth_history": list(growth_history or []),
            }
            self.save_called = 0
            self.update_metrics_called = 0

        def get(self):
            return self._state

        def get_metric(self, key):
            return self._state["metrics"].get(key, 0.0)

        # 下面这些方法如果被调用,测试会失败
        def save(self):
            self.save_called += 1
            raise AssertionError("GrowthState.save() must NOT be called by adapter")

        def update_metrics(self, deltas):
            self.update_metrics_called += 1
            raise AssertionError("GrowthState.update_metrics() must NOT be called by adapter")

        def add_milestone(self, event_id, topic):
            raise AssertionError("GrowthState.add_milestone() must NOT be called by adapter")

        def add_identity(self, identity):
            raise AssertionError("GrowthState.add_identity() must NOT be called by adapter")

        def set_behavior(self, behavior, value):
            raise AssertionError("GrowthState.set_behavior() must NOT be called by adapter")

        def mark_event_processed(self, event_id):
            raise AssertionError("GrowthState.mark_event_processed() must NOT be called by adapter")

        def mark_growth_applied(self, event_id):
            raise AssertionError("GrowthState.mark_growth_applied() must NOT be called by adapter")

        def reset(self):
            raise AssertionError("GrowthState.reset() must NOT be called by adapter")

    return _MockGrowthState()


def _make_proposal(
    proposal_id: str,
    status: str = "pending",
    topic: str = "test",
) -> Any:
    """构造一个 mock Proposal。"""

    class _MockProposal:
        def __init__(self):
            self.id = proposal_id
            self.status = status
            self.topic = topic

        def to_dict(self):
            return {
                "id": self.id,
                "status": self.status,
                "topic": self.topic,
            }

    return _MockProposal()


def _make_proposal_store(
    proposals: Optional[List[Any]] = None,
    raise_on_list: bool = False,
) -> Any:
    """构造一个 mock ProposalStore(只含 list() 与 get())。"""

    class _MockProposalStore:
        def __init__(self):
            self._items = list(proposals or [])
            self.save_called = 0
            self.update_called = 0
            self.delete_called = 0

        def list(self, status=None, limit=50, offset=0):
            if raise_on_list:
                raise RuntimeError("proposal_store.list failed")
            items = self._items
            if status is not None:
                items = [p for p in items if (p.status if hasattr(p, "status") else p.get("status")) == status]
            return list(items)

        def get(self, proposal_id):
            for p in self._items:
                pid = p.id if hasattr(p, "id") else p.get("id")
                if pid == proposal_id:
                    return p
            return None

        # 下面这些方法如果被调用,测试会失败
        def save(self, proposal):
            self.save_called += 1
            raise AssertionError("ProposalStore.save() must NOT be called by adapter")

        def update(self, proposal):
            self.update_called += 1
            raise AssertionError("ProposalStore.update() must NOT be called by adapter")

        def delete(self, proposal_id):
            self.delete_called += 1
            raise AssertionError("ProposalStore.delete() must NOT be called by adapter")

    return _MockProposalStore()


def _make_growth_engine() -> Any:
    """构造一个 mock GrowthEngine(只含 get_state())。"""

    class _MockGrowthEngine:
        def __init__(self):
            self.apply_called = 0
            self.apply_batch_called = 0
            self.reset_called = 0

        def get_state(self):
            return {
                "version": "0.2",
                "metrics": {"trust": 0.5, "closeness": 0.3},
            }

        def apply(self, event):
            self.apply_called += 1
            raise AssertionError("GrowthEngine.apply() must NOT be called by adapter")

        def apply_batch(self, events):
            self.apply_batch_called += 1
            raise AssertionError("GrowthEngine.apply_batch() must NOT be called by adapter")

        def apply_evaluated(self, evaluated_event):
            return None

        def reset(self):
            self.reset_called += 1
            raise AssertionError("GrowthEngine.reset() must NOT be called by adapter")

    return _MockGrowthEngine()


def _make_growth_evaluator() -> Any:
    """构造一个 mock GrowthEvaluator(只含 evaluate())。"""

    class _MockGrowthEvaluator:
        def evaluate(self, event, history=None):
            return {
                "growth_allowed": False,
                "growth_signal": "",
                "confidence": 0.0,
            }

    return _MockGrowthEvaluator()


# ============================================================
# 2. TestProtocol - 协议 / schema / 工厂
# ============================================================


class TestProtocol:
    def test_name_is_growth(self):
        assert GrowthRuntimeAdapter.name == "growth"
        assert GrowthRuntimeAdapter.name == STANDARD_ADAPTER_GROWTH

    def test_schema_version_is_1_0(self):
        assert GrowthRuntimeAdapter.schema_version == "1.0"
        assert GROWTH_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"

    def test_is_cycle_adapter(self):
        adapter = create_growth_runtime_adapter()
        # 触发 duck-type 校验
        assert is_cycle_adapter(adapter) is True

    def test_factory_returns_adapter(self):
        adapter = create_growth_runtime_adapter(user_id="test_user")
        assert isinstance(adapter, GrowthRuntimeAdapter)
        assert adapter._user_id == "test_user"

    def test_phases_constants(self):
        assert PHASE_C6_NAME == "phase_c6"
        assert PHASE_C6_VERSION == "1.0.0"
        assert SOURCE_NAME == "growth_runtime_adapter"
        assert PHASE_C6_STAGE_GROWTH_READ in ALL_PHASE_C6_STAGES
        assert PHASE_C6_STAGE_GROWTH_DEGRADED in ALL_PHASE_C6_STAGES

    def test_all_phase_c6_stages(self):
        assert len(ALL_PHASE_C6_STAGES) == 2


# ============================================================
# 3. TestReadOnly - 禁止副作用
# ============================================================


class TestReadOnly:
    def test_growth_state_save_not_called(self):
        state = _make_growth_state()
        store = _make_proposal_store()
        adapter = create_growth_runtime_adapter(
            growth_state=state,
            proposal_store=store,
        )
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        assert state.save_called == 0
        assert state.update_metrics_called == 0

    def test_growth_engine_apply_not_called(self):
        state = _make_growth_state()
        engine = _make_growth_engine()
        store = _make_proposal_store()
        adapter = create_growth_runtime_adapter(
            growth_state=state,
            growth_engine=engine,
            proposal_store=store,
        )
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        assert engine.apply_called == 0
        assert engine.apply_batch_called == 0
        assert engine.reset_called == 0

    def test_proposal_store_write_not_called(self):
        state = _make_growth_state()
        store = _make_proposal_store()
        adapter = create_growth_runtime_adapter(
            growth_state=state,
            proposal_store=store,
        )
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        assert store.save_called == 0
        assert store.update_called == 0
        assert store.delete_called == 0

    def test_read_growth_does_not_modify_state(self):
        state = _make_growth_state(metrics={"trust": 0.5, "closeness": 0.3})
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        before_metrics = dict(state._state["metrics"])
        for _ in range(3):
            adapter.read_growth()
        # 内部 state 字典必须保持原样
        assert dict(state._state["metrics"]) == before_metrics

    def test_state_deep_copy(self):
        state = _make_growth_state(metrics={"trust": 0.5})
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        out = adapter.read_growth()
        # 修改 output,不应影响 state
        out["current_metrics"]["trust"] = 999.0
        assert state._state["metrics"]["trust"] == 0.5

    def test_growth_output_file_mtime_unchanged(self, tmp_path):
        """真实 growth_state.json 的 mtime 不应被改变(若存在)。"""
        # 这个测试不依赖真实文件,只验证 mock 不调用 save
        state = _make_growth_state()
        store = _make_proposal_store()
        adapter = create_growth_runtime_adapter(
            growth_state=state,
            proposal_store=store,
        )
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_mtime"})
        adapter.process_cycle(ctx)
        # save_called 必须为 0
        assert state.save_called == 0


# ============================================================
# 4. TestProcessing - 正常处理 / 写入 ctx.growth_output
# ============================================================


class TestProcessing:
    def test_process_cycle_writes_ctx_growth_output(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        # 初始 growth_output 是空 list
        assert ctx.growth_output == []
        adapter.process_cycle(ctx)
        # 写入一个 dict
        assert len(ctx.growth_output) == 1
        out = ctx.growth_output[0]
        assert isinstance(out, dict)
        assert out["source"] == "growth_runtime_adapter"
        assert out["schema_version"] == "1.0"

    def test_process_cycle_returns_ctx(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        result = adapter.process_cycle(ctx)
        assert result is ctx

    def test_process_cycle_with_normal_state(self):
        state = _make_growth_state(
            metrics={"trust": 0.5, "closeness": 0.3, "safety": 0.7},
            behaviors={"active_care": True, "use_nickname": False},
            identities=["yuyi_001"],
            milestones=[{"event_id": "e1", "topic": "first_meet"}],
        )
        store = _make_proposal_store(proposals=[
            _make_proposal("p1", "pending"),
            _make_proposal("p2", "accepted"),
            _make_proposal("p3", "applied"),
        ])
        adapter = create_growth_runtime_adapter(
            growth_state=state,
            proposal_store=store,
        )
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        assert out["growth_available"] is True
        assert out["degraded"] is False
        assert out["current_metrics"]["trust"] == 0.5
        assert out["behaviors"]["active_care"] is True
        assert "yuyi_001" in out["identities"]
        assert len(out["milestones"]) == 1
        # pending_proposals 只包含 status=pending
        assert len(out["pending_proposals"]) == 1
        assert out["pending_proposals"][0]["id"] == "p1"
        # proposal_stats
        assert out["proposal_stats"]["total"] == 3
        assert out["proposal_stats"]["pending"] == 1
        assert out["proposal_stats"]["accepted"] == 1
        assert out["proposal_stats"]["applied"] == 1
        assert out["proposal_stats"]["rejected"] == 0
        assert out["error"] is None
        assert out["timestamp"] != ""

    def test_process_cycle_with_empty_state(self):
        # 空 state
        class _EmptyState:
            def get(self):
                return {}

            def get_metric(self, key):
                return 0.0

        adapter = create_growth_runtime_adapter(growth_state=_EmptyState())
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        # 空 state 时:growth_available 仍 True(因为 state 存在),degraded False
        assert out["growth_available"] is True
        assert out["degraded"] is False
        assert out["current_metrics"] == {}
        assert out["behaviors"] == {}
        assert out["identities"] == []
        assert out["milestones"] == []

    def test_process_cycle_missing_metrics_field(self):
        class _BadState:
            def get(self):
                return {"behaviors": {}, "identities": []}  # no metrics

            def get_metric(self, key):
                return 0.0

        adapter = create_growth_runtime_adapter(growth_state=_BadState())
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        assert out["current_metrics"] == {}

    def test_process_cycle_extreme_metric_values(self):
        # 极大/极小值
        state = _make_growth_state(metrics={
            "trust": 1e10,
            "closeness": -1e10,
            "safety": float("inf"),
        })
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        # 不应抛异常
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        assert isinstance(out["current_metrics"], dict)

    def test_process_cycle_invalid_types_in_state(self):
        # state 内部类型错误
        class _CorruptState:
            def get(self):
                return {
                    "metrics": "not a dict",   # 错误类型
                    "behaviors": 123,          # 错误类型
                    "identities": "not a list",
                    "milestones": None,
                }

            def get_metric(self, key):
                return 0.0

        adapter = create_growth_runtime_adapter(growth_state=_CorruptState())
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        # 不应抛异常
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        # 错误类型 → 空值,不应崩溃
        assert out["current_metrics"] == {}
        assert out["behaviors"] == {}
        assert out["identities"] == []

    def test_process_cycle_multiple_calls_accumulate(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        adapter.process_cycle(ctx)
        adapter.process_cycle(ctx)
        # 每次 append 一个 dict
        assert len(ctx.growth_output) == 3

    def test_read_growth_public_api(self):
        state = _make_growth_state(metrics={"trust": 0.7})
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        out = adapter.read_growth()
        assert out["growth_available"] is True
        assert out["current_metrics"]["trust"] == 0.7


# ============================================================
# 5. TestExceptionIsolation - 异常隔离
# ============================================================


class TestExceptionIsolation:
    def test_state_get_raises(self):
        class _BrokenState:
            def get(self):
                raise RuntimeError("state broken")

            def get_metric(self, key):
                return 0.0

        adapter = create_growth_runtime_adapter(growth_state=_BrokenState())
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        # 不应抛
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        assert out["degraded"] is True
        assert out["growth_available"] is False
        assert "state broken" in (out["error"] or "")

    def test_state_get_returns_non_dict(self):
        # state.get() 返回非 dict → 视为损坏
        # 直接注入已 attach 状态(避免 attach() 自建读取真实文件)
        class _BadState:
            def get(self):
                return "not a dict"

            def get_metric(self, key):
                return 0.0

        adapter = GrowthRuntimeAdapter(
            growth_state=_BadState(),
            proposal_store=None,
        )
        adapter._state = _BadState()  # 强制使用 mock
        adapter._attached = True
        # 阻止 store 自建读取真实 proposals.jsonl
        with mock.patch("src.growth.proposal_store.ProposalStore", side_effect=Exception("blocked")):
            ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
            adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        # 非 dict → degraded
        assert out["degraded"] is True
        assert out["growth_available"] is False

    def test_state_no_get_method(self):
        class _NoGetState:
            pass

        adapter = GrowthRuntimeAdapter(
            growth_state=_NoGetState(),
            proposal_store=None,
        )
        adapter._state = _NoGetState()
        adapter._attached = True
        with mock.patch("src.growth.proposal_store.ProposalStore", side_effect=Exception("blocked")):
            ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
            adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        assert out["degraded"] is True
        assert out["growth_available"] is False

    def test_state_is_none(self):
        # 阻止 attach() 时自建真实 GrowthState,保持 state=None
        adapter = GrowthRuntimeAdapter(growth_state=None, proposal_store=None)
        with mock.patch("src.growth.growth_state.GrowthState", side_effect=Exception("blocked")):
            with mock.patch("src.growth.proposal_store.ProposalStore", side_effect=Exception("blocked")):
                adapter.attach()
        # 此时 self._state 仍为 None
        assert adapter._state is None
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        assert out["degraded"] is True
        assert out["growth_available"] is False
        assert "not_attached" in (out["error"] or "")

    def test_proposal_store_list_raises(self):
        state = _make_growth_state()
        store = _make_proposal_store(raise_on_list=True)
        adapter = create_growth_runtime_adapter(
            growth_state=state,
            proposal_store=store,
        )
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        # state 仍能读,但 proposal 部分降级
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        # proposal 异常被吞,output 仍 OK 但 pending_proposals 为空
        assert out["growth_available"] is True
        assert out["pending_proposals"] == []
        assert out["proposal_stats"]["total"] == 0

    def test_proposal_store_is_none(self):
        # 阻止 attach() 时自建真实 ProposalStore
        state = _make_growth_state()
        adapter = GrowthRuntimeAdapter(
            growth_state=state,
            proposal_store=None,
        )
        with mock.patch("src.growth.proposal_store.ProposalStore", side_effect=Exception("blocked")):
            adapter.attach()
        # store 仍为 None
        assert adapter._proposal_store is None
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        out = ctx.growth_output[0]
        # store=None 时 proposal 字段为空,但不算降级
        assert out["growth_available"] is True
        assert out["pending_proposals"] == []
        assert out["proposal_stats"] == {
            "total": 0,
            "pending": 0,
            "accepted": 0,
            "rejected": 0,
            "applied": 0,
        }

    def test_process_cycle_with_non_ctx(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        result = adapter.process_cycle("not a ctx")
        # 不抛,返回原值
        assert result == "not a ctx"

    def test_multiple_exceptions_dont_break_adapter(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        # 多次调用,不应留下内部状态破坏
        for i in range(5):
            ctx = RuntimeCycleContext(input_event={"id": f"evt_{i}"})
            adapter.process_cycle(ctx)
        # process_count 正常累加
        assert adapter.process_count == 5


# ============================================================
# 6. TestHealthSnapshot - 健康检查 / 快照
# ============================================================


class TestHealthSnapshot:
    def test_health_check_no_state(self):
        adapter = create_growth_runtime_adapter(growth_state=None)
        adapter.attach()
        # 注入 state=None,attach() 会自建或失败
        h = adapter.health_check()
        assert h["adapter"] == "growth_runtime_adapter"
        assert h["schema_version"] == "1.0"
        assert h["status"] in ("healthy", "degraded")
        assert "process_count" in h
        assert "degraded_count" in h

    def test_health_check_with_state(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        h = adapter.health_check()
        assert h["state_available"] is True
        assert h["status"] == "healthy"

    def test_health_check_user_id(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state, user_id="alice")
        adapter.attach()
        h = adapter.health_check()
        assert h["user_id"] == "alice"

    def test_snapshot_no_calls(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        s = adapter.snapshot()
        assert s["name"] == "growth"
        assert s["schema_version"] == "1.0"
        assert s["attached"] is True
        assert s["state_available"] is True
        assert s["process_count"] == 0
        assert s["read_count"] == 0
        assert s["degraded_count"] == 0
        assert s["last_output"] is None
        assert s["last_snapshot"] is None

    def test_snapshot_after_read(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        adapter.read_growth()
        s = adapter.snapshot()
        assert s["read_count"] == 1
        assert s["last_output"] is not None
        assert s["last_snapshot"] is not None

    def test_snapshot_after_cycle(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        s = adapter.snapshot()
        assert s["process_count"] == 1
        assert s["last_output"] is not None

    def test_snapshot_after_degraded(self):
        class _BrokenState:
            def get(self):
                raise RuntimeError("boom")

            def get_metric(self, key):
                return 0.0

        adapter = create_growth_runtime_adapter(growth_state=_BrokenState())
        adapter.attach()
        ctx = RuntimeCycleContext(input_event={"id": "evt_1"})
        adapter.process_cycle(ctx)
        s = adapter.snapshot()
        assert s["degraded_count"] == 1
        assert s["available"] is False
        assert s["last_error"] is not None


# ============================================================
# 7. TestRuntimeIntegration - 与 RuntimeCycleOrchestrator 集成
# ============================================================


class TestRuntimeIntegration:
    def test_register_to_orchestrator(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        orch = RuntimeCycleOrchestrator()
        ok = orch.register_adapter(adapter, name="growth")
        assert ok is True
        assert orch.has_adapter("growth") is True
        assert orch.adapter_count == 1

    def test_orchestrator_step_5_growth(self):
        state = _make_growth_state(metrics={"trust": 0.5})
        adapter = create_growth_runtime_adapter(growth_state=state)
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(adapter, name="growth")
        ctx = orch.process_event({"id": "evt_1", "user_input": "hi"})
        assert len(ctx.growth_output) == 1
        out = ctx.growth_output[0]
        assert out["source"] == "growth_runtime_adapter"
        assert out["current_metrics"]["trust"] == 0.5
        # adapter_results 中应包含 growth 的结果
        assert "growth" in ctx.adapter_results
        assert ctx.adapter_results["growth"]["ok"] is True

    def test_orchestrator_no_growth_adapter(self):
        # 不注册 growth adapter,应跳过 step 5
        orch = RuntimeCycleOrchestrator()
        ctx = orch.process_event({"id": "evt_1", "user_input": "hi"})
        # growth_output 应为空(未注册 adapter)
        assert len(ctx.growth_output) == 0
        # adapter_results 标记 skipped
        assert "growth" in ctx.adapter_results
        assert ctx.adapter_results["growth"]["skipped"] is True

    def test_orchestrator_step_5_degraded(self):
        class _BrokenState:
            def get(self):
                raise RuntimeError("corrupt state")

            def get_metric(self, key):
                return 0.0

        adapter = create_growth_runtime_adapter(growth_state=_BrokenState())
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(adapter, name="growth")
        ctx = orch.process_event({"id": "evt_1", "user_input": "hi"})
        # 不应抛,output 写入但 degraded
        assert len(ctx.growth_output) == 1
        out = ctx.growth_output[0]
        assert out["degraded"] is True
        # orchestrator 不应标记 adapter 失败(因为 fail-soft)
        assert ctx.adapter_results["growth"]["ok"] is True

    def test_orchestrator_step_order_5_is_growth(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(adapter, name="growth")
        ctx = orch.process_event({"id": "evt_1", "user_input": "hi"})
        # step 5 是 growth,从 stage_log 可观察
        stages = ctx.get_stage_names()
        # 应该出现 "cycle_growth_completed"
        assert "cycle_growth_completed" in stages

    def test_safe_get_growth_adapter_summary_none(self):
        s = safe_get_growth_adapter_summary(None)
        assert s["name"] == "growth"
        assert s["available"] is False
        assert s["attached"] is False

    def test_safe_get_growth_adapter_summary_valid(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        s = safe_get_growth_adapter_summary(adapter)
        assert s["name"] == "growth"
        assert s["schema_version"] == "1.0"


# ============================================================
# 8. TestThreadSafety - 线程安全
# ============================================================


class TestThreadSafety:
    def test_concurrent_process_cycle(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        results: List[Any] = []
        errors: List[Exception] = []

        def worker():
            try:
                ctx = RuntimeCycleContext(input_event={"id": "evt_thread"})
                out = adapter.process_cycle(ctx)
                results.append(out)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(results) == 10
        # process_count 应等于 10
        assert adapter.process_count == 10


# ============================================================
# 9. TestOutputSchema - 输出 schema 严格匹配
# ============================================================


class TestOutputSchema:
    """验证 output 字典包含规范要求的所有字段。"""

    REQUIRED_FIELDS = [
        "growth_available",
        "degraded",
        "source",
        "schema_version",
        "current_metrics",
        "behaviors",
        "identities",
        "milestones",
        "recent_records",
        "growth_history",
        "pending_proposals",
        "proposal_stats",
        "growth_signals",
        "error",
        "timestamp",
    ]

    def test_normal_output_has_all_fields(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        out = adapter.read_growth()
        for f in self.REQUIRED_FIELDS:
            assert f in out, f"Missing field: {f}"

    def test_degraded_output_has_all_fields(self):
        # 阻止 attach() 时自建真实数据,保持 state=None
        adapter = GrowthRuntimeAdapter(growth_state=None, proposal_store=None)
        with mock.patch("src.growth.growth_state.GrowthState", side_effect=Exception("blocked")):
            with mock.patch("src.growth.proposal_store.ProposalStore", side_effect=Exception("blocked")):
                adapter.attach()
        out = adapter.read_growth()
        for f in self.REQUIRED_FIELDS:
            assert f in out, f"Missing field in degraded: {f}"
        assert out["degraded"] is True
        assert out["growth_available"] is False

    def test_output_source_is_correct(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        out = adapter.read_growth()
        assert out["source"] == "growth_runtime_adapter"
        assert out["source"] == SOURCE_NAME

    def test_output_schema_version_is_correct(self):
        state = _make_growth_state()
        adapter = create_growth_runtime_adapter(growth_state=state)
        adapter.attach()
        out = adapter.read_growth()
        assert out["schema_version"] == "1.0"
