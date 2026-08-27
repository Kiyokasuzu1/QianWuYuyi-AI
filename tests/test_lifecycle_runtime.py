# -*- coding: utf-8 -*-
"""tests/test_lifecycle_runtime.py

P2.7 Phase D-1: LifecycleRuntime 薄层单测。

任务书 Step 6 要求(Fake Clock):
- 到时间执行
- 未到时间跳过
- 重复启动不会重复执行
"""

from src.contracts.lifecycle_event_schema import TASK_TYPE_MEMORY_CYCLE
from src.runtime.lifecycle.audit_writer import LifecycleAuditWriter
from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_decision import AfterInterval
from src.runtime.lifecycle.lifecycle_runtime import LifecycleRuntime
from src.runtime.lifecycle.lifecycle_task import SimpleTask


class _StubScheduler:
    """不启动真实 Timer 线程的调度器替身(记录注册/启停调用)。"""

    def __init__(self):
        self.added = []
        self.started = 0
        self.stopped = 0

    def add_interval_task(self, name, interval_seconds, callback, *args, **kwargs):
        self.added.append((name, interval_seconds, callback))
        return object()

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


class _TypedTask(SimpleTask):
    """带生命周期元数据的任务(验证审计字段映射)。"""

    TASK_TYPE = TASK_TYPE_MEMORY_CYCLE
    IDEMPOTENCY_BUCKET_SECONDS = 86400


def _make_runtime(tmp_path, clock, tasks=(), scheduler=None):
    audit_path = tmp_path / "audit" / "lifecycle_events.jsonl"
    rt = LifecycleRuntime(
        {"audit_path": str(audit_path), "tick_interval_seconds": 60.0},
        clock=clock,
        scheduler=scheduler if scheduler is not None else _StubScheduler(),
    )
    for t in tasks:
        rt.register_task(t)
    return rt, audit_path


def _counting_action(store):
    def _action(ctx):
        store["calls"] += 1
        return {"metrics": {"k": store["calls"]}}

    return _action


# ============================================================
# Fake Clock: 到时间执行 / 未到时间跳过 / 推进后再执行
# ============================================================
def test_due_task_executes_and_writes_audit(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = {"calls": 0}
    task = SimpleTask(
        "t.demo", "test", _counting_action(store), condition=AfterInterval(10.0)
    )
    rt, audit_path = _make_runtime(tmp_path, clock, tasks=[task])
    assert rt.start() is True

    events = rt.tick()
    assert len(events) == 1
    assert events[0].status == "SUCCESS"
    assert store["calls"] == 1

    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent()
    assert len(rows) == 1
    assert rows[0]["result"]["status"] == "SUCCESS"
    assert rows[0]["result"]["metrics"]["k"] == 1
    assert rows[0]["audit"]["idempotency_key"]
    assert rows[0]["audit"]["run_count"] == 1
    assert rows[0]["timestamp"]


def test_not_due_task_skipped(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = {"calls": 0}
    task = SimpleTask(
        "t.demo", "test", _counting_action(store), condition=AfterInterval(10.0)
    )
    rt, audit_path = _make_runtime(tmp_path, clock, tasks=[task])
    rt.start()
    rt.tick()  # 第一次执行

    events = rt.tick()  # 立即第二次:未到间隔
    assert len(events) == 1
    assert events[0].status == "SKIPPED"
    assert events[0].result["reason"]
    assert store["calls"] == 1  # 未重复执行

    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent()
    assert [r["result"]["status"] for r in rows] == ["SKIPPED", "SUCCESS"]


def test_due_again_after_clock_advance(tmp_path):
    clock = FrozenClock(initial=1000.0)
    store = {"calls": 0}
    task = SimpleTask(
        "t.demo", "test", _counting_action(store), condition=AfterInterval(10.0)
    )
    rt, _ = _make_runtime(tmp_path, clock, tasks=[task])
    rt.start()
    rt.tick()
    rt.tick()  # SKIPPED
    clock.advance(11.0)

    events = rt.tick()
    assert events[0].status == "SUCCESS"
    assert store["calls"] == 2


# ============================================================
# 重复启动不会重复执行
# ============================================================
def test_repeat_start_is_idempotent_and_registers_once(tmp_path):
    clock = FrozenClock(initial=1000.0)
    scheduler = _StubScheduler()
    rt, _ = _make_runtime(tmp_path, clock, tasks=[], scheduler=scheduler)
    assert rt.start() is True
    assert rt.start() is True  # 幂等
    assert rt.start() is True
    assert len(scheduler.added) == 1  # 驱动任务只注册一次
    assert scheduler.started == 1
    name, interval, callback = scheduler.added[0]
    assert name == "lifecycle_runtime_tick"
    assert interval == 60.0
    assert callable(callback)


def test_stop_is_idempotent_and_halts_scheduling(tmp_path):
    clock = FrozenClock(initial=1000.0)
    scheduler = _StubScheduler()
    store = {"calls": 0}
    task = SimpleTask("t.demo", "test", _counting_action(store))
    rt, _ = _make_runtime(tmp_path, clock, tasks=[task], scheduler=scheduler)
    rt.start()
    assert rt.stop() is True
    assert rt.stop() is True  # 幂等
    assert scheduler.stopped == 1
    assert rt.tick() == []  # Manager 已 STOPPED,不再调度
    assert store["calls"] == 0


def test_tick_before_start_returns_empty(tmp_path):
    clock = FrozenClock(initial=1000.0)
    rt, _ = _make_runtime(tmp_path, clock, tasks=[])
    assert rt.tick() == []
    assert rt.is_started is False


# ============================================================
# 异常隔离 + 审计字段映射
# ============================================================
def test_failure_isolated_and_audited(tmp_path):
    clock = FrozenClock(initial=1000.0)

    def _boom(ctx):
        raise RuntimeError("boom")

    bad = SimpleTask("t.bad", "test", _boom)
    good = SimpleTask("t.good", "test", lambda ctx: {"metrics": {"ok": True}})
    rt, audit_path = _make_runtime(tmp_path, clock, tasks=[bad, good])
    rt.start()

    events = rt.tick()  # 不抛
    statuses = {e.task_type: e.status for e in events}
    assert statuses["t.bad"] == "FAILED"
    assert statuses["t.good"] == "SUCCESS"

    writer = LifecycleAuditWriter(audit_path)
    rows = {r["task_type"]: r for r in writer.read_recent()}
    assert rows["t.bad"]["audit"]["error"] == "boom"
    assert rows["t.good"]["result"]["metrics"]["ok"] is True


def test_audit_maps_task_type_and_bucket(tmp_path):
    clock = FrozenClock(initial=500000.0)
    task = _TypedTask("t.typed", "test", lambda ctx: {"metrics": {}})
    rt, audit_path = _make_runtime(tmp_path, clock, tasks=[task])
    rt.start()
    events = rt.tick()
    assert events[0].task_type == TASK_TYPE_MEMORY_CYCLE
    assert events[0].audit["idempotency_key"] == f"memory_cycle:{int(500000.0 / 86400)}"

    writer = LifecycleAuditWriter(audit_path)
    rows = writer.read_recent(task_type=TASK_TYPE_MEMORY_CYCLE)
    assert len(rows) == 1


def test_get_status_shape(tmp_path):
    clock = FrozenClock(initial=1000.0)
    rt, audit_path = _make_runtime(tmp_path, clock, tasks=[])
    rt.start()
    status = rt.get_status()
    assert status["started"] is True
    assert status["manager_state"] == "RUNNING"
    assert status["task_count"] == 0
    assert status["tick_interval_seconds"] == 60.0
    assert str(audit_path) in status["audit_path"]
    assert status["tasks"] == {}
