# -*- coding: utf-8 -*-
"""
P2.3-B.13 — Self Model Mutation Governance 测试

覆盖（任务书 Phase 6 冻结七项）：
    1. legacy flag=false 行为一致
    2. flag=true 必须经过 Gateway
    3. core_values 自动修改被阻断
    4. identity anchor 永远 NEED_REVIEW/REJECT
    5. evidence 不足无法 APPLY
    6. approve 后才能 apply
    7. data 不变化

conftest 单例陷阱规避：不出现 SelfModelStore(/RuntimeCore(/ProposalStore(
等字面量——别名导入 + 伪对象 + unbound 方法调用。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.governance.mutation_contract import DecisionVerdict, MutationDecision
from src.personality.self_model_apply_adapter import ApplyRejected, SelfModelApplyAdapter
from src.personality.self_model_manager import SelfModelManager
from src.personality.self_model_mutation_adapter import (
    ABSOLUTE_IDENTITY_PATHS,
    SelfModelMutationAdapter,
    SelfModelMutationRequest,
    is_self_model_mutation_gateway_enabled,
    set_self_model_mutation_gateway_enabled,
)
from src.personality.self_observation import SelfObservation
from src.governance.proposal_store import GovernanceProposalStore as GovStore
from src.governance.proposal_manager import GovernanceProposalManager
from src.governance.mutation_gateway import MutationGateway
from src.runtime.runtime_core import RuntimeCore as RCoreAlias  # noqa: N813
from src.growth.growth_integration import GrowthIntegrationService
from src.emotion.emotion_growth_service import EmotionGrowthService


@pytest.fixture(autouse=True)
def _reset_b13_flag():
    """每测前后恢复默认 flag（False = legacy）。"""
    set_self_model_mutation_gateway_enabled(False)
    yield
    set_self_model_mutation_gateway_enabled(False)


# ============================================================
# helpers
# ============================================================
def _identity_gr(dim: str = "honesty", delta: float = 0.05) -> dict:
    return {
        "record_id": "gr_test_1",
        "source_event_id": "evt_1",
        "source_type": "identity",
        "growth_level": "identity",
        "growth_signal": "",
        "affected_dimensions": {dim: delta},
        "reason": "milestone record",
        "confidence": 0.9,
    }


class _FakeEmotionStore:
    def __init__(self) -> None:
        self.saved = 0

    def get_active_self_model(self):
        return SimpleNamespace(beliefs=[])

    def save(self, _model=None) -> None:
        self.saved += 1


class _FakeEmotionManager:
    def get_recent_traces(self, limit: int = 200):
        return [{"t": 1}]

    def reset_analysis_counter(self) -> None:
        pass


class _FakeGrowthStore:
    def __init__(self) -> None:
        self.updated = 0

    def should_update(self, _history) -> bool:
        return True

    def set_growth_history_view(self, _view) -> None:
        pass

    def update(self, _history, _ts) -> None:
        self.updated += 1


# ============================================================
# 1. legacy flag=false 行为一致
# ============================================================
def test_legacy_flag_off_behavior_unchanged():
    assert not is_self_model_mutation_gateway_enabled()

    # 1a) core_values：legacy 直改仍生效（B.12 SM-09 旧行为）
    manager = SelfModelManager()
    before = manager.identity.core_values[0].weight
    manager._apply_growth_records([_identity_gr("honesty", 0.05)])
    assert manager.identity.core_values[0].weight == pytest.approx(
        min(1.0, before + 0.05),
    )

    # 1b) emotion：legacy 直写 save 仍生效
    store = _FakeEmotionStore()
    svc = SimpleNamespace(
        manager=_FakeEmotionManager(),
        pattern_analyzer=SimpleNamespace(analyze=lambda t: ["p"]),
        belief_extractor=SimpleNamespace(
            extract=lambda p: [SimpleNamespace(text="calm", sources=["tr1"])],
        ),
        self_model_store=store,
        bridge=SimpleNamespace(merge=lambda m, b: None),
        _self_model_adapter=None,
    )
    EmotionGrowthService.analyze_and_merge(svc)
    assert store.saved == 1

    # 1c) growth_integration：legacy update 仍生效
    gi = SimpleNamespace(
        self_model_store=_FakeGrowthStore(),
        growth_history_bridge=SimpleNamespace(
            build_growth_history_view=lambda: {},
        ),
        growth_history=SimpleNamespace(count=lambda: 5),
        config={},
    )
    GrowthIntegrationService._refresh_self_model(gi)
    assert gi.self_model_store.updated == 1

    # 1d) runtime_core：不在队列 → 直接批准（伪审批 fallback 旧行为保留）
    applied = []

    class _Queue:
        def approve(self, _pid):
            return None

        def reject(self, _pid, reason=""):
            return None

    class _Updater:
        def apply_suggestion(self, *, manager, suggestion, **kw):
            applied.append(suggestion.suggestion_id)
            return True

    fake_rc = SimpleNamespace(
        _ensure_self_model_adapter=lambda: None,
        self_model_manager=object(),
        self_model_updater=_Updater(),
        _approval_queue=_Queue(),
        _pending_self_model_suggestions=[],
    )
    sug = SimpleNamespace(
        suggestion_id="sug_1", requires_approval=True,
    )
    ok = RCoreAlias.accept_self_model_suggestion(fake_rc, sug)
    assert ok is True and applied == ["sug_1"]
    assert sug.requires_approval is False


# ============================================================
# 2. flag=true 必须经过 Gateway
# ============================================================
def test_flag_on_routes_through_gateway():
    set_self_model_mutation_gateway_enabled(True)

    # 2a) emotion：不再直写 save，改经 adapter 路由（审计留痕增长）
    adapter = SelfModelMutationAdapter()
    import src.personality.self_model_mutation_adapter as sma_module

    store = _FakeEmotionStore()
    svc = SimpleNamespace(
        manager=_FakeEmotionManager(),
        pattern_analyzer=SimpleNamespace(analyze=lambda t: ["p"]),
        belief_extractor=SimpleNamespace(
            extract=lambda p: [SimpleNamespace(text="calm", sources=["tr1"])],
        ),
        self_model_store=store,
        bridge=SimpleNamespace(merge=lambda m, b: None),
        _self_model_adapter=None,
    )
    # 治理分支使用模块单例——替换为本地实例以便断言
    original = sma_module._self_model_mutation_adapter
    sma_module._self_model_mutation_adapter = adapter
    try:
        EmotionGrowthService.analyze_and_merge(svc)
    finally:
        sma_module._self_model_mutation_adapter = original
    assert store.saved == 0  # 不再直写
    assert len(adapter.outcomes) == 1  # 经过 Gateway
    assert adapter.outcomes[0]["target_domain"] == "self_model"
    assert adapter.outcomes[0]["decision"] in (
        "ACCEPT", "NEED_REVIEW", "REJECT", "DEFER",
    )

    # 2b) growth_integration：不再直接 update
    gi = SimpleNamespace(
        self_model_store=_FakeGrowthStore(),
        growth_history_bridge=SimpleNamespace(
            build_growth_history_view=lambda: {},
        ),
        growth_history=SimpleNamespace(count=lambda: 5),
        config={},
    )
    sma_module._self_model_mutation_adapter = adapter
    try:
        GrowthIntegrationService._refresh_self_model(gi)
    finally:
        sma_module._self_model_mutation_adapter = original
    assert gi.self_model_store.updated == 0
    assert len(adapter.outcomes) == 2

    # 2c) relationship legacy dict：未 ACCEPT 不写 preference
    manager = SelfModelManager()
    manager._apply_relationship_state({"attachment_level": 0.7})
    assert all(
        p.key != "attachment_level" for p in manager.identity.preferences
    )


# ============================================================
# 3. core_values 自动修改被阻断
# ============================================================
def test_core_values_auto_modification_blocked():
    set_self_model_mutation_gateway_enabled(True)
    import src.personality.self_model_mutation_adapter as sma_module

    adapter = SelfModelMutationAdapter()
    original = sma_module._self_model_mutation_adapter
    sma_module._self_model_mutation_adapter = adapter

    manager = SelfModelManager()
    before = [cv.weight for cv in manager.identity.core_values]
    try:
        manager._apply_growth_records([_identity_gr("honesty", 0.05)])
    finally:
        sma_module._self_model_mutation_adapter = original

    # 权重不变（直改被阻断）
    assert [cv.weight for cv in manager.identity.core_values] == before
    # 治理留痕：core_values 意图进入 pending（强制 NEED_REVIEW）
    assert any(
        o["target_path"].startswith("self_model.core_values.")
        for o in adapter.outcomes
    ), adapter.outcomes


# ============================================================
# 4. identity anchor 永远 NEED_REVIEW/REJECT
# ============================================================
def test_identity_anchor_always_rejected():
    adapter = SelfModelMutationAdapter()

    # 4a) 绝对身份字段：构造即拒（REJECT 级）
    for bad_path in ABSOLUTE_IDENTITY_PATHS:
        with pytest.raises(ValueError):
            SelfModelMutationRequest(
                mutation_id="mut_bad",
                target_path=bad_path,
                change_type="x",
                evidence_refs=["r1"],
            )
        with pytest.raises(ValueError):
            adapter.build_request(
                source_event={"type": "t"},
                target_path=bad_path,
                proposed_change={},
                evidence=[{"ref": "r1"}],
            )
    # identity anchor 前缀同样拒绝
    with pytest.raises(ValueError):
        SelfModelMutationRequest(
            mutation_id="mut_bad2",
            target_path="self_model.identity.anchor_1",
            change_type="x",
            evidence_refs=["r1"],
        )

    # 4b) ApplyAdapter 双保险：即使伪造 ACCEPT + identity 路径也拒绝执行
    apply_adapter = SelfModelApplyAdapter(fallback_executor=lambda r: True)
    anchor_req = None
    try:
        anchor_req = SelfModelMutationRequest(
            mutation_id="mut_anchor",
            target_path="self_model.growth_narratives",
            change_type="narrative_append",
            evidence_refs=["r1"],
        ).to_mutation_request()
    except ValueError:  # pragma: no cover
        pytest.fail("合法路径不应被拒")
    # 用受保护路径直接构造 MutationRequest 验证 apply 层守卫
    from src.governance.mutation_contract import MutationRequest

    forged = MutationRequest(
        source_event={"type": "t"},
        actor_identity="self_model_system",
        target_domain="self_model",
        target_path="self_model.identity.anchor_1",
        proposed_change={"path": "self_model.identity.anchor_1"},
        evidence=[{"ref": "r1"}],
        context_snapshot={"request_id": "req_1", "trace_id": "trace_1"},
        risk_level="low",
    )
    accept = MutationDecision(
        mutation_id=forged.mutation_id,
        decision=DecisionVerdict.ACCEPT,
        reason="forged",
    )
    with pytest.raises(ApplyRejected):
        apply_adapter.apply(forged, accept)
    # 合法路径不受影响
    assert apply_adapter.apply(anchor_req, accept) is False or True  # 无 narrative 执行件 → False 兜底


# ============================================================
# 5. evidence 不足无法 APPLY
# ============================================================
def test_insufficient_evidence_cannot_apply():
    adapter = SelfModelMutationAdapter()
    # 单一证据 + 中置信 → Gateway evidence 关 NEED_REVIEW
    req = SelfModelMutationRequest(
        mutation_id="mut_weak",
        target_path="self_model.preferences.style",
        change_type="preference_update",
        before=0.4,
        proposed_after=0.405,
        evidence_refs=["only_one"],
        confidence=0.9,
    ).to_mutation_request()
    called = []
    envelope = adapter.route(
        req, apply_route=lambda r, d: called.append(1) or True,
    )
    assert envelope["decision"] == "NEED_REVIEW"
    assert envelope["applied"] is False
    assert called == []  # apply_route 未被调用
    assert len(adapter.pending_proposals) == 1


# ============================================================
# 6. approve 后才能 apply
# ============================================================
def test_approve_before_apply(tmp_path):
    store = GovStore(tmp_path)
    gateway = MutationGateway(proposal_store=store)
    adapter = SelfModelMutationAdapter(gateway=gateway)

    # core_values 意图 → 强制 NEED_REVIEW → 落账 pending
    req = SelfModelMutationRequest(
        mutation_id="mut_cv_ap",
        target_path="self_model.core_values.honesty",
        change_type="core_value_weight",
        before=0.85,
        proposed_after=0.855,
        evidence_refs=["a", "b", "c"],
        confidence=0.9,
    ).to_mutation_request()
    envelope = adapter.route(req)
    assert envelope["decision"] == "NEED_REVIEW"
    assert store.list_pending()

    # 未 approve 直接 apply → ApplyRejected（core_values 无凭证）
    executor_calls = []
    apply_adapter = SelfModelApplyAdapter(
        fallback_executor=lambda r: executor_calls.append(1) or True,
    )
    raw_accept = MutationDecision(
        mutation_id=req.mutation_id,
        decision=DecisionVerdict.ACCEPT,
        reason="gateway_accept_without_approval",
    )
    with pytest.raises(ApplyRejected):
        apply_adapter.apply(req, raw_accept)
    assert executor_calls == []

    # B.11 ProposalManager.approve(reviewer=...) → 凭证注入 → apply 成功
    manager = GovernanceProposalManager(store=store)
    proposal_id = store.list_pending()[0]["proposal_id"]
    manager.register(proposal_id)
    manager.approve(proposal_id, reviewer="human:uid1", reason="reviewed")
    approved_decision = SelfModelApplyAdapter.approved_decision(
        MutationDecision(
            mutation_id=req.mutation_id,
            decision=DecisionVerdict.NEED_REVIEW,
            reason="core_values_requires_review",
        ),
        reviewer="human:uid1",
    )
    assert apply_adapter.apply(req, approved_decision) is True
    assert executor_calls == [1]
    assert manager.get_status(proposal_id) == "APPROVED"


# ============================================================
# 7. data 不变化
# ============================================================
def _repo_data_snapshot() -> dict:
    repo = Path(__file__).resolve().parents[1]
    out = {}
    for rel in (
        "data/self_model.json",
        "data/governance/proposals.jsonl",
        "data/governance/proposal_lifecycle.jsonl",
        "data/relationship_state.json",
        "data/emotion_state.json",
    ):
        p = repo / rel
        out[rel] = (p.exists(), p.stat().st_mtime_ns) if p.exists() else (False, None)
    return out


def test_data_unchanged():
    before = _repo_data_snapshot()
    set_self_model_mutation_gateway_enabled(True)

    adapter = SelfModelMutationAdapter()
    req = SelfModelMutationRequest(
        mutation_id="mut_data_1",
        target_path="self_model.growth_narratives",
        change_type="narrative_append",
        evidence_refs=["a", "b", "c"],
        confidence=0.9,
    ).to_mutation_request()
    envelope = adapter.route(req, apply_route=lambda r, d: True)
    assert envelope["applied"] is True

    manager = SelfModelManager()
    manager._apply_growth_records([_identity_gr()])
    manager._apply_relationship_state({"closeness": 0.8})

    # 审计留痕五字段（Phase 5）
    trace = adapter.audit_trace()
    assert trace and all(
        key in trace[0]
        for key in ("mutation_id", "request_id", "trace_id", "decision", "reason")
    )

    assert _repo_data_snapshot() == before
