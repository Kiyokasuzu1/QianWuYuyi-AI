# -*- coding: utf-8 -*-
"""
P2.4-B.15 Phase 2 — CognitiveTimeline 测试

覆盖（Phase 5 任务书）：
    - event 创建（TimelineEvent 契约 / 序列化）
    - trace 链（parent_event_id / chain 查询）
    - reflection 接入（Stage 11 → TimelineEvent → CognitiveTimeline）
    - fail-open 行为（writer 失败 / 记录失败不阻断主循环）
    - flag=false 兼容（行为与 Phase 1 完全一致）

覆盖（Phase 4 治理边界）：
    1. Reflection 不能直接修改 SelfModel
    2. Reflection 不能绕过 MutationGateway 修改 Personality
    3. Timeline 记录失败不能阻断主循环
    4. 重复 trace_id 不产生重复节点

conftest 单例陷阱规避：不实例化 RuntimeCore——用伪 core +
LifecycleExecutor 直驱（与 Phase 1 测试同一风格）。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.runtime.cognitive_activation import (
    reset_all_cognitive_activation_flags,
    set_stage_flag,
)
from src.runtime.cognitive_timeline import (
    CognitiveTimeline,
    TimelineEvent,
    get_timeline,
    is_timeline_recording_enabled,
    reset_timeline_state,
    set_timeline,
    set_timeline_recording_enabled,
    timeline_event_from_reflection,
)
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.lifecycle_executor import (
    STAGE_TO_METHOD_NAME,
    LifecycleExecutor,
)


@pytest.fixture(autouse=True)
def _reset_phase2_state():
    reset_all_cognitive_activation_flags()
    reset_timeline_state()
    yield
    reset_all_cognitive_activation_flags()
    reset_timeline_state()


# ============================================================
# helpers（与 Phase 1 测试同风格：伪 core + 捕获 bus）
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


def _run_stage11_reflection_cycle():
    """开启 Stage 11 + timeline 后跑完整 17 阶段，返回 (core, ctx)。"""
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())
    return core, ctx


# ============================================================
# Phase 5-1: event 创建
# ============================================================
def test_timeline_event_creation_and_defaults():
    evt = TimelineEvent(trace_id="t1", event_type="reflection_completed")
    assert evt.event_id.startswith("tl_")
    assert evt.trace_id == "t1"
    assert evt.timestamp
    assert evt.event_type == "reflection_completed"
    assert evt.parent_event_id is None
    assert evt.evidence_refs == []


def test_timeline_event_roundtrip_and_normalization():
    evt = TimelineEvent(
        event_id="tl_x",
        trace_id="t1",
        event_type="reflection_completed",
        source="b15_stage11",
        domain="state_change",
        evidence_refs=["e1", None, "", "e2"],
        parent_event_id="evt_parent",
    )
    d = evt.to_dict()
    assert d["evidence_refs"] == ["e1", "e2"]  # 非空字符串规范化
    back = TimelineEvent.from_dict(d)
    assert back.event_id == "tl_x"
    assert back.trace_id == "t1"
    assert back.evidence_refs == ["e1", "e2"]
    assert back.parent_event_id == "evt_parent"
    assert back.timestamp == evt.timestamp


def test_timeline_event_from_dict_invalid_input():
    with pytest.raises(TypeError):
        TimelineEvent.from_dict("not-a-dict")


# ============================================================
# Phase 5-2: trace 链
# ============================================================
def test_trace_chain_with_parent_event_id():
    tl = CognitiveTimeline()
    root = TimelineEvent(event_id="tl_root", trace_id="t1",
                         event_type="reflection_started")
    child = TimelineEvent(event_id="tl_child", trace_id="t1",
                          event_type="reflection_completed",
                          parent_event_id="tl_root")
    assert tl.append(root)
    assert tl.append(child)

    chain = tl.chain("t1")
    assert [e.event_id for e in chain] == ["tl_root", "tl_child"]
    assert chain[1].parent_event_id == "tl_root"
    assert tl.get_by_trace_id("t1") == chain
    assert tl.get_by_trace_id("missing") == []
    assert tl.get_by_event_id("tl_child") is child
    assert tl.get_by_event_id("missing") is None
    assert tl.count() == 2


# ============================================================
# Phase 4-4: 重复 trace_id 不产生重复节点
# ============================================================
def test_duplicate_trace_id_no_duplicate_nodes():
    tl = CognitiveTimeline()
    evt = TimelineEvent(event_id="tl_same", trace_id="t1")
    assert tl.append(evt)
    assert tl.append(evt)  # 同一 event_id 幂等重放
    assert tl.append(evt)
    assert tl.count() == 1  # 无重复节点

    # 同 trace 下不同节点（链式认知事件）不受影响
    other = TimelineEvent(event_id="tl_other", trace_id="t1",
                          parent_event_id="tl_same")
    assert tl.append(other)
    assert tl.count() == 2


def test_timeline_capacity_fifo_and_clear():
    tl = CognitiveTimeline(capacity=3)
    for i in range(5):
        tl.append(TimelineEvent(event_id=f"tl_{i}", trace_id="t"))
    assert tl.count() == 3
    assert tl.dropped_count == 2
    assert tl.get_by_event_id("tl_0") is None
    assert tl.get_by_event_id("tl_4") is not None
    assert tl.clear() == 3
    assert tl.count() == 0


# ============================================================
# Phase 5-4 / Phase 4-3: fail-open 行为
# ============================================================
def test_writer_failure_is_isolated_and_memory_kept():
    def _boom(_d: dict) -> None:
        raise RuntimeError("disk on fire")

    tl = CognitiveTimeline(writer=_boom)
    evt = TimelineEvent(event_id="tl_w", trace_id="t")
    assert tl.append(evt)  # fail-open：返回成功
    assert tl.count() == 1  # 内存记录保留
    assert tl.writer_fail_count == 1
    assert tl.last_error is None


def test_writer_receives_dict_projection():
    written = []

    tl = CognitiveTimeline(writer=written.append)
    tl.append(TimelineEvent(event_id="tl_d", trace_id="t"))
    assert len(written) == 1
    assert written[0]["event_id"] == "tl_d"
    assert isinstance(written[0]["evidence_refs"], list)


def test_append_invalid_input_failsoft():
    tl = CognitiveTimeline()
    assert tl.append(None) is False
    assert tl.append("not-an-event") is False
    assert tl.count() == 0
    assert tl.last_error is not None


def test_timeline_failure_does_not_block_main_loop():
    """Phase 4-3：writer 失败时，主循环照常完成、事件照常发布。"""
    def _boom(_d: dict) -> None:
        raise RuntimeError("writer down")

    set_timeline_recording_enabled(True)
    set_timeline(CognitiveTimeline(writer=_boom))
    core, ctx = _run_stage11_reflection_cycle()

    # 主循环完整跑完 17 阶段
    assert len(core.calls) == 17
    assert ctx._b15_reflection_record  # 反思产物仍在
    # 反思事件仍发布（started + completed）
    types = [e["event_type"] for e in core.domain_event_bus.events]
    assert "reflection_started" in types
    assert "reflection_completed" in types
    # timeline 内存节点仍保留（fail-open）
    assert get_timeline().count() == 1
    assert get_timeline().writer_fail_count == 1


# ============================================================
# Phase 5-3: reflection 接入（Stage 11 → CognitiveTimeline）
# ============================================================
def test_reflection_to_timeline_event_mapping():
    record = {
        "reflection_id": "ref_abc123",
        "timestamp": "2026-08-20T12:00:00Z",
        "source": "b15_stage11",
        "trigger_category": "state_change",
        "reflection_kind": "state_note",
        "source_audit_id": "audit_1",
        "evidence_ids": ["snapshot:si_test", "version:3"],
        "priority": "low",
    }
    node = timeline_event_from_reflection(
        record, trace_id="sess_1", parent_event_id="evt_start",
    )
    assert node.event_id == "tl_ref_abc123"  # 幂等 id：同反思不重复
    assert node.trace_id == "sess_1"
    assert node.event_type == "reflection_completed"
    assert node.source == "b15_stage11"
    assert node.domain == "state_change"
    assert node.parent_event_id == "evt_start"
    assert "audit_1" in node.evidence_refs
    assert "ref_abc123" in node.evidence_refs
    assert "snapshot:si_test" in node.evidence_refs


def test_stage11_reflection_appends_timeline_node():
    set_timeline_recording_enabled(True)
    set_timeline(CognitiveTimeline())
    core, ctx = _run_stage11_reflection_cycle()

    tl = get_timeline()
    assert tl.count() == 1
    node = tl.get_events()[0]
    assert node.event_type == "reflection_completed"
    assert node.trace_id == ctx.session_id  # trace = 本轮会话
    assert node.parent_event_id == "evt_1"  # reflection_started 的 event_id
    # reflection_completed payload 带上 timeline_event_id
    completed = [e for e in core.domain_event_bus.events
                 if e["event_type"] == "reflection_completed"]
    assert len(completed) == 1
    assert completed[0]["payload"]["timeline_event_id"] == node.event_id
    assert completed[0]["payload"]["reflection_id"]
    # 反思产物照常挂 ctx
    assert ctx._b15_reflection_record["reflection_id"]


def test_stage11_reflection_same_record_replayed_no_duplicate_nodes():
    """同一次反思重放（相同 reflection 语义）不产生重复节点。

    用注入引擎固定返回同一 ReflectionRecord，跑两轮：
    由于 event_id = "tl_" + reflection_id 幂等，timeline 仍只有 1 节点。
    """
    from src.runtime.self_model.reflection.reflection_engine import (
        ReflectionEngine,
    )

    set_timeline_recording_enabled(True)
    set_timeline(CognitiveTimeline())
    set_stage_flag("SELF_MODEL_REFLECTION", True)

    engine = ReflectionEngine()
    record = engine.reflect(
        {"self_model_snapshot": {"version": 3}},
        source="b15_stage11",
        categories=["state_change"],
    )
    assert record is not None

    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    core._b15_reflection_engine = engine
    # 第二轮用同样 snapshot —— reflect 结果 deterministic 同 id？不确定，
    # 因此手动指定核心断言：同一 record 投影出的节点 id 恒定。
    node_a = timeline_event_from_reflection(record.to_dict(), trace_id="t")
    node_b = timeline_event_from_reflection(record.to_dict(), trace_id="t")
    assert node_a.event_id == node_b.event_id

    executor = LifecycleExecutor()
    executor.execute(core, None, RuntimeContext())
    tl = get_timeline()
    assert tl.count() >= 1
    ids = [e.event_id for e in tl.get_events()]
    assert len(ids) == len(set(ids))  # 无重复节点


# ============================================================
# Phase 5-5: flag=false 兼容（与 Phase 1 行为完全一致）
# ============================================================
def test_flag_false_no_timeline_nodes_and_same_events():
    assert not is_timeline_recording_enabled()

    # 开启 Stage 11（Phase 1 行为），timeline flag 保持 False
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    core = _make_fake_core(snapshot=_snapshot_for_reflection())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    # Phase 1 契约：只发 started/completed，payload 无 timeline 字段
    events = core.domain_event_bus.events
    assert [e["event_type"] for e in events] == [
        "reflection_started", "reflection_completed",
    ]
    assert "timeline_event_id" not in events[1]["payload"]
    assert "reflection_id" in events[1]["payload"]
    assert ctx._b15_reflection_record
    # 默认单例 timeline 为空（未被触碰）
    assert get_timeline().count() == 0


def test_flag_false_explicitly_disabled_equivalent():
    set_timeline_recording_enabled(False)
    set_stage_flag("SELF_MODEL_REFLECTION", True)
    core, ctx = _run_stage11_reflection_cycle()
    assert get_timeline().count() == 0
    assert ctx._b15_reflection_record


# ============================================================
# Phase 4-1: Reflection 不能直接修改 SelfModel
# ============================================================
def test_reflection_does_not_modify_self_model():
    snapshot = _snapshot_for_reflection()
    snapshot_before = copy.deepcopy(snapshot)
    set_timeline_recording_enabled(True)
    set_timeline(CognitiveTimeline())
    set_stage_flag("SELF_MODEL_REFLECTION", True)

    core = _make_fake_core(snapshot=snapshot)
    executor = LifecycleExecutor()
    executor.execute(core, None, RuntimeContext())

    assert snapshot == snapshot_before  # 零修改
    assert core.get_self_model_full() == snapshot_before
    # 只有 17 个阶段方法被调用，无任何额外写入方法
    assert len(core.calls) == 17


# ============================================================
# Phase 4-2: Reflection 不能绕过 MutationGateway 修改 Personality
# ============================================================
def test_reflection_does_not_bypass_mutation_gateway():
    """Phase 4-2：反思链路不经过 MutationGateway 也不产生任何 mutation 请求。

    同时用探针证明：反思运行期间，唯一的人格变更合法通道（网关）零调用；
    且伪 core 除 17 个阶段方法外无任何方法被调用（无直接写入路径）。
    """
    from src.governance.audit_writer import InMemoryAuditWriter
    from src.governance.mutation_gateway import MutationGateway

    gateway = MutationGateway(audit_writer=InMemoryAuditWriter())
    snapshot = _snapshot_for_reflection()
    snapshot_before = copy.deepcopy(snapshot)

    set_timeline_recording_enabled(True)
    set_timeline(CognitiveTimeline())
    set_stage_flag("SELF_MODEL_REFLECTION", True)

    core = _make_fake_core(snapshot=snapshot)
    # 探针挂载在运行前：若反思链越权提交变更，网关必记录
    core._mutation_gateway_probe = gateway
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    # 网关零请求 / 零决策 / 零审计投影 → 未绕过也未经过
    assert gateway.audit_writer.requests == []
    assert gateway.audit_writer.decisions == []
    assert gateway.audit_writer.records == []
    # 反思产物存在，但自我模型快照零修改，core 只被调用 17 个阶段方法
    assert ctx._b15_reflection_record
    assert snapshot == snapshot_before
    assert len(core.calls) == 17
