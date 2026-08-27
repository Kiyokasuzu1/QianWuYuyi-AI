# -*- coding: utf-8 -*-
"""tests/test_lifecycle_audit_writer.py

P2.7 Phase D-1: LifecycleAuditWriter 单测(append-only JSONL)。
"""

from src.contracts.lifecycle_event_schema import LifecycleEvent
from src.runtime.lifecycle.audit_writer import LifecycleAuditWriter


def _event(task_type="memory_cycle", status="SUCCESS", idx=0):
    return LifecycleEvent(
        task_type=task_type,
        result={"status": status, "reason": ""},
        audit={"idempotency_key": f"{task_type}:{idx}"},
    )


def test_append_and_read_recent(tmp_path):
    writer = LifecycleAuditWriter(tmp_path / "audit" / "events.jsonl")
    assert writer.append(_event(idx=1)) is True
    assert writer.append(_event(idx=2)) is True
    assert writer.count() == 2
    items = writer.read_recent()
    assert len(items) == 2
    assert items[0]["audit"]["idempotency_key"] == "memory_cycle:2"
    assert items[1]["result"]["status"] == "SUCCESS"


def test_read_recent_filters_by_task_type(tmp_path):
    writer = LifecycleAuditWriter(tmp_path / "events.jsonl")
    writer.append(_event(task_type="memory_cycle"))
    writer.append(_event(task_type="emotion_cycle"))
    writer.append(_event(task_type="emotion_cycle"))
    only_emotion = writer.read_recent(task_type="emotion_cycle")
    assert len(only_emotion) == 2
    assert all(x["task_type"] == "emotion_cycle" for x in only_emotion)
    only_memory = writer.read_recent(task_type="memory_cycle")
    assert len(only_memory) == 1


def test_read_recent_skips_corrupt_lines(tmp_path):
    writer = LifecycleAuditWriter(tmp_path / "events.jsonl")
    writer.append(_event(idx=1))
    # 手工注入一行损坏 JSON
    with open(writer.path, "a", encoding="utf-8") as f:
        f.write("this is not json\n")
    writer.append(_event(idx=2))
    assert writer.count() == 2
    items = writer.read_recent()
    assert len(items) == 2


def test_missing_file_reads_empty(tmp_path):
    writer = LifecycleAuditWriter(tmp_path / "missing" / "events.jsonl")
    assert writer.read_recent() == []
    assert writer.count() == 0


def test_append_fails_soft_on_directory_path(tmp_path):
    # 路径指向已存在目录 → 写入必然失败,append 返回 False 且不抛
    writer = LifecycleAuditWriter(tmp_path)
    assert writer.append(_event()) is False
    assert writer.append(None) is False


def test_append_dict_convenience(tmp_path):
    writer = LifecycleAuditWriter(tmp_path / "events.jsonl")
    assert writer.append_dict(
        {"task_type": "memory_cycle", "result": {"status": "FAILED", "reason": "x"}}
    ) is True
    items = writer.read_recent()
    assert items[0]["task_type"] == "memory_cycle"
    assert items[0]["result"]["status"] == "FAILED"
