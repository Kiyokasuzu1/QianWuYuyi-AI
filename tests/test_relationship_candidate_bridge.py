# -*- coding: utf-8 -*-
"""Phase 2.5-D Commit 2 契约测试:MemoryCreatedEvent → RelationshipCandidateBridge。

治理约束(全部必须成立):
- 桥接是事件消费者:订阅 EventType.MEMORY_CREATED,与发布方(Orchestrator /
  未来 QQ/网页/语音/游戏入口)完全解耦,绝不挂在 save_memory 调用链里;
- 不新建 EventExtractor:直接消费事件携带的 user_id/memory_id/content/timestamp
  证据链,复用 2.5-C RelationshipCoreEvaluator 规则引擎(无 LLM);
- 系统侧永远只能产出 pending_review;accepted/activated 只能由人工端点达成;
- 桥接绝不写 RelationshipCore、绝不触碰人格/情绪/关系状态;
- 任何异常完全隔离:handler 抛错不影响事件发布方与其他订阅者;
- 去重:同一记忆 id 只产生一个提案。
"""
import inspect

from src.events.bus import EventBus, get_event_bus
from src.events.events import EventType, MemoryCreatedEvent, MessageRespondedEvent
from src.relationship.relationship_candidate_bridge import (
    RelationshipCandidateBridge,
    ensure_relationship_candidate_bridge,
    relationship_candidate_handler,
)
from src.relationship.relationship_proposal import PROPOSAL_STATUS
from src.relationship.relationship_proposal_store import RelationshipProposalStore

CANDIDATE_CONTENT = "羽依,你和我之间的约定:永远不要忘记我们的称呼规则"


def _make_event(memory_id="mem_1", user_id="366648462", content=CANDIDATE_CONTENT):
    return MemoryCreatedEvent(
        memory_id=memory_id, user_id=user_id, content=content, source="test",
    )


def _make_bridge(tmp_path):
    store = RelationshipProposalStore(str(tmp_path / "proposals.jsonl"))
    bridge = RelationshipCandidateBridge(proposal_store=store)
    return bridge, store


# ---------------- handler 对外契约(镜像 CT-11 模式) ----------------

def test_handler_importable_and_callable():
    assert callable(relationship_candidate_handler)


def test_handler_signature_takes_event():
    sig = inspect.signature(relationship_candidate_handler)
    params = list(sig.parameters.keys())
    assert len(params) >= 1
    assert "event" in params[0].lower()


def test_handler_missing_fields_silent():
    from src.events.events import YuyiEvent
    assert relationship_candidate_handler(
        YuyiEvent(event_type=EventType.MEMORY_CREATED, data={}),
    ) is None


# ---------------- 事件消费:候选生成 ----------------

def test_bridge_creates_pending_review_proposal_from_event(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)

    bus.publish(_make_event())

    proposals = store.list()
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == PROPOSAL_STATUS["PENDING_REVIEW"]
    assert p["source_memory_ids"] == ["mem_1"]
    assert p["source_user_id"] == "366648462"
    assert p["category"] == "relationship_core_candidate"
    assert p["score"] >= 0.5
    # 全部流转由 evaluator 系统角色完成
    assert [a["action"] for a in p["audit"]] == ["transition", "transition"]
    assert all(a["actor"] == "evaluator" for a in p["audit"])


def test_bridge_never_reaches_accepted_or_activated(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(_make_event())

    status = store.list()[0]["status"]
    assert status == PROPOSAL_STATUS["PENDING_REVIEW"]
    assert status not in (PROPOSAL_STATUS["ACCEPTED"], PROPOSAL_STATUS["ACTIVATED"])


def test_bridge_writes_no_core_and_touches_nothing_else(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(_make_event())
    # 桥接唯一副作用 = 提案文件;绝无关系核心文件
    assert list(tmp_path.iterdir()) == [tmp_path / "proposals.jsonl"]


# ---------------- 证据链与过滤 ----------------

def test_weak_evidence_no_proposal(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(_make_event(user_id=""))  # 无 user_id → 证据链断裂
    assert store.list() == []


def test_transient_content_no_proposal(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(_make_event(content="今天刚才我们随便聊了聊天气"))
    assert store.list() == []


def test_non_memory_events_ignored(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(MessageRespondedEvent(user_id="u1", content=CANDIDATE_CONTENT))
    assert store.list() == []


# ---------------- 去重 ----------------

def test_dedup_same_memory_id_single_proposal(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(_make_event())
    bus.publish(_make_event())  # 同一条记忆再次发布
    assert len(store.list()) == 1


def test_different_memory_ids_create_separate_proposals(tmp_path):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)
    bus.publish(_make_event(memory_id="mem_1"))
    bus.publish(_make_event(memory_id="mem_2"))
    assert len(store.list()) == 2


# ---------------- 异常隔离 ----------------

def test_evaluator_crash_is_isolated(tmp_path, monkeypatch):
    bridge, store = _make_bridge(tmp_path)
    bus = EventBus()
    bridge.subscribe(bus)

    def boom(record):
        raise RuntimeError("evaluator down")

    monkeypatch.setattr(bridge.evaluator, "evaluate", boom)
    bus.publish(_make_event())  # 不抛异常,不产生提案
    assert store.list() == []


def test_store_save_failure_returns_ok_false(tmp_path, monkeypatch):
    bridge, store = _make_bridge(tmp_path)
    monkeypatch.setattr(store, "save", lambda p: False)
    result = bridge.on_memory_created(_make_event())
    assert result is not None
    assert result["ok"] is False
    assert store.list() == []


# ---------------- 单例订阅与生产接线 ----------------

def test_ensure_singleton_idempotent_and_subscribed():
    bridge = ensure_relationship_candidate_bridge()
    try:
        assert bridge is ensure_relationship_candidate_bridge()  # 幂等单例
        bus = get_event_bus()
        handlers = bus._handlers.get(EventType.MEMORY_CREATED, [])
        assert relationship_candidate_handler in handlers
    finally:
        bridge.unsubscribe()


def test_register_builtin_handlers_includes_bridge():
    from src.events.handlers import register_builtin_handlers
    register_builtin_handlers()
    bus = get_event_bus()
    handlers = bus._handlers.get(EventType.MEMORY_CREATED, [])
    assert relationship_candidate_handler in handlers


def test_orchestrator_helper_subscribes_fail_soft():
    from src.orchestrator import Orchestrator
    from src.relationship.relationship_candidate_bridge import (
        ensure_relationship_candidate_bridge,
    )
    orch = Orchestrator.__new__(Orchestrator)
    try:
        assert orch._ensure_relationship_candidate_bridge() is True
        bus = get_event_bus()
        assert relationship_candidate_handler in bus._handlers.get(
            EventType.MEMORY_CREATED, [],
        )
    finally:
        ensure_relationship_candidate_bridge().unsubscribe()
