# -*- coding: utf-8 -*-
"""
v1.1 Phase 2.3 验收测试: Memory Consolidation → Growth 治理链接线。

覆盖:
① 默认关闭, legacy 行为零变化（SHOULD_CONSOLIDATE 不触发整理）。
② SHOULD_CONSOLIDATE 事件驱动只读整理（引擎收到记忆, 无写回）。
③ 整理建议三类生成（冲突 / 合并 / 权重调整）。
④ 整理建议经 accept_experience 进入成长治理链（metadata.source 标注）。
⑤ 无建议时跳过治理链。
⑥ 引擎异常 fail-soft（不阻塞 tick）。
⑦ 记忆只读: 不删除、不写回 MemoryStore。
⑧ 同一事件幂等消费（重复 tick 不重复整理）。

隔离: 假 service / 假引擎 / 假只读 loader; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.runtime.integration.integration_event import (
    INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
    make_integration_event,
)
from src.runtime.integration.runtime_integration_host import (
    consolidation_record_from_report,
    consolidation_suggestions_from_report,
)


def _make_host(consolidation_enabled=True, loader=None, engine=None):
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost

    host = RuntimeIntegrationHost(
        name="v23_host",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        memory_consolidation_enabled=consolidation_enabled,
        memory_loader=loader,
        consolidation_engine=engine,
    )
    host.start()
    return host


def _consolidate_event(event_id="mcev_1"):
    return make_integration_event(
        event_type=INTEGRATION_MEMORY_SHOULD_CONSOLIDATE,
        source="memory",
        payload={"task_id": "memory_lifecycle_task", "tick": 1},
        related_ids=["memory_lifecycle_task"],
    )


class _FakeReport:
    def __init__(self, report_id="mcr_1", conflicts=None, semantic=None, episodic=None):
        self.report_id = report_id
        self.conflicts = conflicts or []
        self.semantic_memories = semantic or []
        self.episodic_memories = episodic or []


class _FakeEngine:
    def __init__(self, report):
        self._report = report
        self.calls = []

    def consolidate(self, memories, limit=None):
        self.calls.append((list(memories), limit))
        if isinstance(self._report, Exception):
            raise self._report
        return self._report


class _FakeService:
    def __init__(self):
        self.records = []

    def accept_experience(self, record):
        self.records.append(record)
        return {"pipeline_state": "created"}


class _ReadOnlyMemory:
    """只读记忆容器: 允许 load, 拒绝任何写操作。"""

    def __init__(self, memories):
        self._memories = list(memories)
        self.load_calls = 0

    def load(self):
        self.load_calls += 1
        return [dict(m) for m in self._memories]

    def add(self, *args, **kwargs):
        raise AssertionError("MemoryConsolidation 循环禁止写 MemoryStore")

    def add_many(self, *args, **kwargs):
        raise AssertionError("MemoryConsolidation 循环禁止写 MemoryStore")


# ------------------------------------------------------------
# ① 默认关闭: legacy 行为零变化
# ------------------------------------------------------------
def test_consolidation_cycle_disabled_by_default(monkeypatch):
    calls = []

    class _SpyEngine:
        def consolidate(self, memories, limit=None):
            calls.append("called")
            return _FakeReport()

    host = _make_host(
        consolidation_enabled=False,
        loader=lambda: [{"content": "x"}],
        engine=_SpyEngine(),
    )
    host.publish_integration_event(_consolidate_event())
    host.tick()
    assert calls == [], "开关关闭时不应执行整理引擎"


# ------------------------------------------------------------
# ② SHOULD_CONSOLIDATE 事件驱动只读整理
# ------------------------------------------------------------
def test_should_consolidate_event_drives_readonly_engine():
    memories = [
        {"content": "喜欢咖啡", "memory_class": "preference", "id": "m1"},
        {"content": "喜欢咖啡", "memory_class": "preference", "id": "m2"},
    ]
    engine = _FakeEngine(_FakeReport(report_id="mcr_a", semantic=[
        {"content": "喜欢咖啡", "reinforcement_count": 2, "decay_score": 0.9},
    ]))
    host = _make_host(loader=lambda: memories, engine=engine)
    host.publish_integration_event(_consolidate_event("mcev_a"))
    host.tick()
    assert len(engine.calls) == 1, "应执行一次只读整理"
    assert [m["id"] for m in engine.calls[0][0]] == ["m1", "m2"], "引擎应收到记忆副本"


# ------------------------------------------------------------
# ③ 建议三类生成（冲突 / 合并 / 权重调整）
# ------------------------------------------------------------
def test_suggestion_generation_three_kinds():
    report = _FakeReport(
        report_id="mcr_b",
        conflicts=[{"subject": "咖啡"}],
        semantic=[{"content": "喜欢咖啡", "reinforcement_count": 3, "decay_score": 0.9}],
        episodic=[{"content": "某次闲聊", "decay_score": 0.1}],
    )
    suggestions = consolidation_suggestions_from_report(report)
    assert any("冲突" in s for s in suggestions)
    assert any("合并建议" in s for s in suggestions)
    assert any("权重调整" in s for s in suggestions)
    assert len(suggestions) == 3


def test_no_suggestions_when_report_empty():
    assert consolidation_suggestions_from_report(_FakeReport(report_id="mcr_e")) == []
    assert consolidation_suggestions_from_report(None) == []


# ------------------------------------------------------------
# ④ 整理建议经 accept_experience 进入成长治理链
# ------------------------------------------------------------
def test_suggestions_flow_to_growth_chain(monkeypatch):
    report = _FakeReport(
        report_id="mcr_c",
        conflicts=[{"subject": "咖啡"}],
    )
    engine = _FakeEngine(report)
    service = _FakeService()
    host = _make_host(loader=lambda: [{"content": "喜欢咖啡"}], engine=engine)
    monkeypatch.setattr(host, "_get_growth_service", lambda: service)
    host.publish_integration_event(_consolidate_event("mcev_c"))
    host.tick()
    assert len(service.records) == 1
    rec = service.records[0]
    assert rec["metadata"]["source"] == "memory_consolidation"
    assert "咖啡" in rec["content"]
    assert rec["role"] == "user"
    assert rec["user_id"] == "yuyi"


def test_record_conversion_from_event_and_report():
    report = _FakeReport(report_id="mcr_x", conflicts=[{"subject": "游戏"}])
    record = consolidation_record_from_report(_consolidate_event("mcev_x"), report)
    assert record is not None
    assert record["id"] == "mcons_mcr_x"
    assert record["metadata"]["conflict_count"] == 1
    assert consolidation_record_from_report(
        _consolidate_event("mcev_y"), _FakeReport(report_id="mcr_y")
    ) is None, "无建议的报告不应生成记录"


# ------------------------------------------------------------
# ⑤ 无建议时跳过治理链
# ------------------------------------------------------------
def test_no_suggestions_skips_growth_chain(monkeypatch):
    engine = _FakeEngine(_FakeReport(report_id="mcr_d"))
    service = _FakeService()
    host = _make_host(loader=lambda: [], engine=engine)
    monkeypatch.setattr(host, "_get_growth_service", lambda: service)
    host.publish_integration_event(_consolidate_event("mcev_d"))
    host.tick()
    assert service.records == [], "无建议时不应进入治理链"


# ------------------------------------------------------------
# ⑥ 引擎异常 fail-soft
# ------------------------------------------------------------
def test_engine_failure_isolated():
    host = _make_host(loader=lambda: [{"content": "x"}], engine=_FakeEngine(RuntimeError("boom")))
    host.publish_integration_event(_consolidate_event("mcev_f"))
    host.tick()  # 不应抛出
    assert host.tick_count == 1
    # 后续 tick 仍可用
    host.tick()
    assert host.tick_count == 2


# ------------------------------------------------------------
# ⑦ 记忆只读: 不删除、不写回
# ------------------------------------------------------------
def test_memory_readonly_no_writes(monkeypatch):
    from src.memory.memory_consolidation_engine import MemoryConsolidationEngine

    memories = [
        {"content": "喜欢咖啡", "memory_class": "preference", "id": "m1", "timestamp": "2026-01-01T00:00:00Z"},
        {"content": "喜欢咖啡", "memory_class": "preference", "id": "m2", "timestamp": "2026-01-02T00:00:00Z"},
        {"content": "不喜欢咖啡", "memory_class": "preference", "id": "m3", "timestamp": "2026-01-03T00:00:00Z"},
    ]
    readonly = _ReadOnlyMemory(memories)
    service = _FakeService()
    host = _make_host(
        loader=readonly.load,
        engine=MemoryConsolidationEngine(),
    )
    monkeypatch.setattr(host, "_get_growth_service", lambda: service)
    host.publish_integration_event(_consolidate_event("mcev_g"))
    host.tick()
    assert readonly.load_calls == 1
    assert [m["id"] for m in readonly.load()] == ["m1", "m2", "m3"], "记忆不得被删除"
    # 真实引擎应产出冲突建议（喜欢/不喜欢咖啡）→ 进入治理链
    assert len(service.records) == 1
    assert service.records[0]["metadata"]["source"] == "memory_consolidation"


# ------------------------------------------------------------
# ⑧ 幂等: 同一事件重复 tick 不重复整理
# ------------------------------------------------------------
def test_same_event_consumed_once():
    engine = _FakeEngine(_FakeReport(report_id="mcr_h", conflicts=[{"subject": "咖啡"}]))
    host = _make_host(loader=lambda: [{"content": "喜欢咖啡"}], engine=engine)
    ev = _consolidate_event("mcev_h")
    host.publish_integration_event(ev)
    host.tick()
    host.publish_integration_event(ev)  # 同一事件对象再次注入
    host.tick()
    assert len(engine.calls) == 1, "同一事件应只消费一次"
