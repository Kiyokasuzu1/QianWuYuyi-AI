# -*- coding: utf-8 -*-
"""Phase 2.5-D Commit 3 契约测试:RuntimeCore relationship_core 注入。

治理约束(全部必须成立):
- 只读链路:RelationshipCoreStore → RelationshipCoreAdapter → ctx →
  [RELATIONSHIP_CONTEXT] → 两条回复路径(ResponseAdapter / engine 降级);
- 隔离:其他用户永远拿不到清清的 relationship_only 核心;
- 启动播种:runtime 初始化把已审核核心的 anchor_memory_ids 播种进
  CORE_RELATIONSHIP_MEMORY_IDS(重启不丢);
- 全部 fail-soft:store 缺失 / 异常 → 空串或不注入,绝不影响回复链路。
"""
from types import SimpleNamespace

from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS
from src.relationship.relationship_core import (
    DEFAULT_VISIBILITY,
    VISIBILITY_GLOBAL,
)
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.runtime.runtime_core import RuntimeCore

UID = "366648462"
OTHER_USER = "999999999"


def _core(relationship_id, visibility=DEFAULT_VISIBILITY, agreements=None,
          anchor_memory_ids=None):
    return {
        "relationship_id": relationship_id,
        "source_user_id": relationship_id.split(":")[-1],
        "relationship_type": "creator",
        "agreements": agreements or [],
        "boundaries": [],
        "anchor_memory_ids": anchor_memory_ids or [],
        "visibility": visibility,
    }


def _make_runtime(tmp_path, store, config=None):
    core = RuntimeCore.__new__(RuntimeCore)
    core.config = config or {"memory": {"target_user_id": UID}, "user_id": "yuyi"}
    core._relationship_core_store = store
    return core


def _make_event(user_id):
    return SimpleNamespace(payload={"user_id": user_id}, type="user_input")


def _make_ctx(user_message="你好"):
    return SimpleNamespace(user_message=user_message)


# ---------------- 启动播种 ----------------

def test_runtime_seeds_anchor_whitelist_from_core_store(tmp_path):
    store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    assert store.save(_core(
        f"yuyi:{UID}", agreements=["只对清清保持某些称呼"],
        anchor_memory_ids=["mem_seed_a", "mem_seed_b"],
    ))
    runtime = _make_runtime(tmp_path, store)
    before = set(CORE_RELATIONSHIP_MEMORY_IDS)
    try:
        CORE_RELATIONSHIP_MEMORY_IDS.difference_update(["mem_seed_a", "mem_seed_b"])
        assert runtime._seed_relationship_anchors() == 2
        assert "mem_seed_a" in CORE_RELATIONSHIP_MEMORY_IDS
        assert "mem_seed_b" in CORE_RELATIONSHIP_MEMORY_IDS
    finally:
        CORE_RELATIONSHIP_MEMORY_IDS.clear()
        CORE_RELATIONSHIP_MEMORY_IDS.update(before)


def test_runtime_seed_fails_soft_without_store():
    runtime = RuntimeCore.__new__(RuntimeCore)
    runtime._relationship_core_store = None
    assert runtime._seed_relationship_anchors() == 0


# ---------------- ctx 注入与隔离 ----------------

def test_runtime_injects_relationship_core_context_for_current_user(tmp_path):
    store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    assert store.save(_core(
        f"yuyi:{UID}", agreements=["只对清清保持某些称呼"],
    ))
    runtime = _make_runtime(tmp_path, store)

    ctx = _make_ctx()
    block = runtime._build_relationship_core_context_block(ctx, _make_event(UID))
    assert "[RELATIONSHIP_CONTEXT]" in block
    assert "只对清清保持某些称呼" in block
    assert getattr(ctx, "user_id", "") == UID
    assert isinstance(ctx.relationship_core_context, list)
    assert [c.get("relationship_id") for c in ctx.relationship_core_context] == [f"yuyi:{UID}"]


def test_runtime_isolation_other_user_never_gets_private_core(tmp_path):
    store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    # 清清的私人核心(relationship_only)+ 其他用户的公开核心(global)
    assert store.save(_core(
        f"yuyi:{UID}", agreements=["只对清清保持某些称呼"],
        visibility=DEFAULT_VISIBILITY,
    ))
    assert store.save(_core(
        "yuyi:888888888", agreements=["与用户888的公开约定"],
        visibility=VISIBILITY_GLOBAL,
    ))
    runtime = _make_runtime(tmp_path, store)

    ctx = _make_ctx()
    block = runtime._build_relationship_core_context_block(ctx, _make_event(OTHER_USER))
    assert "只对清清保持某些称呼" not in block
    assert "与用户888的公开约定" in block
    # ctx 结构化数据同样只含可见核心
    visible_ids = [c.get("relationship_id") for c in ctx.relationship_core_context]
    assert visible_ids == ["yuyi:888888888"]


def test_runtime_block_empty_without_store(tmp_path):
    runtime = RuntimeCore.__new__(RuntimeCore)
    runtime.config = {"memory": {"target_user_id": UID}}
    runtime._relationship_core_store = None
    assert runtime._build_relationship_core_context_block(_make_ctx(), _make_event(UID)) == ""


# ---------------- Stage 14 注入两条回复路径 ----------------

def test_stage14_injects_core_block_into_fallback_prompt(tmp_path, monkeypatch):
    store = RelationshipCoreStore(str(tmp_path / "cores.jsonl"))
    assert store.save(_core(
        f"yuyi:{UID}", agreements=["只对清清保持某些称呼"],
    ))
    runtime = _make_runtime(tmp_path, store)
    runtime.adapter_registry = None
    runtime.response_adapter = None
    monkeypatch.setattr(runtime, "_build_communication_strategy", lambda ctx: {})
    monkeypatch.setattr(runtime, "_build_communication_strategy_block", lambda s: "")
    monkeypatch.setattr(runtime, "_build_experience_context", lambda ctx: [])
    monkeypatch.setattr(runtime, "_build_behavior_guidance_block", lambda ctx: "")
    monkeypatch.setattr(runtime, "_build_runtime_relationship_snapshot", lambda: {})
    monkeypatch.setattr(runtime, "_build_relationship_prompt_context", lambda ctx: {})

    captured = {}

    class _FakeEngine:
        def generate(self, **kwargs):
            captured.update(kwargs)
            return "羽依回复"

    runtime._orchestrator_engine_ref = _FakeEngine()

    ctx = _make_ctx()
    runtime._stage_14_response_generation(_make_event(UID), ctx)
    assert ctx._final_reply == "羽依回复"
    blocks = [b.get("content", "") for b in (captured.get("context_prompt_blocks") or [])]
    assert any("[RELATIONSHIP_CONTEXT]" in b and "只对清清保持某些称呼" in b for b in blocks)


# ---------------- 桥接订阅接线 ----------------

def test_runtime_ensure_candidate_bridge_idempotent():
    from src.events.bus import get_event_bus
    from src.events.events import EventType
    from src.relationship.relationship_candidate_bridge import (
        ensure_relationship_candidate_bridge,
        relationship_candidate_handler,
    )
    runtime = RuntimeCore.__new__(RuntimeCore)
    try:
        assert runtime._ensure_relationship_candidate_bridge() is True
        bus = get_event_bus()
        assert relationship_candidate_handler in bus._handlers.get(
            EventType.MEMORY_CREATED, [],
        )
    finally:
        ensure_relationship_candidate_bridge().unsubscribe()
