# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段一契约测试:RelationshipCore 数据模型 + 持久化存储。

覆盖:
- RelationshipCore 强类型结构与默认值(visibility 默认 relationship_only)
- to_dict / from_dict 序列化往返 + 缺字段容错(旧数据可读)
- store append-only 保存/读取、重复 relationship_id 拒绝
- 损坏行跳过、整体损坏文件备份后降级空列表、文件不存在降级空列表
"""
import json
import os
import tempfile

from src.relationship.relationship_core import (
    DEFAULT_VISIBILITY,
    VISIBILITY_GLOBAL,
    VISIBILITY_RELATIONSHIP_ONLY,
    RelationshipCore,
)
from src.relationship.relationship_core_store import RelationshipCoreStore

CREATOR = "366648462"


def _make_core(relationship_id="yuyi:366648462", **overrides) -> RelationshipCore:
    kwargs = dict(
        relationship_id=relationship_id,
        source_user_id=CREATOR,
        relationship_type="creator",
        importance=0.9,
        events=[{"event": "共同建立羽依项目", "at": "2026-08-01T00:00:00"}],
        agreements=["不要随便和别人抱抱"],
        boundaries=["私人约定不适用于其他用户"],
        anchor_memory_ids=["mem_agreement"],
        visibility=VISIBILITY_RELATIONSHIP_ONLY,
    )
    kwargs.update(overrides)
    return RelationshipCore(**kwargs)


# ---------------- 数据结构 ----------------

def test_core_defaults():
    core = RelationshipCore()
    assert core.relationship_id == ""
    assert core.source_user_id == ""
    assert core.importance == 0.5
    assert core.events == []
    assert core.agreements == []
    assert core.boundaries == []
    assert core.anchor_memory_ids == []
    assert core.visibility == DEFAULT_VISIBILITY
    assert core.visibility == VISIBILITY_RELATIONSHIP_ONLY
    assert core.created_at
    assert core.updated_at


def test_core_global_visibility_is_explicit():
    """global 只能显式声明,绝不作为默认值出现。"""
    core = _make_core(visibility=VISIBILITY_GLOBAL)
    assert core.visibility == VISIBILITY_GLOBAL


def test_core_serialization_roundtrip():
    core = _make_core()
    data = core.to_dict()
    restored = RelationshipCore.from_dict(data)
    for key in (
        "relationship_id", "source_user_id", "relationship_type",
        "importance", "events", "agreements", "boundaries",
        "anchor_memory_ids", "visibility",
    ):
        assert getattr(restored, key) == getattr(core, key), key


def test_core_from_dict_tolerates_missing_fields():
    """旧数据/缺字段必须可读,全部回退默认值(零迁移)。"""
    restored = RelationshipCore.from_dict({"relationship_id": "yuyi:123"})
    assert restored.relationship_id == "yuyi:123"
    assert restored.agreements == []
    assert restored.visibility == DEFAULT_VISIBILITY
    assert restored.importance == 0.5


# ---------------- 持久化存储 ----------------

def test_store_save_and_load():
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        assert store.save(_make_core())
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0]["relationship_id"] == "yuyi:366648462"
        assert loaded[0]["agreements"] == ["不要随便和别人抱抱"]
        assert store.get("yuyi:366648462") is not None
        assert store.get("yuyi:nonexistent") is None
        assert len(store.list_all()) == 1


def test_store_append_only_and_rejects_duplicate_id():
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        assert store.save(_make_core("yuyi:366648462"))
        assert store.save(_make_core("yuyi:123456"))
        # 同一 relationship_id 重复写入必须被拒绝(append-only,不做隐式覆盖)
        assert not store.save(_make_core("yuyi:366648462"))
        loaded = store.load()
        assert len(loaded) == 2
        ids = [r["relationship_id"] for r in loaded]
        assert ids == ["yuyi:366648462", "yuyi:123456"]


def test_store_corrupt_line_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "relationship_core.jsonl")
        store = RelationshipCoreStore(path)
        store.save(_make_core("yuyi:366648462"))
        with open(path, "a", encoding="utf-8") as f:
            f.write("NOT_JSON_LINE\n")
            f.write(json.dumps(_make_core("yuyi:123456").to_dict(), ensure_ascii=False) + "\n")
        loaded = store.load()
        ids = [r["relationship_id"] for r in loaded]
        assert ids == ["yuyi:366648462", "yuyi:123456"]


def test_store_fully_corrupt_file_backed_up():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "relationship_core.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{{{ garbage \n")
            f.write("not json at all\n")
        store = RelationshipCoreStore(path)
        loaded = store.load()
        assert loaded == []
        backups = [n for n in os.listdir(tmp) if ".corrupt." in n]
        assert backups, "整体损坏文件必须被备份,便于审计恢复"


def test_store_missing_file_returns_empty():
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipCoreStore(os.path.join(tmp, "nonexistent.jsonl"))
        assert store.load() == []
