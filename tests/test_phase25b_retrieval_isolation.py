# -*- coding: utf-8 -*-
"""Phase 2.5-B 检索隔离测试(scope 授权过滤 + 老数据兼容)。

覆盖需求场景:
- 场景1:清夏铃的约定(白名单锚点)跨窗口存在,但不泄漏私人内容
- 场景2:用户B无法读取清夏铃私人内容
- 场景3:用户B的记忆不得混入清夏铃会话
- 老 memory.json 加载正常、内容零修改
"""
import json
import os
import tempfile

from src.memory.memory_store import MemoryStore
from src.memory.memory_service import MemoryService
from src.memory.memory_relevance_evaluator import MemoryRelevanceEvaluator
from src.memory.memory_scope import (
    SCOPE_PRIVATE_USER,
    derive_scope,
    collect_allowed_records,
)
from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS

CREATOR = "366648462"
OTHER = "123456"


class _FakeVector:
    """返回全部候选,由 memory_service 做 scope 授权过滤。"""

    def __init__(self, entries):
        self._entries = [dict(e) for e in entries]

    def search(self, query, top_k=None, user_id=None):
        return [dict(e) for e in self._entries]


def _build_store(tmp):
    store = MemoryStore(os.path.join(tmp, "memory.json"))
    store.add({
        "id": "mem_creator_private", "user_id": CREATOR, "role": "user",
        "content": "清清今天去了XX餐厅", "timestamp": "2026-08-17T10:00:00",
        "metadata": {"memory_type": "user_shared"},
    })
    store.add({
        "id": "mem_b_private", "user_id": OTHER, "role": "user",
        "content": "用户B喜欢某游戏", "timestamp": "2026-08-17T11:00:00",
        "metadata": {"memory_type": "user_shared"},
    })
    store.add({
        "id": "mem_agreement", "user_id": CREATOR, "role": "user",
        "content": "羽依,不要随便和别人抱抱", "timestamp": "2026-08-16T09:00:00",
        "metadata": {"memory_type": "user_shared"},
    })
    return store


def _make_service(store):
    return MemoryService(
        store,
        relevance_evaluator=MemoryRelevanceEvaluator(),
        vector_memory=_FakeVector([
            {"mem_id": "mem_creator_private", "relevance": 0.9},
            {"mem_id": "mem_b_private", "relevance": 0.85},
            {"mem_id": "mem_agreement", "relevance": 0.8},
        ]),
    )


def test_scoped_search_creator_session_excludes_other_private():
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(_build_store(tmp))
        results = service.semantic_search("餐厅 游戏 约定", top_k=3, user_id=CREATOR)
        ids = {r.get("id") for r in results}
        assert "mem_creator_private" in ids
        assert "mem_b_private" not in ids  # 场景3:用户B信息不得混入清清会话


def test_scoped_search_other_session_cannot_see_creator_private():
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(_build_store(tmp))
        results = service.semantic_search("餐厅 游戏 约定", top_k=3, user_id=OTHER)
        ids = {r.get("id") for r in results}
        assert "mem_b_private" in ids
        assert "mem_creator_private" not in ids  # 场景2:不能读取清清私人内容


def test_scoped_search_anchor_visible_cross_session():
    """场景1:约定进白名单后,用户B会话仍召回该约束,但私人内容不泄漏。"""
    CORE_RELATIONSHIP_MEMORY_IDS.add("mem_agreement")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            service = _make_service(_build_store(tmp))
            results = service.semantic_search("抱抱", top_k=3, user_id=OTHER)
            ids = {r.get("id") for r in results}
            assert "mem_agreement" in ids
            assert "mem_creator_private" not in ids
    finally:
        CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")


def test_semantic_search_without_user_id_keeps_old_behavior():
    """user_id=None 时必须保持旧行为:全库召回、无授权过滤。"""
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(_build_store(tmp))
        results = service.semantic_search("x", top_k=3)
        ids = {r.get("id") for r in results}
        assert ids == {"mem_creator_private", "mem_b_private", "mem_agreement"}


def test_scoped_results_annotated_with_scope_fields():
    with tempfile.TemporaryDirectory() as tmp:
        service = _make_service(_build_store(tmp))
        results = service.semantic_search("餐厅", top_k=3, user_id=CREATOR)
        annotated = [r for r in results if r.get("id") == "mem_creator_private"]
        assert annotated
        assert "_scope" in annotated[0]


def test_collect_allowed_records_owner_only_without_anchor():
    with tempfile.TemporaryDirectory() as tmp:
        store = _build_store(tmp)
        recs = collect_allowed_records(store, OTHER)
        ids = {r.get("id") for r in recs}
        assert "mem_b_private" in ids
        assert "mem_creator_private" not in ids
        assert "mem_agreement" not in ids  # 未进白名单前 = 私人记忆


def test_collect_allowed_records_anchor_included_for_everyone():
    CORE_RELATIONSHIP_MEMORY_IDS.add("mem_agreement")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_store(tmp)
            recs = collect_allowed_records(store, OTHER)
            ids = {r.get("id") for r in recs}
            assert "mem_agreement" in ids  # 场景6:换用户/重启后约束仍在
            assert "mem_creator_private" not in ids
    finally:
        CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")


def test_legacy_memory_json_loads_unchanged():
    """老 memory.json(无 metadata/scope 字段)必须原样可读、内容零修改。"""
    legacy = [
        {"id": "old_1", "content": "老记录", "timestamp": "2026-01-01T00:00:00",
         "user_id": "366648462", "role": "user", "importance": 0.5},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "memory.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        store = MemoryStore(path)
        loaded = store.load()
        assert len(loaded) == 1
        assert loaded[0]["id"] == "old_1"
        assert loaded[0].get("content") == "老记录"
        # 读时推导,不写回(零迁移)
        assert derive_scope(loaded[0]) == SCOPE_PRIVATE_USER
        assert loaded[0].get("metadata", {}).get("memory_scope") is None
