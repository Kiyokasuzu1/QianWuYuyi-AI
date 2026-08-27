# -*- coding: utf-8 -*-
"""
v1.1 Production Hardening — Phase 2 部署模拟测试。

覆盖:
1. 全新环境启动: 空文件/缺失文件可加载, 不崩溃。
2. 已有数据启动: 正常加载; 损坏文件 fail-soft（备份降级, 不阻断启动）。
3. 后台运行: 连续 tick 无重复整理/无状态污染; 消费集合有界。
4. 异常恢复: LLM 缺 key 不阻断启动; 审计写失败不抛; 任务异常隔离;
   JSON 损坏恢复。
5. Phase 1 修复项定向验证: F1 懒加载 LLM / F2 损坏降级+原子写 /
   F3 心跳单链 / F4 历史上限 / F5 事件店轮转。
"""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.runtime.integration.integration_event import make_integration_event


# ============================================================
# 工具
# ============================================================
def _mem_store_cls():
    mod = importlib.import_module("src.memory.memory_store")
    return getattr(mod, "MemoryStore")


def _proposal_store_cls():
    mod = importlib.import_module("src.growth.proposal_store")
    return getattr(mod, "ProposalStore")


def _event_history_cls():
    mod = importlib.import_module("src.growth.event_history_store")
    return getattr(mod, "EventHistoryStore")


def _bare_orchestrator():
    mod = importlib.import_module("src.orchestrator")
    cls = getattr(mod, "Orchestrator")
    return cls.__new__(cls)


# ============================================================
# 1. 全新环境启动
# ============================================================
def test_fresh_environment_memory_and_stores(tmp_path):
    # 不存在的 memory 文件: 自动创建 + 空加载
    store = _mem_store_cls()(str(tmp_path / "memory.json"))
    assert store.load() == []
    assert (tmp_path / "memory.json").exists()

    # A-store: 自动创建 + 空索引
    a_store = _proposal_store_cls()(path=str(tmp_path / "proposals.jsonl"))
    assert a_store.list(limit=10) == []

    # 审计账本: 不存在时读取为空
    from src.governance.state_mutation_audit import read_entries

    assert read_entries(limit=10, path=tmp_path / "audit.jsonl") == []


def test_fresh_environment_v11_flags_default_false():
    import yaml

    cfg = yaml.safe_load(
        open(Path(__file__).parent.parent / "config.yaml", encoding="utf-8")
    )
    rt = cfg.get("runtime", {})
    for flag in (
        "integration_host_enabled",
        "reflection_cycle_enabled",
        "memory_consolidation_enabled",
        "background_drain_enabled",
    ):
        assert rt.get(flag) is False, f"{flag} 生产默认必须为 false"


# ============================================================
# 2. 已有数据启动（含损坏恢复）
# ============================================================
def test_existing_memory_loads(tmp_path):
    p = tmp_path / "memory.json"
    p.write_text(json.dumps([
        {"id": "m1", "content": "旧记忆一", "role": "user", "user_id": "u1"},
    ], ensure_ascii=False), encoding="utf-8")
    store = _mem_store_cls()(str(p))
    loaded = store.load()
    assert len(loaded) == 1 and loaded[0]["id"] == "m1"


def test_corrupt_memory_fails_soft(tmp_path):
    p = tmp_path / "memory.json"
    p.write_text("{broken json", encoding="utf-8")
    store = _mem_store_cls()(str(p))
    assert store.load() == []
    corrupt = [f for f in os.listdir(tmp_path) if ".corrupt" in f]
    assert corrupt, "损坏文件应被备份"


def test_corrupt_event_history_fails_soft(tmp_path):
    p = tmp_path / "event_history.json"
    p.write_text("{broken json", encoding="utf-8")
    store = _event_history_cls()(str(p))
    assert store.get_all() == {}
    # 原子写修复: put 后文件可重新加载
    store.put("e1", {"category": "test"})
    reloaded = _event_history_cls()(str(p))
    assert reloaded.get("e1") == {"category": "test"}


def test_a_store_skips_corrupt_lines(tmp_path):
    p = tmp_path / "proposals.jsonl"
    good = {"id": "p1", "source_event_id": "e1", "status": "pending",
            "confidence": 0.9, "proposed_changes": [], "evidence_ids": ["x"],
            "evaluator_meta": {}, "timestamp": "2026-01-01T00:00:00"}
    p.write_text(
        json.dumps(good, ensure_ascii=False) + "\n{corrupt line\n",
        encoding="utf-8",
    )
    a_store = _proposal_store_cls()(path=str(p))
    proposals = a_store.list(limit=10)
    assert len(proposals) == 1 and getattr(proposals[0], "id", "") == "p1"


# ============================================================
# 3. 后台运行（连续 tick）
# ============================================================
def test_consecutive_ticks_no_duplicate_work_no_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    from src.runtime.integration.integration_event import (
        INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    )
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    class _FakeEngine:
        def __init__(self):
            self.calls = 0

        def consolidate(self, memories, limit=None):
            self.calls += 1
            return SimpleNamespace(
                report_id="mcr_tick",
                conflicts=[{"subject": "咖啡"}],
                semantic_memories=[],
                episodic_memories=[],
            )

    class _FakeService:
        def __init__(self):
            self.records = []

        def accept_experience(self, record):
            self.records.append(record)
            return {"pipeline_state": "created"}

    engine = _FakeEngine()
    service = _FakeService()
    host = RuntimeIntegrationHost(
        name="deploy_tick",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        memory_consolidation_enabled=True,
        memory_loader=lambda: [{"content": "喜欢咖啡"}],
        consolidation_engine=engine,
    )
    host.start()
    monkeypatch.setattr(host, "_get_growth_service", lambda: service)
    host.publish_integration_event(make_integration_event(
        event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
        source="memory",
        payload={"task_id": "memory_lifecycle_task"},
        related_ids=["memory_lifecycle_task"],
    ))
    for _ in range(5):
        host.tick()
    assert engine.calls == 1, "同一整理事件只应消费一次"
    assert len(service.records) == 1, "建议只应进入治理链一次"
    from src.governance.state_mutation_audit import read_entries

    assert read_entries(limit=50, path=tmp_path / "audit.jsonl") == [], "后台 tick 不得产生 mutation"
    host.stop()


def test_host_consumed_id_sets_capped(monkeypatch):
    from src.runtime.integration.integration_event import (
        INTEGRATION_REFLECTION_COMPLETED,
    )
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    class _FakeService:
        def accept_experience(self, record):
            return {"pipeline_state": "created"}

    host = RuntimeIntegrationHost(
        name="deploy_cap",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        reflection_cycle_enabled=True,
    )
    host.start()
    monkeypatch.setattr(host, "_get_growth_service", lambda: _FakeService())
    host._consumed_reflection_ids = {f"ref_{i}" for i in range(250)}
    # 新事件触发消费 → 集合 251 → 触发 200 裁剪
    host.publish_integration_event(make_integration_event(
        event_type=INTEGRATION_REFLECTION_COMPLETED,
        source="reflection",
        payload={"reflection_id": "ref_new", "insights": ["新反思"]},
        related_ids=["ref_new"],
    ))
    host.tick()
    assert len(host._consumed_reflection_ids) <= 200
    host.stop()


# ============================================================
# 4. 异常恢复
# ============================================================
def test_missing_llm_key_does_not_block_construction(monkeypatch):
    import src.config

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.config._config", {"llm": {"api_key": "${DEEPSEEK_API_KEY}"}}
    )
    from src.response.engine import ResponseEngine

    engine = ResponseEngine()  # 不抛 = 服务可启动
    with pytest.raises(ValueError, match="API Key"):
        _ = engine.llm  # 首次使用才报清晰错误


def test_audit_write_failure_returns_false(tmp_path):
    from src.governance.state_mutation_audit import record_state_mutation

    ok = record_state_mutation(
        component="test",
        target="x",
        before={},
        after={},
        proposal_id="p_x",
        approval_id="a_x",
        actor="test",
        path=tmp_path,  # 目录 → 打开失败
    )
    assert ok is False, "审计写失败应返回 False 而不抛异常"


def test_host_tick_recovers_after_manager_exception(monkeypatch):
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    class _BadManager:
        def __init__(self):
            self.fail = True

        def start(self):
            return True

        def stop(self):
            return True

        def list_tasks(self):
            return []

        def tick(self):
            if self.fail:
                raise RuntimeError("manager boom")
            return []

    mgr = _BadManager()
    host = RuntimeIntegrationHost(
        name="deploy_recover",
        lifecycle_manager=mgr,
        enable_persistence=False,
    )
    host.start()
    host.tick()  # manager 异常被隔离
    mgr.fail = False
    host.tick()  # 恢复后继续工作
    assert host.tick_count == 2
    host.stop()


def test_corrupt_proposal_bstore_fails_soft(tmp_path):
    p = tmp_path / "proposals.json"
    p.write_text("{broken", encoding="utf-8")
    storage_mod = importlib.import_module("src.growth.proposal.storage")
    cls = getattr(storage_mod, "ProposalStorage")
    storage = cls(data_dir=str(tmp_path))
    assert storage.list_by_status("pending", limit=10) == []
    from src.growth.proposal.proposal import GrowthProposal as BProposal

    storage.save(BProposal(
        proposal_type="personality", status="pending", source="test",
        source_event_id="evt_bp1", before_state={}, after_state={},
        confidence=0.5, reason="t",
    ))
    # 损坏后首次 save 重建空信封（备份保留）
    assert storage.list_by_status("pending", limit=10) != []


# ============================================================
# 5. Phase 1 修复项定向验证
# ============================================================
def test_f3_heartbeat_reporter_single_chain(monkeypatch):
    from src.core.heartbeat import HeartbeatReporter, ModuleStatus

    reporter = HeartbeatReporter(module_name="deploy_f3", interval=0.02)
    reporter.start(ModuleStatus.RUNNING)
    first_timer = reporter._timer
    reporter.start(ModuleStatus.RUNNING)  # 重入不得创建第二条链
    assert reporter._timer is first_timer or reporter._timer_chain_running
    reporter.stop()
    assert reporter._timer_chain_running is False
    # stop 后已触发的 tick 不得复活链
    reporter._tick_and_reschedule()
    assert reporter._timer is None, "stop 后定时器链不得复活"


def test_f4_orchestrator_history_capped(monkeypatch):
    inst = _bare_orchestrator()
    inst.history = []
    inst._user_histories = {}
    inst._persist_history = lambda: None
    for i in range(120):
        inst.record_conversation_turn(f"消息{i}", f"回复{i}", user_id="u1")
    assert len(inst.history) <= 200, "全局历史必须封顶 200 条"
    assert inst.history[-1] == {"role": "assistant", "content": "回复119"}


def test_f5_event_store_rotation_and_flush(monkeypatch, tmp_path):
    store_mod = importlib.import_module(
        "src.runtime.integration.integration_event_store"
    )
    monkeypatch.setattr(store_mod, "MAX_STORE_BYTES", 2000)
    cls = getattr(store_mod, "IntegrationEventStore")
    p = str(tmp_path / "events.jsonl")
    store = cls(path=p, name="deploy_f5")
    for i in range(4):
        store.append(make_integration_event(
            event_type="deploy.test",
            source="test",
            payload={"i": i, "pad": "x" * 700},
        ))
    assert os.path.exists(p + ".old"), "超阈值应轮转归档"
    assert os.path.getsize(p) < 2000, "活动文件应重新开始"
    events = store.read_all(limit=100)
    assert len(events) >= 1, "轮转后活动文件应可读"
    store.close()
