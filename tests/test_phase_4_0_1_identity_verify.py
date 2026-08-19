"""
Phase 4.0.1 — SelfModel Authority 身份共享验证

验证目标：
  验证 RuntimeCore → RuntimeBridge → Orchestrator 链路中
  SelfModelStore 是否已经是同一个对象（is 而非 ==）。

策略：
  先验证现有路径是否天然共享。如果已满足，只加架构声明和测试，
  不做无意义重构。

硬约束：
  - 不合并两个 SelfModelStore 类
  - 不删除 Orchestrator fallback
  - 不提前迁移新 SelfModel 架构
"""
from __future__ import annotations

import os
import tempfile
import uuid

import pytest


def _config(tmp: str) -> dict:
    return {
        "adapters_enabled": True,
        "relationship_enabled": True,
        "experience_enabled": True,
        "event_driven_enabled": True,
        "memory_store_path": os.path.join(tmp, "mem.json"),
        "experience_journal_path": os.path.join(tmp, "exp_journal.jsonl"),
        "growth_proposals_path": os.path.join(tmp, "gp.json"),
        "state_file": os.path.join(tmp, "runtime.json"),
        "self_model_data_dir": os.path.join(tmp, "self_model"),
    }


# ============================================================
# 测试 1: RuntimeCore → RuntimeBridge 身份共享
# ============================================================

class TestRuntimeCoreToBridgeIdentity:
    """验证 RuntimeCore.get_self_model_store() 与 RuntimeBridge.get_self_model_store() 返回同一对象。"""

    def test_runtime_core_and_bridge_share_same_store(self):
        """RuntimeCore 和 RuntimeBridge 应返回同一个 SelfModelStore 实例。"""
        from src.runtime.runtime_bridge import get_runtime_bridge, reset_runtime_bridge

        reset_runtime_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge = get_runtime_bridge(config=_config(tmp))
            bridge.initialize()

            rc = bridge.get_runtime_core()
            assert rc is not None, "RuntimeBridge 启动后应有 RuntimeCore"

            store_from_rc = rc.get_self_model_store()
            store_from_bridge = bridge.get_self_model_store()

            assert store_from_rc is not None, "RuntimeCore 应返回非空 SelfModelStore"
            assert store_from_bridge is not None, "RuntimeBridge 应返回非空 SelfModelStore"
            assert store_from_rc is store_from_bridge, (
                f"RuntimeCore 和 RuntimeBridge 应共享同一 SelfModelStore 实例\n"
                f"  rc id:     {id(store_from_rc)}\n"
                f"  bridge id: {id(store_from_bridge)}"
            )
            bridge.shutdown()


# ============================================================
# 测试 2: Orchestrator → RuntimeBridge 身份共享
# ============================================================

class TestOrchestratorBridgeIdentity:
    """验证 Orchestrator 的 self_model_store 与 RuntimeBridge 共享同一实例。"""

    def test_orchestrator_shares_store_with_bridge(self):
        """Orchestrator 应通过 RuntimeBridge 获取 RuntimeCore 的 SelfModelStore。"""
        from src.runtime.runtime_bridge import get_runtime_bridge, reset_runtime_bridge
        from src.orchestrator import Orchestrator

        reset_runtime_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            # 先启动 RuntimeBridge（创建 RuntimeCore）
            bridge = get_runtime_bridge(config=_config(tmp))
            bridge.initialize()

            # 再创建 Orchestrator（会通过 get_runtime_bridge() 获取共享实例）
            orch = Orchestrator()

            store_from_bridge = bridge.get_self_model_store()
            store_from_orch = orch.self_model_store

            assert store_from_orch is not None, "Orchestrator 应有非空 SelfModelStore"
            assert store_from_bridge is not None, "RuntimeBridge 应有非空 SelfModelStore"
            assert store_from_orch is store_from_bridge, (
                f"Orchestrator 和 RuntimeBridge 应共享同一 SelfModelStore 实例\n"
                f"  orch id:   {id(store_from_orch)}\n"
                f"  bridge id: {id(store_from_bridge)}"
            )
            bridge.shutdown()


# ============================================================
# 测试 3: SelfModelUpdater → Orchestrator 身份共享
# ============================================================

class TestUpdaterStoreIdentity:
    """验证 SelfModelUpdater 的 store 与 Orchestrator 的 self_model_store 是同一实例。"""

    def test_updater_store_is_orchestrator_store(self):
        """SelfModelUpdater.store 应为 Orchestrator.self_model_store 的同一引用。"""
        from src.runtime.runtime_bridge import get_runtime_bridge, reset_runtime_bridge
        from src.orchestrator import Orchestrator

        reset_runtime_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge = get_runtime_bridge(config=_config(tmp))
            bridge.initialize()

            orch = Orchestrator()

            updater = getattr(orch, "_self_model_updater", None)
            if updater is None:
                pytest.skip("Orchestrator._self_model_updater 未初始化（可能 adapters 未启用）")

            assert updater.store is orch.self_model_store, (
                f"SelfModelUpdater.store 应为 Orchestrator.self_model_store 的同一实例\n"
                f"  updater.store id: {id(updater.store)}\n"
                f"  orch.store id:    {id(orch.self_model_store)}"
            )
            bridge.shutdown()


# ============================================================
# 测试 4: 整链 is 验证
# ============================================================

class TestFullChainIdentity:
    """验证完整链路：RuntimeCore → Bridge → Orchestrator → Updater。"""

    def test_full_chain_single_instance(self):
        """整条链路中的所有 SelfModelStore 引用应为同一对象。"""
        from src.runtime.runtime_bridge import get_runtime_bridge, reset_runtime_bridge
        from src.orchestrator import Orchestrator

        reset_runtime_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge = get_runtime_bridge(config=_config(tmp))
            bridge.initialize()

            rc = bridge.get_runtime_core()
            orch = Orchestrator()

            store_rc = rc.get_self_model_store()
            store_bridge = bridge.get_self_model_store()
            store_orch = orch.self_model_store

            assert store_rc is not None
            assert store_bridge is not None
            assert store_orch is not None

            # 核心验证：三个引用应为同一对象
            assert store_rc is store_bridge is store_orch, (
                f"整链 is 验证失败：\n"
                f"  rc id:     {id(store_rc)}\n"
                f"  bridge id: {id(store_bridge)}\n"
                f"  orch id:   {id(store_orch)}"
            )

            # SelfModelUpdater 也使用同一 store
            updater = getattr(orch, "_self_model_updater", None)
            if updater is not None:
                assert updater.store is store_orch, (
                    f"SelfModelUpdater 应使用同一 SelfModelStore\n"
                    f"  updater.store id: {id(updater.store)}\n"
                    f"  orch.store id:    {id(store_orch)}"
                )
            bridge.shutdown()


# ============================================================
# 测试 5: PersonalityResolver 注入后共享
# ============================================================

class TestResolverInjection:
    """验证 Orchestrator 将共享 Store 注入 PersonalityResolver 后，两者引用一致。"""

    def test_resolver_receives_injected_store(self):
        """Orchestrator 注入后，PersonalityResolver.self_model_store 应为共享实例。"""
        from src.runtime.runtime_bridge import get_runtime_bridge, reset_runtime_bridge
        from src.orchestrator import Orchestrator

        reset_runtime_bridge()
        with tempfile.TemporaryDirectory() as tmp:
            bridge = get_runtime_bridge(config=_config(tmp))
            bridge.initialize()

            orch = Orchestrator()

            resolver = orch.personality_resolver
            if not hasattr(resolver, "self_model_store"):
                pytest.skip("PersonalityResolver 无 self_model_store 属性")

            assert resolver.self_model_store is orch.self_model_store, (
                f"PersonalityResolver.self_model_store 应为 Orchestrator 的同一实例\n"
                f"  resolver id: {id(resolver.self_model_store)}\n"
                f"  orch id:     {id(orch.self_model_store)}"
            )
            bridge.shutdown()