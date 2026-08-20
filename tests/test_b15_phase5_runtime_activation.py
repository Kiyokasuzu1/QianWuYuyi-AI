# -*- coding: utf-8 -*-
"""
P2.4-B.15 Phase 5 — Runtime Cognitive Loop 最小灰度激活 测试

覆盖（Phase 3 任务书 7 项）：
    1. Runtime cycle 能完整跑完（17 阶段）
    2. Timeline 有完整事件链（cycle_started → 阶段事件 →
       reflection_started → reflection_completed → cycle_completed）
    3. trace_id 连续（全部节点 == ctx.session_id）
    4. Reflection 产生记录（ctx._b15_reflection_record）
    5. SelfModel 不变化（快照 deepcopy 前后一致）
    6. MutationGateway 无调用（探针运行前挂载，零请求/决策/记录）
    7. flag 关闭时行为恢复（staging 默认 False → 零事件、零节点）

staging 语义：
    - runtime_cognitive_activation_enabled 默认 False
    - True 时 apply_runtime_cognitive_activation() 开启最小组合：
      lifecycle_cycle_events / timeline_recording /
      timeline_event_projection / SELF_MODEL_REFLECTION adapter
    - 绝不开启：scheduler / autonomous / growth mutation /
      reflection growth bridge / self model mutation auto apply

conftest 单例陷阱规避：伪 core + 捕获 bus（与 Phase 3/4 测试同风格）。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.contracts.runtime_event_schema import RuntimeDomainEvent
from src.runtime.cognitive_activation import (
    apply_runtime_cognitive_activation,
    is_lifecycle_cycle_events_enabled,
    is_runtime_cognitive_activation_enabled,
    is_stage_enabled,
    reset_all_cognitive_activation_flags,
    set_runtime_cognitive_activation_enabled,
)
from src.runtime.cognitive_timeline import (
    CognitiveTimeline,
    get_timeline,
    is_timeline_recording_enabled,
    reset_timeline_state,
    set_timeline,
)
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.lifecycle_executor import (
    STAGE_TO_METHOD_NAME,
    LifecycleExecutor,
)
from src.runtime.timeline_projection import (
    is_timeline_event_projection_enabled,
    reset_timeline_projection_state,
)


@pytest.fixture(autouse=True)
def _reset_phase5_state():
    reset_all_cognitive_activation_flags()
    reset_timeline_state()
    reset_timeline_projection_state()
    yield
    reset_all_cognitive_activation_flags()
    reset_timeline_state()
    reset_timeline_projection_state()


class _CaptureBus:
    """捕获型 domain_event_bus：构造并返回真实 RuntimeDomainEvent。"""

    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event_type, *, source="", source_id="", payload=None,
             related_ids=None, emit_legacy_aliases=True):
        event = RuntimeDomainEvent(
            event_type=event_type,
            source=source or "lifecycle_executor",
            source_id=source_id,
            payload=dict(payload or {}),
            related_ids=list(related_ids or []),
        )
        self.events.append(event)
        return event


def _make_fake_core(*, snapshot: dict | None = None) -> SimpleNamespace:
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


EXPECTED_FULL_CHAIN = [
    "cycle_started",
    "cycle_memory_completed",
    "cycle_emotion_completed",
    "cycle_growth_completed",
    "cycle_personality_completed",
    "reflection_started",
    "reflection_completed",
    "cycle_completed",
]


# ============================================================
# staging flag 语义
# ============================================================
def test_staging_flag_defaults_false():
    assert not is_runtime_cognitive_activation_enabled()


def test_apply_noop_when_staging_off():
    assert apply_runtime_cognitive_activation() is False  # no-op
    assert not is_lifecycle_cycle_events_enabled()
    assert not is_timeline_recording_enabled()
    assert not is_timeline_event_projection_enabled()
    assert not is_stage_enabled("SELF_MODEL_REFLECTION")


def test_apply_enables_minimal_combo_only():
    set_runtime_cognitive_activation_enabled(True)
    assert apply_runtime_cognitive_activation() is True
    assert is_lifecycle_cycle_events_enabled()
    assert is_timeline_recording_enabled()
    assert is_timeline_event_projection_enabled()
    assert is_stage_enabled("SELF_MODEL_REFLECTION")
    # 其余 stage adapter 一律不动（growth 相关绝不开启）
    assert not is_stage_enabled("SELF_MODEL_EVOLUTION")
    assert not is_stage_enabled("SELF_MODEL_BUILD")
    assert not is_stage_enabled("PERCEPTION_OBSERVATION")
    assert not is_stage_enabled("SELF_MODEL_VALIDATION")


def test_apply_idempotent():
    set_runtime_cognitive_activation_enabled(True)
    assert apply_runtime_cognitive_activation() is True
    assert apply_runtime_cognitive_activation() is True  # 重复调用安全


# ============================================================
# Phase 3 检查 1-7
# ============================================================
def _run_staging_cycle():
    """staging 开启后跑完整 17 阶段，返回 (core, ctx)。"""
    set_runtime_cognitive_activation_enabled(True)
    set_timeline(CognitiveTimeline())
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())
    return core, ctx


def test_runtime_cycle_runs_completely_and_timeline_chain():
    """检查 1 + 2：17 阶段完整 + Timeline 完整事件链。"""
    core, ctx = _run_staging_cycle()

    assert len(core.calls) == 17  # 主循环完整

    bus_types = [e.event_type for e in core.domain_event_bus.events]
    assert bus_types == EXPECTED_FULL_CHAIN

    nodes = get_timeline().get_events()
    assert [n.event_type for n in nodes] == bus_types  # 顺序一致
    assert ctx is not None


def test_trace_id_continuous():
    """检查 3：Timeline 全部节点 trace_id 连续（== 本轮 session_id）。"""
    core, ctx = _run_staging_cycle()
    assert all(n.trace_id == ctx.session_id
               for n in get_timeline().get_events())


def test_reflection_produces_record():
    """检查 4：Reflection 产生记录（挂 ctx，不写 store）。"""
    core, ctx = _run_staging_cycle()
    record = ctx._b15_reflection_record
    assert record
    assert record["reflection_id"]
    completed = [e for e in core.domain_event_bus.events
                 if e.event_type == "reflection_completed"]
    assert len(completed) == 1
    assert completed[0].payload["reflection_id"] == record["reflection_id"]


def test_self_model_unchanged():
    """检查 5：SelfModel 快照零修改。"""
    snapshot = _snapshot_for_reflection()
    snapshot_before = copy.deepcopy(snapshot)
    set_runtime_cognitive_activation_enabled(True)
    set_timeline(CognitiveTimeline())
    core = _make_fake_core(snapshot=snapshot)
    LifecycleExecutor().execute(core, None, RuntimeContext())

    assert snapshot == snapshot_before
    assert core.get_self_model_full() == snapshot_before
    assert len(core.calls) == 17  # 无任何额外写入方法被调用


def test_mutation_gateway_no_calls():
    """检查 6：MutationGateway 零请求/零决策/零记录。"""
    from src.governance.audit_writer import InMemoryAuditWriter
    from src.governance.mutation_gateway import MutationGateway

    gateway = MutationGateway(audit_writer=InMemoryAuditWriter())
    set_runtime_cognitive_activation_enabled(True)
    set_timeline(CognitiveTimeline())
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    core._mutation_gateway_probe = gateway  # 运行前挂载
    LifecycleExecutor().execute(core, None, RuntimeContext())

    assert gateway.audit_writer.requests == []
    assert gateway.audit_writer.decisions == []
    assert gateway.audit_writer.records == []
    assert get_timeline().count() > 0  # timeline 有记录，但零 mutation


def test_flag_off_restores_behavior():
    """检查 7：staging 关闭（默认）→ 行为与 B.15 Phase 4 A 态一致。"""
    set_timeline(CognitiveTimeline())
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    assert len(core.calls) == 17
    assert core.domain_event_bus.events == []  # 零事件
    assert get_timeline().count() == 0  # 零节点
    assert not getattr(ctx, "_b15_reflection_record", None)  # 零反思记录
