# -*- coding: utf-8 -*-
"""
P2.4-B.15 Phase 1 — Runtime Cognitive Activation 测试

覆盖（任务书冻结五项）：
    1. flag=false 时 17 阶段行为不变
    2. flag=true 时对应 stage 被调用
    3. EventBus 收到 cycle 事件
    4. ReflectionRecord 可以生成
    5. data/ 不变化

conftest 单例陷阱规避：不实例化 RuntimeCore——用伪 core +
LifecycleExecutor 直驱（duck-typed 契约即生产分派方式）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.runtime.cognitive_activation import (
    STAGE_CYCLE_EVENTS,
    is_lifecycle_cycle_events_enabled,
    is_stage_enabled,
    publish_cycle_event,
    reset_all_cognitive_activation_flags,
    run_stage_adapter,
    set_lifecycle_cycle_events_enabled,
    set_stage_flag,
)
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.lifecycle_executor import (
    STAGE_TO_METHOD_NAME,
    LifecycleExecutor,
)
from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER


@pytest.fixture(autouse=True)
def _reset_b15_flags():
    reset_all_cognitive_activation_flags()
    yield
    reset_all_cognitive_activation_flags()


# ============================================================
# helpers
# ============================================================
class _CaptureBus:
    """捕获型 domain_event_bus（不触全局 EventBus 单例）。"""

    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event_type, *, source="", source_id="", payload=None,
             related_ids=None, emit_legacy_aliases=True):
        self.events.append(
            {"event_type": event_type, "payload": dict(payload or {})},
        )
        return SimpleNamespace(event_id=f"evt_{len(self.events)}")


def _make_fake_core(*, snapshot: dict | None = None) -> SimpleNamespace:
    """17 阶段方法齐全的伪 core（分派契约与生产一致）。"""
    core = SimpleNamespace(
        calls=[],
        domain_event_bus=_CaptureBus(),
        _last_process_phase_errors={},
        _on_event=lambda e: None,
    )

    def _make_stage_method(name):
        def _method(event, ctx):
            core.calls.append(name)

        return _method

    for _, method_name in STAGE_TO_METHOD_NAME:
        setattr(core, method_name, _make_stage_method(method_name))

    if snapshot is not None:

        def _get_full():
            return snapshot

        core.get_self_model_full = _get_full
    return core


def _snapshot_for_reflection() -> dict:
    return {
        "identity_id": "si_test",
        "version": 3,
        "stable_traits": {"warmth": 0.7, "curiosity": 0.8},
        "growth_history": [{"summary": "g1"}, {"summary": "g2"}],
    }


# ============================================================
# 1. flag=false 时 17 阶段行为不变
# ============================================================
def test_flag_off_17_stage_behavior_unchanged():
    assert not is_lifecycle_cycle_events_enabled()
    assert not any(is_stage_enabled(s.name) for s in RUNTIME_LIFECYCLE_ORDER)

    core = _make_fake_core()
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    # 17 阶段全部按冻结顺序分派
    assert executor.last_stage_order == [s.name for s in RUNTIME_LIFECYCLE_ORDER]
    assert len(executor.last_stage_order) == 17
    expected_calls = [name for _, name in STAGE_TO_METHOD_NAME]
    assert core.calls == expected_calls
    # 无事件发布、无 B.15 标记（零行为差异）
    assert core.domain_event_bus.events == []
    assert not hasattr(ctx, "_b15_stage_calls") or not ctx._b15_stage_calls
    # Stage 1 契约：_on_event 恰好一次由 core 方法侧体现（calls 含 receive）
    assert core.calls[1] == "_stage_01_receive_event"


def test_flag_off_equivalent_to_double_run():
    """两次执行（flag 均关）结果确定性一致——无隐藏状态引入。"""
    core1 = _make_fake_core()
    core2 = _make_fake_core()
    e1, e2 = LifecycleExecutor(), LifecycleExecutor()
    ctx1 = e1.execute(core1, None, RuntimeContext())
    ctx2 = e2.execute(core2, None, RuntimeContext())
    assert core1.calls == core2.calls
    assert e1.last_stage_order == e2.last_stage_order
    assert ctx1._phase_errors == ctx2._phase_errors == {}


# ============================================================
# 2. flag=true 时对应 stage 被调用
# ============================================================
def test_flag_on_stage_adapter_invoked():
    for stage in (
        "PERCEPTION_OBSERVATION", "PERCEPTION_ANALYSIS",
        "SELF_MODEL_BUILD", "SELF_MODEL_EVOLUTION",
        "SELF_MODEL_REFLECTION", "SELF_MODEL_VALIDATION",
        "SELF_MODEL_PERSISTENCE",
    ):
        set_stage_flag(stage, True)
    core = _make_fake_core()
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    # 07-13 全部留下调用标记（flag=True 生效）
    b15_calls = ctx._b15_stage_calls
    for stage in (
        "PERCEPTION_OBSERVATION", "PERCEPTION_ANALYSIS",
        "SELF_MODEL_BUILD", "SELF_MODEL_EVOLUTION",
        "SELF_MODEL_REFLECTION", "SELF_MODEL_VALIDATION",
        "SELF_MODEL_PERSISTENCE",
    ):
        assert b15_calls.get(stage) is True, stage
    # 主链 17 阶段方法仍全部执行（旁路不替代主链）
    assert core.calls == [name for _, name in STAGE_TO_METHOD_NAME]


def test_stage07_perception_registry_observed():
    set_stage_flag("PERCEPTION_OBSERVATION", True)
    observations = [{"kind": "screen", "text": "hello"}]
    core = _make_fake_core()
    core.perception_registry = SimpleNamespace(
        observe_all=lambda: observations,
    )
    ctx = RuntimeContext()
    run_stage_adapter(core, "PERCEPTION_OBSERVATION", None, ctx)
    assert ctx.perception_observations == observations
    # 未注入 registry 的 core → 保持 noop（不抛错）
    core2 = _make_fake_core()
    ctx2 = RuntimeContext()
    run_stage_adapter(core2, "PERCEPTION_OBSERVATION", None, ctx2)
    assert not hasattr(ctx2, "perception_observations")


# ============================================================
# 3. EventBus 收到 cycle 事件
# ============================================================
def test_eventbus_receives_cycle_events():
    set_lifecycle_cycle_events_enabled(True)
    core = _make_fake_core()
    executor = LifecycleExecutor()
    executor.execute(core, None, RuntimeContext())

    types = [e["event_type"] for e in core.domain_event_bus.events]
    # 生命周期边界 + 四个域完成事件（全部为已有常量，无新增类型）
    assert types[0] == "cycle_started"
    assert types[-1] == "cycle_completed"
    for expected in (
        "cycle_memory_completed",
        "cycle_emotion_completed",
        "cycle_growth_completed",
        "cycle_personality_completed",
    ):
        assert expected in types, types
    # 已有常量映射之外不发布（07-13 无专属常量 → 无 per-stage 事件）
    assert len(types) == 6, types
    # payload 携带 stage 名
    memory_evt = next(
        e for e in core.domain_event_bus.events
        if e["event_type"] == "cycle_memory_completed"
    )
    assert memory_evt["payload"]["stage"] == "MEMORY_RETRIEVAL"


def test_cycle_failed_published_on_stage_error():
    set_lifecycle_cycle_events_enabled(True)
    core = _make_fake_core()

    def _boom(event, ctx):
        raise RuntimeError("stage14 down")

    core._stage_14_response_generation = _boom
    executor = LifecycleExecutor()
    executor.execute(core, None, RuntimeContext())
    types = [e["event_type"] for e in core.domain_event_bus.events]
    assert "cycle_failed" in types
    failed = next(e for e in core.domain_event_bus.events
                  if e["event_type"] == "cycle_failed")
    assert failed["payload"]["stage"] == "RESPONSE_GENERATION"
    # fail-soft：cycle_completed 仍发布（17 阶段全部跑完）
    assert types[-1] == "cycle_completed"


# ============================================================
# 4. ReflectionRecord 可以生成
# ============================================================
def test_reflection_record_generated_via_stage11():
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    record = getattr(ctx, "_b15_reflection_record", None)
    assert isinstance(record, dict)
    # ReflectionRecord schema 字段（不改 schema，只投影）
    for key in (
        "reflection_id", "observation", "interpretation",
        "confidence", "priority", "source",
    ):
        assert key in record, key
    assert record["source"] == "b15_stage11"
    # 只生成记录：不改人格/自我模型（伪 core 快照未被写回）
    assert core.get_self_model_full() == _snapshot_for_reflection()


def test_reflection_engine_direct_no_state_change():
    """引擎直测：reflect 只产出记录，diff 输入不被修改。"""
    from src.runtime.self_model.reflection.reflection_engine import (
        ReflectionEngine,
    )

    diff = {
        "self_model_snapshot": {
            "version": 2, "stable_trait_count": 3, "growth_history_count": 5,
        },
    }
    snapshot_before = repr(diff)
    engine = ReflectionEngine()
    record = engine.reflect(diff, source="b15_test", categories=["state_change"])
    assert record is not None
    assert record.to_dict()["source"] == "b15_test"
    assert repr(diff) == snapshot_before  # 输入不可变


def test_stage11_without_snapshot_noop():
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    core = _make_fake_core()  # 无 get_self_model_full，无 ctx 快照
    ctx = RuntimeContext()
    run_stage_adapter(core, "SELF_MODEL_REFLECTION", None, ctx)
    assert not hasattr(ctx, "_b15_reflection_record")


def test_publish_cycle_event_failsoft():
    # 无 domain_event_bus 的 core → 静默
    publish_cycle_event(SimpleNamespace(), "cycle_started", {})
    # emit 抛错 → 隔离
    class _BadBus:
        def emit(self, *a, **k):
            raise ValueError("boom")

    publish_cycle_event(
        SimpleNamespace(domain_event_bus=_BadBus()), "cycle_started", {},
    )


# ============================================================
# 5. data/ 不变化
# ============================================================
def _repo_data_snapshot() -> dict:
    repo = Path(__file__).resolve().parents[1]
    out = {}
    for rel in (
        "data/runtime_context",
        "data/self_model.json",
        "data/self_model",
        "data/memory.json",
        "data/emotion_state.json",
    ):
        p = repo / rel
        if p.exists():
            out[rel] = (True, p.stat().st_mtime_ns)
        else:
            out[rel] = (False, None)
    return out


def test_data_unchanged_after_full_activation():
    before = _repo_data_snapshot()
    # 全 flag 开启跑一轮完整 17 阶段（含反思链 + 事件发布）
    set_lifecycle_cycle_events_enabled(True)
    for stage in (
        "PERCEPTION_OBSERVATION", "PERCEPTION_ANALYSIS",
        "SELF_MODEL_BUILD", "SELF_MODEL_EVOLUTION",
        "SELF_MODEL_REFLECTION", "SELF_MODEL_VALIDATION",
        "SELF_MODEL_PERSISTENCE",
    ):
        set_stage_flag(stage, True)
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())
    assert ctx._b15_stage_calls
    assert core.domain_event_bus.events
    assert _repo_data_snapshot() == before
