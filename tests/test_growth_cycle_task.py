# -*- coding: utf-8 -*-
"""tests/test_growth_cycle_task.py

P2.8 Phase D-2.0: GrowthCycleTask 单测 + 治理测试。

任务书 Step 4:
- FakeClock: 到期执行 SUCCESS / 未到期 SKIPPED / 异常 FAIL_SOFT
- 治理测试: 落盘提案 status=pending; 无 direct_apply(apply 类调用为零)

注意: 本文件文本不得出现 conftest 单例陷阱 token
(真实 ProposalStore/管道组件仅由隔离实验脚本验证)。
"""

import pytest

from src.contracts.lifecycle_event_schema import TASK_TYPE_GROWTH_CYCLE
from src.contracts.growth_schema import ChangeItem, GrowthProposal
from src.runtime.lifecycle.audit_writer import LifecycleAuditWriter
from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_runtime import LifecycleRuntime
from src.runtime.lifecycle.tasks.growth_cycle import GrowthCycleTask


# ============================================================
# 测试替身(仅内存, 无任何文件/单例副作用)
# ============================================================
class _FakeExtractor:
    def __init__(self, events, error=None):
        self._events = list(events)
        self._error = error
        self.calls = 0

    def extract(self, limit=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._events)


class _IdentityNormalizer:
    @staticmethod
    def normalize(events):
        return list(events)


class _IdentityValidator:
    @staticmethod
    def validate(events):
        return list(events)


class _FakeMatcher:
    def __init__(self):
        self.history = []

    def track(self, events, force_first_run=False):
        for e in events:
            e["is_first_occurrence"] = True
        self.history.extend(events)
        return list(events)

    def get_history(self, canonical_topic=None, event_type=None):
        return []


class _FakeEvaluator:
    def __init__(self, allowed=True):
        self._allowed = bool(allowed)

    def evaluate(self, event, history=None, **kwargs):
        return {
            "growth_allowed": self._allowed,
            "confidence": 0.9,
            "target_candidates": ["openness"],
            "applied_delta": 0.03,
            "growth_signal": "general_preference",
            "growth_level": "context",
            "growth_domain": "preference",
        }


class _RecordingEngine:
    """记录一切 apply 类调用(治理测试断言必须为零)。"""

    def __init__(self):
        self.calls = []

    def apply(self, *args, **kwargs):
        self.calls.append("apply")
        return {"status": "applied"}

    def apply_proposal(self, *args, **kwargs):
        self.calls.append("apply_proposal")
        return {"status": "applied"}

    def apply_relationship(self, *args, **kwargs):
        self.calls.append("apply_relationship")

    def apply_evaluated(self, *args, **kwargs):
        self.calls.append("apply_evaluated")
        return None


class _FakePipeline:
    def __init__(self, events=None, allowed=True, extract_error=None):
        self.extractor = _FakeExtractor(events or [], error=extract_error)
        self.normalizer = _IdentityNormalizer()
        self.validator = _IdentityValidator()
        self.matcher = _FakeMatcher()
        self.evaluator = _FakeEvaluator(allowed)
        self.growth_engine = _RecordingEngine()

    def _build_proposal_from_evaluated(self, evaluated, event):
        return GrowthProposal(
            source_event_id=event.get("event_id", "") or "ev_seed",
            proposed_changes=[
                ChangeItem(path="openness", before=None, after=0.03, reason="t")
            ],
            confidence=0.9,
            evidence_ids=["ev_1"],
            evaluator_meta={"growth_signal": "general_preference"},
            status="proposed",
        )


class _FakeStore:
    def __init__(self, similar_map=None):
        self.saved = []
        self._similar_map = dict(similar_map or {})

    def exists_similar(self, source_event_id, fingerprint):
        return self._similar_map.get(source_event_id)

    def save(self, proposal):
        self.saved.append(proposal)

    def count(self):
        return len(self.saved)


def _make_event(event_id="ev_1"):
    return {
        "event_id": event_id,
        "event_type": "preference",
        "canonical_topic": "咖啡",
        "topic": "咖啡",
        "evidence": [{"id": "ev_1", "text": "用户说喜欢喝咖啡"}],
        "metadata": {"validator_apply": True},
    }


def _make_runtime(tmp_path, clock, tasks):
    audit_path = tmp_path / "audit" / "lifecycle_events.jsonl"
    rt = LifecycleRuntime(
        {"audit_path": str(audit_path), "tick_interval_seconds": 60.0},
        clock=clock,
    )
    for t in tasks or []:
        rt.register_task(t)
    rt.start()
    return rt, audit_path


# ============================================================
# 元数据
# ============================================================
def test_task_metadata_and_defaults():
    task = GrowthCycleTask(pipeline=_FakePipeline(), proposal_store=_FakeStore())
    assert task.TASK_TYPE == TASK_TYPE_GROWTH_CYCLE
    assert task.IDEMPOTENCY_BUCKET_SECONDS == 86400
    assert task.task_id == "growth.cycle"
    assert task.owner == "p2.8.d2"
    assert task.interval_seconds == 86400.0


def test_custom_interval_metadata():
    task = GrowthCycleTask(
        pipeline=_FakePipeline(), proposal_store=_FakeStore(), interval_seconds=7200.0
    )
    assert task.interval_seconds == 7200.0


# ============================================================
# FakeClock: 到期执行 SUCCESS / 未到期 SKIPPED
# ============================================================
def test_due_task_executes_success_and_saves_pending(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = _FakeStore()
    task = GrowthCycleTask(
        pipeline=_FakePipeline(events=[_make_event()]),
        proposal_store=store,
        interval_seconds=10.0,
    )
    rt, audit_path = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert len(events) == 1
    assert events[0].status == "SUCCESS"
    assert store.count() == 1
    saved = store.saved[0]
    assert saved.status == "pending"
    assert saved.evaluator_meta["_governance_origin"] == "growth_cycle_task"
    assert saved.evaluator_meta["_governance"]["decision"] == "pending_review"

    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent()
    assert len(rows) == 1
    assert rows[0]["result"]["status"] == "SUCCESS"
    assert rows[0]["result"]["produced_events"] == [saved.id]
    assert rows[0]["result"]["metrics"]["proposals_created"] == 1
    assert rows[0]["audit"]["idempotency_key"]


def test_not_due_task_skipped_without_reexecution(tmp_path):
    clock = FrozenClock(initial=1000.0)
    pipeline = _FakePipeline(events=[_make_event()])
    store = _FakeStore()
    task = GrowthCycleTask(
        pipeline=pipeline, proposal_store=store, interval_seconds=10.0
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])
    rt.tick()  # 第一次执行

    events = rt.tick()  # 立即第二次: 未到间隔
    assert len(events) == 1
    assert events[0].status == "SKIPPED"
    assert events[0].result["reason"]
    assert pipeline.extractor.calls == 1  # 未重复执行
    assert store.count() == 1


# ============================================================
# 异常 FAIL_SOFT
# ============================================================
def test_extract_failure_isolated_as_failed(tmp_path):
    clock = FrozenClock(initial=1000.0)
    task = GrowthCycleTask(
        pipeline=_FakePipeline(extract_error=RuntimeError("llm down")),
        proposal_store=_FakeStore(),
        interval_seconds=10.0,
    )
    rt, audit_path = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert len(events) == 1
    assert events[0].status == "FAILED"
    assert "llm down" in events[0].result["reason"]

    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent()
    assert len(rows) == 1
    assert rows[0]["audit"]["error"] == "llm down"


def test_failure_does_not_block_other_tasks(tmp_path):
    clock = FrozenClock(initial=1000.0)
    bad = GrowthCycleTask(
        pipeline=_FakePipeline(extract_error=ValueError("boom")),
        proposal_store=_FakeStore(),
        interval_seconds=10.0,
    )
    ok = GrowthCycleTask(
        pipeline=_FakePipeline(events=[_make_event("ev_ok")]),
        proposal_store=_FakeStore(),
        task_id="growth.cycle.ok",
        interval_seconds=10.0,
    )
    rt, _ = _make_runtime(tmp_path, clock, [bad, ok])

    events = rt.tick()
    assert len(events) == 2
    statuses = {e.status for e in events}
    assert statuses == {"FAILED", "SUCCESS"}
    ok_ev = [e for e in events if e.status == "SUCCESS"][0]
    assert ok_ev.result["metrics"]["proposals_created"] == 1


# ============================================================
# 治理测试: status=pending / 无 direct_apply
# ============================================================
def test_governance_proposals_all_pending_no_apply_called(tmp_path):
    clock = FrozenClock(initial=1000.0)
    pipeline = _FakePipeline(events=[_make_event("ev_a"), _make_event("ev_b")])
    store = _FakeStore()
    task = GrowthCycleTask(
        pipeline=pipeline, proposal_store=store, interval_seconds=10.0
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert events[0].status == "SUCCESS"

    # 1) 全部提案 status=pending(非 proposed/applied/accepted/approved/rejected)
    assert store.count() == 2
    for p in store.saved:
        assert p.status == "pending"

    # 2) 无 direct_apply: 任何 apply 类调用为零
    assert pipeline.growth_engine.calls == []
    assert events[0].result["metrics"]["proposals_created"] == 2
    assert events[0].result["metrics"]["proposals_deduped"] == 0


def test_governance_no_proposal_when_evaluator_denies(tmp_path):
    clock = FrozenClock(initial=1000.0)
    pipeline = _FakePipeline(events=[_make_event()], allowed=False)
    store = _FakeStore()
    task = GrowthCycleTask(
        pipeline=pipeline, proposal_store=store, interval_seconds=10.0
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert events[0].status == "SUCCESS"
    assert store.count() == 0
    assert events[0].result["metrics"]["growth_allowed"] == 0
    assert events[0].result["metrics"]["proposals_created"] == 0
    assert pipeline.growth_engine.calls == []


def test_governance_dedupe_skips_existing_proposal(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = _FakeStore(similar_map={"ev_dup": "prop_existing"})
    task = GrowthCycleTask(
        pipeline=_FakePipeline(events=[_make_event("ev_dup")]),
        proposal_store=store,
        interval_seconds=10.0,
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert events[0].status == "SUCCESS"
    assert store.count() == 0
    assert events[0].result["metrics"]["proposals_deduped"] == 1
    assert events[0].result["produced_events"] == []


# ============================================================
# 空输入
# ============================================================
def test_no_events_returns_success_zero_metrics(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = _FakeStore()
    task = GrowthCycleTask(
        pipeline=_FakePipeline(events=[]), proposal_store=store, interval_seconds=10.0
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert events[0].status == "SUCCESS"
    assert store.count() == 0
    m = events[0].result["metrics"]
    assert m["events_extracted"] == 0
    assert m["proposals_created"] == 0
