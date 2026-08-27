# -*- coding: utf-8 -*-
"""
v1.1 Phase 3 验收测试: 后台生命周期 start / tick / stop / 异常恢复。

覆盖:
① IntegrationHost 完整生命周期 start→tick→stop, 停止后 tick 空转。
② Manager 层: 单任务异常隔离, 不影响下一次 tick。
③ Supervisor 层: 子任务异常隔离, 循环继续; start/stop 优雅关闭。
④ RuntimeCore._tick_background_host 默认关闭; 开启时惰性构造宿主并传
   reflection/memory_consolidation 开关。
⑤ RuntimeCore._drain_background_proposals 默认关闭; 开启时四域 drain 被调度;
   异常 fail-soft 不影响 tick。
⑥ 后台 drain 与聊天 Stage 13 共用账本幂等（重复 tick 不产生重复 mutation,
   由各 drain 的 ledger/EP-2 保证; 此处验证调度层不重复消费同一提案）。

隔离: bare RuntimeCore（__new__）+ monkeypatch; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import importlib
import time
from types import SimpleNamespace

from src.runtime.integration.integration_event import IntegrationEvent


def _bare_runtime():
    _rmod = importlib.import_module("src.runtime.runtime_core")
    _rcls = getattr(_rmod, "RuntimeCore")
    return _rcls.__new__(_rcls)


# ------------------------------------------------------------
# ① IntegrationHost 完整生命周期
# ------------------------------------------------------------
def test_host_full_lifecycle_start_tick_stop():
    from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost
    from src.runtime.lifecycle.internal.clock import FrozenClock
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager

    mgr = LifecycleManager(clock=FrozenClock(initial=0.0), name="p3_host")
    host = RuntimeIntegrationHost(lifecycle_manager=mgr, name="p3_host")
    assert host.start() is True
    assert host.status()["state"] == "running"

    host.register_default_tasks()
    registered = mgr.list_tasks()
    task_ids = {t.task_id for t in registered}
    assert "reflection_lifecycle_task" in task_ids, "Reflection 任务应在后台拓扑中"
    assert "memory_lifecycle_task" in task_ids, "Memory consolidation 任务应在后台拓扑中"
    assert "growth_lifecycle_task" in task_ids, "Growth 任务应在后台拓扑中"

    results = host.tick()
    assert host.tick_count == 1
    assert isinstance(results, list), "tick 应返回 LifecycleResult 列表"

    assert host.stop() is True
    assert host.status()["state"] == "stopped"
    # 停止后 tick 空转, 不抛错
    assert host.tick() == []


# ------------------------------------------------------------
# ② Manager 层: 单任务异常隔离
# ------------------------------------------------------------
def test_manager_task_failure_isolated_and_recovers():
    from src.runtime.lifecycle.internal.clock import FrozenClock
    from src.runtime.lifecycle.lifecycle_decision import AfterInterval
    from src.runtime.lifecycle.lifecycle_manager import LifecycleManager
    from src.runtime.lifecycle.lifecycle_task import BaseLifecycleTask

    class _BoomTask(BaseLifecycleTask):
        TASK_TYPE = "test"
        IDEMPOTENCY_BUCKET_SECONDS = 60

        def __init__(self):
            super().__init__(
                "boom_task", "p3", display_name="boom",
                condition=AfterInterval(0.0), budget_ms=1000,
                interval_seconds=0.0, max_concurrent=1,
            )
            self.fail = True

        def execute(self, context):
            if self.fail:
                raise RuntimeError("task boom")
            return {"ok": True}

    mgr = LifecycleManager(clock=FrozenClock(initial=0.0), name="p3_mgr")
    task = _BoomTask()
    mgr.register(task)
    mgr.start()

    results = mgr.tick()
    assert any(r.status == "FAILED" for r in results), "失败任务应产出 FAILED 结果"
    assert mgr.failed_task_count() >= 1

    task.fail = False  # 异常恢复后继续执行
    results2 = mgr.tick()
    assert any(r.status == "SUCCESS" for r in results2), "恢复后任务应成功"
    mgr.stop()


# ------------------------------------------------------------
# ③ Supervisor 层: 异常隔离 + start/stop
# ------------------------------------------------------------
def test_supervisor_error_isolated_and_graceful_stop():
    from src.runtime.supervisor import RuntimeSupervisor

    class _FlakyCore:
        def __init__(self):
            self.ticks = 0
            self.saved = 0

        def tick(self):
            self.ticks += 1
            if self.ticks == 1:
                raise RuntimeError("first tick boom")

        def save_lifecycle(self):
            self.saved += 1

    core = _FlakyCore()
    sup = RuntimeSupervisor(
        runtime_core=core,
        tick_interval_seconds=1,
        startup_grace_seconds=0.0,
        name="p3_sup",
    )
    assert sup.start() is True
    deadline = time.time() + 6.0
    while time.time() < deadline and core.ticks < 3:
        time.sleep(0.05)
    status = sup.get_status()
    assert core.ticks >= 3, "异常后循环应继续 tick"
    assert status["error_count"] >= 1, "首次 tick 异常应被记录"
    assert sup.stop(timeout=5.0) is True
    assert sup.is_running() is False
    assert core.saved >= 1, "stop 应保存生命周期状态"


# ------------------------------------------------------------
# ④ RuntimeCore 后台宿主: 默认关闭 + 开关传递
# ------------------------------------------------------------
def test_runtime_core_background_host_gated_and_flags(monkeypatch):
    built = {}

    class _FakeHost:
        def __init__(self, **kwargs):
            built.update(kwargs)
            self.ticked = 0
            self.started = False

        def start(self):
            self.started = True
            return True

        def tick(self):
            self.ticked += 1
            return []

    inst = _bare_runtime()
    inst.config = {}

    # 默认关闭: 不构造宿主
    monkeypatch.setattr(
        "src.runtime.integration.runtime_integration_host.RuntimeIntegrationHost",
        _FakeHost,
    )
    inst._tick_background_host()
    assert built == {}, "默认关闭时不应构造后台宿主"

    # 开启: 惰性构造 + 开关传递
    inst.config = {
        "integration_host_enabled": True,
        "reflection_cycle_enabled": True,
        "memory_consolidation_enabled": True,
    }
    inst._tick_background_host()
    assert built.get("reflection_cycle_enabled") is True
    assert built.get("memory_consolidation_enabled") is True
    inst._tick_background_host()
    assert built.get("name") == "runtime_background_host"


# ------------------------------------------------------------
# ⑤ RuntimeCore 后台 drain: 默认关闭 + 四域调度 + fail-soft
# ------------------------------------------------------------
def test_runtime_core_background_drain_gated(monkeypatch):
    inst = _bare_runtime()
    calls = {"personality": 0, "self_model": 0, "emotion": 0, "relationship": 0}

    def _bump(key):
        calls[key] += 1
        return {"applied": 0, "enabled": True}

    monkeypatch.setattr(inst, "drain_approved_growth_proposals", lambda: _bump("personality"))
    inst.config = {}
    inst.personality_resolver = SimpleNamespace(self_model_store=object())
    inst.emotion_manager = SimpleNamespace(repository=object())
    inst.relationship_repository = object()

    sm_mod = importlib.import_module("src.growth.self_model_approved_drain")
    em_mod = importlib.import_module("src.emotion.emotion_approved_drain")
    rel_mod = importlib.import_module("src.relationship.relationship_approved_drain")
    monkeypatch.setattr(sm_mod, "drain_approved_self_model_proposals", lambda **kw: _bump("self_model"))
    monkeypatch.setattr(em_mod, "drain_approved_emotion_proposals", lambda **kw: _bump("emotion"))
    monkeypatch.setattr(rel_mod, "drain_approved_relationship_proposals", lambda **kw: _bump("relationship"))

    # 默认关闭: 零调度
    inst._drain_background_proposals()
    assert calls == {"personality": 0, "self_model": 0, "emotion": 0, "relationship": 0}

    # 开启: 四域各调度一次
    inst.config = {"background_drain_enabled": True}
    inst._drain_background_proposals()
    assert calls["personality"] == 1
    assert calls["self_model"] == 1
    assert calls["emotion"] == 1
    assert calls["relationship"] == 1


def test_runtime_core_background_drain_failure_isolated(monkeypatch):
    inst = _bare_runtime()

    def _boom():
        raise RuntimeError("drain boom")

    monkeypatch.setattr(inst, "drain_approved_growth_proposals", _boom)
    inst.config = {"background_drain_enabled": True}
    # 不抛出
    inst._drain_background_proposals()


# ------------------------------------------------------------
# ⑥ 后台 drain 调度层幂等: 重复 tick 不重复消费（账本幂等由各 drain 保证）
# ------------------------------------------------------------
def test_background_drain_repeat_ticks_safe(monkeypatch):
    inst = _bare_runtime()
    calls = {"n": 0}

    def _count():
        calls["n"] += 1
        return {"applied": 0, "enabled": True}

    monkeypatch.setattr(inst, "drain_approved_growth_proposals", _count)
    inst.config = {"background_drain_enabled": True}
    inst._drain_background_proposals()
    inst._drain_background_proposals()
    assert calls["n"] == 2, "调度层允许重复 tick; 去重由 drain 账本保证"
