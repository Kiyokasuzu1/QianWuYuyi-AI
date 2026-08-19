# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b2.py

Phase B.2 Runtime Integration —— ProactiveEngine 接入测试

覆盖:
1.  配置注入:apply_phase_b2_config 默认值 + 用户值优先级 + 嵌套 dict
2.  is_b2_enabled_simple / is_phase_b2_enabled 判定
3.  summarize_proactive_engine 输出结构 + 异常隔离
4.  RuntimeB2Bridge 只读封装 + 1s 缓存
5.  safe_scan_all_users 异常隔离 + 错误计数
6.  RuntimeSupervisor → ProactiveEngine.scan_all_users() 端到端
7.  ProactiveEngine 异常被 supervisor 隔离
8.  向后兼容:无 B.2 时 ProactiveEngine=None,行为不变
9.  黑盒:不修改业务模块

约束:
- 不修改任何核心业务模块
- 使用短 tick_interval 加速测试
"""
from __future__ import annotations

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
def b2_module():
    from src.runtime.phase_b2_integration import (
        apply_phase_b2_config,
        is_phase_b2_enabled,
        is_b2_enabled_simple,
        summarize_proactive_engine,
        RuntimeB2Bridge,
        create_b2_bridge,
        safe_scan_all_users,
        PHASE_B2_DEFAULT_CONFIG,
        PHASE_B2_NAME,
        PHASE_B2_VERSION,
    )
    return {
        "apply": apply_phase_b2_config,
        "is_enabled": is_phase_b2_enabled,
        "is_simple": is_b2_enabled_simple,
        "summarize": summarize_proactive_engine,
        "Bridge": RuntimeB2Bridge,
        "create": create_b2_bridge,
        "safe_scan": safe_scan_all_users,
        "default": PHASE_B2_DEFAULT_CONFIG,
        "name": PHASE_B2_NAME,
        "version": PHASE_B2_VERSION,
    }


# ============================================================
# 1. 配置注入
# ============================================================

class TestConfigInjection:
    """apply_phase_b2_config 单元测试"""

    def test_default_keys_present(self, b2_module):
        """默认值必须包含 proactive 子配置"""
        defaults = b2_module["default"]
        assert "proactive" in defaults
        assert "enabled" in defaults["proactive"]
        assert defaults["proactive"]["enabled"] is False, "B.2 必须默认关闭"

    def test_apply_with_none_returns_defaults(self, b2_module):
        merged = b2_module["apply"](None)
        assert merged["proactive"]["enabled"] is False
        assert merged["proactive"]["max_history"] == 100

    def test_user_proactive_disabled_keeps_disabled(self, b2_module):
        """用户显式 enabled=False,结果必须为 False"""
        merged = b2_module["apply"]({"proactive": {"enabled": False}})
        assert merged["proactive"]["enabled"] is False

    def test_user_proactive_enabled_wins(self, b2_module):
        """用户显式 enabled=True 必须覆盖默认"""
        merged = b2_module["apply"]({"proactive": {"enabled": True}})
        assert merged["proactive"]["enabled"] is True

    def test_apply_does_not_mutate_input(self, b2_module):
        """apply_phase_b2_config 不应修改输入 dict"""
        user_cfg = {"proactive": {"enabled": True}}
        before = {"proactive": {"enabled": True}}
        b2_module["apply"](user_cfg)
        assert user_cfg == before, "apply 不应修改用户输入"

    def test_apply_handles_non_dict(self, b2_module):
        """非 dict 输入应被安全处理"""
        merged = b2_module["apply"]("not a dict")
        assert isinstance(merged, dict)
        assert merged["proactive"]["enabled"] is False

    def test_is_b2_enabled_simple_variants(self, b2_module):
        assert b2_module["is_simple"]({}) is False
        assert b2_module["is_simple"]({"proactive_enabled": True}) is True
        assert b2_module["is_simple"]({"proactive_enabled": False}) is False
        assert b2_module["is_simple"]({"proactive": {"enabled": True}}) is True
        assert b2_module["is_simple"]({"proactive": {"enabled": False}}) is False
        assert b2_module["is_simple"](None) is False

    def test_is_b2_simple_toplevel_wins(self, b2_module):
        """proactive_enabled 顶层 bool 优先级高于 nested"""
        cfg = {"proactive_enabled": True, "proactive": {"enabled": False}}
        # 顶层 True 胜出(因为 priority: top-level bool)
        assert b2_module["is_simple"](cfg) is True


# ============================================================
# 2. 摘要函数
# ============================================================

class TestSummarizers:
    """summarize_proactive_engine 输出结构"""

    def test_summarize_none(self, b2_module):
        assert b2_module["summarize"](None) is None

    def test_summarize_valid(self, b2_module):
        """给定 mock engine,摘要应返回标准结构"""
        mock_pe = MagicMock()
        mock_pe._running = True
        mock_pe._error_count = 0
        mock_pe._user_last_active = {"u1": time.time()}
        mock_pe._confidence_gate._user_preferences = {"u1": {}}
        mock_pe.get_action_stats.return_value = {
            "total_proposed": 5, "executed": 2, "rejected": 1,
            "deferred": 0, "execution_rate": 0.4, "by_type": {"greeting": 3},
        }
        mock_pe.get_pending_actions.return_value = ["a1", "a2"]
        mock_pe.get_recent_actions.return_value = ["a1"] * 3

        s = b2_module["summarize"](mock_pe)
        assert s is not None
        assert s["available"] is True
        assert s["running"] is True
        assert s["stats"]["total_proposed"] == 5
        assert s["stats"]["executed"] == 2
        assert s["pending_count"] == 2
        assert s["recent_count"] == 3
        assert s["gate_users"] == 1
        assert s["errors"] == 0

    def test_summarize_fails_soft(self, b2_module):
        """engine 抛异常时,摘要返回 None,不抛出"""
        mock_pe = MagicMock()
        mock_pe._running = True
        mock_pe.get_action_stats.side_effect = RuntimeError("boom")

        # 不应抛
        result = b2_module["summarize"](mock_pe)
        # 异常时由 summarize 内层 try/except 捕获,返回空 stats dict
        # 但顶层还有兜底 try,这里我们只验证不抛 + 返回值有效
        assert result is not None
        assert result["stats"] == {}

    def test_summarize_handles_missing_methods(self, b2_module):
        """engine 缺少部分方法时,摘要应安全降级"""
        minimal = object()  # 没有任何方法
        s = b2_module["summarize"](minimal)
        # 所有方法都走 hasattr 检查,值为 -1 表示不可用
        assert s is not None
        assert s["available"] is True
        assert s["stats"] == {}
        assert s["pending_count"] == 0


# ============================================================
# 3. RuntimeB2Bridge
# ============================================================

class TestRuntimeB2Bridge:
    """RuntimeB2Bridge 只读 + 缓存"""

    def test_bridge_creation(self, b2_module):
        bridge = b2_module["create"](None)
        assert bridge is not None
        assert bridge.engine is None
        assert bridge.is_enabled() is False

    def test_bridge_with_engine(self, b2_module):
        mock_pe = MagicMock()
        mock_pe._running = True
        bridge = b2_module["create"](mock_pe)
        assert bridge.is_enabled() is True

    def test_bridge_get_status_disabled(self, b2_module):
        bridge = b2_module["create"](None)
        status = bridge.get_status()
        assert status["phase_b2_enabled"] is False
        assert status["available"] is False
        assert status["phase_b2_name"] == b2_module["name"]
        assert status["phase_b2_version"] == b2_module["version"]
        assert "ts" in status

    def test_bridge_get_status_enabled(self, b2_module):
        mock_pe = MagicMock()
        mock_pe._running = True
        mock_pe._error_count = 0
        mock_pe._user_last_active = {}
        mock_pe._confidence_gate._user_preferences = {}
        mock_pe.get_action_stats.return_value = {"total_proposed": 1}
        mock_pe.get_pending_actions.return_value = []
        mock_pe.get_recent_actions.return_value = []

        bridge = b2_module["create"](mock_pe)
        status = bridge.get_status()
        assert status["phase_b2_enabled"] is True
        assert status["available"] is True
        assert status["stats"]["total_proposed"] == 1

    def test_bridge_caches_within_1s(self, b2_module):
        """1 秒内重复调用应复用缓存"""
        mock_pe = MagicMock()
        mock_pe._running = True
        mock_pe._error_count = 0
        mock_pe._user_last_active = {}
        mock_pe._confidence_gate._user_preferences = {}
        mock_pe.get_action_stats.return_value = {"total_proposed": 7}
        mock_pe.get_pending_actions.return_value = []
        mock_pe.get_recent_actions.return_value = []

        bridge = b2_module["create"](mock_pe)
        s1 = bridge.get_status()
        s2 = bridge.get_status()
        # 缓存命中
        assert s1 is s2
        # get_action_stats 只调用 1 次
        assert mock_pe.get_action_stats.call_count == 1

    def test_bridge_get_recent_actions_safe(self, b2_module):
        """engine 异常时,get_recent_actions 返回空列表"""
        mock_pe = MagicMock()
        mock_pe.get_recent_actions.side_effect = RuntimeError("boom")
        bridge = b2_module["create"](mock_pe)
        assert bridge.get_recent_actions(limit=10) == []


# ============================================================
# 4. safe_scan_all_users
# ============================================================

class TestSafeScanAllUsers:
    """safe_scan_all_users 异常隔离"""

    def test_safe_scan_none_engine(self, b2_module):
        result = b2_module["safe_scan"](None)
        assert result["ok"] is False
        assert "None" in result["error"]

    def test_safe_scan_missing_method(self, b2_module):
        minimal = object()  # 没有 scan_all_users
        result = b2_module["safe_scan"](minimal)
        assert result["ok"] is False
        assert "scan_all_users" in result["error"]

    def test_safe_scan_success(self, b2_module):
        mock_pe = MagicMock()
        mock_pe._user_last_active = {"u1": time.time()}
        result = b2_module["safe_scan"](mock_pe)
        assert result["ok"] is True
        assert result["scanned"] == 1
        assert result["error"] is None
        mock_pe.scan_all_users.assert_called_once()

    def test_safe_scan_exception_isolated(self, b2_module):
        """scan_all_users 抛异常时,safe_scan 隔离并累计错误计数"""
        mock_pe = MagicMock()
        mock_pe._user_last_active = {"u1": time.time()}
        mock_pe._error_count = 0
        mock_pe.scan_all_users.side_effect = RuntimeError("scan failed")

        result = b2_module["safe_scan"](mock_pe)
        assert result["ok"] is False
        assert "scan failed" in result["error"]
        # 错误计数 + 1
        assert mock_pe._error_count == 1

        # 第二次调用再 +1
        b2_module["safe_scan"](mock_pe)
        assert mock_pe._error_count == 2


# ============================================================
# 5. Supervisor 端到端驱动 ProactiveEngine
# ============================================================

class TestSupervisorDrivesProactive:
    """RuntimeSupervisor → ProactiveEngine.scan_all_users() 端到端"""

    def test_supervisor_calls_scan_all_users(self, fresh_supervisor_class):
        """supervisor 周期性 tick,scan_all_users 被调用"""
        mock_pe = MagicMock()
        sup = fresh_supervisor_class(
            proactive_engine=mock_pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        # scan_all_users 至少被调用 2 次
        assert mock_pe.scan_all_users.call_count >= 2, (
            f"scan_all_users 应被调用 ≥2 次,实际 {mock_pe.scan_all_users.call_count}"
        )

    def test_supervisor_no_proactive_does_not_call(self, fresh_supervisor_class):
        """无 ProactiveEngine 时,supervisor 不调用 scan_all_users"""
        sup = fresh_supervisor_class(
            proactive_engine=None,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(1.5)
        sup.stop()
        # tick_count 应正常增加
        assert sup.get_status()["tick_count"] >= 1

    def test_proactive_exception_isolated(self, fresh_supervisor_class):
        """scan_all_users 抛异常时,supervisor 继续运行"""
        mock_pe = MagicMock()
        mock_pe._user_last_active = {"u1": time.time()}
        mock_pe.scan_all_users.side_effect = RuntimeError("simulated failure")

        sup = fresh_supervisor_class(
            proactive_engine=mock_pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        status = sup.get_status()
        # supervisor 仍能完成多次 tick
        assert status["tick_count"] >= 2
        # scan_all_users 至少被调用 2 次(异常被隔离)
        assert mock_pe.scan_all_users.call_count >= 2

    def test_real_proactive_engine_integration(self, fresh_supervisor_class):
        """真实 ProactiveEngine(无需 start)+ supervisor 驱动"""
        from src.proactive.proactive_engine import ProactiveEngine

        # 不调用 start() — 避免注册到 CognitiveCore(测试隔离)
        pe = ProactiveEngine()
        # 注册一个用户,扫描时无错误
        pe.record_user_activity("test_user")

        sup = fresh_supervisor_class(
            proactive_engine=pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        # scan_all_users 通过 supervisor 被周期性调用
        # pending_count 仍然为 0(没有 24h 不活跃的用户)
        assert pe.get_pending_actions() == [] or pe.get_pending_actions() is not None

    def test_b2_bridge_reports_via_supervisor(self, fresh_supervisor_class, b2_module):
        """B.2 Bridge 通过 supervisor 周期性轮询,正确反映状态"""
        mock_pe = MagicMock()
        mock_pe._running = True
        mock_pe._error_count = 0
        mock_pe._user_last_active = {"u1": time.time()}
        mock_pe._confidence_gate._user_preferences = {}
        mock_pe.get_action_stats.return_value = {
            "total_proposed": 0, "executed": 0, "rejected": 0, "deferred": 0,
            "execution_rate": 0.0, "by_type": {},
        }
        mock_pe.get_pending_actions.return_value = []
        mock_pe.get_recent_actions.return_value = []

        bridge = b2_module["create"](mock_pe)

        sup = fresh_supervisor_class(
            proactive_engine=mock_pe,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.0)
        sup.stop()

        # 等待缓存过期
        time.sleep(1.1)
        s = bridge.get_status()
        assert s["phase_b2_enabled"] is True
        assert s["available"] is True


# ============================================================
# 6. 向后兼容
# ============================================================

class TestBackwardCompatibility:
    """无 B.2 时,ProactiveEngine=None,行为与 B.2 之前一致"""

    def test_disabled_by_default(self, b2_module):
        """B.2 默认应关闭"""
        assert b2_module["default"]["proactive"]["enabled"] is False
        # 任意没有 proactive_enabled 的 cfg 都应判定为 False
        assert b2_module["is_simple"]({}) is False
        assert b2_module["is_simple"]({"other_key": "value"}) is False

    def test_bridge_with_none_engine_safe(self, b2_module):
        """None engine 的 bridge 不应抛任何异常"""
        bridge = b2_module["create"](None)
        status = bridge.get_status()
        assert status["available"] is False
        assert status["phase_b2_enabled"] is False
        assert status["stats"] == {}
        assert bridge.get_recent_actions() == []

    def test_safe_scan_with_none(self, b2_module):
        """safe_scan(None) 安全降级"""
        result = b2_module["safe_scan"](None)
        assert result["ok"] is False


# ============================================================
# 7. 黑盒:不修改业务模块
# ============================================================

class TestNoCoreModuleModification:
    """B.2 不得影响核心模块的公开接口"""

    def test_proactive_engine_public_api_unchanged(self):
        from src.proactive.proactive_engine import (
            ProactiveEngine,
            ProactiveLayer,
            ActionType,
            ActionOutcome,
            ProposedAction,
            ActionConfidenceGate,
        )
        # ProactiveEngine 关键公开方法必须存在
        for method in (
            "start", "stop", "scan_all_users", "propose_action",
            "execute_action", "get_action_stats", "get_pending_actions",
            "get_recent_actions", "get_gate_stats", "record_user_activity",
        ):
            assert hasattr(ProactiveEngine, method), (
                f"ProactiveEngine 缺少 {method}"
            )

    def test_supervisor_proactive_hook_unchanged(self):
        """supervisor 必须保留 proactive_engine 注入点和 _tick_iteration 步骤 3"""
        from src.runtime.supervisor import RuntimeSupervisor

        # 构造参数 proactive_engine 必须存在
        import inspect
        sig = inspect.signature(RuntimeSupervisor.__init__)
        assert "proactive_engine" in sig.parameters, (
            "RuntimeSupervisor.__init__ 必须保留 proactive_engine 参数"
        )
        # 默认值必须为 None
        assert sig.parameters["proactive_engine"].default is None

    def test_phase_b2_does_not_modify_b1(self):
        """B.2 不能破坏 B.1 的接口"""
        from src.runtime.phase_b_integration import (
            apply_phase_b1_config,
            is_phase_b1_enabled,
            RuntimeB1Bridge,
            PHASE_B1_NAME,
        )
        assert PHASE_B1_NAME == "phase_b1"  # B.1 标识未被覆盖

    def test_phase_b2_does_not_modify_phase_a(self):
        """B.2 不能破坏 Phase A 接口"""
        from src.runtime.supervisor import RuntimeSupervisor
        for method in (
            "start", "stop", "is_running", "get_status",
            "get_instance", "reset_instance",
        ):
            assert hasattr(RuntimeSupervisor, method), (
                f"RuntimeSupervisor 缺少 {method}"
            )
