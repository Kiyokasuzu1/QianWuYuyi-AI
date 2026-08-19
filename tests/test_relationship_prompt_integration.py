# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段五契约测试:Orchestrator 层的 prompt 集成与锚点播种。

覆盖:
- _build_relationship_core_block 渲染当前会话可见的关系核心(注入【用户关系】块内)
- 其他用户拿不到清清的 relationship_only 核心(orchestrator 层隔离)
- 无核心 / 存储不可用时降级空字符串(旧行为不变)
- _seed_relationship_anchors 把已审核核心的锚点播种进内存白名单(修复重启丢失)

注意:测试用 Orchestrator.__new__ 绕过 __init__(避免 RuntimeBridge/
单例副作用),只验证本阶段新增方法的纯行为。
"""
import os
import tempfile

from src.orchestrator import Orchestrator
from src.relationship.relationship_core import RelationshipCore
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS

CREATOR = "366648462"
OTHER = "123456"


def _make_core(visibility="global", **overrides):
    kwargs = dict(
        relationship_id=f"yuyi:{CREATOR}",
        source_user_id=CREATOR,
        relationship_type="creator",
        importance=0.9,
        agreements=["不要随便和别人抱抱"],
        boundaries=["私人约定不适用于其他用户"],
        events=[{"content": "清清说:要记住我们的约定", "timestamp": "2026-08-01T09:00:00"}],
        anchor_memory_ids=["mem_agreement"],
        visibility=visibility,
    )
    kwargs.update(overrides)
    return RelationshipCore(**kwargs)


def _make_orchestrator(tmp, core=None, user_id=CREATOR):
    orch = Orchestrator.__new__(Orchestrator)
    orch.target_user_id = user_id
    path = os.path.join(tmp, "relationship_core.jsonl")
    store = RelationshipCoreStore(path)
    if core is not None:
        store.save(core)
    orch._relationship_core_store = store
    return orch


def test_orchestrator_core_block_renders_for_creator():
    with tempfile.TemporaryDirectory() as tmp:
        orch = _make_orchestrator(tmp, core=_make_core(visibility="relationship_only"))
        block = orch._build_relationship_core_block()
        assert "[RELATIONSHIP_CONTEXT]" in block
        assert "清夏铃" in block
        assert "不要随便和别人抱抱" in block
        assert "可以影响行为的边界" in block


def test_orchestrator_core_block_hides_private_core_from_other():
    with tempfile.TemporaryDirectory() as tmp:
        orch = _make_orchestrator(
            tmp, core=_make_core(visibility="relationship_only"), user_id=OTHER,
        )
        block = orch._build_relationship_core_block()
        assert block == ""  # relationship_only 且非本人 → 不注入


def test_orchestrator_core_block_global_visible_to_other():
    with tempfile.TemporaryDirectory() as tmp:
        orch = _make_orchestrator(tmp, core=_make_core(visibility="global"), user_id=OTHER)
        block = orch._build_relationship_core_block()
        assert "[RELATIONSHIP_CONTEXT]" in block
        assert "不要随便和别人抱抱" in block
        assert "对羽依的约束" in block  # 必须标注是清清的约束


def test_orchestrator_core_block_empty_without_store_data():
    with tempfile.TemporaryDirectory() as tmp:
        orch = _make_orchestrator(tmp, core=None)
        assert orch._build_relationship_core_block() == ""


def test_orchestrator_seed_relationship_anchors():
    with tempfile.TemporaryDirectory() as tmp:
        orch = _make_orchestrator(tmp, core=_make_core())
        try:
            added = orch._seed_relationship_anchors()
            assert added == 1
            assert "mem_agreement" in CORE_RELATIONSHIP_MEMORY_IDS
        finally:
            CORE_RELATIONSHIP_MEMORY_IDS.discard("mem_agreement")


def test_orchestrator_seed_fails_soft_on_corrupt_store():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "relationship_core.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("not json\n")
        orch = Orchestrator.__new__(Orchestrator)
        orch.target_user_id = CREATOR
        orch._relationship_core_store = RelationshipCoreStore(path)
        assert orch._seed_relationship_anchors() == 0
