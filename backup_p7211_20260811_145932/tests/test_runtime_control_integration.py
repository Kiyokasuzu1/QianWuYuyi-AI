# -*- coding: utf-8 -*-
"""
tests/test_runtime_control_integration.py

Phase C.10.6 — Runtime Control Integration 测试

覆盖:
1. RuntimeControlProvider (Server 端,新接口)
   - 默认全启用
   - is_enabled() 各模块独立
   - safe_mode:growth/initiative/dream 自动 disable
   - maintenance_mode:几乎所有非 runtime 模块 disable
   - get_control_mode() 三种模式判定
   - check_module() 返回 ModuleCheckResult
   - audit sink 集成
   - record_control_change() 写 audit + 触发 event

2. RuntimeControlContext (Server 端,新)
   - 默认 NullProvider(全 allow)
   - 注入 provider 后正确转发
   - allow() 异常 fail-soft
   - 批量 allow / which_allowed / which_blocked

3. RuntimeCore 集成(Server 端)
   - 未注入 context → 行为不变(向后兼容)
   - 注入 context → Memory/Emotion/Growth 在 disable 时被跳过
   - safe_mode 开启时,growth 阶段被跳过
   - maintenance_mode 开启时,所有非 runtime 阶段被跳过
   - configure_control_context() 运行时切换
   - configure_control_adapter() 与 configure_control_context() 同步
   - get_control_check() 摘要
   - get_control_state_snapshot()

4. EventBus 集成(新)
   - RuntimeControlChangedEvent 在状态变化时发出
   - RuntimeControlSkippedEvent 在阶段被跳过时发出

5. 默认行为不改变
   - 未注入 context 时,RUNTIME_LIFECYCLE 完全不变
   - 现有 E2E 测试不回归

强约束:
- 不修改 src/runtime 业务逻辑结构(只读 + 阶段 hook)
- 不删除任何已有测试
- 不修改 src/memory / src/growth / src/personality / src/self_model
- 不修改 src/control/state / src/control/manager / src/control/api
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil
from typing import Any, Dict, List, Optional

import pytest


# ============================================================
# 路径设置
# ============================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture(autouse=True)
def _reset_control_state():
    """每个 test 前重置 ControlState / Provider / Context 单例。"""
    tmp = tempfile.mkdtemp(prefix="yuyi_ctrl_test_")
    try:
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.manager.control_manager import (
            reset_control_manager_for_testing,
        )
        from src.control.runtime import (
            reset_runtime_control_provider_for_testing,
        )
        from src.runtime.context.control_context import (
            reset_default_control_context_for_testing,
        )

        # 关键:先创建带 tempdir 的 persistence 实例
        persistence = reset_control_state_persistence_for_testing(data_dir=tmp)
        reset_module_registry_for_testing()
        # manager 和 provider 都使用同一个 persistence 实例(显式注入)
        reset_control_manager_for_testing(state_persistence=persistence)
        reset_runtime_control_provider_for_testing(state_persistence=persistence)
        # 重置默认 context
        reset_default_control_context_for_testing()
    except Exception as exc:
        print(f"[_reset_control_state] failed: {exc}")
        pass
    yield
    # cleanup
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


def _make_simple_event(payload: Optional[Dict[str, Any]] = None):
    """构造一个简单 Event 对象。"""
    try:
        from src.runtime.events import Event

        ev = Event(
            type="user_message",
            payload=payload or {"text": "hello", "content": "hello"},
        )
        return ev
    except Exception:
        return {"type": "user_message", "payload": payload or {"text": "hello"}}


# ============================================================
# Test: RuntimeControlProvider 基础
# ============================================================
class TestRuntimeControlProviderBasic:
    """RuntimeControlProvider 基础行为测试。"""

    def test_default_all_enabled(self):
        """默认所有模块 enabled(全开)。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        provider = get_runtime_control_provider()
        for m in ["memory", "emotion", "growth", "initiative", "dream", "live2d", "runtime"]:
            assert provider.is_enabled(m) is True, f"{m} should be enabled by default"

    def test_runtime_always_enabled(self):
        """runtime 模块永远 enabled。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        # 即使把 state 设为 false,provider 也应返回 True
        get_control_state_persistence().set_field(
            "runtime_enabled", False, operator="test",
        )
        assert provider.is_runtime_enabled() is True
        assert provider.is_enabled("runtime") is True

    def test_is_enabled_respects_control_state(self):
        """is_enabled 应该正确读取 ControlState。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        persistence = get_control_state_persistence()
        provider = get_runtime_control_provider()

        # 关闭 growth
        persistence.set_field("growth_enabled", False, operator="test")
        assert provider.is_growth_enabled() is False
        assert provider.is_enabled("growth") is False
        # 其他模块仍 enabled
        assert provider.is_memory_enabled() is True
        assert provider.is_emotion_enabled() is True

    def test_unknown_module_defaults_true(self):
        """未知模块默认 enabled(最小侵入)。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        provider = get_runtime_control_provider()
        assert provider.is_enabled("some_future_module") is True
        assert provider.is_enabled("") is True
        assert provider.is_enabled("unknown_xyz") is True

    def test_get_control_mode_normal(self):
        """默认模式:normal。"""
        from src.control.runtime import (
            RuntimeControlMode,
            get_runtime_control_provider,
        )

        provider = get_runtime_control_provider()
        assert provider.get_control_mode() == RuntimeControlMode.NORMAL
        assert provider.is_normal_mode() is True
        assert provider.is_safe_mode() is False
        assert provider.is_maintenance_mode() is False

    def test_get_state_snapshot(self):
        """get_state_snapshot 返回 ControlState 的 dict。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        provider = get_runtime_control_provider()
        snap = provider.get_state_snapshot()
        assert isinstance(snap, dict)
        assert snap.get("runtime_enabled") is True
        assert snap.get("growth_enabled") is True


# ============================================================
# Test: Safe Mode
# ============================================================
class TestSafeMode:
    """Safe Mode 行为测试。"""

    def test_safe_mode_disables_growth_initiative_dream(self):
        """safe_mode 开启时,growth/initiative/dream 自动 disable。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("safe_mode", True, operator="test")

        assert provider.is_safe_mode() is True
        assert provider.is_growth_enabled() is False
        assert provider.is_initiative_enabled() is False
        assert provider.is_dream_enabled() is False
        # 其他模块仍 enabled
        assert provider.is_memory_enabled() is True
        assert provider.is_emotion_enabled() is True
        assert provider.is_runtime_enabled() is True

    def test_safe_mode_get_control_mode(self):
        """safe_mode 开启时,get_control_mode 返回 SAFE。"""
        from src.control.runtime import (
            RuntimeControlMode,
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("safe_mode", True, operator="test")
        assert provider.get_control_mode() == RuntimeControlMode.SAFE

    def test_safe_mode_should_allow_methods(self):
        """safe_mode 开启时,各行为判定。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("safe_mode", True, operator="test")

        assert provider.should_allow_initiative() is False
        assert provider.should_allow_growth() is False
        assert provider.should_skip_external_action() is True
        assert provider.should_allow_basic_chat() is True
        assert provider.should_allow_health() is True
        assert provider.should_allow_diagnostic() is True
        assert provider.should_allow_readonly() is True

    def test_safe_mode_audit(self):
        """safe_mode 状态变化会记录 audit。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        events: List[Dict[str, Any]] = []

        class _Sink:
            def emit(self, e):
                events.append(dict(e))

        provider = get_runtime_control_provider()
        provider.set_audit_sink(_Sink())
        persistence = get_control_state_persistence()
        persistence.set_field("safe_mode", True, operator="test")
        # check_module 会发出 audit
        result = provider.check_module("growth", cycle_id="test_cycle_1")
        # 至少包含 control_state_checked 事件
        assert any(e.get("event") == "control_state_checked" for e in events)
        # result 应该 skip
        assert result.action == "skip"
        assert result.enabled is False


# ============================================================
# Test: Maintenance Mode
# ============================================================
class TestMaintenanceMode:
    """Maintenance Mode 行为测试。"""

    def test_maintenance_mode_disables_all_except_runtime(self):
        """maintenance_mode 开启时,所有非 runtime 模块 disable。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("maintenance_mode", True, operator="test")

        assert provider.is_maintenance_mode() is True
        assert provider.is_runtime_enabled() is True
        # 几乎所有模块都 disabled
        assert provider.is_memory_enabled() is False
        assert provider.is_emotion_enabled() is False
        assert provider.is_growth_enabled() is False
        assert provider.is_initiative_enabled() is False
        assert provider.is_dream_enabled() is False
        assert provider.is_live2d_enabled() is False

    def test_maintenance_mode_get_control_mode(self):
        """maintenance_mode 开启时,get_control_mode 返回 MAINTENANCE。"""
        from src.control.runtime import (
            RuntimeControlMode,
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("maintenance_mode", True, operator="test")
        assert provider.get_control_mode() == RuntimeControlMode.MAINTENANCE

    def test_maintenance_mode_allows_diagnostic(self):
        """maintenance_mode 允许 health/diagnostic/readonly。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("maintenance_mode", True, operator="test")

        assert provider.should_allow_health() is True
        assert provider.should_allow_diagnostic() is True
        assert provider.should_allow_readonly() is True
        # 外部动作/initiative/growth 不允许
        assert provider.should_allow_initiative() is False
        assert provider.should_allow_growth() is False
        assert provider.should_skip_external_action() is True


# ============================================================
# Test: Mode Priority
# ============================================================
class TestModePriority:
    """模式优先级:SAFE > MAINTENANCE > NORMAL。"""

    def test_safe_overrides_maintenance(self):
        """safe_mode 和 maintenance_mode 同时为 True 时,SAFE 优先。"""
        from src.control.runtime import (
            RuntimeControlMode,
            get_runtime_control_provider,
        )
        from src.control.state.control_state import (
            get_control_state_persistence,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("safe_mode", True, operator="test")
        persistence.set_field("maintenance_mode", True, operator="test")
        assert provider.get_control_mode() == RuntimeControlMode.SAFE
        assert provider.is_safe_mode() is True


# ============================================================
# Test: RuntimeControlContext
# ============================================================
class TestRuntimeControlContext:
    """RuntimeControlContext 行为测试。"""

    def test_default_context_allows_all(self):
        """默认 NullContext:所有模块 allow。"""
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )
        ctx = RuntimeControlContext()
        assert isinstance(ctx.provider, NullControlStateProvider)
        for m in ["memory", "emotion", "growth", "initiative", "dream", "live2d"]:
            assert ctx.allow(m) is True

    def test_context_with_provider(self):
        """注入 provider 后,allow() 正确转发。"""
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        class _MockProvider:
            def __init__(self, allow_set: set):
                self._allow = allow_set

            def is_enabled(self, m: str) -> bool:
                return m in self._allow

        provider = _MockProvider({"memory", "emotion"})
        ctx = RuntimeControlContext(provider=provider)
        assert ctx.allow("memory") is True
        assert ctx.allow("emotion") is True
        assert ctx.allow("growth") is False
        assert ctx.allow("initiative") is False

    def test_context_provider_exception_fails_soft(self):
        """provider 抛错时,allow() 返回 True(fail-soft)。"""
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        class _BadProvider:
            def is_enabled(self, m: str) -> bool:
                raise RuntimeError("boom")

        ctx = RuntimeControlContext(provider=_BadProvider())
        for m in ["memory", "emotion", "growth"]:
            assert ctx.allow(m) is True

    def test_context_batch_methods(self):
        """allow_all / which_allowed / which_blocked 批量方法。"""
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        class _MockProvider:
            def is_enabled(self, m: str) -> bool:
                return m in ("memory",)

        ctx = RuntimeControlContext(provider=_MockProvider())
        all_d = ctx.allow_all(["memory", "growth", "emotion"])
        assert all_d == {"memory": True, "growth": False, "emotion": False}
        assert ctx.which_allowed(["memory", "growth", "emotion"]) == ["memory"]
        assert set(ctx.which_blocked(["memory", "growth", "emotion"])) == {
            "growth", "emotion",
        }

    def test_context_set_provider(self):
        """运行时切换 provider。"""
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )

        class _MockProvider:
            def is_enabled(self, m: str) -> bool:
                return m == "memory"

        ctx = RuntimeControlContext()  # 默认 NullProvider
        assert ctx.allow("memory") is True
        assert ctx.allow("growth") is True
        # 切换
        ctx.set_provider(_MockProvider())
        assert ctx.allow("memory") is True
        assert ctx.allow("growth") is False
        # 切回 None → NullProvider
        ctx.set_provider(None)
        assert isinstance(ctx.provider, NullControlStateProvider)
        assert ctx.allow("growth") is True


# ============================================================
# Test: RuntimeCore 集成
# ============================================================
class TestRuntimeCoreIntegration:
    """RuntimeCore 与 RuntimeControlContext 的集成。"""

    def test_no_context_default_behavior(self):
        """未注入 context 时,Runtime 行为完全不变。"""
        from src.runtime.runtime import RuntimeCore

        core = RuntimeCore()
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        # 没有 adapter 时,所有阶段正常执行(没有 _control_check)
        check = core.get_control_check(ctx)
        # 未注入 adapter → ctx._control_check 不存在
        assert check is None

    def test_context_attached_control_check_recorded(self):
        """注入 context 后,_control_check 会被记录。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )
        from src.control.runtime import get_runtime_control_provider

        provider = get_runtime_control_provider()
        ctx_obj = RuntimeControlContext(provider=provider)
        core = RuntimeCore()
        core.configure_control_context(ctx_obj)
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        check = core.get_control_check(ctx)
        assert check is not None
        assert "modules" in check
        assert "runtime_mode" in check
        assert check["runtime_mode"] == "normal"

    def test_memory_disabled_skips_memory_stage(self):
        """memory_enabled=false 时,Memory 阶段被跳过。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("memory_enabled", False, operator="test")

        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        # _stage_errors 应该标记 memory_retrieval 为 control_disabled
        errors = core.get_stage_errors()
        assert "memory_retrieval" in errors
        assert errors["memory_retrieval"] == "control_disabled"

    def test_growth_disabled_skips_growth_stage(self):
        """growth_enabled=false 时,Growth 阶段被跳过。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("growth_enabled", False, operator="test")

        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        errors = core.get_stage_errors()
        assert "growth_evaluation" in errors
        assert errors["growth_evaluation"] == "control_disabled"

    def test_emotion_disabled_skips_emotion_stage(self):
        """emotion_enabled=false 时,Emotion 阶段被跳过。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("emotion_enabled", False, operator="test")

        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        errors = core.get_stage_errors()
        assert "emotion_update" in errors
        assert errors["emotion_update"] == "control_disabled"

    def test_safe_mode_blocks_growth(self):
        """safe_mode 开启时,Growth 阶段被跳过。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("safe_mode", True, operator="test")

        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        errors = core.get_stage_errors()
        assert "growth_evaluation" in errors

    def test_maintenance_mode_blocks_most_stages(self):
        """maintenance_mode 开启时,Memory/Emotion/Growth 都被跳过。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        persistence = get_control_state_persistence()
        persistence.set_field("maintenance_mode", True, operator="test")

        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        errors = core.get_stage_errors()
        # Memory / Emotion / Growth 都被 maintenance_mode 阻断
        assert "memory_retrieval" in errors
        assert "emotion_update" in errors
        assert "growth_evaluation" in errors

    def test_configure_control_adapter_and_context_synced(self):
        """configure_control_adapter() 和 configure_control_context() 同步。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )

        core = RuntimeCore()
        # 默认无 control
        assert isinstance(core.control_context.provider, NullControlStateProvider)
        # 通过 adapter 注入
        provider = get_runtime_control_provider()
        core.configure_control_adapter(provider)
        assert core.control_context.provider is provider
        # 通过 context 注入 None → 回到 NullProvider
        core.configure_control_context(None)
        assert isinstance(core.control_context.provider, NullControlStateProvider)
        # 通过 context 注入新 ctx
        new_ctx = RuntimeControlContext(provider=provider)
        core.configure_control_context(new_ctx)
        assert core.control_context is new_ctx

    def test_get_control_state_snapshot(self):
        """get_control_state_snapshot 返回 ControlState 快照。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        core = RuntimeCore()
        # 未注入 → None
        assert core.get_control_state_snapshot() is None

        # 注入 → dict
        provider = get_runtime_control_provider()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        snap = core.get_control_state_snapshot()
        assert isinstance(snap, dict)
        assert snap.get("runtime_enabled") is True

    def test_no_business_module_touched(self):
        """运行 control 后,业务模块仍正常工作(未污染)。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        # 业务模块字段仍存在(说明未污染)
        assert hasattr(ctx, "memory_context")
        assert hasattr(ctx, "emotion_state")
        assert hasattr(ctx, "personality_snapshot")
        assert hasattr(ctx, "growth_proposals")


# ============================================================
# Test: EventBus 集成
# ============================================================
class TestEventBusIntegration:
    """EventBus RuntimeControlChanged / RuntimeControlSkipped 事件测试。"""

    def test_control_changed_event_emitted(self):
        """状态变化时,EventBus 发出 RuntimeControlChangedEvent。"""
        from src.events.bus import get_event_bus, subscribe_event
        from src.events.events import (
            RuntimeControlChangedEvent,
            EventType,
        )
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        received: List[Any] = []

        def _on_change(event):
            received.append(event)

        bus = get_event_bus()
        bus.subscribe(EventType.RUNTIME_CONTROL_CHANGED, _on_change)

        try:
            provider = get_runtime_control_provider()
            core = RuntimeCore()
            core.configure_control_context(RuntimeControlContext(provider=provider))
            core.start()
            # 第一次 process: 记录初始 snapshot
            core.process(_make_simple_event())
            # 修改 ControlState
            persistence = get_control_state_persistence()
            persistence.set_field("growth_enabled", False, operator="test")
            # 第二次 process: 应该发出 RuntimeControlChanged
            core.process(_make_simple_event())
            # 验证
            assert len(received) >= 1
            last = received[-1]
            assert isinstance(last, RuntimeControlChangedEvent)
            assert last.module == "growth"
            assert last.new_value is False
        finally:
            try:
                bus.unsubscribe(EventType.RUNTIME_CONTROL_CHANGED, _on_change)
            except Exception:
                pass

    def test_control_skipped_event_emitted(self):
        """阶段被跳过时,EventBus 发出 RuntimeControlSkippedEvent。"""
        from src.events.bus import get_event_bus, subscribe_event
        from src.events.events import (
            RuntimeControlSkippedEvent,
            EventType,
        )
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        received: List[Any] = []

        def _on_skip(event):
            received.append(event)

        bus = get_event_bus()
        bus.subscribe(EventType.RUNTIME_CONTROL_SKIPPED, _on_skip)

        try:
            provider = get_runtime_control_provider()
            persistence = get_control_state_persistence()
            persistence.set_field("growth_enabled", False, operator="test")

            core = RuntimeCore()
            core.configure_control_context(RuntimeControlContext(provider=provider))
            core.start()
            core.process(_make_simple_event())
            # 验证
            assert len(received) >= 1
            last = received[-1]
            assert isinstance(last, RuntimeControlSkippedEvent)
            assert last.module == "growth"
        finally:
            try:
                bus.unsubscribe(EventType.RUNTIME_CONTROL_SKIPPED, _on_skip)
            except Exception:
                pass

    def test_no_event_emitted_when_state_unchanged(self):
        """状态未变化时,不会发出 RuntimeControlChangedEvent。"""
        from src.events.bus import get_event_bus, subscribe_event
        from src.events.events import EventType
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        received: List[Any] = []

        def _on_change(event):
            received.append(event)

        bus = get_event_bus()
        bus.subscribe(EventType.RUNTIME_CONTROL_CHANGED, _on_change)

        try:
            provider = get_runtime_control_provider()
            core = RuntimeCore()
            core.configure_control_context(RuntimeControlContext(provider=provider))
            core.start()
            # 连续两次 process,状态未变
            core.process(_make_simple_event())
            core.process(_make_simple_event())
            # 第一次 process 时会发出"初始"事件(因为 last_snapshot 为空)
            # 但第二次之后,无变化
            first_event_count = len(received)
            core.process(_make_simple_event())
            assert len(received) == first_event_count  # 后续无变化
        finally:
            try:
                bus.unsubscribe(EventType.RUNTIME_CONTROL_CHANGED, _on_change)
            except Exception:
                pass


# ============================================================
# Test: Audit 增强
# ============================================================
class TestAuditEnhancement:
    """Audit 集成测试。"""

    def test_control_state_checked_event_emitted(self):
        """check_module 发出 control_state_checked 事件。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        events: List[Dict[str, Any]] = []

        class _Sink:
            def emit(self, e):
                events.append(dict(e))

        provider = get_runtime_control_provider()
        provider.set_audit_sink(_Sink())
        provider.check_module("memory", cycle_id="c_1")
        checked_events = [e for e in events if e.get("event") == "control_state_checked"]
        assert len(checked_events) >= 1
        evt = checked_events[-1]
        assert evt["module"] == "memory"
        assert evt["cycle_id"] == "c_1"
        assert evt["runtime_mode"] in ("normal", "safe", "maintenance")

    def test_runtime_behavior_changed_event(self):
        """record_behavior_change 发出 runtime_behavior_changed 事件。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        events: List[Dict[str, Any]] = []

        class _Sink:
            def emit(self, e):
                events.append(dict(e))

        provider = get_runtime_control_provider()
        provider.set_audit_sink(_Sink())
        provider.record_behavior_change(
            "memory",
            "skip",
            cycle_id="c_2",
            details={"stage": "memory_retrieval", "reason": "memory_disabled"},
        )
        behavior_events = [e for e in events if e.get("event") == "runtime_behavior_changed"]
        assert len(behavior_events) == 1
        assert behavior_events[0]["module"] == "memory"
        assert behavior_events[0]["action"] == "skip"
        assert behavior_events[0]["cycle_id"] == "c_2"
        assert behavior_events[0]["details"]["stage"] == "memory_retrieval"

    def test_runtime_control_changed_event_audit(self):
        """record_control_change 发出 runtime_control_changed 审计事件。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        events: List[Dict[str, Any]] = []

        class _Sink:
            def emit(self, e):
                events.append(dict(e))

        provider = get_runtime_control_provider()
        provider.set_audit_sink(_Sink())
        provider.record_control_change(
            "growth",
            old_value=True,
            new_value=False,
            source="test",
        )
        control_events = [e for e in events if e.get("event") == "runtime_control_changed"]
        assert len(control_events) == 1
        assert control_events[0]["module"] == "growth"
        assert control_events[0]["old_value"] is True
        assert control_events[0]["new_value"] is False
        assert control_events[0]["source"] == "test"

    def test_audit_sink_isolated(self):
        """audit sink 异常被隔离,不影响主流程。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        class _BadSink:
            def emit(self, e):
                raise RuntimeError("audit failed")

        provider = get_runtime_control_provider()
        provider.set_audit_sink(_BadSink())
        # 不应抛错
        provider.check_module("memory", cycle_id="c_3")
        provider.record_behavior_change("growth", "skip", cycle_id="c_4")
        provider.record_control_change("memory", True, False, source="test")


# ============================================================
# Test: 默认行为不改变(回归保护)
# ============================================================
class TestDefaultBehaviorPreserved:
    """保证不引入控制逻辑后,默认 Runtime 行为不变。"""

    def test_runtime_lifecycle_runs_normally_without_context(self):
        """未注入 context 时,完整的 lifecycle 正常运行。"""
        from src.runtime.runtime import RuntimeCore

        core = RuntimeCore()
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        # _final_reply 应该被设置(即使为空)
        assert hasattr(ctx, "_final_reply")

    def test_multiple_processes_without_context(self):
        """连续多次 process,无 context 时表现一致。"""
        from src.runtime.runtime import RuntimeCore

        core = RuntimeCore()
        core.start()
        for i in range(5):
            ev = _make_simple_event({"text": f"msg {i}"})
            ctx = core.process(ev)
            assert ctx is not None
        # 5 次 process 都没有 _control_check
        assert core.get_control_check(ctx) is None

    def test_context_property_and_configure(self):
        """context property + configure 正常工作。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )
        from src.control.runtime import get_runtime_control_provider

        core = RuntimeCore()
        # 默认 NullProvider
        assert core.control_context is not None
        provider = get_runtime_control_provider()
        ctx = RuntimeControlContext(provider=provider)
        core.configure_control_context(ctx)
        assert core.control_context is ctx
        # 切回 None
        core.configure_control_context(None)
        assert core.control_context is not None
        assert core.control_context.provider is not provider

    def test_is_module_allowed_no_context_returns_true(self):
        """_is_module_allowed 在 NullContext 时返回 True(完全向后兼容)。"""
        from src.runtime.runtime import RuntimeCore

        core = RuntimeCore()
        for m in ["memory", "emotion", "growth", "initiative", "dream", "live2d"]:
            assert core._is_module_allowed(m) is True

    def test_is_module_allowed_provider_exception_returns_true(self):
        """_is_module_allowed 在 provider 抛错时返回 True(fail-soft)。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        class _BadProvider:
            def is_enabled(self, m):
                raise RuntimeError("boom")

        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=_BadProvider()))
        # fail-soft:抛错时仍返回 True(不阻塞 Runtime)
        for m in ["memory", "emotion", "growth"]:
            assert core._is_module_allowed(m) is True


# ============================================================
# Test: 端到端控制流
# ============================================================
class TestEndToEndControlFlow:
    """完整控制流端到端测试。"""

    def test_full_control_flow_enable_disable_module(self):
        """完整控制流:enable → disable → 验证阶段跳过。"""
        from src.runtime.runtime import RuntimeCore
        from src.control.runtime import get_runtime_control_provider
        from src.control.manager.control_manager import (
            get_control_manager,
        )
        from src.runtime.context.control_context import (
            RuntimeControlContext,
        )

        provider = get_runtime_control_provider()
        manager = get_control_manager()
        core = RuntimeCore()
        core.configure_control_context(RuntimeControlContext(provider=provider))
        core.start()
        ev = _make_simple_event()

        # 初始:growth enabled → growth 阶段不报错
        ctx1 = core.process(ev)
        assert "growth_evaluation" not in core.get_stage_errors()

        # 关闭 growth via manager
        result = manager.disable_module("growth", operator="test")
        assert result.success is True

        # 再 process:growth 阶段应被跳过
        ctx2 = core.process(ev)
        errors = core.get_stage_errors()
        assert errors.get("growth_evaluation") == "control_disabled"

    def test_control_state_checked_includes_cycle_id(self):
        """control_state_checked 事件包含 cycle_id。"""
        from src.control.runtime import (
            get_runtime_control_provider,
        )

        events: List[Dict[str, Any]] = []

        class _Sink:
            def emit(self, e):
                events.append(dict(e))

        provider = get_runtime_control_provider()
        provider.set_audit_sink(_Sink())
        provider.check_module("memory", cycle_id="c_test_e2e_1")

        # 应有 cycle_id 事件
        for e in events:
            if e.get("event") == "control_state_checked":
                if e.get("cycle_id") == "c_test_e2e_1":
                    return
        pytest.fail("no control_state_checked event with cycle_id found")
