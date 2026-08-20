# -*- coding: utf-8 -*-
"""
P2.5 Growth Loop 最小激活 — 测试

覆盖（Phase 3 任务书 6 项）：
    1. Growth 事件可以产生 Proposal（内存态 pending，proposed_changes 恒空）
    2. Proposal 经过治理链（测试路径：gateway.evaluate 五道检查 + 审计落账）
    3. MutationGateway 是唯一修改入口（运行时 adapter 零调用）
    4. flag 关闭无行为变化（与旧行为等价）
    5. SelfModel 保护有效（快照零修改）
    6. Personality 变化可审计（评估全程留痕 + 零变化）

边界（P2.5 任务书 + Phase 4.3 红线映射）：
    - growth_loop_activation_enabled 默认 False，独立于 B.15 全部 flag
    - 只允许：事件产生 → evaluator 判断 → proposal 生成（pending）
    - 禁止：自动 apply / 落盘 store / 直调变更引擎 / user_message 推断

conftest 单例陷阱规避：伪 core + 捕获 bus（与 B.15 Phase 3/4/5 测试同风格）。
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from src.contracts.runtime_event_schema import RuntimeDomainEvent
from src.runtime.cognitive_activation import (
    apply_runtime_cognitive_activation,
    is_growth_loop_activation_enabled,
    reset_all_cognitive_activation_flags,
    run_growth_loop_adapter,
    set_growth_loop_activation_enabled,
    set_runtime_cognitive_activation_enabled,
)
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.lifecycle_executor import (
    STAGE_TO_METHOD_NAME,
    LifecycleExecutor,
)


@pytest.fixture(autouse=True)
def _reset_growth_loop_state():
    reset_all_cognitive_activation_flags()
    yield
    reset_all_cognitive_activation_flags()


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


def _snapshot() -> dict:
    return {
        "identity_id": "si_test",
        "version": 3,
        "stable_traits": {"warmth": 0.7, "curiosity": 0.8},
        "growth_history": [{"summary": "g1"}, {"summary": "g2"}],
    }


def _make_fake_core(*, snapshot: dict | None = None) -> SimpleNamespace:
    core = SimpleNamespace(
        calls=[],
        domain_event_bus=_CaptureBus(),
        _last_process_phase_errors={},
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


def _run_growth_cycle():
    """growth loop 开启（B.15 其余 flag 全关）跑完整 17 阶段。"""
    set_growth_loop_activation_enabled(True)
    core = _make_fake_core(snapshot=_snapshot())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())
    return core, ctx


# ============================================================
# 独立 flag 语义
# ============================================================
def test_growth_flag_defaults_false_and_b15_independent():
    reset_all_cognitive_activation_flags()
    assert not is_growth_loop_activation_enabled()

    # B.15 最小激活组合绝不开启 growth loop（不开启无限自主成长）
    set_runtime_cognitive_activation_enabled(True)
    assert apply_runtime_cognitive_activation() is True
    assert not is_growth_loop_activation_enabled()

    # reset 一并复位（可回滚）
    reset_all_cognitive_activation_flags()
    assert not is_growth_loop_activation_enabled()


# ============================================================
# 检查 1：Growth 事件可以产生 Proposal
# ============================================================
def test_growth_event_produces_pending_proposal():
    core, ctx = _run_growth_cycle()
    assert len(core.calls) == 17  # 主循环完整，adapter 不干扰

    trace = ctx.lifecycle_trace["growth_loop_activation"]
    assert trace["status"] == "proposal_created"
    assert trace["growth_allowed"] is True
    assert trace["growth_level"] in ("trace", "context", "preference", "trait")

    proposal = ctx._growth_loop_proposal
    assert proposal.id == "b15gl_" + trace["event_id"]  # 确定性 id，可追溯
    assert proposal.source_event_id == trace["event_id"]
    assert proposal.status == "proposed"  # 只生成 pending，绝不自动 apply
    assert proposal.proposed_changes == []  # 元事件不伪造维度变更
    assert proposal.evidence_ids  # 证据链来自本轮真实阶段轨迹
    assert proposal.confidence > 0
    assert proposal.evaluator_meta["_governance_origin"] == "b15_growth_loop"

    evaluation = ctx._growth_loop_evaluation
    assert evaluation["growth_allowed"] is True
    assert evaluation["event_type"] == "runtime_cognitive_loop"


def test_no_growth_without_evidence():
    """无阶段轨迹时：只评估不生成 proposal（no_growth）。"""
    set_growth_loop_activation_enabled(True)
    ctx = RuntimeContext()
    run_growth_loop_adapter(None, ctx)

    trace = ctx.lifecycle_trace["growth_loop_activation"]
    assert trace["status"] == "no_growth"
    assert trace["growth_allowed"] is False
    assert not getattr(ctx, "_growth_loop_proposal", None)


# ============================================================
# 检查 2 + 6：Proposal 经过治理链（测试路径）+ 可审计
# ============================================================
def test_proposal_passes_governance_chain():
    from src.governance.audit_writer import InMemoryAuditWriter
    from src.governance.mutation_gateway import MutationGateway
    from src.governance.mutation_contract import MutationRequest

    core, ctx = _run_growth_cycle()
    proposal = ctx._growth_loop_proposal

    # 测试专用路径：把生成的 proposal 交给 MutationGateway 走五道检查。
    # 网关只做决策 + 审计，不执行变更；生产链中 adapter 不调用网关。
    gateway = MutationGateway(audit_writer=InMemoryAuditWriter())
    request = MutationRequest(
        source_event={
            "id": proposal.source_event_id,
            "type": "runtime_cognitive_loop",
        },
        actor_identity="growth_system",
        target_domain="growth",
        target_path="growth.pending_proposal",
        proposed_change={
            "proposal_id": proposal.id,
            "proposed_changes": [],
            "confidence": proposal.confidence,
        },
        evidence=[{"id": eid} for eid in proposal.evidence_ids],
        risk_level="medium",
    )
    decision = gateway.evaluate(request)

    assert decision.decision in {"ACCEPT", "NEED_REVIEW", "REJECT", "DEFER"}
    # 五道检查全程留痕：请求 + 决策 + 统一审计投影
    assert len(gateway.audit_writer.requests) == 1
    assert len(gateway.audit_writer.decisions) == 1
    assert len(gateway.audit_writer.records) == 1
    assert gateway.audit_writer.records[0].mutation_id == request.mutation_id
    # 治理链零状态变更：proposal 仍是 pending，未发生任何 apply
    assert proposal.status == "proposed"


def test_personality_change_auditable():
    """检查 6：人格零变化 + 评估/提案/追踪全程留痕可审计。"""
    core, ctx = _run_growth_cycle()

    # 人格与 SelfModel 零修改
    full = core.get_self_model_full()
    assert full["stable_traits"]["warmth"] == 0.7
    assert full["stable_traits"]["curiosity"] == 0.8
    assert len(full["growth_history"]) == 2

    # 审计链完整：事件 id → 评估元数据 → proposal → trace
    trace = ctx.lifecycle_trace["growth_loop_activation"]
    assert trace["event_id"].startswith("b15_gl_")
    assert trace["proposal_created"] is True
    assert ctx._growth_loop_proposal.evidence_ids
    evaluation = ctx._growth_loop_evaluation
    assert evaluation["confidence"] == trace["confidence"]
    assert evaluation["growth_level"] == trace["growth_level"]


# ============================================================
# 检查 3：MutationGateway 是唯一修改入口
# ============================================================
def test_mutation_gateway_is_only_mutation_entry():
    from src.governance.audit_writer import InMemoryAuditWriter
    from src.governance.mutation_gateway import MutationGateway

    gateway = MutationGateway(audit_writer=InMemoryAuditWriter())
    set_growth_loop_activation_enabled(True)
    core = _make_fake_core(snapshot=_snapshot())
    core._mutation_gateway_probe = gateway  # 运行前挂载
    LifecycleExecutor().execute(core, None, RuntimeContext())

    # adapter 运行时零次调用网关（唯一修改入口未被触碰）
    assert gateway.audit_writer.requests == []
    assert gateway.audit_writer.decisions == []
    assert gateway.audit_writer.records == []


# ============================================================
# 检查 4：flag 关闭无行为变化
# ============================================================
def test_flag_off_no_behavior_change():
    reset_all_cognitive_activation_flags()
    core = _make_fake_core(snapshot=_snapshot())
    executor = LifecycleExecutor()
    ctx = executor.execute(core, None, RuntimeContext())

    assert len(core.calls) == 17
    assert core.domain_event_bus.events == []  # 零事件（B.15 全关）
    assert not getattr(ctx, "_growth_loop_proposal", None)
    assert "growth_loop_activation" not in getattr(ctx, "lifecycle_trace", {})


# ============================================================
# 检查 5：SelfModel 保护有效
# ============================================================
def test_self_model_protected():
    snapshot = _snapshot()
    before = copy.deepcopy(snapshot)
    set_growth_loop_activation_enabled(True)
    core = _make_fake_core(snapshot=snapshot)
    LifecycleExecutor().execute(core, None, RuntimeContext())

    assert snapshot == before
    assert core.get_self_model_full() == before
    assert len(core.calls) == 17  # 无任何额外写入方法被调用


# ============================================================
# 附加：零文件 I/O（data/ 不允许生产写入）
# ============================================================
def test_growth_loop_zero_file_io(monkeypatch):
    """adapter 全程不触碰持久化 store：实例化即失败（哨兵）。"""
    import src.growth.proposal_store as proposal_store_module

    instantiations: list = []

    def _boom(*args, **kwargs):
        instantiations.append(1)
        raise AssertionError("growth loop 不允许实例化持久化 store")

    monkeypatch.setattr(proposal_store_module, "ProposalStore", _boom)

    core, ctx = _run_growth_cycle()
    assert ctx._growth_loop_proposal is not None  # 内存 proposal 正常产生
    assert instantiations == []  # 持久化 store 零实例化
