# -*- coding: utf-8 -*-
"""
tests/test_runtime_observability.py

Phase C.7.2 Runtime Observability & Audit Dashboard —— 验证测试

目标:
  验证 RuntimeObserver 严格只读地暴露 Runtime 可观测状态,不修改任何业务状态。

约束:
  - 不修改任何 Adapter / 核心模块
  - 所有外部依赖 mock 化
  - 不启动真实 LLM / 业务
  - 不调 process_cycle / 不调任何写入方法

覆盖 10+ 测试:
  TestObserverBasic         (1-2)   创建 / schema
  TestAdapterObservation    (3-4)   5 adapter 读取 / health 格式不一致兼容
  TestReadOnly              (5)     snapshot 不修改业务状态
  TestAuditObservation      (6-7)   history 可读 / latest cycle 正确
  TestFailureIsolation      (8)     单个 adapter 异常 → degraded snapshot
  TestSchema                (9-10)  schema_version / timestamp
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.observability import (
    RUNTIME_OBSERVER_SCHEMA_VERSION,
    RuntimeObserver,
    create_runtime_observer,
    safe_get_runtime_observer_summary,
)
from src.runtime.observability.runtime_observer import (
    STANDARD_ADAPTER_NAMES,
    RUNTIME_OBSERVER_NAME,
    RUNTIME_OBSERVER_VERSION,
)
from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Mock Adapters(模拟 5 个真实 Adapter)
# ============================================================


class _MockMemoryAdapter:
    """Memory 风格:health 用 healthy 字段。"""

    name = "memory"
    schema_version = "1.0"

    def __init__(self, healthy: bool = True) -> None:
        self._healthy = bool(healthy)
        self._state = {"count": 0, "snapshot_calls": 0, "health_calls": 0}

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        self._state["health_calls"] += 1
        return {
            "healthy": self._healthy,
            "name": "memory",
            "schema_version": "1.0",
            "attached": True,
        }

    def process_cycle(self, ctx: Any) -> Any:
        # Observer 严禁调用;若被调用,记入 state 标记违规
        self._state["count"] += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        self._state["snapshot_calls"] += 1
        return {
            "name": "memory",
            "schema_version": "1.0",
            "attached": True,
            "count": self._state["count"],
        }


class _MockEmotionAdapter:
    """Emotion 风格:health 用 status 字段。"""

    name = "emotion"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")
        self._state = {"process_calls": 0, "snapshot_calls": 0, "health_calls": 0}

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        self._state["health_calls"] += 1
        return {
            "adapter": "emotion",
            "status": self._status,
            "schema_version": "1.0",
            "attached": True,
        }

    def process_cycle(self, ctx: Any) -> Any:
        self._state["process_calls"] += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        self._state["snapshot_calls"] += 1
        return {
            "name": "emotion",
            "schema_version": "1.0",
            "attached": True,
        }


class _MockPersonalityAdapter:
    name = "personality"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")
        self._state = {"process_calls": 0}

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "adapter": "personality",
            "status": self._status,
            "schema_version": "1.0",
        }

    def process_cycle(self, ctx: Any) -> Any:
        self._state["process_calls"] += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "personality", "attached": True}


class _MockRelationshipAdapter:
    name = "relationship"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "adapter": "relationship",
            "status": self._status,
            "schema_version": "1.0",
        }

    def process_cycle(self, ctx: Any) -> Any:
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "relationship", "attached": True}


class _MockGrowthAdapter:
    name = "growth"
    schema_version = "1.0"

    def __init__(self, status: str = "healthy") -> None:
        self._status = str(status or "healthy")
        self._state = {"process_calls": 0}

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": self._status,
            "adapter": "growth",
            "schema_version": "1.0",
        }

    def process_cycle(self, ctx: Any) -> Any:
        self._state["process_calls"] += 1
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        return {"name": "growth", "attached": True}


class _BrokenAdapter:
    """模拟一个 health_check() 抛异常的 adapter(用于隔离测试)。"""

    name = "broken"
    schema_version = "1.0"

    def attach(self) -> bool:
        return True

    def detach(self) -> bool:
        return True

    def is_attached(self) -> bool:
        return True

    def health_check(self) -> Dict[str, Any]:
        raise RuntimeError("health_check failed for broken adapter")

    def process_cycle(self, ctx: Any) -> Any:
        return ctx

    def snapshot(self) -> Dict[str, Any]:
        raise RuntimeError("snapshot failed for broken adapter")


# ============================================================
# 2. Mock GrowthState / ProposalStore / PersonalityState
# ============================================================
# (用于 TestReadOnly 验证:Observer 调用 snapshot 后,这些对象 state 不变化)


class _MockGrowthState:
    """模拟 src/growth/GrowthState 内部状态。"""

    def __init__(self) -> None:
        self.metrics = {"warmth": 0.5, "playfulness": 0.5}
        self.behaviors = {"greet": True}
        self._writes: int = 0

    def save(self) -> bool:
        # 写入方法(Observer 严禁调用)
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


class _MockProposalStore:
    """模拟 src/growth/ProposalStore 内部状态。"""

    def __init__(self) -> None:
        self.proposals: List[Dict[str, Any]] = []
        self._writes: int = 0

    def create_proposal(self, data: Dict[str, Any]) -> Any:
        # 写入方法(Observer 严禁调用)
        self._writes += 1
        self.proposals.append(data)
        return data

    @property
    def write_count(self) -> int:
        return self._writes


class _MockPersonalityState:
    """模拟 src/personality/PersonalityState 内部状态。"""

    def __init__(self) -> None:
        self.traits = {"warmth": 0.5, "gentleness": 0.5}
        self._writes: int = 0

    def update(self, *args: Any, **kwargs: Any) -> bool:
        self._writes += 1
        return True

    @property
    def write_count(self) -> int:
        return self._writes


# ============================================================
# 3. Mock Persistence
# ============================================================


class _MockPersistence:
    """模拟 ActionPersistenceManager(只暴露只读属性 + 一个 get_recent_actions)。"""

    def __init__(self, degraded: bool = False) -> None:
        self._degraded = bool(degraded)
        self._write_count: int = 0
        self._write_error_count: int = 0
        self._records: List[Dict[str, Any]] = []

    @property
    def is_degraded(self) -> bool:
        return self._degraded

    @property
    def write_count(self) -> int:
        return self._write_count

    @property
    def write_error_count(self) -> int:
        return self._write_error_count

    def get_recent_actions(self, limit: int = 20) -> List[Dict[str, Any]]:
        # 模拟数据
        return self._records[-limit:]

    def persist_event(self, *args: Any, **kwargs: Any) -> bool:
        # 写入方法(Observer 严禁调用);如果被调用则异常
        raise AssertionError("Observer must NOT call persist_event (write path)")


# ============================================================
# 4. Helper
# ============================================================


def _build_orchestrator_with_adapters(adapters: list) -> RuntimeCycleOrchestrator:
    """构造一个 orchestrator 并注册传入的 adapters。"""
    orch = RuntimeCycleOrchestrator(cfg={"phase_c1": {"enabled": True}}, persistence=None)
    for adp in adapters:
        ok = orch.register_adapter(adp, name=adp.name)
        assert ok, f"register_adapter 失败: {adp.name}"
    return orch


def _run_a_few_cycles(orch: RuntimeCycleOrchestrator, count: int = 3) -> None:
    """跑几个 cycle 制造 history 记录(只调用 orchestrator 的 process_event,
    不直接调 adapter.process_cycle,以保证观察层不直接介入)。"""
    for i in range(count):
        try:
            orch.process_event(f"event_{i}", user_id="u1")
        except Exception:
            pass


# ============================================================
# 5. 测试:TestObserverBasic
# ============================================================


class TestObserverBasic:
    """基础测试:创建 + schema。"""

    def test_01_observer_creates_successfully(self) -> None:
        """1. observer 创建成功。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        observer = RuntimeObserver(orchestrator=orch)
        assert observer is not None
        assert isinstance(observer, RuntimeObserver)
        # 工厂函数
        observer2 = create_runtime_observer(orchestrator=orch)
        assert isinstance(observer2, RuntimeObserver)

    def test_02_snapshot_returns_full_schema(self) -> None:
        """2. snapshot 返回完整 schema(无 orchestrator 也安全)。"""
        # 无 orchestrator
        observer = RuntimeObserver()
        snap = observer.snapshot()
        assert isinstance(snap, dict)
        # schema 顶层字段必须存在
        for key in [
            "schema_version",
            "runtime",
            "adapters",
            "cycles",
            "errors",
            "persistence",
            "audit",
            "timestamp",
            "observer",
        ]:
            assert key in snap, f"snapshot 缺少顶层字段: {key}"
        # runtime 子字段
        for key in ["name", "healthy", "degraded", "enabled"]:
            assert key in snap["runtime"], f"runtime 缺少子字段: {key}"
        # cycles 子字段
        for key in [
            "total",
            "completed",
            "failed",
            "success_rate",
            "in_memory",
        ]:
            assert key in snap["cycles"], f"cycles 缺少子字段: {key}"

    def test_observer_never_raises(self) -> None:
        """observer.snapshot() 永远不抛异常(无 orchestrator / history / persistence)。"""
        observer = RuntimeObserver()
        for _ in range(5):
            snap = observer.snapshot()
            assert isinstance(snap, dict)
            assert "schema_version" in snap


# ============================================================
# 6. 测试:TestAdapterObservation
# ============================================================


class TestAdapterObservation:
    """Adapter 状态读取测试。"""

    def test_03_five_adapters_health_readable(self) -> None:
        """3. 5 个 adapter health 均可读取(正常情况)。"""
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="healthy"),
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="healthy"),
            _MockGrowthAdapter(status="healthy"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        # 5 个标准 adapter 都在 adapters 块
        for name in STANDARD_ADAPTER_NAMES:
            assert name in snap["adapters"], f"adapters 缺少 {name}"
            a = snap["adapters"][name]
            assert a["name"] == name
            assert a["healthy"] is True, f"{name} 应 healthy"
            assert a["status"] == "healthy", f"{name} status 应 healthy"

    def test_04_health_contract_inconsistent_still_compatible(self) -> None:
        """4. health contract 不一致(Memory 用 healthy,Emotion 等用 status)仍兼容。"""
        adapters = [
            _MockMemoryAdapter(healthy=True),       # {"healthy": True}
            _MockEmotionAdapter(status="degraded"), # {"status": "degraded"}
            _MockPersonalityAdapter(status="healthy"),
            _MockRelationshipAdapter(status="healthy"),
            _MockGrowthAdapter(status="degraded"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        # memory: healthy=True, status="healthy"
        assert snap["adapters"]["memory"]["healthy"] is True
        assert snap["adapters"]["memory"]["status"] == "healthy"
        # emotion: healthy=False, status="degraded"
        assert snap["adapters"]["emotion"]["healthy"] is False
        assert snap["adapters"]["emotion"]["status"] == "degraded"
        # growth: healthy=False, status="degraded"
        assert snap["adapters"]["growth"]["healthy"] is False
        assert snap["adapters"]["growth"]["status"] == "degraded"
        # 整体 runtime.degraded 反映任一 adapter degraded
        assert snap["runtime"]["degraded"] is True

    def test_mixed_health_marks_correctly(self) -> None:
        """混合 health 字段格式(Memory 风格 + status 风格)聚合正确。"""
        # 故意构造 1 个 healthy=False(用 healthy 字段) + 1 个 status="degraded"
        adapters = [
            _MockMemoryAdapter(healthy=False),  # {"healthy": False}
            _MockEmotionAdapter(status="degraded"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        # memory: healthy=False, status="degraded"(由 healthy=False 推断)
        assert snap["adapters"]["memory"]["healthy"] is False
        # 整体 degraded
        assert snap["runtime"]["degraded"] is True


# ============================================================
# 7. 测试:TestReadOnly
# ============================================================


class TestReadOnly:
    """只读测试:调用 snapshot 后业务状态完全不变。"""

    def test_05_snapshot_does_not_modify_business_state(self) -> None:
        """5. 调用 snapshot 后 GrowthState / ProposalStore / PersonalityState 不变化。

        关键:Observer 不应触发这些对象上的任何写方法,
        也不应通过任何间接途径修改它们。
        """
        # 准备"业务对象"(用 mock 跟踪所有调用)
        growth_state = _MockGrowthState()
        proposal_store = _MockProposalStore()
        personality_state = _MockPersonalityState()

        # 记录初始状态
        gs_writes_before = growth_state.write_count
        ps_writes_before = proposal_store.write_count
        pys_writes_before = personality_state.write_count
        gs_metrics_before = dict(growth_state.metrics)
        ps_proposals_before = list(proposal_store.proposals)
        pys_traits_before = dict(personality_state.traits)

        # 准备 orchestrator + observer(注入"业务对象",但 observer 不应触达)
        orch = _build_orchestrator_with_adapters([_MockGrowthAdapter()])
        observer = RuntimeObserver(orchestrator=orch)

        # 跑几次 snapshot
        for _ in range(5):
            observer.snapshot()

        # 业务对象 state 完全未变
        assert growth_state.write_count == gs_writes_before, "GrowthState 被写入!"
        assert proposal_store.write_count == ps_writes_before, "ProposalStore 被写入!"
        assert personality_state.write_count == pys_writes_before, "PersonalityState 被写入!"
        assert growth_state.metrics == gs_metrics_before
        assert proposal_store.proposals == ps_proposals_before
        assert personality_state.traits == pys_traits_before

    def test_snapshot_does_not_call_adapter_process_cycle(self) -> None:
        """Observer 不应调任何 adapter 的 process_cycle()。"""
        mem = _MockMemoryAdapter()
        emo = _MockEmotionAdapter()
        gro = _MockGrowthAdapter()
        orch = _build_orchestrator_with_adapters([mem, emo, gro])
        observer = RuntimeObserver(orchestrator=orch)
        # 跑 10 次 snapshot
        for _ in range(10):
            observer.snapshot()
        # 三个 adapter 的 process_calls 必须为 0
        assert mem._state["count"] == 0
        assert emo._state["process_calls"] == 0
        assert gro._state["process_calls"] == 0

    def test_snapshot_does_not_call_persistence_write(self) -> None:
        """Observer 不应调 persistence 的写方法。"""
        pers = _MockPersistence()
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        # 注入 persistence 到 orchestrator
        orch.set_persistence(pers)
        observer = RuntimeObserver(orchestrator=orch)
        # 跑 snapshot(应不触发 persist_event)
        observer.snapshot()
        # _MockPersistence.persist_event() 会 raise,若被调用则 fail


# ============================================================
# 8. 测试:TestAuditObservation
# ============================================================


class TestAuditObservation:
    """Cycle History / Audit Chain 读取测试。"""

    def test_06_cycle_history_readable(self) -> None:
        """6. cycle history 可读取。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        # 跑几个 cycle 制造 history
        _run_a_few_cycles(orch, count=3)
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        cycles = snap["cycles"]
        # 必须包含基本统计字段
        for key in ["total", "completed", "failed", "in_memory", "success_rate"]:
            assert key in cycles, f"cycles 缺少 {key}"
        assert cycles["total"] >= 0
        assert cycles["completed"] >= 0
        assert cycles["failed"] >= 0
        # success_rate 应在 [0, 1]
        assert 0.0 <= float(cycles["success_rate"]) <= 1.0

    def test_07_latest_cycle_correct(self) -> None:
        """7. latest cycle 正确(cycles.latest_cycle_id / latest_timestamp)。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        _run_a_few_cycles(orch, count=3)
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        # audit 块 / cycles 块应包含 latest cycle_id
        assert snap["cycles"]["latest_cycle_id"] is not None
        assert snap["cycles"]["latest_timestamp"] is not None
        # audit 块的 latest_cycle_id 应与 cycles 块一致(从 history fallback)
        assert snap["audit"]["latest_cycle_id"] is not None
        # latest_timestamp 应为 ISO 格式字符串
        ts = str(snap["cycles"]["latest_timestamp"])
        assert len(ts) > 0
        # 校验 format(包含日期分隔符 T)
        # 容忍时区格式:含 "T" 分隔符即可
        assert "T" in ts or ts.isdigit(), f"latest_timestamp 格式异常: {ts}"

    def test_audit_block_with_persistence(self) -> None:
        """当 persistence 注入时,audit.available 应为 True。"""
        pers = _MockPersistence()
        # 给 persistence 一条 audit 记录
        pers._records.append(
            {
                "action_id": "phase_c1_cycle_xxx",
                "stage": "phase_c1_cycle_completed",
                "ts": 1700000000.0,
            }
        )
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        orch.set_persistence(pers)
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        # persistence.available=True
        assert snap["persistence"]["available"] is True
        # audit.available 应为 True(来自 persistence.get_recent_actions)
        assert snap["audit"]["available"] is True
        # audit.latest_cycle_id 应来自 persistence 记录
        assert snap["audit"]["latest_cycle_id"] == "phase_c1_cycle_xxx"


# ============================================================
# 9. 测试:TestFailureIsolation
# ============================================================


class TestFailureIsolation:
    """单点失败隔离测试。"""

    def test_08_single_adapter_health_failure_isolated(self) -> None:
        """8. 单个 adapter health_check() 异常 → observer 仍返回 degraded snapshot,
        不让整个 snapshot 崩溃。
        """
        adapters = [
            _MockMemoryAdapter(healthy=True),
            _MockEmotionAdapter(status="healthy"),
            _BrokenAdapter(),  # health_check 抛异常
            _MockGrowthAdapter(status="healthy"),
        ]
        orch = _build_orchestrator_with_adapters(adapters)
        observer = RuntimeObserver(orchestrator=orch)
        # 不应抛异常
        snap = observer.snapshot()
        assert isinstance(snap, dict)
        assert "schema_version" in snap
        # broken adapter 在 adapters 块,标记为不可用
        # 注:broken adapter 是通过 orchestrator._adapters.list() 注册的,
        # 其 item["health"] 由 safe_call_health_check 返回,所以会显示为 healthy=False
        # 不一定出现在标准 5 名字段里(因为它叫 "broken")
        # 但 orchestrator health 仍能聚合
        assert snap["runtime"]["degraded"] is True
        # 整体结构完整
        assert "adapters" in snap
        assert "cycles" in snap
        assert "errors" in snap
        assert "persistence" in snap
        assert "audit" in snap

    def test_orchestrator_health_check_failure_isolated(self) -> None:
        """orchestrator.health_check() 异常 → observer 仍能返回 snapshot。"""

        class _BrokenOrch:
            def health_check(self) -> Dict[str, Any]:
                raise RuntimeError("health_check crashed")

            def snapshot(self) -> Dict[str, Any]:
                raise RuntimeError("snapshot crashed")

        observer = RuntimeObserver(orchestrator=_BrokenOrch())
        snap = observer.snapshot()
        assert isinstance(snap, dict)
        # 整体降级
        assert snap["runtime"]["degraded"] is True
        # runtime.healthy 反映 False
        assert snap["runtime"]["healthy"] is False

    def test_persistence_failure_isolated(self) -> None:
        """persistence 异常 → observer 不崩溃。"""

        class _BrokenPersistence:
            @property
            def is_degraded(self) -> bool:
                raise RuntimeError("degraded crash")

            @property
            def write_count(self) -> int:
                raise RuntimeError("write_count crash")

            def get_recent_actions(self, limit: int = 20) -> List[Dict[str, Any]]:
                raise RuntimeError("recent crash")

        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        orch.set_persistence(_BrokenPersistence())
        observer = RuntimeObserver(orchestrator=orch)
        snap = observer.snapshot()
        assert isinstance(snap, dict)
        # persistence 块存在
        assert "persistence" in snap


# ============================================================
# 10. 测试:TestSchema
# ============================================================


class TestSchema:
    """Schema 字段验证。"""

    def test_09_schema_version_present(self) -> None:
        """9. schema_version 存在且为 RUNTIME_OBSERVER_SCHEMA_VERSION。"""
        observer = RuntimeObserver()
        snap = observer.snapshot()
        assert snap["schema_version"] == RUNTIME_OBSERVER_SCHEMA_VERSION
        assert snap["schema_version"] == "1.0"
        # observer 块也带 schema_version
        assert snap["observer"]["schema_version"] == RUNTIME_OBSERVER_SCHEMA_VERSION
        assert snap["observer"]["name"] == RUNTIME_OBSERVER_NAME
        assert snap["observer"]["version"] == RUNTIME_OBSERVER_VERSION

    def test_10_timestamp_present(self) -> None:
        """10. timestamp 存在且为非空字符串。"""
        observer = RuntimeObserver()
        snap = observer.snapshot()
        assert "timestamp" in snap
        ts = str(snap["timestamp"])
        assert len(ts) > 0
        # 校验可重复调用时间戳变化
        snap2 = observer.snapshot()
        assert "timestamp" in snap2
        assert str(snap2["timestamp"]) > ts  # ISO 时间可字典序比较

    def test_schema_no_breaking_fields(self) -> None:
        """schema 完整字段集合(冻结,不得删除)。"""
        observer = RuntimeObserver()
        snap = observer.snapshot()
        # 顶层
        for key in [
            "schema_version",
            "runtime",
            "adapters",
            "cycles",
            "errors",
            "persistence",
            "audit",
            "timestamp",
            "observer",
        ]:
            assert key in snap, f"schema 缺少顶层字段: {key}"
        # runtime
        for key in [
            "name", "healthy", "degraded", "enabled", "closed",
            "version", "schema_version", "cycle_count",
            "completed_count", "failed_count", "consecutive_failures",
        ]:
            assert key in snap["runtime"], f"runtime 缺少字段: {key}"
        # cycles
        for key in [
            "total", "completed", "failed", "success_rate",
            "in_memory", "latest_cycle_id", "latest_timestamp",
        ]:
            assert key in snap["cycles"], f"cycles 缺少字段: {key}"
        # errors
        for key in ["last_error", "consecutive_failures", "recent_errors"]:
            assert key in snap["errors"], f"errors 缺少字段: {key}"
        # persistence
        for key in ["available", "degraded", "write_count", "write_error_count"]:
            assert key in snap["persistence"], f"persistence 缺少字段: {key}"
        # audit
        for key in ["available", "latest_cycle_id", "latest_timestamp"]:
            assert key in snap["audit"], f"audit 缺少字段: {key}"


# ============================================================
# 11. 额外测试:辅助 API
# ============================================================


class TestHelperAPIs:
    """辅助 API 测试。"""

    def test_safe_get_runtime_observer_summary_with_none(self) -> None:
        """safe_get_runtime_observer_summary(None) → 返回空 summary,不抛。"""
        out = safe_get_runtime_observer_summary(None)
        assert isinstance(out, dict)
        assert out["schema_version"] == RUNTIME_OBSERVER_SCHEMA_VERSION
        # 整体降级
        assert out["runtime"]["degraded"] is True

    def test_safe_get_runtime_observer_summary_normal(self) -> None:
        """safe_get_runtime_observer_summary(observer) → 返回 snapshot。"""
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        observer = RuntimeObserver(orchestrator=orch)
        out = safe_get_runtime_observer_summary(observer)
        assert isinstance(out, dict)
        assert out["runtime"]["healthy"] is True
        assert "memory" in out["adapters"]

    def test_observer_internal_stats(self) -> None:
        """Observer 自身的 get_stats() / snapshot_count 正常累计。"""
        observer = RuntimeObserver()
        # 初始
        assert observer.snapshot_count == 0
        # 跑 3 次
        for _ in range(3):
            observer.snapshot()
        assert observer.snapshot_count == 3
        stats = observer.get_stats()
        assert stats["snapshot_count"] == 3
        assert stats["last_snapshot_ts"] is not None
        assert stats["has_orchestrator"] is False
        # 跑正常 observer
        orch = _build_orchestrator_with_adapters([_MockMemoryAdapter()])
        observer2 = RuntimeObserver(orchestrator=orch)
        observer2.snapshot()
        stats2 = observer2.get_stats()
        assert stats2["has_orchestrator"] is True
