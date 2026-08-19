# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段四契约测试:RelationshipCoreAdapter(只读)+ [RELATIONSHIP_CONTEXT] prompt 块。

覆盖:
- 适配器把当前会话可见的关系核心注入 ctx.relationship_core_context(只读)
- 隔离:清清的关系核心(private)绝不出现在其他用户会话;global 约定可见(约束羽依行为)
- 适配器绝不写盘:store 文件字节不变、无新增文件
- 损坏 store → fail-soft 降级为空上下文,不抛异常
- [RELATIONSHIP_CONTEXT] 结构化块:当前交互对象 / 关系事实 / 已审核关系约定 / 可以影响行为的边界
- 其他用户会话的 prompt 块不包含清清私人关系内容
"""
import os
import tempfile
from types import SimpleNamespace

from src.relationship.relationship_core import RelationshipCore
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.runtime.adapters.impl.relationship_core_adapter import (
    RelationshipCoreAdapter,
    build_relationship_context_block,
)

CREATOR = "366648462"
OTHER = "123456"


def _make_core(relationship_id, source_user_id, visibility, **overrides):
    kwargs = dict(
        relationship_id=relationship_id,
        source_user_id=source_user_id,
        relationship_type="creator" if source_user_id == CREATOR else "other",
        importance=0.9,
        agreements=["不要随便和别人抱抱"],
        boundaries=["私人约定不适用于其他用户"],
        events=[{"content": "清清说:要记住我们的约定", "timestamp": "2026-08-01T09:00:00"}],
        anchor_memory_ids=["mem_agreement"],
        visibility=visibility,
    )
    kwargs.update(overrides)
    return RelationshipCore(**kwargs)


def _build_store(tmp, creator_visibility="global"):
    path = os.path.join(tmp, "relationship_core.jsonl")
    store = RelationshipCoreStore(path)
    store.save(_make_core(f"yuyi:{CREATOR}", CREATOR, creator_visibility))
    store.save(_make_core(
        f"yuyi:{OTHER}", OTHER, "relationship_only",
        agreements=["用户B喜欢被称呼为某某"], importance=0.6,
    ))
    return path, store


# ---------------- 适配器:注入 + 隔离 ----------------

def test_adapter_injects_cores_for_creator():
    with tempfile.TemporaryDirectory() as tmp:
        path, store = _build_store(tmp)
        adapter = RelationshipCoreAdapter(store=store)
        ctx = SimpleNamespace(user_id=CREATOR)
        result = adapter.process_cycle(ctx)
        assert result is ctx
        cores = ctx.relationship_core_context
        assert isinstance(cores, list)
        assert cores, "清清的会话应看到自己的关系核心"
        ids = {c["relationship_id"] for c in cores}
        assert f"yuyi:{CREATOR}" in ids
        assert f"yuyi:{OTHER}" not in ids  # 其他用户的关系核心不进清清会话


def test_adapter_other_user_gets_only_global_and_own():
    """其他用户:只获得 global 可见核心 + 自己的核心,绝无清清私人核心。"""
    with tempfile.TemporaryDirectory() as tmp:
        path, store = _build_store(tmp, creator_visibility="global")
        adapter = RelationshipCoreAdapter(store=store)
        ctx = SimpleNamespace(user_id=OTHER)
        adapter.process_cycle(ctx)
        ids = {c["relationship_id"] for c in ctx.relationship_core_context}
        assert f"yuyi:{OTHER}" in ids
        assert f"yuyi:{CREATOR}" in ids  # global 约定跨会话可见(约束羽依行为)


def test_adapter_other_user_never_gets_creator_private_core():
    """核心安全约束:清清的关系核心为 relationship_only 时,其他用户绝不可见。"""
    with tempfile.TemporaryDirectory() as tmp:
        path, store = _build_store(tmp, creator_visibility="relationship_only")
        adapter = RelationshipCoreAdapter(store=store)
        ctx = SimpleNamespace(user_id=OTHER)
        adapter.process_cycle(ctx)
        cores = ctx.relationship_core_context
        ids = {c["relationship_id"] for c in cores}
        assert f"yuyi:{OTHER}" in ids
        assert f"yuyi:{CREATOR}" not in ids
        for core in cores:
            assert "不要随便和别人抱抱" not in core.get("agreements", [])


def test_adapter_is_read_only():
    with tempfile.TemporaryDirectory() as tmp:
        path, store = _build_store(tmp)
        before_bytes = open(path, "rb").read()
        before_files = set(os.listdir(tmp))
        adapter = RelationshipCoreAdapter(store=store)
        adapter.process_cycle(SimpleNamespace(user_id=CREATOR))
        adapter.process_cycle(SimpleNamespace(user_id=OTHER))
        after_bytes = open(path, "rb").read()
        after_files = set(os.listdir(tmp))
        assert before_bytes == after_bytes, "适配器不得写 relationship_core 文件"
        assert before_files == after_files, "适配器不得创建任何文件"
        assert store.load() and len(store.load()) == 2, "store 内容不得被修改"
        for forbidden in ("save", "write", "delete", "approve", "activate"):
            assert not hasattr(adapter, forbidden), f"只读适配器不得暴露 {forbidden}"


def test_adapter_fails_soft_on_corrupt_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "relationship_core.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json\n")
        adapter = RelationshipCoreAdapter(store=RelationshipCoreStore(path))
        ctx = SimpleNamespace(user_id=CREATOR)
        adapter.process_cycle(ctx)  # 不抛异常
        assert ctx.relationship_core_context == []


def test_adapter_without_store_degrades_empty():
    adapter = RelationshipCoreAdapter(store=None)
    ctx = SimpleNamespace(user_id=CREATOR)
    adapter.process_cycle(ctx)
    assert ctx.relationship_core_context == []


# ---------------- [RELATIONSHIP_CONTEXT] prompt 块 ----------------

def test_context_block_has_structured_sections():
    core = _make_core(f"yuyi:{CREATOR}", CREATOR, "global")
    block = build_relationship_context_block([core], current_user_id=CREATOR)
    assert "[RELATIONSHIP_CONTEXT]" in block
    assert "当前交互对象" in block
    assert "清夏铃" in block
    assert "关系事实" in block
    assert "已审核关系约定" in block
    assert "不要随便和别人抱抱" in block
    assert "可以影响行为的边界" in block
    assert "私人约定不适用于其他用户" in block


def test_context_block_empty_when_no_cores():
    assert build_relationship_context_block([], current_user_id=CREATOR) == ""


def test_context_block_other_user_no_creator_private_content():
    private = _make_core(f"yuyi:{CREATOR}", CREATOR, "relationship_only",
                         agreements=["清清的私人小秘密约定"])
    block = build_relationship_context_block([private], current_user_id=OTHER)
    assert "清清的私人小秘密约定" not in block
    assert "不要随便和别人抱抱" not in block


def test_context_block_other_user_global_core_labeled_as_constraint():
    global_core = _make_core(f"yuyi:{CREATOR}", CREATOR, "global")
    block = build_relationship_context_block([global_core], current_user_id=OTHER)
    # global 约定可见,但必须标注是清清的约定、是对羽依的约束
    assert "不要随便和别人抱抱" in block
    assert "清夏铃" in block
    assert "对羽依的约束" in block
