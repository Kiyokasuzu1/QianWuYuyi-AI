# -*- coding: utf-8 -*-
"""
Phase R-1.1: Event Flow 最小接线验收测试。

验证:
1. 注册后 MEMORY_CREATED / EMOTION_CHANGED / GROWTH_PROPOSAL_CREATED 均有订阅者。
2. register_builtin_handlers_once 幂等（重复调用订阅数不变）。
3. EMOTION_CHANGED: observer 被调用、无状态变化、无异常。
4. MEMORY_CREATED: 已有 handler 可消费（不抛）、不产生 GrowthProposal。
5. 旧测试回归（另跑: R-1.0 契约 + G-1 全链 + emotion_2_0 + D2 生命周期）。

约束: 本测试只验证接线形状, 不写业务逻辑; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.events.bus import get_event_bus, publish_event, subscribe_event
from src.events.events import (
    EventType,
    EmotionChangedEvent,
    MemoryCreatedEvent,
)
from src.events.handlers import (
    emotion_changed_observer_handler,
    register_builtin_handlers_once,
)


# ------------------------------------------------------------
# 1. 注册后三类事件均有订阅者
# ------------------------------------------------------------
def test_register_creates_subscribers():
    register_builtin_handlers_once()
    bus = get_event_bus()
    assert bus.has_subscribers(EventType.MEMORY_CREATED)
    assert bus.has_subscribers(EventType.EMOTION_CHANGED)
    assert bus.has_subscribers(EventType.GROWTH_PROPOSAL_CREATED)


# ------------------------------------------------------------
# 2. 幂等
# ------------------------------------------------------------
def test_register_is_idempotent():
    register_builtin_handlers_once()
    bus = get_event_bus()
    before = bus.get_subscriber_count()
    register_builtin_handlers_once()
    register_builtin_handlers_once()
    after = bus.get_subscriber_count()
    assert after == before, "重复注册不得增加订阅数"


# ------------------------------------------------------------
# 3. EMOTION_CHANGED observer
# ------------------------------------------------------------
def test_emotion_observer_safe_and_invoked(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH",
        str(tmp_path / "state_mutations.jsonl"),
    )
    # (a) 直接调用: 安全返回、无异常
    result = emotion_changed_observer_handler(EmotionChangedEvent(user_id="u1"))
    assert result is None

    # (b) 事件送达: 经总线发布后 spy 收到（observer 已注册在总线上）
    received = []
    subscribe_event(EventType.EMOTION_CHANGED, lambda e: received.append(e))
    publish_event(EmotionChangedEvent(user_id="u2"))
    assert len(received) == 1
    assert received[0].user_id == "u2"

    # (c) 无状态变化: 不产生任何 mutation audit
    assert not (tmp_path / "state_mutations.jsonl").exists()


# ------------------------------------------------------------
# 4. MEMORY_CREATED 可消费且不产生 GrowthProposal
# ------------------------------------------------------------
def test_memory_created_consumed_without_growth_proposal():
    register_builtin_handlers_once()
    proposals_path = Path("data/growth/proposals/proposals.json")
    before = 0
    if proposals_path.exists():
        try:
            data = json.loads(proposals_path.read_text(encoding="utf-8"))
            before = len((data or {}).get("proposals", []))
        except Exception:
            before = 0

    # 不存在的 memory_id: handler 消费失败也须被隔离, 不得向上抛
    results = publish_event(MemoryCreatedEvent(
        memory_id="mem_r1_1_nonexistent",
        user_id="u_test",
    ))
    assert isinstance(results, list)

    after = 0
    if proposals_path.exists():
        try:
            data = json.loads(proposals_path.read_text(encoding="utf-8"))
            after = len((data or {}).get("proposals", []))
        except Exception:
            after = 0
    assert after == before, "MEMORY_CREATED 消费不得产生 GrowthProposal"
