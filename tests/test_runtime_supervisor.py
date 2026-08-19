# -*- coding: utf-8 -*-
"""
tests/test_runtime_supervisor.py

Phase 3.5 Runtime Startup —— RuntimeSupervisor 单元测试

覆盖:
1. 启动 supervisor → 等待 tick → tick_count 增加
2. RuntimeCore.tick() 抛异常 → supervisor 不退出
3. stop() → 线程关闭 → 状态可读
4. system.tick 事件发布
5. HeartbeatCollector 上报
6. 优雅关闭
7. 单例模式
8. get_status 字段完整性
9. disabled 模式
10. 多次连续异常后仍能 tick

约束:
- 不修改任何核心业务模块
- 使用短 tick_interval 加速测试
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 强制 mock LLM
os.environ.setdefault("YUYI_LLM_MOCK", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def fresh_supervisor_class():
    """每个测试前重置 RuntimeSupervisor 单例"""
    from src.runtime.supervisor import RuntimeSupervisor
    RuntimeSupervisor.reset_instance()
    yield RuntimeSupervisor
    RuntimeSupervisor.reset_instance()


@pytest.fixture
def heartbeat_collector():
    """获取 HeartbeatCollector 单例"""
    try:
        from src.core.heartbeat import HeartbeatCollector, ModuleStatus
        HeartbeatCollector.reset_instance()
        hc = HeartbeatCollector.get_instance(timeout=10.0)
        yield hc, ModuleStatus
        HeartbeatCollector.reset_instance()
    except Exception:
        yield None, None


# ============================================================
# 1. 基本启动 / 停止
# ============================================================

class TestBasicStartStop:
    """基本启动/停止测试"""

    def test_start_returns_true(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            runtime_core=None,
            cognitive_core=None,
            heartbeat_collector=None,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        assert sup.start() is True
        assert sup.is_running() is True
        sup.stop()

    def test_double_start_is_idempotent(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        assert sup.start() is True
        assert sup.start() is True  # 第二次 noop
        sup.stop()

    def test_stop_when_not_running_is_safe(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(tick_interval_seconds=1, enabled=False)
        assert sup.stop() is True

    def test_disabled_supervisor_does_not_start(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            enabled=False,
        )
        assert sup.start() is False
        assert sup.is_running() is False


# ============================================================
# 2. Tick 行为
# ============================================================

class TestTickBehavior:
    """tick 行为验证"""

    def test_tick_count_increases(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        # 等待至少 2.5 秒,确保至少 2 次 tick
        time.sleep(2.5)
        sup.stop()

        status = sup.get_status()
        assert status["tick_count"] >= 2, f"应至少 2 次 tick,实际 {status['tick_count']}"
        assert status["last_tick_time"] is not None

    def test_runtime_core_tick_called(self, fresh_supervisor_class):
        """RuntimeCore.tick() 应被周期性调用"""
        mock_rc = MagicMock()
        sup = fresh_supervisor_class(
            runtime_core=mock_rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        # RuntimeCore.tick() 至少被调用 2 次
        assert mock_rc.tick.call_count >= 2, (
            f"RuntimeCore.tick() 应被调用 ≥2 次,实际 {mock_rc.tick.call_count}"
        )

    def test_system_tick_event_emitted(self, fresh_supervisor_class):
        """system.tick 事件应被发布到 CognitiveCore"""
        mock_cc = MagicMock()
        sup = fresh_supervisor_class(
            cognitive_core=mock_cc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        # emit_event 至少被调用 2 次
        assert mock_cc.emit_event.call_count >= 2
        # 检查至少有一次是 system.tick
        system_tick_calls = [
            c for c in mock_cc.emit_event.call_args_list
            if c.kwargs.get("event_type") == "system.tick"
            or (c.args and len(c.args) > 0 and c.args[0] == "system.tick")
        ]
        assert len(system_tick_calls) >= 1, "应至少发出一次 system.tick 事件"


# ============================================================
# 3. 异常隔离
# ============================================================

class TestExceptionIsolation:
    """子任务异常不应导致 supervisor 退出"""

    def test_runtime_core_exception_does_not_stop_supervisor(
        self, fresh_supervisor_class
    ):
        """RuntimeCore.tick() 抛异常时,supervisor 继续运行"""
        mock_rc = MagicMock()
        # 第一次抛异常,第二次正常
        call_count = {"n": 0}

        def tick_with_first_fail() -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated tick failure")
            # 后续正常

        mock_rc.tick.side_effect = tick_with_first_fail
        sup = fresh_supervisor_class(
            runtime_core=mock_rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        # 等待 3 秒(应至少 2-3 次 tick)
        time.sleep(3.0)
        sup.stop()

        status = sup.get_status()
        assert status["tick_count"] >= 2, (
            f"异常后仍应继续 tick,实际 tick_count={status['tick_count']}"
        )
        assert status["error_count"] >= 1, (
            f"应至少记录 1 次错误,实际 error_count={status['error_count']}"
        )
        assert status["last_error"] is not None
        assert "simulated tick failure" in status["last_error"]

    def test_emit_event_exception_does_not_stop_supervisor(
        self, fresh_supervisor_class
    ):
        """cognitive_core.emit_event() 抛异常时,supervisor 继续运行"""
        mock_cc = MagicMock()
        mock_cc.emit_event.side_effect = RuntimeError("emit failed")
        sup = fresh_supervisor_class(
            cognitive_core=mock_cc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        status = sup.get_status()
        assert status["tick_count"] >= 2
        assert status["error_count"] >= 1

    def test_continuous_exceptions_still_keep_loop(self, fresh_supervisor_class):
        """连续 5 次异常,supervisor 仍能 tick"""
        mock_rc = MagicMock()
        mock_rc.tick.side_effect = RuntimeError("always fails")
        sup = fresh_supervisor_class(
            runtime_core=mock_rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(5.5)  # 5-6 次 tick
        sup.stop()

        status = sup.get_status()
        assert status["tick_count"] >= 5
        assert status["error_count"] >= 5
        assert sup.is_running() is False  # 已 stop


# ============================================================
# 4. 优雅关闭
# ============================================================

class TestGracefulShutdown:
    """优雅关闭测试"""

    def test_stop_joins_thread(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(1.5)
        assert sup.is_running() is True

        stopped = sup.stop(timeout=5.0)
        assert stopped is True
        assert sup.is_running() is False
        # 线程已退出
        assert sup._thread is None or not sup._thread.is_alive()

    def test_stop_saves_lifecycle(self, fresh_supervisor_class):
        """stop() 应调用 runtime_core.save_lifecycle()"""
        mock_rc = MagicMock()
        sup = fresh_supervisor_class(
            runtime_core=mock_rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(1.5)
        sup.stop()

        assert mock_rc.save_lifecycle.called, "save_lifecycle() 应被调用"

    def test_stop_with_long_tick_returns_quickly(self, fresh_supervisor_class):
        """即使 tick_interval 很长,stop() 也能快速返回"""
        sup = fresh_supervisor_class(
            tick_interval_seconds=3600,  # 1 小时
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(0.5)

        start_ts = time.time()
        sup.stop(timeout=5.0)
        elapsed = time.time() - start_ts

        # 应远小于 tick_interval(1 小时)
        assert elapsed < 5.0, f"stop() 应 < 5s,实际 {elapsed}s"

    def test_stop_when_runtime_core_save_fails(self, fresh_supervisor_class):
        """save_lifecycle() 抛异常时,supervisor 仍能 stop"""
        mock_rc = MagicMock()
        mock_rc.save_lifecycle.side_effect = RuntimeError("save failed")
        sup = fresh_supervisor_class(
            runtime_core=mock_rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(1.5)
        stopped = sup.stop(timeout=5.0)
        assert stopped is True
        assert sup.is_running() is False


# ============================================================
# 5. Heartbeat 集成
# ============================================================

class TestHeartbeatIntegration:
    """HeartbeatCollector 集成测试"""

    def test_heartbeat_beat_called(self, fresh_supervisor_class, heartbeat_collector):
        hc, ModuleStatus = heartbeat_collector
        if hc is None:
            pytest.skip("HeartbeatCollector 不可用")

        sup = fresh_supervisor_class(
            heartbeat_collector=hc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        # 心跳应被记录
        status = hc.get_status("runtime_supervisor")
        assert status is not None, "Heartbeat 应有 runtime_supervisor 记录"
        assert status["module_name"] == "runtime_supervisor"
        assert status["status"] == "stopped", (
            f"stop() 后应为 stopped,实际 {status['status']}"
        )
        assert status["error_count"] == 0
        assert "tick_count" in status["custom_metrics"]


# ============================================================
# 6. 状态接口
# ============================================================

class TestStatusInterface:
    """get_status 字段完整性"""

    def test_status_contains_required_fields(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=60,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(0.2)
        status = sup.get_status()
        sup.stop()

        # 必备字段
        for key in (
            "running", "tick_count", "last_tick_time",
            "error_count", "last_error", "uptime",
            "tick_interval_seconds", "enabled", "name",
        ):
            assert key in status, f"get_status 缺少字段: {key}"

    def test_status_running_true_after_start(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        assert sup.get_status()["running"] is True
        sup.stop()

    def test_status_running_false_after_stop(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(0.5)
        sup.stop()
        assert sup.get_status()["running"] is False

    def test_status_uptime_calculated(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(0.5)
        status = sup.get_status()
        sup.stop()

        assert status["uptime"] is not None
        assert status["uptime"] >= 0.5


# ============================================================
# 7. 单例模式
# ============================================================

class TestSingleton:
    """单例模式测试"""

    def test_get_instance_returns_same(self, fresh_supervisor_class):
        s1 = fresh_supervisor_class.get_instance(tick_interval_seconds=10)
        s2 = fresh_supervisor_class.get_instance()
        assert s1 is s2
        s1.stop()

    def test_reset_instance_creates_new(self, fresh_supervisor_class):
        s1 = fresh_supervisor_class.get_instance(tick_interval_seconds=10)
        fresh_supervisor_class.reset_instance()
        s2 = fresh_supervisor_class.get_instance()
        assert s1 is not s2


# ============================================================
# 8. Disabled 模式
# ============================================================

class TestDisabledMode:
    """disabled 模式测试"""

    def test_disabled_does_not_start_thread(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(
            tick_interval_seconds=1,
            enabled=False,
        )
        result = sup.start()
        assert result is False
        assert sup.is_running() is False
        assert sup._thread is None

    def test_disabled_status_shows_enabled_false(self, fresh_supervisor_class):
        sup = fresh_supervisor_class(enabled=False)
        status = sup.get_status()
        assert status["enabled"] is False


# ============================================================
# 9. ProactiveEngine 集成(可选,如有)
# ============================================================

class TestProactiveIntegration:
    """ProactiveEngine.scan_all_users() 集成测试"""

    def test_proactive_scan_called(self, fresh_supervisor_class):
        mock_pe = MagicMock()
        sup = fresh_supervisor_class(
            proactive_engine=mock_pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        assert mock_pe.scan_all_users.call_count >= 2

    def test_proactive_exception_isolated(self, fresh_supervisor_class):
        """ProactiveEngine 抛异常时,supervisor 继续"""
        mock_pe = MagicMock()
        mock_pe.scan_all_users.side_effect = RuntimeError("scan failed")
        sup = fresh_supervisor_class(
            proactive_engine=mock_pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        status = sup.get_status()
        assert status["tick_count"] >= 2
        assert status["error_count"] >= 1


# ============================================================
# 10. 综合场景
# ============================================================

class TestEndToEnd:
    """端到端综合测试"""

    def test_full_simulation(self, fresh_supervisor_class, heartbeat_collector):
        """模拟完整 Runtime 生命周期"""
        hc, _ = heartbeat_collector
        if hc is None:
            pytest.skip("HeartbeatCollector 不可用")

        mock_rc = MagicMock()
        mock_cc = MagicMock()
        mock_pe = MagicMock()

        sup = fresh_supervisor_class(
            runtime_core=mock_rc,
            cognitive_core=mock_cc,
            heartbeat_collector=hc,
            proactive_engine=mock_pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )

        sup.start()
        # 运行 4 秒
        time.sleep(4.0)
        sup.stop()

        # 所有子系统都被调用
        assert mock_rc.tick.call_count >= 3
        assert mock_cc.emit_event.call_count >= 3
        assert mock_pe.scan_all_users.call_count >= 3

        # 心跳有记录
        hb = hc.get_status("runtime_supervisor")
        assert hb is not None

        # 状态正确
        status = sup.get_status()
        assert status["running"] is False
        assert status["tick_count"] >= 3
        assert status["error_count"] == 0
        assert status["uptime"] >= 3.0
