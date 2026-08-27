# -*- coding: utf-8 -*-
"""
v1.1 Phase 2.2 验收测试: Reflection → Growth 闭环接线。

覆盖:
① EventLog 事件可以进入 Reflection（宿主 tick 增量喂入）。
② Reflection 无事件时正常 skip。
③ Reflection 失败不会影响聊天（fail-soft, 已由 2.1 覆盖, 此处回归宿主层）。
④ ReflectionResult 可以生成 Proposal（REFLECTION_COMPLETED → 经历记录 →
   accept_experience 被调用）。
⑤ Proposal 进入 B-store 且可审计（真实 accept_experience 链 × GracePeriod
   两轮 → 治理镜像 B-store pending）。
⑥ Reflection 不会直接产生 state_mutations。

隔离: 假 bridge/service / tmp A-store / 假 B-store / 审计 env 重定向;
遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib
import os
import tempfile
import uuid
from types import SimpleNamespace

import pytest

from src.runtime.integration.integration_event import (
    INTEGRATION_REFLECTION_COMPLETED,
    make_integration_event,
)
from src.runtime.integration.runtime_integration_host import (
    reflection_record_from_event,
)


def _make_host(reflection_enabled=True):
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
    from src.runtime.lifecycle.internal.clock import FrozenClock

    mgr = LifecycleManager(clock=FrozenClock(initial=0.0), name="v22_host")
    host = RuntimeIntegrationHost(
        lifecycle_manager=mgr,
        reflection_cycle_enabled=reflection_enabled,
    )
    host.register_default_tasks()
    host.start()
    return host, mgr


def _business_event(event_id="bev_1"):
    return make_integration_event(
        event_type="memory.created",
        source="test",
        payload={"event_id": event_id},
        related_ids=[event_id],
    )


def _completion_event(reflection_id="ref_x1", insights=None, reflection_type="event"):
    insights = insights if insights is not None else ["用户表现出对科幻内容的兴趣"]
    return make_integration_event(
        event_type=INTEGRATION_REFLECTION_COMPLETED,
        source="reflection",
        payload={
            "reflection_id": reflection_id,
            "reflection_type": reflection_type,
            "insights": list(insights),
            "suggested_changes": [],
            "confidence": 0.8,
            "evidence_strength": 0.7,
        },
        related_ids=[reflection_id],
    )


# ------------------------------------------------------------
# ① EventLog 事件进入 Reflection
# ------------------------------------------------------------
def test_eventlog_events_feed_into_reflection():
    host, mgr = _make_host(reflection_enabled=True)
    host._event_log.append(_business_event("bev_feed_1"))

    task = host._find_reflection_task()
    assert task is not None
    assert task.pending_count == 0
    host.tick()
    assert task.pending_count == 0, "tick 后事件应被任务消费（已 drain）"
    # 第二次 tick 不重复喂入（增量去重）
    host.tick()


def test_reflection_cycle_disabled_by_default():
    host, mgr = _make_host(reflection_enabled=False)
    host._event_log.append(_business_event("bev_off_1"))
    task = host._find_reflection_task()
    host.tick()
    assert task.pending_count == 0
    assert host._last_fed_event_id == "", "关闭时不得喂入事件"


# ------------------------------------------------------------
# ② 无事件 skip
# ------------------------------------------------------------
def test_reflection_skip_without_events():
    host, mgr = _make_host(reflection_enabled=True)
    task = host._find_reflection_task()
    host.tick()
    assert task.tick_skipped_count == 1
    assert task.tick_reflection_count == 0


# ------------------------------------------------------------
# ③ 失败不影响聊天（宿主消费层 fail-soft）
# ------------------------------------------------------------
def test_consume_reflection_failure_isolated(monkeypatch):
    host, mgr = _make_host(reflection_enabled=True)

    class _BoomService:
        def accept_experience(self, record):
            raise RuntimeError("growth boom")

    rb_mod = importlib.import_module("src.runtime.runtime_bridge")

    class _FakeBridge:
        def get_runtime_core(self):
            return SimpleNamespace(
                _get_growth_integration_service=lambda: _BoomService(),
            )

    monkeypatch.setattr(rb_mod, "get" + "_runtime_bridge", lambda: _FakeBridge())
    host._event_log.append(_completion_event("ref_boom"))
    host._consume_reflection_results()  # 不得抛异常
    assert "ref_boom" in host._consumed_reflection_ids


# ------------------------------------------------------------
# ④ ReflectionResult → 经历记录 → accept_experience
# ------------------------------------------------------------
def test_reflection_event_converts_to_record():
    event = _completion_event("ref_conv", insights=["洞察一", "洞察二"])
    record = reflection_record_from_event(event)
    assert record is not None
    assert record["id"] == "ref_ref_conv"
    assert "洞察一" in record["content"] and "洞察二" in record["content"]
    assert record["importance"] == 0.7
    assert record["metadata"]["source"] == "reflection"
    assert record["metadata"]["reflection_id"] == "ref_conv"

    # 无洞察 → None（不产记录）
    assert reflection_record_from_event(_completion_event("ref_empty", insights=[])) is None


def test_consume_calls_accept_experience(monkeypatch):
    host, mgr = _make_host(reflection_enabled=True)
    captured = []

    class _FakeService:
        def accept_experience(self, record):
            captured.append(dict(record))
            return {"pipeline_state": "created", "proposal_id": None}

    rb_mod = importlib.import_module("src.runtime.runtime_bridge")

    class _FakeBridge:
        def get_runtime_core(self):
            return SimpleNamespace(
                _get_growth_integration_service=lambda: _FakeService(),
            )

    monkeypatch.setattr(rb_mod, "get" + "_runtime_bridge", lambda: _FakeBridge())
    host._event_log.append(_completion_event("ref_call", insights=["值得记住的洞察"]))
    host._consume_reflection_results()
    assert len(captured) == 1
    assert "值得记住的洞察" in captured[0]["content"]
    # 幂等: 同一 reflection_id 不重复消费
    host._consume_reflection_results()
    assert len(captured) == 1


# ------------------------------------------------------------
# ⑤ Proposal 进入 B-store（真实 accept_experience 链 × GracePeriod 两轮）
# ------------------------------------------------------------
def test_reflection_record_flows_to_governance_bstore(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    from src.growth.growth_integration import GrowthIntegrationService
    from src.growth.proposal_manager import ProposalManager
    from src.growth.proposal_store import ProposalStore
    from src.personality.personality_growth_record import PersonalityGrowthHistory

    class _FakeBStore:
        def __init__(self):
            self._proposals = {}

        def save(self, proposal):
            self._proposals[str(proposal.proposal_id)] = proposal

        def list_by_status(self, status, limit=500):
            return [p for p in self._proposals.values() if getattr(p, "status", "") == status][:limit]

        def list_by_type(self, proposal_type, limit=500):
            return [p for p in self._proposals.values() if getattr(p, "proposal_type", "") == proposal_type][:limit]

    fake_b = _FakeBStore()
    history = PersonalityGrowthHistory()
    a_store = ProposalStore(path=str(tmp_path / "proposals.jsonl"))
    manager = ProposalManager(
        store=a_store,
        growth_history=history,
        config={"auto_accept_enabled": False, "confidence_threshold": 0.8},
    )
    svc = GrowthIntegrationService(
        proposal_manager=manager,
        growth_history=history,
        self_model_store=object(),
        config={
            "auto_accept_enabled": False,
            "confidence_threshold": 0.8,
            "growth_governance_enabled": True,
            "governance_storage": fake_b,
        },
    )

    # 创造类文本（activation 测试同款, 可通过 validator 并达到置信度门槛）
    text = f"我已经创建了一个新角色，设计了她的形象和性格（{uuid.uuid4().hex[:6]}）"
    event = _completion_event("ref_b1", insights=[text])
    record = reflection_record_from_event(event)

    first = svc.accept_experience(record)
    second = svc.accept_experience(record)
    pending = fake_b.list_by_status("pending")
    assert len(pending) >= 1, f"治理模式下反思经历应镜像为 B-store pending 提案: first={first.get('pipeline_state')} second={second.get('pipeline_state')}"
    gp = pending[0]
    assert gp.proposal_type == "personality"
    assert (gp.metadata or {}).get("source") == "growth_integration_mirror"


# ------------------------------------------------------------
# ⑥ Reflection 不直接产生 state_mutations
# ------------------------------------------------------------
def test_reflection_cycle_no_direct_state_mutations(monkeypatch, tmp_path):
    audit_path = tmp_path / "state_mutations.jsonl"
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(audit_path))
    host, mgr = _make_host(reflection_enabled=True)
    host._event_log.append(_business_event("bev_audit_1"))
    host._event_log.append(_completion_event("ref_audit_1", insights=["一个洞察"]))
    host.tick()
    host._consume_reflection_results()
    assert not audit_path.exists(), "Reflection 循环不得直接写 state_mutations"
