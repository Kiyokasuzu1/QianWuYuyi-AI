# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段二契约测试:锚点持久化与四层可见性(关系核心跨重启存在)。

覆盖:
- jsonl → registry → CORE_RELATIONSHIP_MEMORY_IDS 的加载链
- 重新初始化 registry(模拟重启)后关系核心锚点仍存在
- 存储损坏/缺失时自动降级空集合(行为 = 2.5-B 现状)
- 清夏铃窗口:YUI_CORE + RELATIONSHIP_CORE + 私人记忆全可见
- 用户B窗口:只看到羽依自身/公开约束,看不到清夏铃私人内容
"""
import os
import tempfile

from src.relationship.anchor_registry import AnchorRegistry
from src.relationship.relationship_core import RelationshipCore
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.memory.memory_store import MemoryStore
from src.memory.memory_scope import (
    SCOPE_RELATIONSHIP_CORE,
    derive_scope,
    collect_allowed_records,
)
from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS

CREATOR = "366648462"
OTHER = "123456"


def _make_core() -> RelationshipCore:
    return RelationshipCore(
        relationship_id=f"yuyi:{CREATOR}",
        source_user_id=CREATOR,
        relationship_type="creator",
        importance=0.9,
        agreements=["不要随便和别人抱抱"],
        boundaries=["私人约定不适用于其他用户"],
        anchor_memory_ids=["mem_agreement"],
        visibility="global",
    )


def _build_memory_store(tmp) -> MemoryStore:
    store = MemoryStore(os.path.join(tmp, "memory.json"))
    store.add({
        "id": "mem_agreement", "user_id": CREATOR, "role": "user",
        "content": "羽依,不要随便和别人抱抱", "timestamp": "2026-08-16T09:00:00",
        "metadata": {"memory_type": "user_shared"},
    })
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
    return store


# ---------------- 锚点加载链 ----------------

def test_registry_loads_anchor_ids_from_store():
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        store.save(_make_core())
        registry = AnchorRegistry(store=store)
        assert registry.load_anchor_ids() == {"mem_agreement"}


def test_registry_seed_updates_global_whitelist():
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        store.save(_make_core())
        registry = AnchorRegistry(store=store)
        try:
            added = registry.seed_into(CORE_RELATIONSHIP_MEMORY_IDS)
            assert added == 1
            assert "mem_agreement" in CORE_RELATIONSHIP_MEMORY_IDS
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")


def test_anchor_record_derives_relationship_core_scope():
    with tempfile.TemporaryDirectory() as tmp:
        store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        store.save(_make_core())
        registry = AnchorRegistry(store=store)
        record = {"id": "mem_agreement", "role": "user", "user_id": CREATOR,
                  "content": "约定", "timestamp": "2026-08-16T09:00:00"}
        try:
            registry.seed_into(CORE_RELATIONSHIP_MEMORY_IDS)
            assert derive_scope(record) == SCOPE_RELATIONSHIP_CORE
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")


def test_registry_reinit_keeps_core_after_restart():
    """决策2验收:重新初始化 registry(模拟重启)后关系核心锚点仍存在。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "relationship_core.jsonl")
        store = RelationshipCoreStore(path)
        store.save(_make_core())
        first = AnchorRegistry(store=store).load_anchor_ids()
        # 模拟重启:用同一路径重建 store 与 registry
        second = AnchorRegistry(store=RelationshipCoreStore(path)).load_anchor_ids()
        assert first == {"mem_agreement"}
        assert second == {"mem_agreement"}


def test_registry_corrupt_store_falls_back_empty():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "relationship_core.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json\n")
        registry = AnchorRegistry(store=RelationshipCoreStore(path))
        assert registry.load_anchor_ids() == set()


def test_registry_without_store_falls_back_empty():
    registry = AnchorRegistry(store=None)
    assert registry.load_anchor_ids() == set()


# ---------------- 四层可见性(经 registry 激活后) ----------------

def test_creator_window_sees_core_and_private():
    with tempfile.TemporaryDirectory() as tmp:
        core_store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        core_store.save(_make_core())
        registry = AnchorRegistry(store=core_store)
        try:
            registry.seed_into(CORE_RELATIONSHIP_MEMORY_IDS)
            recs = collect_allowed_records(_build_memory_store(tmp), CREATOR)
            ids = {r.get("id") for r in recs}
            assert "mem_agreement" in ids       # RELATIONSHIP_CORE
            assert "mem_creator_private" in ids  # 清夏铃私人层
            assert "mem_b_private" not in ids    # 用户B私人不得混入
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")


def test_other_window_gets_constraint_but_not_creator_private():
    with tempfile.TemporaryDirectory() as tmp:
        core_store = RelationshipCoreStore(os.path.join(tmp, "relationship_core.jsonl"))
        core_store.save(_make_core())
        registry = AnchorRegistry(store=core_store)
        try:
            registry.seed_into(CORE_RELATIONSHIP_MEMORY_IDS)
            recs = collect_allowed_records(_build_memory_store(tmp), OTHER)
            ids = {r.get("id") for r in recs}
            assert "mem_agreement" in ids         # 公开约束(对羽依的约束)可见
            assert "mem_b_private" in ids          # 用户B自己的记忆可见
            assert "mem_creator_private" not in ids  # 清夏铃私人内容不可见
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")
