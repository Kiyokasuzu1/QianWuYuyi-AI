# -*- coding: utf-8 -*-
"""tests/test_self_model_cycle_task.py

P2.8 Phase D-4.0: SelfModelCycleTask 单测 + 治理/红线测试。

任务书 Step 4:
- Case 1: 周期触发 → pending proposal 创建; self_model 不变; 保存成功
- Case 2: 重复执行 → interval gate 生效; 不重复生成
- Case 3: 异常隔离 → FAIL_SOFT; 不影响其他 task
- Case 4: 红线 AST —— SelfModelCycleTask 不允许 apply/approve/accept/
  self_model 写入方法
- Case 5: 去重 → 相同 evidence: created=0, deduped=N

注意: 本文件文本不得出现 conftest 单例陷阱 token。
"""

import ast
from pathlib import Path

from src.contracts.lifecycle_event_schema import TASK_TYPE_SELF_MODEL_CYCLE
from src.personality.self_model_updater import SelfModelUpdater
from src.runtime.lifecycle.audit_writer import LifecycleAuditWriter
from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_runtime import LifecycleRuntime
from src.runtime.lifecycle.tasks.self_model_cycle import (
    GOVERNANCE_ORIGIN,
    SelfModelCycleTask,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CYCLE_SRC = (
    REPO_ROOT / "src" / "runtime" / "lifecycle" / "tasks" / "self_model_cycle.py"
)


# ============================================================
# 测试替身(仅内存, 无任何文件/单例副作用)
# ============================================================
class _FakeModelStore:
    """self_model 存储替身: 记录所有写方法调用(断言必须为零)。"""

    def __init__(self):
        self.write_calls = []

    def get(self):
        return None

    def save(self):
        self.write_calls.append("save")

    def apply_change_proposal(self, *args, **kwargs):
        self.write_calls.append("apply_change_proposal")

    def update(self, *args, **kwargs):
        self.write_calls.append("update")


class _RecordingUpdater(SelfModelUpdater):
    """包装真实 updater, 统计治理相关方法调用次数。"""

    def __init__(self, store=None):
        super().__init__(self_model_store=store)
        self.create_calls = 0
        self.update_calls = 0
        self.apply_calls = 0

    def create_proposal_from_growth(self, record):
        self.create_calls += 1
        return super().create_proposal_from_growth(record)

    def update_from_growth(self, record):
        self.update_calls += 1
        return super().update_from_growth(record)

    def apply_proposal(self, proposal):
        self.apply_calls += 1
        return super().apply_proposal(proposal)


class _FakeProvider:
    def __init__(self, records, error=None):
        self._records = list(records)
        self._error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._records)


class _FakeStore:
    def __init__(self):
        self.saved = []
        self._seen = set()

    def exists_similar(self, source_event_id, fingerprint):
        return (source_event_id, fingerprint) in self._seen

    def save(self, proposal):
        self.saved.append(proposal)
        from src.growth.proposal_store import compute_fingerprint

        fp = compute_fingerprint(proposal.to_dict())
        self._seen.add((proposal.source_event_id or "", fp))

    def count(self):
        return len(self.saved)


def _make_record(record_id="gr_1", source_event_id="evt_1", confidence=0.6):
    return {
        "record_id": record_id,
        "source_event_id": source_event_id,
        "growth_signal": "knowledge_exploration",
        "source_type": "preference",
        "growth_level": "context",
        "affected_dimensions": {"curiosity": 0.001},
        "confidence": confidence,
        "reason": "开始学习做咖啡",
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
# Case 1: 周期触发 → pending proposal; self_model 不变
# ============================================================
def test_case1_cycle_creates_pending_proposal_self_model_untouched(tmp_path):
    clock = FrozenClock(initial=1000.0)
    fake_model = _FakeModelStore()
    updater = _RecordingUpdater(store=fake_model)
    store = _FakeStore()
    provider = _FakeProvider([_make_record()])
    task = SelfModelCycleTask(
        updater=updater,
        proposal_store=store,
        growth_records_provider=provider,
        interval_seconds=10.0,
    )
    rt, audit_path = _make_runtime(tmp_path, clock, [task])

    events = rt.tick()
    assert len(events) == 1
    assert events[0].status == "SUCCESS"
    assert events[0].task_type == TASK_TYPE_SELF_MODEL_CYCLE

    # proposal 保存成功且形态正确(pending + 治理键)
    assert store.count() == 1
    saved = store.saved[0]
    assert saved.status == "pending"
    assert saved.evaluator_meta["_governance_origin"] == GOVERNANCE_ORIGIN == "self_model_cycle"
    assert saved.evaluator_meta["_governance"]["decision"] == "pending_review"
    assert saved.evaluator_meta["proposal_type"] == "self_model"
    assert "self_model_proposal" in saved.evaluator_meta
    assert saved.evaluator_meta["source_events"]

    # self_model 不变: 仅纯计算入口被调, 一切写方法零调用
    assert updater.create_calls == 1
    assert updater.update_calls == 0
    assert updater.apply_calls == 0
    assert fake_model.write_calls == []

    # 审计完整: produced_events == proposal ids
    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent()
    assert len(rows) == 1
    assert rows[0]["result"]["status"] == "SUCCESS"
    assert rows[0]["result"]["produced_events"] == [saved.id]
    assert rows[0]["result"]["metrics"]["proposals_created"] == 1
    assert rows[0]["audit"]["idempotency_key"]


# ============================================================
# Case 2: 重复执行 → interval gate 生效; 不重复生成
# ============================================================
def test_case2_interval_gate_prevents_regeneration(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = _FakeStore()
    provider = _FakeProvider([_make_record()])
    task = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=store,
        growth_records_provider=provider,
        interval_seconds=10.0,
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])
    rt.tick()  # 第一次执行 → 生成 1 提案

    events = rt.tick()  # 立即第二次: 未到间隔
    assert len(events) == 1
    assert events[0].status == "SKIPPED"
    assert events[0].result["reason"]
    assert provider.calls == 1  # 输入源未重复读取
    assert store.count() == 1  # 未重复落盘


# ============================================================
# Case 3: 异常隔离 → FAIL_SOFT; 不影响其他 task
# ============================================================
def test_case3_provider_failure_isolated_fail_soft(tmp_path):
    clock = FrozenClock(initial=1000.0)
    bad_provider = _FakeProvider([], error=ValueError("history read failed"))
    bad = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=_FakeStore(),
        growth_records_provider=bad_provider,
        interval_seconds=10.0,
    )
    ok = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=_FakeStore(),
        growth_records_provider=_FakeProvider([_make_record("gr_ok", "evt_ok")]),
        task_id="self_model.cycle.ok",
        interval_seconds=10.0,
    )
    rt, audit_path = _make_runtime(tmp_path, clock, [bad, ok])

    events = rt.tick()
    assert len(events) == 2
    statuses = {e.status for e in events}
    assert statuses == {"FAILED", "SUCCESS"}

    failed = next(e for e in events if e.status == "FAILED")
    assert "history read failed" in failed.result["reason"]
    ok = next(e for e in events if e.status == "SUCCESS")
    assert ok.result["metrics"]["proposals_created"] == 1

    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent()
    assert len(rows) == 2
    failed_row = next(r for r in rows if r["result"]["status"] == "FAILED")
    assert "history read failed" in failed_row["audit"]["error"]


# ============================================================
# Case 4: 红线 AST —— 禁止 apply/approve/accept/写 store
# ============================================================
_FORBIDDEN_CALLS = {
    "apply",
    "approve",
    "accept",
    "apply_proposal",
    "update_from_growth",
    "apply_change_proposal",
}


def test_case4_redline_no_governance_write_calls_in_source():
    """AST 级: SelfModelCycleTask 源码不存在任何治理写/self_model 写调用。"""
    tree = ast.parse(CYCLE_SRC.read_text(encoding="utf-8"))
    called, imported = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            for a in node.names:
                mods.append(a.name)
            for m in mods:
                imported.add(m)
                for part in m.split("."):
                    imported.add(part)

    bad_calls = called & _FORBIDDEN_CALLS
    assert not bad_calls, f"SelfModelCycleTask 存在禁止调用: {bad_calls}"
    assert "self_model_store" not in imported, "周期任务禁止 import self_model store 模块"
    assert "SelfModelStore" not in imported


# ============================================================
# Case 5: 去重 → created=0, deduped=N
# ============================================================
def test_case5_dedupe_same_evidence_skips_recreation(tmp_path):
    store = _FakeStore()
    provider = _FakeProvider([_make_record()])
    task = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=store,
        growth_records_provider=provider,
        interval_seconds=10.0,
    )

    r1 = task.execute(None)
    assert r1["metrics"]["proposals_created"] == 1
    assert r1["metrics"]["proposals_deduped"] == 0

    # 同 store 再次执行(新任务实例, 同输入源) → 去重拦截
    provider2 = _FakeProvider([_make_record()])
    task2 = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=store,
        growth_records_provider=provider2,
        interval_seconds=10.0,
    )
    r2 = task2.execute(None)
    assert r2["metrics"]["proposals_created"] == 0
    assert r2["metrics"]["proposals_deduped"] == 1
    assert store.count() == 1


# ============================================================
# 元数据
# ============================================================
def test_task_metadata_and_defaults():
    task = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=_FakeStore(),
        growth_records_provider=lambda: [],
    )
    assert task.TASK_TYPE == TASK_TYPE_SELF_MODEL_CYCLE
    assert task.task_id == "self_model.cycle"
    assert task.owner == "p2.8.d4"
    assert task.interval_seconds == 86400.0
    assert task.IDEMPOTENCY_BUCKET_SECONDS == 86400


def test_no_records_returns_success_zero_metrics(tmp_path):
    clock = FrozenClock(initial=1000.0)
    task = SelfModelCycleTask(
        updater=SelfModelUpdater(),
        proposal_store=_FakeStore(),
        growth_records_provider=lambda: [],
        interval_seconds=10.0,
    )
    rt, _ = _make_runtime(tmp_path, clock, [task])
    events = rt.tick()
    assert events[0].status == "SUCCESS"
    assert events[0].result["metrics"]["proposals_created"] == 0
