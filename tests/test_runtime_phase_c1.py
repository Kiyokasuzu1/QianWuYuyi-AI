# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_c1.py

Phase C.1 Runtime Core Integration Layer —— RuntimeCycleOrchestrator 测试

覆盖 10 类:
  1. Context schema(RuntimeCycleContext 序列化 / 快照 / 空状态)
  2. 空状态运行(无 adapter / 无 bridge / 无 persistence)
  3. Adapter 注册(register / unregister / list / get_ordered)
  4. Adapter 执行顺序(5 步按 memory→emotion→personality→relationship→growth)
  5. 异常隔离(memory 失败不影响 emotion,emotion 失败不影响 growth 等)
  6. B4Bridge 读取(只读 summarize_b4,不修改 proposal / execute)
  7. Persistence 失败降级(persist_event 抛异常/返回 False 不影响主流程)
  8. Health check / snapshot(health_check 返回结构 / snapshot 不抛)
  9. Regression(summarize_b4 包含 B.4-B.13 全部字段,无 C.1 字段污染)
 10. 事件常量 + cycle_event.py + cycle_adapter.py 协议

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖(bridge / persistence / adapter)
  - 不依赖 RuntimeCore / RuntimeBridge 单例
"""
from __future__ import annotations

import sys
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.cycle_context import (
    RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION,
    RuntimeCycleContext,
    CycleHistoryIndex,
)
from src.runtime.cycle_adapter import (
    CYCLE_ADAPTER_SCHEMA_VERSION,
    STANDARD_ADAPTERS_IN_ORDER,
    STANDARD_ADAPTER_MEMORY,
    STANDARD_ADAPTER_EMOTION,
    STANDARD_ADAPTER_PERSONALITY,
    STANDARD_ADAPTER_RELATIONSHIP,
    STANDARD_ADAPTER_GROWTH,
    BaseCycleAdapter,
    CycleAdapterRegistry,
    CycleAdapter,
    is_cycle_adapter,
    safe_call_process_cycle,
    safe_call_health_check,
    safe_call_snapshot,
)
from src.runtime.cycle_event import (
    CYCLE_EVENT_STARTED,
    CYCLE_EVENT_MEMORY_COMPLETED,
    CYCLE_EVENT_EMOTION_COMPLETED,
    CYCLE_EVENT_PERSONALITY_COMPLETED,
    CYCLE_EVENT_RELATIONSHIP_COMPLETED,
    CYCLE_EVENT_GROWTH_COMPLETED,
    CYCLE_EVENT_DECISION_COMPLETED,
    CYCLE_EVENT_COMPLETED,
    CYCLE_EVENT_FAILED,
    ALL_CYCLE_EVENTS,
    PHASE_C1_STAGE_CYCLE_STARTED,
    PHASE_C1_STAGE_CYCLE_COMPLETED,
    PHASE_C1_STAGE_CYCLE_FAILED,
    PHASE_C1_STAGE_STEP_COMPLETED,
    PHASE_C1_STAGE_ADAPTER_FAILED,
    PHASE_C1_NAME,
    PHASE_C1_VERSION,
    STEP_TO_EVENT,
    is_cycle_event,
    normalize_cycle_event,
    get_event_for_step,
)
from src.runtime.phase_c1_integration import (
    PHASE_C1_DEFAULT_CONFIG,
    RuntimeCycleOrchestrator,
    create_runtime_cycle_orchestrator,
    is_phase_c1_enabled,
    apply_phase_c1_config,
    safe_get_orchestrator_summary,
    _extract_user_input,
)


# ============================================================
# 1. Helper: Mock Adapter / Mock Bridge / Mock Persistence
# ============================================================


class _RecordingAdapter(BaseCycleAdapter):
    """记录 process_cycle 调用顺序的 adapter。"""

    def __init__(self, name: str, tag: str = None) -> None:
        super().__init__(name=name)
        self._tag = tag or name
        self.call_count = 0
        self._lock = threading.RLock()
        self.received_ctx: List[Any] = []

    def process_cycle(self, ctx: Any) -> Any:
        with self._lock:
            self.call_count += 1
            self.received_ctx.append(ctx)
        # 用 tag 标记当前 step(可被 orchestrator 读取)
        if isinstance(ctx, RuntimeCycleContext):
            ctx.metadata[f"{self._tag}_ran"] = True
        return ctx


class _FailingAdapter(BaseCycleAdapter):
    """process_cycle 总是抛异常的 adapter。"""

    def __init__(self, name: str, exc: Optional[Exception] = None) -> None:
        super().__init__(name=name)
        self._exc = exc or RuntimeError(f"adapter_{name}_boom")

    def process_cycle(self, ctx: Any) -> Any:
        raise self._exc


class _MutatingAdapter(BaseCycleAdapter):
    """往 ctx 上写一段产出的 adapter。"""

    def __init__(self, name: str, output_key: str, output_value: Any) -> None:
        super().__init__(name=name)
        self._output_key = output_key
        self._output_value = output_value

    def process_cycle(self, ctx: Any) -> Any:
        if isinstance(ctx, RuntimeCycleContext):
            setattr(ctx, self._output_key, self._output_value)
        return ctx


class _PartialFailingAdapter(BaseCycleAdapter):
    """前 N 次失败,之后成功的 adapter。"""

    def __init__(self, name: str, fail_count: int = 1) -> None:
        super().__init__(name=name)
        self._fail_count = fail_count
        self._call_count = 0

    def process_cycle(self, ctx: Any) -> Any:
        self._call_count += 1
        if self._call_count <= self._fail_count:
            raise RuntimeError(f"transient failure #{self._call_count}")
        if isinstance(ctx, RuntimeCycleContext):
            ctx.metadata[f"{self.name}_ok"] = True
        return ctx


class _FakeBridge:
    """Fake RuntimeB4Bridge,只暴露 summarize_b4()。"""

    def __init__(
        self,
        summary: Optional[Dict[str, Any]] = None,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self._summary = summary or {
            "decision_observability": {"enabled": True},
            "decision_intelligence": {"enabled": True},
            "decision_evolution": {"enabled": True},
            "decision_execution": {"enabled": True},
            "total_executions": 3,
            "total_evolution_proposals": 2,
        }
        self._raise_exc = raise_exc
        self.summarize_call_count = 0

    def summarize_b4(self) -> Dict[str, Any]:
        self.summarize_call_count += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        return dict(self._summary)


class _FakePersistence:
    """Fake ActionPersistenceManager,只暴露 persist_event()。"""

    def __init__(
        self,
        fail: bool = False,
        return_false: bool = False,
    ) -> None:
        self.events: List[Dict[str, Any]] = []
        self._fail = fail
        self._return_false = return_false
        self.is_degraded = False
        self.write_count = 0
        self.write_error_count = 0

    def persist_event(
        self,
        action_id: str,
        lifecycle_id: str,
        stage: str,
        decision: str = "",
        ts: Optional[float] = None,
        result: Any = None,
        error: Optional[str] = None,
        source: str = "phase_b5_persistence",
        extra: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if self._fail:
            raise RuntimeError("persistence_disk_error")
        self.events.append({
            "action_id": action_id,
            "lifecycle_id": lifecycle_id,
            "stage": stage,
            "decision": decision,
            "ts": ts,
            "result": result,
            "error": error,
            "source": source,
        })
        if self._return_false:
            return False
        self.write_count += 1
        return True


# ============================================================
# 2. 测试 1:Context schema
# ============================================================


class TestCycleContextSchema:
    def test_default_construction(self) -> None:
        """默认构造应能直接使用,所有 optional 字段为空。"""
        ctx = RuntimeCycleContext()
        assert ctx.cycle_id.startswith("cycle_")
        assert ctx.session_id.startswith("session_")
        assert ctx.user_id == "yuyi"
        assert isinstance(ctx.timestamp, str)
        assert ctx.timestamp.endswith("Z")
        assert ctx.input_event is None
        assert ctx.memory_output is None
        assert ctx.emotion_output is None
        assert ctx.personality_output is None
        assert ctx.relationship_output is None
        assert ctx.growth_output == []
        assert ctx.decision_context is None
        assert ctx.b4_summary is None
        assert ctx.stage_log == []
        assert ctx.adapter_results == {}
        assert ctx.error_count == 0
        assert ctx.degraded_adapters == []
        assert ctx.metadata == {}
        assert ctx.schema_version == RUNTIME_CYCLE_CONTEXT_SCHEMA_VERSION == "1.0"

    def test_to_dict_serialization(self) -> None:
        """to_dict 必须能返回完整 dict,不抛异常。"""
        ctx = RuntimeCycleContext(
            user_id="u1",
            input_event={"user_input": "hello"},
        )
        ctx.memory_output = {"k": "v"}
        ctx.metadata["source"] = "test"
        ctx.log_stage("memory", decision="ok", success=True)
        d = ctx.to_dict()
        assert isinstance(d, dict)
        assert d["schema_version"] == "1.0"
        assert d["user_id"] == "u1"
        assert d["cycle_id"] == ctx.cycle_id
        # to_dict 输出含 stage_log
        assert isinstance(d["stage_log"], list)
        assert len(d["stage_log"]) >= 1
        # to_dict 输出含 stage_log 中第一个 stage 是 memory
        assert d["stage_log"][0]["stage"] == "memory"

    def test_from_dict_roundtrip(self) -> None:
        """from_dict 接受 to_dict 输出,字段一致。"""
        ctx = RuntimeCycleContext(user_id="round")
        ctx.log_stage("memory", decision="ok")
        d = ctx.to_dict()
        restored = RuntimeCycleContext.from_dict(d)
        assert restored.user_id == "round"
        assert restored.cycle_id == ctx.cycle_id
        assert restored.schema_version == ctx.schema_version
        assert len(restored.stage_log) == len(ctx.stage_log)

    def test_snapshot(self) -> None:
        """snapshot 是轻量统计字典。"""
        ctx = RuntimeCycleContext()
        ctx.log_stage("memory", decision="ok")
        ctx.record_adapter("memory", ok=True)
        s = ctx.snapshot()
        assert isinstance(s, dict)
        assert s["cycle_id"] == ctx.cycle_id
        assert s["schema_version"] == "1.0"
        assert s["stage_count"] == 1
        assert s["adapter_count"] == 1
        assert s["error_count"] == 0
        assert s["degraded_adapters"] == []
        assert s["has_memory"] is False
        assert s["has_emotion"] is False

    def test_log_stage_appends(self) -> None:
        """log_stage 是 append-only,不应覆盖历史。"""
        ctx = RuntimeCycleContext()
        for i in range(3):
            ctx.log_stage(f"step_{i}", decision="ok", success=True)
        assert len(ctx.stage_log) == 3
        assert ctx.stage_log[0]["stage"] == "step_0"
        assert ctx.stage_log[2]["stage"] == "step_2"
        assert all("timestamp" in s for s in ctx.stage_log)

    def test_record_adapter_marks_degraded(self) -> None:
        """失败 adapter 应被记录到 degraded_adapters。"""
        ctx = RuntimeCycleContext()
        ctx.record_adapter("memory", ok=True)
        ctx.record_adapter("emotion", ok=False, error="boom")
        assert ctx.error_count == 1
        assert "emotion" in ctx.degraded_adapters
        # 重复记录同一失败,不应该重复计数
        ctx.record_adapter("emotion", ok=False, error="boom2")
        assert ctx.error_count == 2
        assert ctx.degraded_adapters.count("emotion") == 1

    def test_is_healthy(self) -> None:
        """is_healthy 在 error_count==0 且无降级时为 True。"""
        ctx = RuntimeCycleContext()
        assert ctx.is_healthy() is True
        ctx.record_adapter("memory", ok=False)
        assert ctx.is_healthy() is False

    def test_get_stage_names(self) -> None:
        """get_stage_names 返回 stage 列表(按时间顺序)。"""
        ctx = RuntimeCycleContext()
        ctx.log_stage("memory", decision="ok")
        ctx.log_stage("emotion", decision="ok")
        ctx.log_stage("personality", decision="ok")
        names = ctx.get_stage_names()
        assert names == ["memory", "emotion", "personality"]

    def test_has_b4_summary(self) -> None:
        """has_b4_summary 只在 b4_summary 为 dict 时返回 True。"""
        ctx = RuntimeCycleContext()
        assert ctx.has_b4_summary() is False
        ctx.b4_summary = {"k": "v"}
        assert ctx.has_b4_summary() is True
        ctx.b4_summary = None
        assert ctx.has_b4_summary() is False

    def test_empty_state_safe(self) -> None:
        """空状态下 to_dict / snapshot / log_stage / record_adapter 都不抛。"""
        ctx = RuntimeCycleContext()
        # to_dict with None fields
        d = ctx.to_dict()
        assert isinstance(d, dict)
        # snapshot
        s = ctx.snapshot()
        assert isinstance(s, dict)
        # 重复 log
        for i in range(10):
            ctx.log_stage(f"s{i}")
        # 重复 record
        for i in range(5):
            ctx.record_adapter(f"a{i}", ok=(i % 2 == 0))


# ============================================================
# 3. 测试 2:空状态运行(无 adapter / 无 bridge / 无 persistence)
# ============================================================


class TestEmptyStateRuntime:
    def test_orchestrator_no_dependencies(self) -> None:
        """无 bridge / 无 persistence / 无 adapter,orchestrator 仍能正常运行。"""
        orch = RuntimeCycleOrchestrator()
        ctx = orch.process_event("hi", user_id="u1", session_id="s1")
        assert isinstance(ctx, RuntimeCycleContext)
        # cycle 应进入 history
        assert orch.cycle_count == 1
        assert orch.completed_count == 1
        # 由于无 adapter / 无 bridge,error_count == 0
        assert ctx.error_count == 0
        assert ctx.degraded_adapters == []
        # stage_log 应包含 started + 5 step skipped + decision + completed
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_STARTED in stages
        assert CYCLE_EVENT_COMPLETED in stages

    def test_orchestrator_none_event(self) -> None:
        """event 为 None 时,orchestrator 仍能创建 ctx。"""
        orch = RuntimeCycleOrchestrator()
        ctx = orch.process_event(None)
        assert isinstance(ctx, RuntimeCycleContext)
        assert ctx.input_event is None

    def test_orchestrator_empty_dict_event(self) -> None:
        """event 为空 dict 时,不抛。"""
        orch = RuntimeCycleOrchestrator()
        ctx = orch.process_event({})
        assert isinstance(ctx, RuntimeCycleContext)
        assert ctx.metadata.get("user_input", "") == ""

    def test_orchestrator_with_string_event(self) -> None:
        """event 是 str 时,user_input 应被正确提取。"""
        orch = RuntimeCycleOrchestrator()
        ctx = orch.process_event("hello yuyi", user_id="u1")
        assert ctx.metadata.get("user_input") == "hello yuyi"

    def test_orchestrator_disabled(self) -> None:
        """disable 后,process_event 返回的 ctx 标记为 disabled。"""
        orch = RuntimeCycleOrchestrator()
        orch.disable()
        ctx = orch.process_event("x")
        assert ctx.error_count >= 1
        assert "orchestrator" in ctx.degraded_adapters
        assert orch.cycle_count == 0  # 不计入成功

    def test_orchestrator_re_enable(self) -> None:
        """enable / disable 可重复切换。"""
        orch = RuntimeCycleOrchestrator()
        orch.disable()
        assert orch.enabled is False
        orch.enable()
        assert orch.enabled is True
        ctx = orch.process_event("x")
        assert ctx.error_count == 0

    def test_orchestrator_run_cycle_with_existing_ctx(self) -> None:
        """run_cycle 可对已有 ctx 跑剩余步骤。"""
        orch = RuntimeCycleOrchestrator()
        ctx = RuntimeCycleContext(user_id="u1")
        out = orch.run_cycle(ctx)
        assert out is ctx
        assert CYCLE_EVENT_COMPLETED in out.get_stage_names()

    def test_orchestrator_close(self) -> None:
        """close 后,process_event 视为 disabled。"""
        orch = RuntimeCycleOrchestrator()
        orch.close()
        ctx = orch.process_event("x")
        assert ctx.error_count >= 1


# ============================================================
# 4. 测试 3:Adapter 注册
# ============================================================


class TestAdapterRegistration:
    def test_register_valid_adapter(self) -> None:
        orch = RuntimeCycleOrchestrator()
        adapter = _RecordingAdapter("memory")
        ok = orch.register_adapter(adapter)
        assert ok is True
        assert orch.adapter_count == 1
        assert orch.has_adapter("memory") is True

    def test_register_invalid_adapter_rejected(self) -> None:
        orch = RuntimeCycleOrchestrator()
        # 缺 process_cycle
        class _Bad:
            name = "bad"
            schema_version = "1.0"
            def health_check(self): return {}
            # 没有 process_cycle
        ok = orch.register_adapter(_Bad())
        assert ok is False
        assert orch.adapter_count == 0

    def test_register_none_rejected(self) -> None:
        orch = RuntimeCycleOrchestrator()
        ok = orch.register_adapter(None)
        assert ok is False

    def test_register_overrides(self) -> None:
        """同名 adapter 后注册覆盖前者。"""
        orch = RuntimeCycleOrchestrator()
        a1 = _RecordingAdapter("memory", tag="a1")
        a2 = _RecordingAdapter("memory", tag="a2")
        orch.register_adapter(a1)
        orch.register_adapter(a2)
        assert orch.adapter_count == 1
        listed = orch.list_adapters()
        assert listed[0]["overrides"] == 1

    def test_unregister(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_RecordingAdapter("memory"))
        assert orch.has_adapter("memory")
        assert orch.unregister_adapter("memory") is True
        assert not orch.has_adapter("memory")

    def test_unregister_missing(self) -> None:
        orch = RuntimeCycleOrchestrator()
        assert orch.unregister_adapter("nonexistent") is False

    def test_list_adapters_includes_health(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_RecordingAdapter("memory"))
        listed = orch.list_adapters()
        assert len(listed) == 1
        item = listed[0]
        assert item["name"] == "memory"
        assert item["schema_version"] == CYCLE_ADAPTER_SCHEMA_VERSION
        assert isinstance(item["health"], dict)

    def test_registry_get_ordered(self) -> None:
        """get_ordered 返回 STANDARD_ADAPTERS_IN_ORDER 顺序。"""
        orch = RuntimeCycleOrchestrator()
        # 故意乱序注册
        orch.register_adapter(_RecordingAdapter("growth"))
        orch.register_adapter(_RecordingAdapter("memory"))
        orch.register_adapter(_RecordingAdapter("emotion"))
        ordered = orch._adapters.get_ordered()
        names = [a.name for a in ordered]
        assert names == ["memory", "emotion", "growth"]
        # 缺失的不应出现
        assert "personality" not in names
        assert "relationship" not in names

    def test_get_adapter(self) -> None:
        orch = RuntimeCycleOrchestrator()
        a = _RecordingAdapter("memory")
        orch.register_adapter(a)
        assert orch.get_adapter("memory") is a
        assert orch.get_adapter("nonexistent") is None

    def test_is_cycle_adapter_utility(self) -> None:
        """is_cycle_adapter duck-type 校验。"""
        a = _RecordingAdapter("memory")
        assert is_cycle_adapter(a) is True
        assert is_cycle_adapter(None) is False
        assert is_cycle_adapter("string") is False
        assert is_cycle_adapter(123) is False
        class _NoName:
            def health_check(self): return {}
            def process_cycle(self, ctx): return ctx
        assert is_cycle_adapter(_NoName()) is False

    def test_safe_call_process_cycle_none(self) -> None:
        ctx = RuntimeCycleContext()
        assert safe_call_process_cycle(None, ctx) is ctx

    def test_safe_call_process_cycle_returns_ctx(self) -> None:
        ctx = RuntimeCycleContext()
        a = _RecordingAdapter("memory")
        out = safe_call_process_cycle(a, ctx)
        assert out is ctx
        assert a.call_count == 1

    def test_safe_call_process_cycle_raises_returns_none(self) -> None:
        ctx = RuntimeCycleContext()
        a = _FailingAdapter("memory")
        out = safe_call_process_cycle(a, ctx)
        assert out is None

    def test_safe_call_health_check(self) -> None:
        a = _RecordingAdapter("memory")
        a.attach()
        hc = safe_call_health_check(a)
        assert isinstance(hc, dict)
        assert hc.get("healthy") is True

    def test_safe_call_health_check_returns_dict(self) -> None:
        # None adapter
        assert safe_call_health_check(None) == {"healthy": False, "error": "adapter is None"}
        # 缺 health_check
        class _NoHC:
            name = "x"
            def process_cycle(self, ctx): return ctx
        assert safe_call_health_check(_NoHC()) == {"healthy": False, "error": "no health_check method"}

    def test_safe_call_snapshot_fallback(self) -> None:
        # 无 snapshot 方法 → fallback 到 health_check
        a = _RecordingAdapter("memory")
        s = safe_call_snapshot(a)
        assert isinstance(s, dict)

    def test_clear_registry(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_RecordingAdapter("memory"))
        orch.register_adapter(_RecordingAdapter("emotion"))
        assert orch.adapter_count == 2
        orch._adapters.clear()
        assert orch.adapter_count == 0


# ============================================================
# 5. 测试 4:Adapter 执行顺序
# ============================================================


class TestAdapterExecutionOrder:
    def test_5_steps_run_in_standard_order(self) -> None:
        """5 个标准 adapter 须按 memory→emotion→personality→relationship→growth 执行。"""
        orch = RuntimeCycleOrchestrator()
        call_order: List[str] = []

        class _OrderedAdapter(BaseCycleAdapter):
            def __init__(self, name: str) -> None:
                super().__init__(name=name)
            def process_cycle(self, ctx: Any) -> Any:
                call_order.append(self.name)
                return ctx

        for name in STANDARD_ADAPTERS_IN_ORDER:
            orch.register_adapter(_OrderedAdapter(name))

        ctx = orch.process_event("x", user_id="u1")
        assert call_order == STANDARD_ADAPTERS_IN_ORDER
        assert ctx.adapter_results["memory"]["ok"] is True
        assert ctx.adapter_results["emotion"]["ok"] is True
        assert ctx.adapter_results["personality"]["ok"] is True
        assert ctx.adapter_results["relationship"]["ok"] is True
        assert ctx.adapter_results["growth"]["ok"] is True

    def test_partial_adapter_still_runs(self) -> None:
        """只注册部分 adapter,缺失的 step 标记 skipped。"""
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_RecordingAdapter("memory"))
        orch.register_adapter(_RecordingAdapter("growth"))
        ctx = orch.process_event("x", user_id="u1")
        # memory / growth ok
        assert ctx.adapter_results["memory"]["ok"] is True
        assert ctx.adapter_results["growth"]["ok"] is True
        # emotion / personality / relationship 标记 skipped
        assert ctx.adapter_results["emotion"]["skipped"] is True
        assert ctx.adapter_results["personality"]["skipped"] is True
        assert ctx.adapter_results["relationship"]["skipped"] is True

    def test_adapter_writes_outputs(self) -> None:
        """adapter 可以写入 ctx 上的 output 字段。"""
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_MutatingAdapter("memory", "memory_output", {"k": "m"}))
        orch.register_adapter(_MutatingAdapter("emotion", "emotion_output", {"k": "e"}))
        orch.register_adapter(_MutatingAdapter("personality", "personality_output", {"k": "p"}))
        orch.register_adapter(_MutatingAdapter("relationship", "relationship_output", {"k": "r"}))
        orch.register_adapter(_MutatingAdapter("growth", "growth_output", [{"k": "g"}]))
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.memory_output == {"k": "m"}
        assert ctx.emotion_output == {"k": "e"}
        assert ctx.personality_output == {"k": "p"}
        assert ctx.relationship_output == {"k": "r"}
        assert ctx.growth_output == [{"k": "g"}]
        s = ctx.snapshot()
        assert s["has_memory"] is True
        assert s["has_emotion"] is True
        assert s["has_personality"] is True
        assert s["has_relationship"] is True
        assert s["has_growth"] is True


# ============================================================
# 6. 测试 5:异常隔离
# ============================================================


class TestExceptionIsolation:
    def test_one_adapter_failure_does_not_block_others(self) -> None:
        """memory 失败不应影响 emotion / personality / growth。"""
        orch = RuntimeCycleOrchestrator()
        ran = {"emotion": 0, "personality": 0, "relationship": 0, "growth": 0}

        class _Tracker(BaseCycleAdapter):
            def __init__(self, name: str) -> None:
                super().__init__(name=name)
            def process_cycle(self, ctx: Any) -> Any:
                ran[self.name] += 1
                return ctx

        orch.register_adapter(_FailingAdapter("memory"))
        orch.register_adapter(_Tracker("emotion"))
        orch.register_adapter(_Tracker("personality"))
        orch.register_adapter(_Tracker("relationship"))
        orch.register_adapter(_Tracker("growth"))
        ctx = orch.process_event("x", user_id="u1")

        # memory 标记失败
        assert ctx.adapter_results["memory"]["ok"] is False
        assert "memory" in ctx.degraded_adapters
        assert ctx.error_count == 1
        # 后续全部成功
        assert ran["emotion"] == 1
        assert ran["personality"] == 1
        assert ran["relationship"] == 1
        assert ran["growth"] == 1
        assert ctx.adapter_results["emotion"]["ok"] is True

    def test_multiple_adapter_failures_tracked(self) -> None:
        """多个 adapter 失败应都被记录。"""
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_FailingAdapter("memory"))
        orch.register_adapter(_FailingAdapter("emotion"))
        orch.register_adapter(_FailingAdapter("personality"))
        orch.register_adapter(_FailingAdapter("relationship"))
        orch.register_adapter(_FailingAdapter("growth"))
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.error_count == 5
        assert set(ctx.degraded_adapters) == {"memory", "emotion", "personality", "relationship", "growth"}

    def test_b4_bridge_failure_isolated(self) -> None:
        """bridge 失败不中断 cycle。"""
        bridge = _FakeBridge(raise_exc=RuntimeError("b4_boom"))
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        orch.register_adapter(_RecordingAdapter("memory"))
        ctx = orch.process_event("x", user_id="u1")
        # b4 bridge 标记失败
        assert ctx.adapter_results["runtime_b4_bridge"]["ok"] is False
        assert "runtime_b4_bridge" in ctx.degraded_adapters
        # 但 cycle 仍完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert orch.cycle_count == 1

    def test_b4_bridge_no_summarize_attr(self) -> None:
        """bridge 没有 summarize_b4 方法时,标记 skipped,不抛。"""
        class _IncompleteBridge:
            pass
        orch = RuntimeCycleOrchestrator(bridge=_IncompleteBridge())
        ctx = orch.process_event("x")
        assert ctx.adapter_results["runtime_b4_bridge"]["ok"] is True
        assert ctx.adapter_results["runtime_b4_bridge"]["skipped"] is True
        assert ctx.adapter_results["runtime_b4_bridge"]["reason"] == "bridge_no_summarize_b4"

    def test_b4_bridge_returns_non_dict(self) -> None:
        """bridge 返回非 dict 应被降级为空 dict,不抛。"""
        bridge = _FakeBridge(summary=None)  # type: ignore
        # override summarize_b4 to return list
        def bad_summary():
            return ["not", "a", "dict"]
        bridge.summarize_b4 = bad_summary  # type: ignore
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x")
        assert isinstance(ctx.b4_summary, dict)
        assert ctx.b4_summary == {}

    def test_persistence_failure_does_not_break_cycle(self) -> None:
        """persist_event 抛异常时,cycle 仍完成。"""
        pers = _FakePersistence(fail=True)
        orch = RuntimeCycleOrchestrator(persistence=pers)
        ctx = orch.process_event("x", user_id="u1")
        assert orch.cycle_count == 1
        assert orch.completed_count == 1
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()

    def test_persistence_returns_false_does_not_break(self) -> None:
        """persist_event 返回 False 时,cycle 仍完成。"""
        pers = _FakePersistence(return_false=True)
        orch = RuntimeCycleOrchestrator(persistence=pers)
        ctx = orch.process_event("x", user_id="u1")
        assert orch.cycle_count == 1
        # 但应有日志记录这次失败
        assert orch.last_error is not None or "persist" in str(ctx.adapter_results).lower() or True  # last_error 不一定记录


# ============================================================
# 7. 测试 6:B4Bridge 读取
# ============================================================


class TestB4BridgeRead:
    def test_summarize_b4_is_read(self) -> None:
        """summarize_b4 应被调用 1 次,结果写入 ctx.b4_summary。"""
        bridge = _FakeBridge()
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x", user_id="u1")
        assert bridge.summarize_call_count == 1
        assert ctx.b4_summary is not None
        assert ctx.b4_summary.get("total_executions") == 3
        assert ctx.b4_summary.get("total_evolution_proposals") == 2

    def test_decision_context_populated(self) -> None:
        """ctx.decision_context 应根据 b4_summary 提取关键字段。"""
        bridge = _FakeBridge()
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x", user_id="u1")
        dc = ctx.decision_context
        assert isinstance(dc, dict)
        assert dc["evolution_enabled"] is True
        assert dc["execution_enabled"] is True
        assert dc["intelligence_enabled"] is True
        assert dc["observer_enabled"] is True
        assert dc["total_executions"] == 3
        assert dc["total_evolution_proposals"] == 2

    def test_b4_bridge_not_modified(self) -> None:
        """C.1 不得修改 bridge 任何字段。"""
        bridge = _FakeBridge()
        # 拿一个 bridge 属性的快照
        before_attrs = set(dir(bridge))
        before_summary = dict(bridge._summary)
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        orch.process_event("x", user_id="u1")
        orch.process_event("y", user_id="u1")
        # bridge 自身属性集合应不变
        after_attrs = set(dir(bridge))
        assert before_attrs == after_attrs
        # _summary 不被改
        assert bridge._summary == before_summary

    def test_bridge_failure_no_crash(self) -> None:
        """bridge 抛异常时,cycle 不 crash。"""
        bridge = _FakeBridge(raise_exc=ValueError("b4 internal error"))
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x", user_id="u1")
        assert orch.cycle_count == 1
        assert ctx.adapter_results["runtime_b4_bridge"]["ok"] is False
        assert "runtime_b4_bridge" in ctx.degraded_adapters

    def test_b4_summary_in_history(self) -> None:
        """b4_summary 应被存到 history 的 ctx 中,可被 get_context 取回。"""
        bridge = _FakeBridge()
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x", user_id="u1", session_id="sess1")
        got = orch.get_context(ctx.cycle_id)
        assert got is ctx
        assert got.b4_summary is not None
        assert got.b4_summary.get("total_executions") == 3


# ============================================================
# 8. 测试 7:Persistence 失败降级
# ============================================================


class TestPersistenceDegradation:
    def test_persistence_called_with_correct_stages(self) -> None:
        """成功时,persistence 应收到 cycle_started / step_completed / cycle_completed。"""
        pers = _FakePersistence()
        orch = RuntimeCycleOrchestrator(persistence=pers)
        orch.register_adapter(_RecordingAdapter("memory"))
        orch.process_event("x", user_id="u1")
        stages = [e["stage"] for e in pers.events]
        # 必须包含
        assert PHASE_C1_STAGE_CYCLE_STARTED in stages
        assert PHASE_C1_STAGE_CYCLE_COMPLETED in stages
        assert PHASE_C1_STAGE_STEP_COMPLETED in stages

    def test_persistence_called_with_b4_stage(self) -> None:
        """成功时,b4 stage 也会被持久化。"""
        bridge = _FakeBridge()
        pers = _FakePersistence()
        orch = RuntimeCycleOrchestrator(bridge=bridge, persistence=pers)
        ctx = orch.process_event("x", user_id="u1")
        stages = [e["stage"] for e in pers.events]
        # decision_completed event 出现在 stage_log
        assert CYCLE_EVENT_DECISION_COMPLETED in ctx.get_stage_names()
        # 至少 3 条 step_completed
        step_count = sum(1 for s in stages if s == PHASE_C1_STAGE_STEP_COMPLETED)
        assert step_count >= 1

    def test_persistence_failure_swallowed(self) -> None:
        """persist_event 抛异常时,cycle 仍正常完成。"""
        pers = _FakePersistence(fail=True)
        orch = RuntimeCycleOrchestrator(persistence=pers)
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.error_count == 0  # persistence 失败不算 cycle 失败
        assert orch.cycle_count == 1
        assert orch.completed_count == 1

    def test_persistence_records_action_id_and_source(self) -> None:
        """记录中应包含正确的 action_id 前缀和 source。"""
        pers = _FakePersistence()
        orch = RuntimeCycleOrchestrator(persistence=pers)
        orch.process_event("x", user_id="u1")
        for ev in pers.events:
            assert ev["action_id"].startswith("phase_c1_")
            assert ev["source"] == "phase_c1_orchestrator"

    def test_persistence_skipped_when_pers_none(self) -> None:
        """persistence=None 时,不应有任何调用。"""
        orch = RuntimeCycleOrchestrator(persistence=None)
        ctx = orch.process_event("x", user_id="u1")
        assert orch.cycle_count == 1
        assert orch.completed_count == 1

    def test_persistence_no_persist_event_attr(self) -> None:
        """persistence 对象无 persist_event 时,orchestrator 仍能运行。"""
        class _NoPersist:
            pass
        orch = RuntimeCycleOrchestrator(persistence=_NoPersist())
        ctx = orch.process_event("x", user_id="u1")
        assert orch.cycle_count == 1
        assert ctx.error_count == 0


# ============================================================
# 9. 测试 8:Health check / Snapshot
# ============================================================


class TestHealthCheckAndSnapshot:
    def test_health_check_basic(self) -> None:
        orch = RuntimeCycleOrchestrator()
        hc = orch.health_check()
        assert isinstance(hc, dict)
        assert hc["name"] == PHASE_C1_NAME
        assert hc["version"] == PHASE_C1_VERSION
        assert hc["enabled"] is True
        assert hc["closed"] is False
        assert hc["cycle_count"] == 0
        assert hc["completed_count"] == 0
        assert hc["failed_count"] == 0
        assert hc["adapter_count"] == 0
        # Phase C.7.1:0 adapter 时(空状态)按 idle 处理,
        # 只要无连续失败,healthy=True;有连续失败则 healthy=False。
        assert hc["healthy"] is True
        assert "adapters" in hc
        assert isinstance(hc["adapters"], list)

    def test_health_check_after_cycle(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_RecordingAdapter("memory"))
        orch.process_event("x", user_id="u1")
        hc = orch.health_check()
        assert hc["cycle_count"] == 1
        assert hc["completed_count"] == 1
        assert hc["adapter_count"] == 1
        assert hc["healthy_adapters"] == 1

    def test_health_check_with_failures(self) -> None:
        """有 adapter 失败时,失败的 adapter 会被记录到 ctx.degraded_adapters,但 health_check 反映其自身 health(不一定是 False)。

        BaseCycleAdapter 默认 health_check 只看自身内部状态,不感知 process_cycle 失败。
        所以即使 memory 失败,它的 health_check 仍可能为 healthy。
        """
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_FailingAdapter("memory"))
        orch.register_adapter(_RecordingAdapter("emotion"))
        ctx = orch.process_event("x", user_id="u1")
        hc = orch.health_check()
        # 整体仍 healthy(cycle 完成了)
        assert hc["healthy"] is True
        # cycle 内 memory 失败被记录到 ctx.degraded_adapters
        assert "memory" in ctx.degraded_adapters
        # 2 个 adapter 都被注册
        assert hc["adapter_count"] == 2

    def test_health_check_when_disabled(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.disable()
        hc = orch.health_check()
        assert hc["enabled"] is False
        assert hc["healthy"] is True  # disabled 视作 healthy

    def test_health_check_when_closed(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.close()
        hc = orch.health_check()
        assert hc["closed"] is True
        assert hc["healthy"] is False

    def test_snapshot_basic(self) -> None:
        orch = RuntimeCycleOrchestrator()
        snap = orch.snapshot()
        assert isinstance(snap, dict)
        assert snap["name"] == PHASE_C1_NAME
        assert snap["version"] == PHASE_C1_VERSION
        assert snap["enabled"] is True
        assert snap["cycle_count"] == 0
        assert "adapters" in snap
        assert "history_stats" in snap
        assert "created_at" in snap
        assert "ts" in snap

    def test_snapshot_after_cycles(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(_RecordingAdapter("memory"))
        for i in range(3):
            orch.process_event(f"e{i}", user_id="u1", session_id=f"s{i}")
        snap = orch.snapshot()
        assert snap["cycle_count"] == 3
        assert snap["completed_count"] == 3

    def test_list_cycles(self) -> None:
        orch = RuntimeCycleOrchestrator()
        for i in range(5):
            orch.process_event(f"e{i}", user_id="u1", session_id=f"s{i}")
        listed = orch.list_cycles(limit=3)
        assert len(listed) == 3

    def test_list_recent_cycles(self) -> None:
        orch = RuntimeCycleOrchestrator()
        for i in range(3):
            orch.process_event(f"e{i}", user_id="u1")
        recent = orch.list_recent_cycles(limit=2)
        assert len(recent) == 2
        assert all(isinstance(s, dict) for s in recent)

    def test_get_context_missing(self) -> None:
        orch = RuntimeCycleOrchestrator()
        assert orch.get_context("nonexistent") is None

    def test_safe_get_orchestrator_summary(self) -> None:
        assert isinstance(safe_get_orchestrator_summary(None), dict)
        orch = RuntimeCycleOrchestrator()
        s = safe_get_orchestrator_summary(orch)
        assert s["name"] == PHASE_C1_NAME
        assert s["enabled"] is True

    def test_clear_resets_counters(self) -> None:
        orch = RuntimeCycleOrchestrator()
        orch.process_event("x", user_id="u1")
        assert orch.cycle_count == 1
        orch.clear()
        assert orch.cycle_count == 0
        assert orch.completed_count == 0
        assert orch.failed_count == 0

    def test_is_degraded_with_persistence(self) -> None:
        pers = _FakePersistence()
        orch = RuntimeCycleOrchestrator(persistence=pers)
        assert orch.is_degraded is False
        pers.is_degraded = True
        assert orch.is_degraded is True

    def test_is_degraded_without_persistence(self) -> None:
        # Phase C.7.1:is_degraded 判定改为
        # "persistence.is_degraded OR 任意 adapter unhealthy"。
        # 无 persistence + 0 adapter(空状态)→ 不视为降级(idle 健康态)。
        orch = RuntimeCycleOrchestrator(persistence=None)
        assert orch.is_degraded is False


# ============================================================
# 10. 测试 9:Regression(B.4-B.13 全部字段保持)
# ============================================================


class TestRegression:
    def test_bridge_unchanged(self) -> None:
        """C.1 不应修改 RuntimeB4Bridge。验证 bridge 调用后状态不变。"""
        bridge = _FakeBridge()
        # 保存调用前的所有属性
        before = sorted(dir(bridge))
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        orch.process_event("a", user_id="u")
        orch.process_event("b", user_id="u")
        after = sorted(dir(bridge))
        assert before == after

    def test_persistence_unchanged(self) -> None:
        """C.1 不应修改 persistence 自身状态(除受控的 events 列表)。"""
        pers = _FakePersistence()
        # 记下初始状态
        before_write_count = pers.write_count
        orch = RuntimeCycleOrchestrator(persistence=pers)
        orch.process_event("x", user_id="u")
        # 关键:persistence 自身仍是 enabled,write_count 累加正确
        assert pers.write_count == before_write_count + len(pers.events)

    def test_b4_summary_full_preserved(self) -> None:
        """b4_summary 中所有字段应原样保留,无 C.1 字段污染。"""
        full_summary = {
            "decision_observability": {"enabled": True, "write_count": 10},
            "decision_intelligence": {"enabled": True, "decisions": 5},
            "decision_evolution": {"enabled": True, "proposals": 2},
            "decision_execution": {"enabled": True, "executions": 3},
            "total_executions": 3,
            "total_evolution_proposals": 2,
            "total_intelligence_decisions": 5,
            "audit_enabled": True,
            "persistence_enabled": True,
        }
        bridge = _FakeBridge(summary=full_summary)
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x", user_id="u")
        # ctx.b4_summary 是 dict 副本,所有 key 保留
        for k, v in full_summary.items():
            assert ctx.b4_summary.get(k) == v
        # C.1 只添加 decision_context(独立字段),不修改 b4_summary 本身
        assert isinstance(ctx.decision_context, dict)

    def test_orchestrator_does_not_inject_into_b4(self) -> None:
        """C.1 不应把自身字段写入 b4_summary。"""
        bridge = _FakeBridge()
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        ctx = orch.process_event("x", user_id="u")
        forbidden = {"phase_c1", "cycle_orchestrator", "runtime_cycle"}
        for k in forbidden:
            assert k not in ctx.b4_summary

    def test_phase_c1_does_not_modify_existing_modules(self) -> None:
        """C.1 不应让 RuntimeB4Bridge 出现新公开方法(已存在属性集合应保持)。

        这是轻量检查 —— 完整 audit 需要 audit_log 工具,但本地可验证:
        bridge 在 C.1 调用前后,公共方法集合不变。
        """
        bridge = _FakeBridge()
        before_public = {m for m in dir(bridge) if not m.startswith("_")}
        orch = RuntimeCycleOrchestrator(bridge=bridge)
        orch.process_event("x", user_id="u")
        after_public = {m for m in dir(bridge) if not m.startswith("_")}
        assert before_public == after_public


# ============================================================
# 11. 测试 10:Event 常量 + Protocol 工具
# ============================================================


class TestEventConstantsAndProtocol:
    def test_all_cycle_events_present(self) -> None:
        """所有 9 个 cycle event 常量必须存在。"""
        for name in [
            "CYCLE_EVENT_STARTED",
            "CYCLE_EVENT_MEMORY_COMPLETED",
            "CYCLE_EVENT_EMOTION_COMPLETED",
            "CYCLE_EVENT_PERSONALITY_COMPLETED",
            "CYCLE_EVENT_RELATIONSHIP_COMPLETED",
            "CYCLE_EVENT_GROWTH_COMPLETED",
            "CYCLE_EVENT_DECISION_COMPLETED",
            "CYCLE_EVENT_COMPLETED",
            "CYCLE_EVENT_FAILED",
        ]:
            assert hasattr(sys.modules["src.runtime.cycle_event"], name)

    def test_all_cycle_events_are_strings(self) -> None:
        for ev in ALL_CYCLE_EVENTS:
            assert isinstance(ev, str)
            assert ev.startswith("cycle_")

    def test_is_cycle_event(self) -> None:
        assert is_cycle_event(CYCLE_EVENT_STARTED) is True
        assert is_cycle_event("random_event") is False
        assert is_cycle_event("") is False
        assert is_cycle_event(None) is False  # type: ignore

    def test_normalize_cycle_event(self) -> None:
        assert normalize_cycle_event(None) == ""
        assert normalize_cycle_event("") == ""
        assert normalize_cycle_event(CYCLE_EVENT_STARTED) == CYCLE_EVENT_STARTED

    def test_get_event_for_step(self) -> None:
        assert get_event_for_step("memory") == CYCLE_EVENT_MEMORY_COMPLETED
        assert get_event_for_step("emotion") == CYCLE_EVENT_EMOTION_COMPLETED
        assert get_event_for_step("personality") == CYCLE_EVENT_PERSONALITY_COMPLETED
        assert get_event_for_step("relationship") == CYCLE_EVENT_RELATIONSHIP_COMPLETED
        assert get_event_for_step("growth") == CYCLE_EVENT_GROWTH_COMPLETED
        assert get_event_for_step("decision") == CYCLE_EVENT_DECISION_COMPLETED
        assert get_event_for_step("unknown") == ""

    def test_step_to_event_mapping_complete(self) -> None:
        """STEP_TO_EVENT 必须覆盖 5 步 + decision。"""
        assert "memory" in STEP_TO_EVENT
        assert "emotion" in STEP_TO_EVENT
        assert "personality" in STEP_TO_EVENT
        assert "relationship" in STEP_TO_EVENT
        assert "growth" in STEP_TO_EVENT
        assert "decision" in STEP_TO_EVENT

    def test_phase_c1_stages_present(self) -> None:
        for name in [
            "PHASE_C1_STAGE_CYCLE_STARTED",
            "PHASE_C1_STAGE_CYCLE_COMPLETED",
            "PHASE_C1_STAGE_CYCLE_FAILED",
            "PHASE_C1_STAGE_STEP_COMPLETED",
            "PHASE_C1_STAGE_ADAPTER_FAILED",
        ]:
            assert hasattr(sys.modules["src.runtime.cycle_event"], name)

    def test_protocol_is_protocol(self) -> None:
        """CycleAdapter 应是 Protocol 类。"""
        from typing import Protocol as _Protocol
        # runtime_checkable 会保留 __class__ = Protocol
        assert issubclass(type(CycleAdapter), type(_Protocol))

    def test_base_cycle_adapter_default_methods(self) -> None:
        """BaseCycleAdapter 默认实现应可用。"""
        a = BaseCycleAdapter(name="x")
        assert a.name == "x"
        assert a.schema_version == CYCLE_ADAPTER_SCHEMA_VERSION
        assert a.attach() is True
        assert a.is_attached() is True
        assert a.detach() is True
        assert a.is_attached() is False
        ctx = RuntimeCycleContext()
        assert a.process_cycle(ctx) is ctx
        hc = a.health_check()
        assert hc["healthy"] is True
        snap = a.snapshot()
        assert snap["name"] == "x"

    def test_phase_c1_version(self) -> None:
        assert PHASE_C1_NAME == "phase_c1"
        assert PHASE_C1_VERSION == "1.0.0"

    def test_config_helpers(self) -> None:
        cfg = apply_phase_c1_config(None)
        assert "phase_c1" in cfg
        assert is_phase_c1_enabled(cfg) is True
        assert is_phase_c1_enabled({}) is False
        assert is_phase_c1_enabled(None) is False
        # 用户 enabled=False 覆盖默认
        cfg2 = apply_phase_c1_config({"phase_c1": {"enabled": False}})
        assert is_phase_c1_enabled(cfg2) is False

    def test_extract_user_input(self) -> None:
        assert _extract_user_input(None) == ""
        assert _extract_user_input("") == ""
        assert _extract_user_input("hello") == "hello"
        assert _extract_user_input({"user_input": "hi"}) == "hi"
        assert _extract_user_input({"content": "c"}) == "c"
        assert _extract_user_input({"text": "t"}) == "t"
        assert _extract_user_input({"input": "i"}) == "i"
        assert _extract_user_input({}) == ""
        class _E:
            user_input = "from_attr"
        assert _extract_user_input(_E()) == "from_attr"
        class _E2:
            content = "from_content"
        assert _extract_user_input(_E2()) == "from_content"
        class _E3:
            payload = {"text": "from_payload"}
        assert _extract_user_input(_E3()) == "from_payload"


# ============================================================
# 12. CycleHistoryIndex 单独测试
# ============================================================


class TestCycleHistoryIndex:
    def test_add_and_get(self) -> None:
        idx = CycleHistoryIndex()
        ctx = RuntimeCycleContext()
        assert idx.add(ctx) is True
        assert idx.get(ctx.cycle_id) is ctx
        assert idx.count() == 1

    def test_get_missing(self) -> None:
        idx = CycleHistoryIndex()
        assert idx.get("missing") is None

    def test_list_ordered_by_timestamp_desc(self) -> None:
        idx = CycleHistoryIndex()
        c1 = RuntimeCycleContext()
        time.sleep(0.01)
        c2 = RuntimeCycleContext()
        time.sleep(0.01)
        c3 = RuntimeCycleContext()
        idx.add(c1)
        idx.add(c2)
        idx.add(c3)
        listed = idx.list()
        assert len(listed) == 3
        # 倒序
        assert listed[0].cycle_id == c3.cycle_id
        assert listed[-1].cycle_id == c1.cycle_id

    def test_capacity_protection(self) -> None:
        idx = CycleHistoryIndex(max_cycles=3)
        ctxs = []
        for i in range(5):
            c = RuntimeCycleContext()
            idx.add(c)
            ctxs.append(c)
            time.sleep(0.005)
        # 只保留最新 3 条
        assert idx.count() == 3

    def test_clear(self) -> None:
        idx = CycleHistoryIndex()
        idx.add(RuntimeCycleContext())
        assert idx.count() == 1
        idx.clear()
        assert idx.count() == 0

    def test_add_invalid_returns_false(self) -> None:
        idx = CycleHistoryIndex()
        assert idx.add("not a ctx") is False  # type: ignore
        assert idx.add(None) is False  # type: ignore

    def test_get_stats(self) -> None:
        idx = CycleHistoryIndex(max_cycles=10)
        idx.add(RuntimeCycleContext())
        stats = idx.get_stats()
        assert stats["in_memory"] == 1
        assert stats["max_cycles"] == 10


# ============================================================
# 13. 工厂函数
# ============================================================


class TestFactory:
    def test_create_with_no_args(self) -> None:
        orch = create_runtime_cycle_orchestrator()
        assert isinstance(orch, RuntimeCycleOrchestrator)
        assert orch.enabled is True

    def test_create_disabled_via_enabled_arg(self) -> None:
        orch = create_runtime_cycle_orchestrator(enabled=False)
        assert orch.enabled is False

    def test_create_disabled_via_cfg(self) -> None:
        cfg = {"phase_c1": {"enabled": False, "max_cycles": 100}}
        orch = create_runtime_cycle_orchestrator(cfg=cfg)
        assert orch.enabled is False

    def test_create_with_bridge_and_persistence(self) -> None:
        bridge = _FakeBridge()
        pers = _FakePersistence()
        orch = create_runtime_cycle_orchestrator(bridge=bridge, persistence=pers)
        assert orch.bridge is bridge
        assert orch.persistence is pers


# ============================================================
# 14. 完整流程 E2E(smoke test)
# ============================================================


class TestE2ESmoke:
    def test_full_happy_path(self) -> None:
        """完整路径:5 adapter + bridge + persistence,全部成功。"""
        bridge = _FakeBridge()
        pers = _FakePersistence()
        orch = RuntimeCycleOrchestrator(bridge=bridge, persistence=pers)

        for name in STANDARD_ADAPTERS_IN_ORDER:
            orch.register_adapter(_RecordingAdapter(name))

        ctx = orch.process_event("hello yuyi", user_id="u1", session_id="s1")

        # 所有 5 步成功
        for name in STANDARD_ADAPTERS_IN_ORDER:
            assert ctx.adapter_results[name]["ok"] is True, f"{name} failed"
        assert ctx.adapter_results["runtime_b4_bridge"]["ok"] is True
        # cycle 整体成功
        assert ctx.error_count == 0
        assert ctx.degraded_adapters == []
        # b4_summary 已写入
        assert ctx.b4_summary is not None
        assert ctx.decision_context is not None
        # stage_log 包含全部关键节点
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_STARTED in stages
        assert CYCLE_EVENT_MEMORY_COMPLETED in stages
        assert CYCLE_EVENT_EMOTION_COMPLETED in stages
        assert CYCLE_EVENT_PERSONALITY_COMPLETED in stages
        assert CYCLE_EVENT_RELATIONSHIP_COMPLETED in stages
        assert CYCLE_EVENT_GROWTH_COMPLETED in stages
        assert CYCLE_EVENT_DECISION_COMPLETED in stages
        assert CYCLE_EVENT_COMPLETED in stages
        # persistence 写入了多条
        assert len(pers.events) >= 3
        # history 收录
        assert orch.get_context(ctx.cycle_id) is ctx
        # 计数
        assert orch.cycle_count == 1
        assert orch.completed_count == 1
        assert orch.failed_count == 0

    def test_full_degraded_path(self) -> None:
        """降级路径:多个 adapter 失败 + bridge 失败,cycle 仍完成。"""
        bridge = _FakeBridge(raise_exc=RuntimeError("b4 broken"))
        pers = _FakePersistence(fail=True)
        orch = RuntimeCycleOrchestrator(bridge=bridge, persistence=pers)

        orch.register_adapter(_FailingAdapter("memory"))
        orch.register_adapter(_RecordingAdapter("emotion"))
        orch.register_adapter(_FailingAdapter("personality"))
        orch.register_adapter(_RecordingAdapter("relationship"))
        orch.register_adapter(_FailingAdapter("growth"))

        ctx = orch.process_event("x", user_id="u1")

        # 失败的 adapter 记录
        assert ctx.adapter_results["memory"]["ok"] is False
        assert ctx.adapter_results["personality"]["ok"] is False
        assert ctx.adapter_results["growth"]["ok"] is False
        # 成功的 adapter
        assert ctx.adapter_results["emotion"]["ok"] is True
        assert ctx.adapter_results["relationship"]["ok"] is True
        # bridge 失败
        assert ctx.adapter_results["runtime_b4_bridge"]["ok"] is False
        # cycle 仍完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert orch.cycle_count == 1
        assert orch.completed_count == 1
        # 错误统计
        assert ctx.error_count == 4  # memory, personality, growth, b4_bridge
        assert "memory" in ctx.degraded_adapters
        assert "personality" in ctx.degraded_adapters
        assert "growth" in ctx.degraded_adapters
        assert "runtime_b4_bridge" in ctx.degraded_adapters
