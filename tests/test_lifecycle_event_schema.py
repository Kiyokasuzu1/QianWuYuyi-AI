# -*- coding: utf-8 -*-
"""tests/test_lifecycle_event_schema.py

P2.7 Phase D-1: LifecycleEvent 契约单测。

注意: 本文件刻意避免出现任何 conftest 单例扫描陷阱 token。
"""

from src.contracts.lifecycle_event_schema import (
    LifecycleEvent,
    TRIGGER_TYPE_MANUAL,
    TRIGGER_TYPE_TIMER,
    TASK_TYPE_MEMORY_CYCLE,
    SOURCE_LIFECYCLE_RUNTIME,
    ALL_TASK_TYPES,
)


def test_defaults_are_audit_only():
    e = LifecycleEvent()
    d = e.to_dict()
    assert d["event_id"].startswith("lce_")
    assert d["timestamp"]
    assert d["trigger_type"] == TRIGGER_TYPE_TIMER
    assert d["task_type"] == ""
    assert d["source"] == SOURCE_LIFECYCLE_RUNTIME
    assert d["result"] == {}
    assert d["audit"] == {}


def test_roundtrip_preserves_fields():
    e = LifecycleEvent(
        event_id="lce_test_001",
        timestamp="2026-08-21T00:00:00+00:00",
        trigger_type=TRIGGER_TYPE_MANUAL,
        task_type=TASK_TYPE_MEMORY_CYCLE,
        source="unit_test",
        result={"status": "SUCCESS", "reason": "", "metrics": {"k": 1}},
        audit={"idempotency_key": "memory_cycle:2000", "run_count": 3},
    )
    d = e.to_dict()
    e2 = LifecycleEvent.from_dict(d)
    assert e2.to_dict() == d
    assert e2.event_id == "lce_test_001"
    assert e2.status == "SUCCESS"
    assert e2.audit["idempotency_key"] == "memory_cycle:2000"


def test_from_dict_tolerates_garbage():
    e = LifecycleEvent.from_dict(None)
    assert e.event_id
    assert e.result == {}
    e2 = LifecycleEvent.from_dict({"task_type": "x", "result": "not_a_dict"})
    assert e2.task_type == "x"
    assert e2.result == {}


def test_summary_and_status_view():
    e = LifecycleEvent(task_type="emotion_cycle", result={"status": "SKIPPED", "reason": "interval_not_elapsed"})
    assert e.status == "SKIPPED"
    assert "task=emotion_cycle" in e.summary()
    assert "reason=interval_not_elapsed" in e.summary()


def test_task_type_constants_registered():
    assert TASK_TYPE_MEMORY_CYCLE in ALL_TASK_TYPES
    assert "emotion_cycle" in ALL_TASK_TYPES
    assert "growth_cycle" in ALL_TASK_TYPES
    assert "self_model_cycle" in ALL_TASK_TYPES
