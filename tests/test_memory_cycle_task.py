# -*- coding: utf-8 -*-
"""tests/test_memory_cycle_task.py

P2.7 Phase D-1: MemoryCycleTask 单测(只读整理 + 摘要)。

红线验证: 任务执行不得修改输入记忆(consolidate 只读)。
"""

import copy

from src.runtime.lifecycle.internal.clock import FrozenClock
from src.runtime.lifecycle.lifecycle_context import LifecycleContext
from src.runtime.lifecycle.lifecycle_task import SimpleTask  # noqa: F401  (说明任务基类可替换)
from src.runtime.lifecycle.tasks.memory_cycle import MemoryCycleTask


def _ctx(now=1000.0):
    return LifecycleContext(clock=FrozenClock(initial=now), task_id="memory.cycle")


def _memory(mid, content, memory_class, ts="2026-08-01T00:00:00+00:00"):
    return {"id": mid, "content": content, "memory_class": memory_class, "timestamp": ts}


def test_consolidates_and_reports_summary():
    memories = [
        _memory("m1", "用户喜欢咖啡", "preference"),
        _memory("m2", "用户不喜欢咖啡", "preference"),
        _memory("m3", "用户今天去了公园", "event"),
    ]
    task = MemoryCycleTask(memory_loader=lambda: memories, limit=10)
    result = task.run_safely(_ctx())
    assert result.is_success()
    metrics = result.metrics
    assert metrics["input_count"] == 3
    summary = metrics["report_summary"]
    assert summary["input_count"] == 3
    assert summary["conflict_count"] == 1  # 咖啡正负偏好冲突被检出


def test_reinforcement_promotes_semantic():
    memories = [
        _memory("m1", "用户喜欢茶", "preference"),
        _memory("m2", "用户喜欢茶", "preference"),
    ]
    task = MemoryCycleTask(memory_loader=lambda: memories, limit=10)
    result = task.run_safely(_ctx())
    assert result.metrics["report_summary"]["semantic_count"] == 1


def test_empty_memories_noop():
    task = MemoryCycleTask(memory_loader=lambda: [], limit=10)
    result = task.run_safely(_ctx())
    assert result.is_success()
    assert result.metrics["input_count"] == 0
    assert result.metrics["report_summary"]["input_count"] == 0


def test_loader_failure_isolated():
    def _boom():
        raise RuntimeError("loader boom")

    task = MemoryCycleTask(memory_loader=_boom)
    result = task.run_safely(_ctx())
    assert result.is_failure()
    assert result.error is not None


def test_execute_does_not_mutate_input_memories():
    memories = [
        _memory("m1", "用户喜欢咖啡", "preference"),
        _memory("m2", "用户不喜欢咖啡", "preference"),
    ]
    snapshot_before = copy.deepcopy(memories)
    task = MemoryCycleTask(memory_loader=lambda: memories, limit=10)
    task.run_safely(_ctx())
    assert memories == snapshot_before  # 输入列表未被引擎改动


def test_task_metadata():
    task = MemoryCycleTask(memory_loader=lambda: [], interval_seconds=1234.0, limit=7)
    assert task.task_id == "memory.cycle"
    assert task.owner == "p2.7.d1"
    assert task.interval_seconds == 1234.0
    assert task.TASK_TYPE == "memory_cycle"
    assert task.IDEMPOTENCY_BUCKET_SECONDS == 86400
