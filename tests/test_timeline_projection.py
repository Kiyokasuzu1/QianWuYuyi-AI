# -*- coding: utf-8 -*-
"""
P2.4-B.15 Phase 3 — Timeline Event Projection 测试

覆盖（Phase 4 任务书）：
    1. EventBus 事件可以投影 Timeline
    2. flag=false 无 Timeline 变化
    3. 重复 event_id 幂等
    4. Timeline 异常不影响 Runtime
    5. Timeline 不能触发 MutationGateway
    6. Timeline 不能修改 SelfModel

conftest 单例陷阱规避：不实例化 RuntimeCore——用伪 core +
LifecycleExecutor 直驱（与 Phase 1/2 测试同一风格）。
domain_event_bus 用捕获型 bus 直接构造真实 RuntimeDomainEvent
（不触全局 EventBus 单例）。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.contracts.runtime_event_schema import RuntimeDomainEvent
from src.runtime.cognitive_activation import (
    reset_all_cognitive_activation_flags,
    set_lifecycle_cycle_events_enabled,
    set_stage_flag,
)
from src.runtime.cognitive_timeline import (
    CognitiveTimeline,
    get_timeline,
    reset_timeline_state,
    set_timeline,
)
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.lifecycle_executor import (
    STAGE_TO_METHOD_NAME,
    LifecycleExecutor,
)
from src.runtime.timeline_projection import (
    PROJECTABLE_EVENT_TYPES,
    is_projectable_event,
    is_timeline_event_projection_enabled,
    project_domain_event,
    reset_timeline_projection_state,
    set_timeline_event_projection_enabled,
)


@pytest.fixture(autouse=True)
def _reset_phase3_state():
    reset_all_cognitive_activation_flags()
    reset_timeline_state()
    reset_timeline_projection_state()
    yield
    reset_all_cognitive_activation_flags()
    reset_timeline_state()
    reset_timeline_projection_state()


# ============================================================
# helpers（伪 core + 构造真实 RuntimeDomainEvent 的捕获 bus）
# ============================================================
class _ProjectionCaptureBus:
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
    """17 阶段方法齐全的伪 core（分派契约与生产一致）。"""
    core = SimpleNamespace(
        calls=[],
        domain_event_bus=_ProjectionCaptureBus(),
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


def _run_projected_cycle(*, snapshot: dict | None = None):
    """cycle 事件 + 投影 flag 全开，跑完整 17 阶段，返回 (core, ctx)。"""
    set_lifecycle_cycle_events_enabled(True)
    set_timeline_event_projection_enabled(True)
    set_timeline(CognitiveTimeline())
    core = _make_fake_core(snapshot=snapshot)
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())
    return core, ctx


# ============================================================
# Phase 4-1: EventBus 事件可以投影 Timeline
# ============================================================
def test_eventbus_events_are_projected_to_timeline():
    core, ctx = _run_projected_cycle()

    bus_types = [e.event_type for e in core.domain_event_bus.events]
    assert bus_types[0] == "cycle_started"
    assert bus_types[-1] == "cycle_completed"

    tl = get_timeline()
    nodes = tl.get_events()
    # 每个 emit 投影一个节点，顺序与发布一致
    assert tl.count() == len(bus_types)
    assert [n.event_type for n in nodes] == bus_types
    # trace = 本轮会话（lifecycle_executor 传入 ctx.session_id）
    assert all(n.trace_id == ctx.session_id for n in nodes)
    # 非 reflection 事件：节点 id = domain event id（可追溯）
    assert [n.event_id for n in nodes] == [
        e.event_id for e in core.domain_event_bus.events
    ]


def test_reflection_completed_projected_with_phase2_merge_id():
    """reflection_completed 投影 id = tl_<reflection_id>（与 Phase 2 同 id）。"""
    set_lifecycle_cycle_events_enabled(True)
    set_timeline_event_projection_enabled(True)
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    set_timeline(CognitiveTimeline())

    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    nodes = get_timeline().get_events()
    reflection_nodes = [n for n in nodes
                        if n.event_type == "reflection_completed"]
    assert len(reflection_nodes) == 1
    record = ctx._b15_reflection_record
    assert reflection_nodes[0].event_id == "tl_" + record["reflection_id"]
    assert reflection_nodes[0].domain == "reflection"
    assert reflection_nodes[0].trace_id == ctx.session_id


# ============================================================
# Phase 4-2: flag=false 无 Timeline 变化
# ============================================================
def test_flag_false_no_timeline_changes():
    assert not is_timeline_event_projection_enabled()
    set_lifecycle_cycle_events_enabled(True)  # Phase 1 行为开启
    set_timeline(CognitiveTimeline())

    core = _make_fake_core()
    executor = LifecycleExecutor()
    executor.execute(core, None, RuntimeContext())

    # 事件照常发布（Phase 1 契约不变）
    assert core.domain_event_bus.events[0].event_type == "cycle_started"
    assert core.domain_event_bus.events[-1].event_type == "cycle_completed"
    # 投影 flag 默认 False → timeline 零节点
    assert get_timeline().count() == 0
    assert len(core.calls) == 17


# ============================================================
# Phase 4-3: 重复 event_id 幂等
# ============================================================
def test_projection_deterministic_event_id_and_dedup():
    event = RuntimeDomainEvent(
        event_type="cycle_started",
        source="lifecycle_executor",
        payload={"stages": 17},
    )
    node_a = project_domain_event(event, trace_id="sess_1")
    node_b = project_domain_event(event, trace_id="sess_1")
    assert node_a is not None and node_b is not None
    assert node_a.event_id == event.event_id
    assert node_a.event_id == node_b.event_id  # 确定性投影

    tl = CognitiveTimeline()
    assert tl.append(node_a)
    assert tl.append(node_b)  # 同一 event_id 重放
    assert tl.count() == 1  # 幂等：不产生重复节点


def test_reflection_completed_projection_merges_with_phase2_node():
    event = RuntimeDomainEvent(
        event_type="reflection_completed",
        payload={"reflection_id": "ref_x", "stage": "SELF_MODEL_REFLECTION"},
        related_ids=["audit_9"],
    )
    node = project_domain_event(event, trace_id="sess_1")
    assert node is not None
    assert node.event_id == "tl_ref_x"  # 与 Phase 2 record 投影同 id
    assert node.domain == "reflection"
    assert "audit_9" in node.evidence_refs
    assert "ref_x" in node.evidence_refs

    # 不同 domain event 但同 reflection_id → 投影同节点 id → 合并去重
    event2 = RuntimeDomainEvent(
        event_type="reflection_completed",
        payload={"reflection_id": "ref_x"},
    )
    node2 = project_domain_event(event2, trace_id="sess_1")
    assert node2 is not None and node2.event_id == "tl_ref_x"
    tl = CognitiveTimeline()
    tl.append(node)
    tl.append(node2)
    assert tl.count() == 1


# ============================================================
# Phase 4-4: Timeline 异常不影响 Runtime
# ============================================================
def test_projection_exception_does_not_affect_runtime(monkeypatch):
    def _boom(_event, *, trace_id="", parent_event_id=None):
        raise RuntimeError("projection down")

    import src.runtime.timeline_projection as _tp

    monkeypatch.setattr(_tp, "project_domain_event", _boom)
    set_lifecycle_cycle_events_enabled(True)
    set_timeline_event_projection_enabled(True)
    set_timeline(CognitiveTimeline())

    core = _make_fake_core()
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    assert ctx is not None
    assert len(core.calls) == 17  # 主循环完整
    assert core.domain_event_bus.events  # 事件照常发布（emit 在投影前）
    assert get_timeline().count() == 0  # 投影失败 → 无节点，不阻断


def test_timeline_append_failure_does_not_affect_runtime(monkeypatch):
    class _BrokenTimeline:
        def append(self, node):
            raise RuntimeError("append down")

    import src.runtime.cognitive_timeline as _ct

    monkeypatch.setattr(_ct, "get_timeline", lambda: _BrokenTimeline())
    set_lifecycle_cycle_events_enabled(True)
    set_timeline_event_projection_enabled(True)

    core = _make_fake_core()
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    assert len(core.calls) == 17
    assert core.domain_event_bus.events
    assert ctx is not None


# ============================================================
# Phase 4-5: Timeline 不能触发 MutationGateway
# ============================================================
def test_projection_does_not_bypass_mutation_gateway():
    from src.governance.audit_writer import InMemoryAuditWriter
    from src.governance.mutation_gateway import MutationGateway

    gateway = MutationGateway(audit_writer=InMemoryAuditWriter())
    snapshot = _snapshot_for_reflection()
    snapshot_before = copy.deepcopy(snapshot)

    set_lifecycle_cycle_events_enabled(True)
    set_timeline_event_projection_enabled(True)
    set_timeline(CognitiveTimeline())

    core = _make_fake_core(snapshot=snapshot)
    # 探针运行前挂载：若投影/反思链越权提交变更，网关必记录
    core._mutation_gateway_probe = gateway
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    assert gateway.audit_writer.requests == []
    assert gateway.audit_writer.decisions == []
    assert gateway.audit_writer.records == []
    assert ctx is not None
    assert snapshot == snapshot_before
    assert len(core.calls) == 17
    assert get_timeline().count() > 0  # timeline 有记录，但零 mutation


# ============================================================
# Phase 4-6: Timeline 不能修改 SelfModel
# ============================================================
def test_projection_does_not_modify_self_model():
    snapshot = _snapshot_for_reflection()
    snapshot_before = copy.deepcopy(snapshot)

    # 全开：cycle 事件 + 投影 + Stage 11 reflection
    set_lifecycle_cycle_events_enabled(True)
    set_timeline_event_projection_enabled(True)
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    set_timeline(CognitiveTimeline())

    core = _make_fake_core(snapshot=snapshot)
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    assert snapshot == snapshot_before  # 自我模型零修改
    assert core.get_self_model_full() == snapshot_before
    assert len(core.calls) == 17  # 无任何额外写入方法被调用
    assert get_timeline().count() > 0  # timeline 有记录，但不改权威状态
    assert ctx._b15_reflection_record  # 反思产物仅挂 ctx，不写 store


# ============================================================
# 投影单元测试（支撑 Phase 4-1 / 4-3）
# ============================================================
def test_projection_field_mapping():
    event = RuntimeDomainEvent(
        event_type="cycle_memory_completed",
        source="lifecycle_executor",
        source_id="src_x",
        payload={"stage": "MEMORY_RETRIEVAL"},
        related_ids=["m_1", "m_2", "m_1"],
    )
    node = project_domain_event(
        event, trace_id="sess_9", parent_event_id="evt_parent",
    )
    assert node is not None
    assert node.event_id == event.event_id
    assert node.trace_id == "sess_9"
    assert node.event_type == "cycle_memory_completed"
    assert node.source == "lifecycle_executor"
    assert node.domain == "memory"
    assert node.evidence_refs == ["m_1", "m_2"]  # 去重
    assert node.parent_event_id == "evt_parent"
    assert node.timestamp == event.timestamp


def test_projection_unsupported_and_malformed_failsoft():
    # 不支持的事件类型 → None
    other = RuntimeDomainEvent(event_type="identity_changed", payload={})
    assert project_domain_event(other) is None
    # 无 event_id 且无 reflection_id → None（不抛异常）
    broken = SimpleNamespace(event_type="cycle_started", payload={})
    assert project_domain_event(broken) is None
    # 畸形输入 → None
    assert project_domain_event(None) is None
    assert project_domain_event("not-an-event") is None
    assert project_domain_event(object()) is None


def test_is_projectable_event_and_domain_map():
    assert is_projectable_event("cycle_started")
    assert is_projectable_event("reflection_completed")
    assert is_projectable_event("cycle_failed")
    assert not is_projectable_event("identity_changed")
    assert not is_projectable_event("")
    assert not is_projectable_event(None)
    assert "cycle_started" in PROJECTABLE_EVENT_TYPES
    assert "cycle_memory_completed" in PROJECTABLE_EVENT_TYPES
    assert "cycle_emotion_completed" in PROJECTABLE_EVENT_TYPES
    assert "cycle_growth_completed" in PROJECTABLE_EVENT_TYPES
    assert "cycle_personality_completed" in PROJECTABLE_EVENT_TYPES
    assert "reflection_started" in PROJECTABLE_EVENT_TYPES
    assert "cycle_completed" in PROJECTABLE_EVENT_TYPES
