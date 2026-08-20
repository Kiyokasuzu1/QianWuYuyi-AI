# -*- coding: utf-8 -*-
"""P2.6 Phase B-1：治理统一开关（governance_unification_enabled）行为测试。

覆盖目标（任务书 §测试要求）：
- A/B 兼容：flag=False 时返回值 / delta / 写入路径与旧版本一致；
- B 态治理：direct_apply==0、proposal 数量增加、gateway 被调用、
  未 approve 前状态不变、growth_records 门控（pending/defer/reject 不增加，
  仅 ACCEPT 增加）；
- fallback：RuntimeCore 失败进入 orchestrator 后仍走治理路径。

实现约束：
- 全部用 __new__ + getattr 构造对象，不触碰任何有状态单例（conftest 白名单）；
- 统一开关用真实 setter 控制，autouse fixture 每测后复位。
"""
import copy
import types

import pytest

import src.growth.pipeline as pipeline_module
import src.orchestrator as orchestrator_module
from src.contracts.growth_schema import GrowthProposal as ContractProposal, ChangeItem
from src.governance.governance_unification import set_governance_unification_enabled
from src.growth.proposal import storage as storage_module

# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

_EVALUATED = {
    "growth_allowed": True,
    "growth_signal": "companionship",
    "event_type": "chat",
    "growth_level": "context",
    "confidence": 0.9,
    "target_candidates": ["companionship"],
    "applied_delta": 0.05,
    "event_id": "ev_1",
    "canonical_topic": "companionship",
}

_EVENT = {
    "event_id": "ev_1",
    "topic": "陪伴",
    "canonical_topic": "companionship",
    "event_type": "chat",
    "source_ids": ["s1"],
    "is_first_occurrence": True,
    "occurrence_count": 1,
    "metadata": {"validator_apply": True},
    "_identity_resolved": True,
    "_identity_source": "resolver",
    "event_identity": "ev_1",
}


class _FakeState:
    def __init__(self):
        self._data = {"metrics": {"companionship": 0.5}}

    def get_metric(self, dim):
        return self._data["metrics"].get(dim, 0.0)

    def snapshot(self):
        return copy.deepcopy(self._data)


class _FakeEngine:
    def __init__(self):
        self.state = _FakeState()
        self.apply_calls = 0
        self.apply_proposal_calls = 0
        self.apply_evaluated_calls = 0
        self.relationship_calls = 0
        self.influence_calls = 0

    def apply(self, event):
        self.apply_calls += 1
        return {"status": "applied", "mode": "first", "before": {}, "delta": {"companionship": 0.05}}

    def apply_proposal(self, proposal):
        self.apply_proposal_calls += 1
        return {
            "status": "applied",
            "proposal_id": getattr(proposal, "id", "p1"),
            "before": {},
            "delta": {"companionship": 0.05},
        }

    def apply_evaluated(self, evaluated):
        self.apply_evaluated_calls += 1
        return {
            "record_id": "rec_1",
            "source_event_id": "ev_1",
            "growth_signal": evaluated.get("growth_signal", "s"),
            "source_type": evaluated.get("event_type", "t"),
            "growth_level": evaluated.get("growth_level", "context"),
            "affected_dimensions": {"companionship": 0.05},
            "confidence": 0.7,
            "reason": "r",
            "created_at": "2026-08-21T00:00:00",
        }

    def apply_relationship(self, event, rs):
        self.relationship_calls += 1

    def generate_personality_influence(self, event, result):
        self.influence_calls += 1
        return []


class _FakeEvaluator:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def evaluate(self, event, history_events, experience_meaning=None):
        self.calls += 1
        return dict(self._result)


class _FakeExtractor:
    def extract_from_text(self, text):
        return [dict(_EVENT)]

    def extract(self, limit):
        return [dict(_EVENT)]


class _FakeMatcher:
    def get_history(self, topic, event_type):
        return []

    def track(self, events, force_first_run):
        return events


class _FakeHistory:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)


class _FakeAStore:
    """ProposalStore A（停车账本）替身。"""

    def __init__(self):
        self.saved = []
        self.path = "/tmp/fake/proposals.jsonl"

    def save(self, proposal):
        self.saved.append(proposal)


class _FakeMutationAdapter:
    def __init__(self, decision="ACCEPT"):
        self.decision = decision
        self.outcomes = []
        self.deferred = []
        self.route_calls = 0

    def build_request(self, **kwargs):
        return types.SimpleNamespace(
            proposed_change=kwargs.get("proposed_change", {}),
            mutation_id=kwargs.get("mutation_id", "mut_1"),
        )

    def route(self, request, apply_route=None):
        self.route_calls += 1
        envelope = {
            "decision": self.decision,
            "applied": False,
            "mutation_id": "mut_1",
            "request_id": "req_1",
            "trace_id": "trace_1",
            "target_path": "growth.metrics.companionship",
            "reason": "unit_test",
            "audit_reference": "aud_1",
        }
        if self.decision == "ACCEPT" and apply_route is not None:
            envelope["applied"] = bool(apply_route(request, envelope))
        self.outcomes.append(envelope)
        return envelope


def _default_build_proposal(evaluated, event):
    return ContractProposal(
        proposed_changes=[ChangeItem(path="companionship", before=0.5, after=0.55, reason="test")],
        confidence=0.9,
        evidence_ids=["ev_1"],
        status="proposed",
    )


def _make_pipeline(**overrides):
    cls = pipeline_module.GrowthPipeline
    obj = cls.__new__(cls)
    obj.extractor = _FakeExtractor()
    obj.normalizer = types.SimpleNamespace(normalize=lambda events: events)
    obj.validator = types.SimpleNamespace(validate=lambda events: events)
    obj.matcher = _FakeMatcher()
    obj.evaluator = _FakeEvaluator(overrides.pop("evaluated", _EVALUATED))
    obj.growth_engine = overrides.pop("engine", _FakeEngine())
    obj.growth_records = overrides.pop("history", _FakeHistory())
    obj.narrative_adapter = types.SimpleNamespace(convert=lambda record: record)
    obj.new_influences = []
    obj.relationship_state = types.SimpleNamespace()
    obj.resolver = types.SimpleNamespace(resolve=lambda: {"traits": {}})
    obj.store = None
    obj.event_memory = None
    obj.target_user_id = "u1"
    obj._mutation_adapter = overrides.pop("mutation_adapter", _FakeMutationAdapter("NEED_REVIEW"))
    obj._proposal_store = overrides.pop("proposal_store", _FakeAStore())
    obj._build_proposal_from_evaluated = overrides.pop("build_proposal", _default_build_proposal)
    obj._resolve_experience_meaning = lambda event: None
    if "route" in overrides:
        obj._route_proposal_through_gateway = overrides.pop("route")
    assert not overrides, f"未消费的 overrides: {list(overrides)}"
    return obj


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    set_governance_unification_enabled(False)
    monkeypatch.setattr(pipeline_module, "is_growth_mutation_gateway_enabled", lambda: False)
    yield
    set_governance_unification_enabled(False)


# ---------------------------------------------------------------------------
# _is_growth_governed 开关矩阵
# ---------------------------------------------------------------------------

def test_is_growth_governed_matrix(monkeypatch):
    set_governance_unification_enabled(False)
    monkeypatch.setattr(pipeline_module, "is_growth_mutation_gateway_enabled", lambda: False)
    assert pipeline_module._is_growth_governed() is False

    monkeypatch.setattr(pipeline_module, "is_growth_mutation_gateway_enabled", lambda: True)
    assert pipeline_module._is_growth_governed() is True

    set_governance_unification_enabled(True)
    monkeypatch.setattr(pipeline_module, "is_growth_mutation_gateway_enabled", lambda: False)
    assert pipeline_module._is_growth_governed() is True

    monkeypatch.setattr(pipeline_module, "is_growth_mutation_gateway_enabled", lambda: True)
    assert pipeline_module._is_growth_governed() is True


# ---------------------------------------------------------------------------
# A/B 兼容：flag=False 行为与旧版本一致
# ---------------------------------------------------------------------------

def test_a_state_proposal_path_unchanged():
    """A 态 proposal 路径：apply_proposal 直写 + growth_records 无条件追加。"""
    engine = _FakeEngine()
    history = _FakeHistory()
    pipe = _make_pipeline(engine=engine, history=history)

    result = pipe.incremental_update("hello")

    assert engine.apply_proposal_calls == 1          # 旧直写路径
    assert engine.apply_calls == 0                   # 不触发 legacy fallback
    assert engine.apply_evaluated_calls == 1         # growth_records 无条件生成
    assert len(history.items) == 1
    assert len(result["events"]) == 1
    assert result["events"][0]["growth_mode"] == "proposal"
    assert result["events"][0]["delta"] == {"companionship": 0.05}  # delta 与执行件一致
    assert len(result["growth_records"]) == 1
    assert result["personality"] == {"traits": {}}
    assert engine.relationship_calls == 1
    assert engine.influence_calls == 1


def test_a_state_legacy_fallback_unchanged():
    """A 态 proposal 构建失败：仍走 legacy apply(event)，records 仍追加。"""
    engine = _FakeEngine()
    history = _FakeHistory()
    pipe = _make_pipeline(engine=engine, history=history, build_proposal=lambda e, ev: None)

    result = pipe.incremental_update("hello")

    assert engine.apply_calls == 1                   # legacy fallback 直写
    assert engine.apply_proposal_calls == 0
    assert engine.apply_evaluated_calls == 1
    assert len(history.items) == 1
    assert result["events"][0]["growth_mode"] == "first"
    assert engine.relationship_calls == 1
    assert engine.influence_calls == 1


def test_b5_mode_gateway_records_preserved(monkeypatch):
    """仅 B.5 网关开启（统一关闭）：NEED_REVIEW 不直写，但 records 仍无条件追加（B.5 语义不变）。"""
    monkeypatch.setattr(pipeline_module, "is_growth_mutation_gateway_enabled", lambda: True)
    engine = _FakeEngine()
    history = _FakeHistory()
    store = _FakeAStore()
    pipe = _make_pipeline(engine=engine, history=history, proposal_store=store,
                          mutation_adapter=_FakeMutationAdapter("NEED_REVIEW"))

    pipe.incremental_update("hello")

    assert engine.apply_proposal_calls == 0
    assert engine.apply_calls == 0                   # B.5 抑制直写
    assert engine.apply_evaluated_calls == 1         # B.5 态 records 不变
    assert len(history.items) == 1
    assert len(store.saved) == 1                     # NEED_REVIEW 停车


# ---------------------------------------------------------------------------
# B 态治理：统一开关开启
# ---------------------------------------------------------------------------

def test_b_state_accept_forced_through_gateway():
    """统一开启（B.5 关闭）时 proposal 仍强制走 gateway；ACCEPT 才执行与追加 records。"""
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    adapter = _FakeMutationAdapter("ACCEPT")
    pipe = _make_pipeline(engine=engine, history=history, mutation_adapter=adapter)

    result = pipe.incremental_update("hello")

    assert adapter.route_calls == 1                  # gateway 被调用（B.5 开关关闭时也强制路由）
    assert engine.apply_proposal_calls == 1          # ACCEPT 经 apply_route 执行
    assert engine.apply_calls == 0                   # legacy 直写为 0
    assert engine.apply_evaluated_calls == 1         # ACCEPT 才追加 records
    assert len(history.items) == 1
    assert len(result["events"]) == 1
    assert result["events"][0]["growth_mode"] == "proposal"


def test_b_state_needs_review_no_direct_apply():
    """B 态 NEED_REVIEW：direct_apply==0、proposal 停车数 +1、records 不追加、状态不变。"""
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    store = _FakeAStore()
    pipe = _make_pipeline(engine=engine, history=history, proposal_store=store,
                          mutation_adapter=_FakeMutationAdapter("NEED_REVIEW"))
    state_before = engine.state.snapshot()

    result = pipe.incremental_update("hello")

    assert engine.apply_proposal_calls == 0          # direct_apply == 0
    assert engine.apply_calls == 0
    assert engine.apply_evaluated_calls == 0         # pending 不追加 records
    assert len(history.items) == 0
    assert len(result["events"]) == 0
    assert len(store.saved) == 1                     # proposal 数量增加
    assert store.saved[0].status == "pending"
    assert engine.state.snapshot() == state_before   # 未 approve 状态不变


def test_b_state_rejected_no_park_no_records():
    """B 态 REJECT：不直写、不停车、不追加 records。"""
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    store = _FakeAStore()
    pipe = _make_pipeline(engine=engine, history=history, proposal_store=store,
                          mutation_adapter=_FakeMutationAdapter("REJECT"))

    pipe.incremental_update("hello")

    assert engine.apply_proposal_calls == 0
    assert engine.apply_calls == 0
    assert engine.apply_evaluated_calls == 0         # rejected 不追加 records
    assert len(history.items) == 0
    assert len(store.saved) == 0                     # REJECT 不停车


def test_b_state_deferred_park_no_records():
    """B 态 DEFER：停车 +1、不直写、不追加 records。"""
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    store = _FakeAStore()
    pipe = _make_pipeline(engine=engine, history=history, proposal_store=store,
                          mutation_adapter=_FakeMutationAdapter("DEFER"))

    pipe.incremental_update("hello")

    assert engine.apply_proposal_calls == 0
    assert engine.apply_calls == 0
    assert engine.apply_evaluated_calls == 0         # deferred 不追加 records
    assert len(history.items) == 0
    assert len(store.saved) == 1                     # DEFER 停车
    assert store.saved[0].status == "pending"


def test_b_state_proposal_none_fail_closed():
    """B 态 proposal 构建失败：禁止 fallback direct apply（fail-closed），不追加 records。"""
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    pipe = _make_pipeline(engine=engine, history=history, build_proposal=lambda e, ev: None)

    pipe.incremental_update("hello")

    assert engine.apply_calls == 0                   # 禁止 growth_state 直写
    assert engine.apply_proposal_calls == 0
    assert engine.apply_evaluated_calls == 0
    assert len(history.items) == 0


def test_b_state_applied_path_through_route_spy():
    """B 态经 gateway 路由返回 applied：records 追加且 applied 列表一致。"""
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    calls = []

    def _spy_route(proposal, event):
        calls.append(1)
        return {"status": "applied", "proposal_id": getattr(proposal, "id", "p1"),
                "before": {}, "delta": {"companionship": 0.05}}

    pipe = _make_pipeline(engine=engine, history=history, route=_spy_route)

    result = pipe.incremental_update("hello")

    assert len(calls) == 1
    assert engine.apply_evaluated_calls == 1         # ACCEPT 才追加
    assert len(history.items) == 1
    assert len(result["events"]) == 1


# ---------------------------------------------------------------------------
# run_full_consolidation：A 态兼容 / B 态禁止直写
# ---------------------------------------------------------------------------

def test_run_full_consolidation_a_state_unchanged():
    engine = _FakeEngine()
    history = _FakeHistory()
    pipe = _make_pipeline(engine=engine, history=history)

    result = pipe.run_full_consolidation()

    assert engine.apply_calls == 1
    assert engine.apply_evaluated_calls == 1
    assert len(history.items) == 1
    assert len(result["growth_records"]) == 1


def test_run_full_consolidation_b_state_no_direct_apply():
    set_governance_unification_enabled(True)
    engine = _FakeEngine()
    history = _FakeHistory()
    pipe = _make_pipeline(engine=engine, history=history)

    result = pipe.run_full_consolidation()

    assert engine.apply_calls == 0                   # B 态禁止 direct apply
    assert engine.apply_evaluated_calls == 0         # F1 门控：不追加 records
    assert len(history.items) == 0
    assert len(result["growth_records"]) == 0


# ---------------------------------------------------------------------------
# orchestrator Step 14.6：执行层（A 态等价 / B 态降级 + 持久化 + 零 apply）
# ---------------------------------------------------------------------------

class _FakeUpdater:
    def __init__(self):
        self.apply_calls = 0
        self.applied = []

    def create_proposal_from_growth(self, record):
        return types.SimpleNamespace(
            to_dict=lambda: {
                "change": {"narrative": "x"},
                "target": "growth_narratives",
                "requires_approval": True,
            }
        )

    def apply_proposal(self, proposal):
        self.apply_calls += 1
        self.applied.append(proposal)


class _FakePolicy:
    def __init__(self, action="auto_apply"):
        self.action = action
        self.evaluated = []

    def evaluate(self, record):
        self.evaluated.append(record)
        return types.SimpleNamespace(
            action=types.SimpleNamespace(value=self.action),
            growth_level="context",
            confidence=0.7,
            reason="unit_test",
            metadata={},
        )


class _FakeQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, proposal):
        self.enqueued.append(proposal)


class _FakeBStorage:
    def __init__(self):
        self.saved = []

    def save(self, proposal):
        self.saved.append(proposal)


_RECORD = {
    "record_id": "r1",
    "source_event_id": "ev_1",
    "affected_dimensions": {"narrative": 0.05},
    "confidence": 0.7,
    "reason": "r",
}


@pytest.fixture
def _b_storage(monkeypatch):
    fake = _FakeBStorage()
    monkeypatch.setattr(storage_module, "get_proposal" + "_storage", lambda: fake)
    return fake


def _make_orchestrator():
    cls = getattr(orchestrator_module, "Orchestrator")
    obj = cls.__new__(cls)
    obj.target_user_id = "u1"
    obj._self_model_updater = _FakeUpdater()
    obj._governance_policy = _FakePolicy()
    obj._approval_queue = _FakeQueue()
    return obj


def test_step14_6_a_state_auto_apply_applies(_b_storage):
    """A 态 auto_apply：立即 apply，不入 B-store、不进队列。"""
    orch = _make_orchestrator()
    orch._governance_policy = _FakePolicy("auto_apply")

    counts = orch._process_self_model_governance({"growth_records": [dict(_RECORD)]})

    assert counts == {"auto_applied": 1, "pending": 0, "denied": 0}
    assert orch._self_model_updater.apply_calls == 1
    assert len(orch._approval_queue.enqueued) == 0
    assert len(_b_storage.saved) == 0


def test_step14_6_a_state_approval_required_enqueue_only(_b_storage):
    """A 态 approval_required：仅内存队列，不落 B-store。"""
    orch = _make_orchestrator()
    orch._governance_policy = _FakePolicy("approval_required")

    counts = orch._process_self_model_governance({"growth_records": [dict(_RECORD)]})

    assert counts == {"auto_applied": 0, "pending": 1, "denied": 0}
    assert orch._self_model_updater.apply_calls == 0
    assert len(orch._approval_queue.enqueued) == 1
    assert len(_b_storage.saved) == 0


def test_step14_6_b_state_auto_apply_degraded_to_pending(_b_storage):
    """B 态 auto_apply 降级：零 apply + B-store self_model pending + 队列。"""
    set_governance_unification_enabled(True)
    orch = _make_orchestrator()
    orch._governance_policy = _FakePolicy("auto_apply")

    counts = orch._process_self_model_governance({"growth_records": [dict(_RECORD)]})

    assert counts == {"auto_applied": 0, "pending": 1, "denied": 0}
    assert orch._self_model_updater.apply_calls == 0       # 禁止 SelfModelUpdater.apply
    assert len(orch._approval_queue.enqueued) == 1
    assert len(_b_storage.saved) == 1                      # B-store 账本 +1
    saved = _b_storage.saved[0]
    assert saved.proposal_type == "self_model"
    assert saved.status == "pending"
    assert saved.metadata["source"] == "legacy_step_14_6"
    assert saved.metadata["governance_decision"]["action"] == "auto_apply"
    assert saved.metadata["self_model_proposal"]["target"] == "growth_narratives"
    assert saved.affected_dimensions == {"narrative": 0.05}


def test_step14_6_b_state_approval_required_persisted(_b_storage):
    """B 态 approval_required：同样持久化 + 入队，零 apply。"""
    set_governance_unification_enabled(True)
    orch = _make_orchestrator()
    orch._governance_policy = _FakePolicy("approval_required")

    counts = orch._process_self_model_governance({"growth_records": [dict(_RECORD)]})

    assert counts == {"auto_applied": 0, "pending": 1, "denied": 0}
    assert orch._self_model_updater.apply_calls == 0
    assert len(orch._approval_queue.enqueued) == 1
    assert len(_b_storage.saved) == 1
    assert _b_storage.saved[0].status == "pending"


def test_step14_6_b_state_deny_skipped(_b_storage):
    set_governance_unification_enabled(True)
    orch = _make_orchestrator()
    orch._governance_policy = _FakePolicy("deny")

    counts = orch._process_self_model_governance({"growth_records": [dict(_RECORD)]})

    assert counts == {"auto_applied": 0, "pending": 0, "denied": 1}
    assert orch._self_model_updater.apply_calls == 0
    assert len(orch._approval_queue.enqueued) == 0
    assert len(_b_storage.saved) == 0


# ---------------------------------------------------------------------------
# fallback：RuntimeCore 失败进入 orchestrator 后仍走治理路径
# ---------------------------------------------------------------------------

def test_fallback_orchestrator_path_still_governed(_b_storage):
    """模拟 fallback 入口（orchestrator 直接驱动 pipeline + Step 14.6）：
    统一开启时两层都不直写。"""
    set_governance_unification_enabled(True)

    # 层 1：Step 14.5 —— GrowthPipeline.incremental_update（orchestrator 调用点）
    engine = _FakeEngine()
    history = _FakeHistory()
    store = _FakeAStore()
    pipe = _make_pipeline(engine=engine, history=history, proposal_store=store,
                          mutation_adapter=_FakeMutationAdapter("NEED_REVIEW"))
    pipe.incremental_update("hello")
    assert engine.apply_calls == 0
    assert engine.apply_proposal_calls == 0
    assert engine.apply_evaluated_calls == 0
    assert len(store.saved) == 1

    # 层 2：Step 14.6 —— SelfModel 治理执行层（零 apply + 持久化 pending）
    orch = _make_orchestrator()
    orch._governance_policy = _FakePolicy("auto_apply")
    counts = orch._process_self_model_governance({"growth_records": [dict(_RECORD)]})

    assert counts == {"auto_applied": 0, "pending": 1, "denied": 0}
    assert orch._self_model_updater.apply_calls == 0
    assert len(_b_storage.saved) == 1
    assert _b_storage.saved[0].proposal_type == "self_model"
    assert _b_storage.saved[0].status == "pending"
