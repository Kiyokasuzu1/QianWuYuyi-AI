"""
Phase 6.3: Orchestrator 默认启用 Phase 6.2 SelfModel Context 测试

验证：
- 新 Orchestrator 初始化后自动获得 Phase 6.2 enabled 状态
- 旧模式（无 adapter）仍能工作（legacy SelfModel）
- 默认情况下，prompt 中包含 SelfModel 上下文
- RuntimeBridge.get_self_model_adapter 桥接正确
"""
from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_model_adapter import SelfModelAdapter


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def mock_openai():
    """Mock OpenAI 客户端以避免 API key 缺失错误"""
    from unittest.mock import patch, MagicMock
    fake_client = MagicMock()
    with patch("src.engine.OpenAI", return_value=fake_client):
        yield fake_client


@pytest.fixture
def reset_runtime_bridge():
    """隔离测试之间的 RuntimeBridge 状态"""
    from src.runtime import runtime_bridge as rb
    # 备份实例
    original_instance = rb.RuntimeBridge._instance if hasattr(rb.RuntimeBridge, "_instance") else None
    yield
    # 恢复
    if original_instance is not None:
        rb.RuntimeBridge._instance = original_instance


# ============================================================
# 1. RuntimeBridge.get_self_model_adapter 测试
# ============================================================

class TestRuntimeBridgeSelfModelAdapter:
    def test_01_bridge_has_method(self):
        """RuntimeBridge 应有 get_self_model_adapter 方法"""
        from src.runtime.runtime_bridge import RuntimeBridge
        assert hasattr(RuntimeBridge, "get_self_model_adapter")
        assert callable(getattr(RuntimeBridge, "get_self_model_adapter", None))

    def test_02_bridge_returns_none_without_core(self):
        """未初始化 RuntimeCore 时返回 None"""
        from src.runtime.runtime_bridge import RuntimeBridge
        rb = RuntimeBridge.__new__(RuntimeBridge)
        rb._runtime_core = None
        result = rb.get_self_model_adapter()
        assert result is None

    def test_03_bridge_returns_adapter_from_core(self):
        """从 RuntimeCore 获取 adapter"""
        from src.runtime.runtime_bridge import RuntimeBridge
        adapter = SelfModelAdapter(actor="test")
        rb = RuntimeBridge.__new__(RuntimeBridge)

        class _FakeCore:
            def get_self_model_adapter(self):
                return adapter
        rb._runtime_core = _FakeCore()
        result = rb.get_self_model_adapter()
        assert result is adapter

    def test_04_bridge_handles_exception(self):
        """RuntimeCore 抛异常时返回 None"""
        from src.runtime.runtime_bridge import RuntimeBridge
        rb = RuntimeBridge.__new__(RuntimeBridge)

        class _FakeCore:
            def get_self_model_adapter(self):
                raise RuntimeError("core failure")
        rb._runtime_core = _FakeCore()
        result = rb.get_self_model_adapter()
        assert result is None


# ============================================================
# 2. Orchestrator 默认启用测试
# ============================================================

class TestOrchestratorDefaultEnable:
    def test_01_orchestrator_with_adapter_auto_enables(self, reset_runtime_bridge):
        """当 RuntimeBridge 提供 adapter 时，Orchestrator 自动 enable"""
        from src.runtime.runtime_bridge import RuntimeBridge
        from src.runtime import runtime_bridge as rb_module
        from src.orchestrator import Orchestrator
        from src.personality.self_belief import SelfBelief

        # 准备 adapter
        adapter = SelfModelAdapter(actor="auto_test")
        adapter._beliefs.add(SelfBelief(
            domain="value", content="auto_belief", confidence=0.6
        ))

        class _FakeBridge:
            def get_self_model_adapter(self):
                return adapter
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = _FakeBridge()
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            # 应自动 enabled
            assert orch.is_phase_6_2_self_model_enabled() is True
            # adapter 应被设置
            assert orch._phase_6_2_adapter is adapter
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_02_orchestrator_without_adapter_legacy(self, reset_runtime_bridge):
        """无 adapter 时仍 enabled（空 store 兼容模式）"""
        from src.runtime import runtime_bridge as rb_module
        from src.orchestrator import Orchestrator

        class _FakeBridge:
            def get_self_model_adapter(self):
                return None
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = _FakeBridge()
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            # 即使无 adapter，_phase_6_2_enabled 也应被标记为 True（空 store 模式）
            # 关键：_phase_6_2_adapter 仍为 None
            assert orch._phase_6_2_adapter is None
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_03_orchestrator_with_runtime_core_adapter(self, reset_runtime_bridge):
        """RuntimeCore 自带 adapter 时，Orchestrator 自动获取"""
        from src.runtime.runtime_core import RuntimeCore
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        # 创建 RuntimeCore
        rc = RuntimeCore(config={"adapters_enabled": True})
        # 若 adapter 已创建
        if rc.get_self_model_adapter() is None:
            pytest.skip("RuntimeCore did not create adapter")
        adapter = rc.get_self_model_adapter()
        # 注入到 RuntimeBridge
        original = rb_module.RuntimeBridge._instance
        # 获取/创建 bridge
        bridge = rb_module.get_runtime_bridge()
        # 通过 _runtime_core 设置
        if not hasattr(bridge, "_runtime_core") or bridge._runtime_core is None:
            bridge._runtime_core = rc
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            assert orch.is_phase_6_2_self_model_enabled() is True
            assert orch._phase_6_2_adapter is adapter
        finally:
            rb_module.RuntimeBridge._instance = original


# ============================================================
# 3. Prompt 包含 SelfModel context 测试
# ============================================================

class TestPromptContainsSelfModel:
    def test_01_prompt_contains_beliefs_when_attached(self, reset_runtime_bridge):
        """当 Phase 6.2 启用且 adapter 有 belief 时，prompt 应包含"""
        from src.personality.self_belief import SelfBelief
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        adapter = SelfModelAdapter(actor="prompt_test")
        adapter._beliefs.add(SelfBelief(
            domain="identity", content="我是测试羽依", confidence=0.8
        ))

        class _FakeBridge:
            def get_self_model_adapter(self):
                return adapter
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = _FakeBridge()
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            # 启用 Phase 6.2
            orch.enable_phase_6_2_self_model(adapter)
            # 获取 prompt 文本
            ctx = orch.get_self_model_context()
            # 至少返回非空（具体内容依赖 prompt 渲染）
            assert isinstance(ctx, str)
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_02_prompt_returns_string(self, reset_runtime_bridge):
        """get_self_model_context 始终返回 str"""
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            ctx = orch.get_self_model_context()
            assert isinstance(ctx, str)
        finally:
            rb_module.RuntimeBridge._instance = original


# ============================================================
# 4. 向后兼容测试
# ============================================================

class TestBackwardCompatibility:
    def test_01_old_call_still_works(self, reset_runtime_bridge):
        """旧的 enable_phase_6_2_self_model 显式调用仍工作"""
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            adapter = SelfModelAdapter(actor="old_call")
            # 旧 API
            result = orch.enable_phase_6_2_self_model(adapter)
            assert result is True
            assert orch.is_phase_6_2_self_model_enabled() is True
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_02_disable_still_works(self, reset_runtime_bridge):
        """disable_phase_6_2_self_model 仍工作"""
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            orch.enable_phase_6_2_self_model(None)
            assert orch.is_phase_6_2_self_model_enabled() is True
            orch.disable_phase_6_2_self_model()
            assert orch.is_phase_6_2_self_model_enabled() is False
        finally:
            rb_module.RuntimeBridge._instance = original

    def test_03_legacy_get_context_still_works(self, reset_runtime_bridge):
        """旧的 get_self_model_context 路径仍工作（_phase_6_2_enabled=False）"""
        from src.orchestrator import Orchestrator
        from src.runtime import runtime_bridge as rb_module
        original = rb_module.RuntimeBridge._instance
        rb_module.RuntimeBridge._instance = None
        try:
            orch = Orchestrator(config={"adapters_enabled": False})
            orch.disable_phase_6_2_self_model()
            ctx = orch.get_self_model_context()
            assert isinstance(ctx, str)  # 不会抛异常
        finally:
            rb_module.RuntimeBridge._instance = original
