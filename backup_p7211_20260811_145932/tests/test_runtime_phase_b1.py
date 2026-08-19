# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b1.py

Phase B.1 Runtime Integration —— AutonomousScheduler 接入测试

覆盖:
1.  配置注入:apply_phase_b1_config 默认值 + 用户值优先级
2.  is_phase_b1_enabled 判定
3.  summarize_scheduler / summarize_runtime_b1 输出结构
4.  RuntimeB1Bridge 只读封装 + 缓存
5.  RuntimeCore + AutonomousScheduler 在 B.1 配置下自动启用
6.  RuntimeCore.tick() 触发 scheduler.on_tick() 链路
7.  通过 RuntimeSupervisor 驱动 scheduler(端到端)
8.  Scheduler 异常不传播(RuntimeCore 内部已隔离)
9.  向后兼容:无 B.1 flag 时 scheduler 为 None
10. Phase B.1 不修改业务模块(黑盒 import + signature 检查)

约束:
- 不修改任何核心业务模块
- 使用短 tick_interval 加速测试
"""
from __future__ import annotations

import os
import sys
import tempfile
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
def temp_workspace():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture
def fresh_supervisor_class():
    """每个测试前重置 RuntimeSupervisor 单例"""
    from src.runtime.supervisor import RuntimeSupervisor
    RuntimeSupervisor.reset_instance()
    yield RuntimeSupervisor
    RuntimeSupervisor.reset_instance()


@pytest.fixture
def b1_module():
    from src.runtime.phase_b_integration import (
        apply_phase_b1_config,
        is_phase_b1_enabled,
        summarize_scheduler,
        summarize_runtime_b1,
        RuntimeB1Bridge,
        create_b1_bridge,
        PHASE_B1_DEFAULT_CONFIG,
        PHASE_B1_NAME,
        PHASE_B1_VERSION,
    )
    return {
        "apply": apply_phase_b1_config,
        "is_enabled": is_phase_b1_enabled,
        "summarize_scheduler": summarize_scheduler,
        "summarize_runtime": summarize_runtime_b1,
        "Bridge": RuntimeB1Bridge,
        "create": create_b1_bridge,
        "default": PHASE_B1_DEFAULT_CONFIG,
        "name": PHASE_B1_NAME,
        "version": PHASE_B1_VERSION,
    }


def _build_runtime_core_config(tmp_dir: str, **overrides: Any) -> Dict[str, Any]:
    """构造 RuntimeCore 测试配置(隔离临时文件)

    重要:autonomous_scheduler 初始化代码位于 RuntimeCore.__init__ 的
    `if adapters_enabled:` 分支内(line 344-660+),因此必须开启
    adapters_enabled 才能触发 scheduler 初始化。
    """
    cfg: Dict[str, Any] = {
        # 必须:adapters_enabled 开启后才会执行 Phase 3.5.24 AutonomousScheduler 初始化
        "adapters_enabled": True,
        # 可选辅助
        "experience_enabled": False,
        "identity_stability_enabled": True,
        # 文件路径隔离
        "memory_store_path": os.path.join(tmp_dir, "mem.json"),
        "growth_proposals_path": os.path.join(tmp_dir, "gp.json"),
        "state_file": os.path.join(tmp_dir, "rt.json"),
    }
    cfg.update(overrides)
    return cfg


# ============================================================
# 1. 配置注入
# ============================================================

class TestConfigInjection:
    """apply_phase_b1_config 单元测试"""

    def test_default_keys_present(self, b1_module):
        """默认值必须包含 AutonomousScheduler 所需 key"""
        defaults = b1_module["default"]
        for key in (
            "adapters_enabled",
            "autonomous_scheduler_enabled",
            "as_memory_interval_ticks",
            "as_identity_interval_ticks",
            "as_growth_eval_interval_ticks",
            "as_reflection_interval_ticks",
            "as_min_memories_for_maintenance",
            "as_proposal_evaluation_batch",
            "as_history_limit",
        ):
            assert key in defaults, f"缺少默认 key: {key}"

    def test_apply_with_none_returns_defaults(self, b1_module):
        merged = b1_module["apply"](None)
        assert merged["autonomous_scheduler_enabled"] is True
        assert merged["as_memory_interval_ticks"] == 10

    def test_user_value_wins(self, b1_module):
        """用户给定的值必须覆盖默认"""
        merged = b1_module["apply"]({
            "as_memory_interval_ticks": 3,
            "as_identity_interval_ticks": 7,
        })
        assert merged["as_memory_interval_ticks"] == 3
        assert merged["as_identity_interval_ticks"] == 7
        # 未给定的仍然使用默认
        assert merged["as_growth_eval_interval_ticks"] == 8

    def test_apply_does_not_mutate_input(self, b1_module):
        """apply_phase_b1_config 不应修改输入 dict"""
        user_cfg = {"as_memory_interval_ticks": 99}
        before = dict(user_cfg)
        b1_module["apply"](user_cfg)
        assert user_cfg == before, "apply 不应修改用户输入"

    def test_is_phase_b1_enabled(self, b1_module):
        assert b1_module["is_enabled"]({"autonomous_scheduler_enabled": True}) is True
        assert b1_module["is_enabled"]({"autonomous_scheduler_enabled": False}) is False
        assert b1_module["is_enabled"](None) is False
        assert b1_module["is_enabled"]({}) is False

    def test_apply_handles_non_dict(self, b1_module):
        """非 dict 输入应被安全处理"""
        merged = b1_module["apply"]("not a dict")
        assert isinstance(merged, dict)
        assert merged["autonomous_scheduler_enabled"] is True


# ============================================================
# 2. 摘要函数
# ============================================================

class TestSummarizers:
    """summarize_* 函数输出结构"""

    def test_summarize_scheduler_none(self, b1_module):
        assert b1_module["summarize_scheduler"](None) is None

    def test_summarize_scheduler_valid(self, b1_module):
        """给定一个 mock scheduler,摘要应返回标准结构"""
        mock_sched = MagicMock()
        mock_sched.get_snapshot.return_value = {
            "total_tasks": 10,
            "executed_tasks": 8,
            "failed_tasks": 1,
        }
        mock_sched.get_history.return_value = [{"task_type": "x"}] * 5
        mock_sched.cfg = MagicMock()
        mock_sched.cfg.__dict__ = {"enabled": True, "history_limit": 200}

        summary = b1_module["summarize_scheduler"](mock_sched)
        assert summary is not None
        assert summary["available"] is True
        assert summary["snapshot"]["total_tasks"] == 10
        assert summary["history_count"] == 5
        assert summary["config"]["enabled"] is True

    def test_summarize_scheduler_fails_soft(self, b1_module):
        """scheduler 抛异常时,摘要返回 None,不抛出"""
        mock_sched = MagicMock()
        mock_sched.get_snapshot.side_effect = RuntimeError("boom")

        # 不应抛
        result = b1_module["summarize_scheduler"](mock_sched)
        # 异常时返回 None(最外层 catch)
        assert result is None

    def test_summarize_runtime_b1_no_core(self, b1_module):
        s = b1_module["summarize_runtime"](None)
        assert s["phase_b1_enabled"] is False
        assert s["scheduler_available"] is False
        assert s["scheduler"] is None
        assert s["tick_count"] == 0

    def test_summarize_runtime_b1_with_mock(self, b1_module):
        mock_sched = MagicMock()
        mock_sched.get_snapshot.return_value = {"total_tasks": 3}
        mock_sched.get_history.return_value = [1, 2, 3]
        mock_sched.cfg.__dict__ = {}

        class _FakeRC:
            autonomous_scheduler = mock_sched
            tick_count = 42

        s = b1_module["summarize_runtime"](_FakeRC())
        assert s["phase_b1_enabled"] is True
        assert s["scheduler_available"] is True
        assert s["scheduler"]["snapshot"]["total_tasks"] == 3
        assert s["tick_count"] == 42


# ============================================================
# 3. RuntimeB1Bridge
# ============================================================

class TestRuntimeB1Bridge:
    """RuntimeB1Bridge 只读 + 缓存"""

    def test_bridge_creation(self, b1_module):
        bridge = b1_module["create"](None)
        assert bridge is not None
        assert bridge.runtime_core is None

    def test_bridge_get_scheduler_none(self, b1_module):
        bridge = b1_module["create"](None)
        assert bridge.get_scheduler() is None

    def test_bridge_get_status_with_none(self, b1_module):
        bridge = b1_module["create"](None)
        status = bridge.get_status()
        assert status["phase_b1_enabled"] is False
        assert status["phase_b1_name"] == b1_module["name"]
        assert status["phase_b1_version"] == b1_module["version"]
        assert "ts" in status

    def test_bridge_caches_within_1s(self, b1_module):
        """1 秒内重复调用应复用缓存(不会重新调用 get_history)"""
        mock_sched = MagicMock()
        mock_sched.get_snapshot.return_value = {"total_tasks": 7}
        mock_sched.get_history.return_value = [1] * 100
        mock_sched.cfg.__dict__ = {}

        class _FakeRC:
            autonomous_scheduler = mock_sched
            tick_count = 1

        bridge = b1_module["create"](_FakeRC())
        s1 = bridge.get_status()
        s2 = bridge.get_status()
        # 缓存命中,get_history 只调用 1 次
        assert s1 is s2
        assert mock_sched.get_history.call_count == 1

    def test_bridge_get_history_safe(self, b1_module):
        """scheduler 异常时,get_history 返回空列表"""
        mock_sched = MagicMock()
        mock_sched.get_history.side_effect = RuntimeError("boom")

        class _FakeRC:
            autonomous_scheduler = mock_sched
            tick_count = 0

        bridge = b1_module["create"](_FakeRC())
        assert bridge.get_history(limit=10) == []


# ============================================================
# 4. RuntimeCore + AutonomousScheduler 真实链路
# ============================================================

class TestRuntimeCoreB1Integration:
    """RuntimeCore 接受 B.1 配置后,scheduler 应被自动创建并被 tick() 触发"""

    def test_b1_config_creates_scheduler(self, temp_workspace, b1_module):
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import apply_phase_b1_config

        cfg = apply_phase_b1_config(_build_runtime_core_config(temp_workspace))
        rc = RuntimeCore(config=cfg)

        try:
            assert rc.autonomous_scheduler is not None, (
                "B.1 配置下,RuntimeCore.autonomous_scheduler 应被创建"
            )
            assert rc.autonomous_scheduler.cfg.enabled is True
        finally:
            try:
                rc.save_lifecycle()
            except Exception:
                pass

    def test_tick_invokes_scheduler(self, temp_workspace, b1_module):
        """RuntimeCore.tick() 应触发 autonomous_scheduler.on_tick()"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import apply_phase_b1_config

        cfg = apply_phase_b1_config(_build_runtime_core_config(
            temp_workspace,
            # 降低 memory_maintenance 门槛,让 scheduler 在第一 tick 就能跑
            as_memory_interval_ticks=1,
            as_identity_interval_ticks=1,
            as_growth_eval_interval_ticks=1,
            as_reflection_interval_ticks=99,  # reflection 需要 reflection_scheduler
            as_min_memories_for_maintenance=0,
        ))
        rc = RuntimeCore(config=cfg)

        try:
            assert rc.autonomous_scheduler is not None
            # 第一次 tick
            rc.tick()
            history = rc.get_autonomous_scheduler_history(limit=50)
            task_types = [h["task_type"] for h in history]
            # 至少应有 identity_check 和 growth_evaluation 被执行
            assert "identity_check" in task_types or "growth_evaluation" in task_types
        finally:
            try:
                rc.save_lifecycle()
            except Exception:
                pass

    def test_scheduler_history_grows_across_ticks(self, temp_workspace, b1_module):
        """多 tick 后,scheduler history 应增加"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import apply_phase_b1_config

        cfg = apply_phase_b1_config(_build_runtime_core_config(
            temp_workspace,
            as_identity_interval_ticks=1,
            as_growth_eval_interval_ticks=1,
            as_reflection_interval_ticks=99,
        ))
        rc = RuntimeCore(config=cfg)

        try:
            assert rc.autonomous_scheduler is not None
            before = len(rc.get_autonomous_scheduler_history(limit=10000))
            for _ in range(3):
                rc.tick()
            after = len(rc.get_autonomous_scheduler_history(limit=10000))
            assert after > before, f"history 应增长:before={before}, after={after}"
        finally:
            try:
                rc.save_lifecycle()
            except Exception:
                pass


# ============================================================
# 5. Supervisor 端到端驱动 scheduler
# ============================================================

class TestSupervisorDrivesScheduler:
    """RuntimeSupervisor → RuntimeCore.tick() → AutonomousScheduler 端到端"""

    def test_supervisor_drives_scheduler_via_runtime_core(
        self, temp_workspace, fresh_supervisor_class
    ):
        """supervisor 周期性 tick,scheduler history 持续增长"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import apply_phase_b1_config

        cfg = apply_phase_b1_config(_build_runtime_core_config(
            temp_workspace,
            as_identity_interval_ticks=1,
            as_growth_eval_interval_ticks=1,
            as_reflection_interval_ticks=99,
        ))
        rc = RuntimeCore(config=cfg)
        assert rc.autonomous_scheduler is not None

        before = len(rc.get_autonomous_scheduler_history(limit=10000))

        sup = fresh_supervisor_class(
            runtime_core=rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(3.0)
        sup.stop()

        after = len(rc.get_autonomous_scheduler_history(limit=10000))
        assert after > before, (
            f"supervisor 驱动 3 秒后,scheduler history 应增长:"
            f"before={before}, after={after}"
        )

    def test_b1_bridge_reports_scheduler_through_supervisor(
        self, temp_workspace, fresh_supervisor_class, b1_module
    ):
        """B.1 Bridge 通过 supervisor 周期性轮询,正确反映 scheduler 状态"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import apply_phase_b1_config

        cfg = apply_phase_b1_config(_build_runtime_core_config(
            temp_workspace,
            as_identity_interval_ticks=1,
            as_growth_eval_interval_ticks=1,
            as_reflection_interval_ticks=99,
        ))
        rc = RuntimeCore(config=cfg)
        bridge = b1_module["create"](rc)

        # 启动前 status
        s0 = bridge.get_status()
        assert s0["phase_b1_enabled"] is True
        assert s0["scheduler_available"] is True
        assert s0["scheduler"] is not None

        sup = fresh_supervisor_class(
            runtime_core=rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        # 启动后 status(避开 1s 缓存)
        time.sleep(1.1)
        s1 = bridge.get_status()
        snap = (s1.get("scheduler") or {}).get("snapshot") or {}
        assert snap.get("total_tasks", 0) > 0, (
            f"supervisor 运行 2.5s 后,scheduler 应至少有任务记录,snap={snap}"
        )

    def test_scheduler_exception_isolated(
        self, temp_workspace, fresh_supervisor_class
    ):
        """scheduler 抛异常不应让 supervisor 退出(已有隔离,本测试做回归)"""
        from src.runtime.runtime_core import RuntimeCore
        from src.runtime.phase_b_integration import apply_phase_b1_config

        cfg = apply_phase_b1_config(_build_runtime_core_config(
            temp_workspace,
            as_identity_interval_ticks=1,
        ))
        rc = RuntimeCore(config=cfg)
        assert rc.autonomous_scheduler is not None

        # monkey-patch 让 on_tick 抛异常
        original_on_tick = rc.autonomous_scheduler.on_tick
        def _boom(runtime):
            raise RuntimeError("simulated scheduler failure")
        rc.autonomous_scheduler.on_tick = _boom

        sup = fresh_supervisor_class(
            runtime_core=rc,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()

        status = sup.get_status()
        # supervisor 仍能完成多次 tick
        assert status["tick_count"] >= 2
        # RuntimeCore 内部已隔离,supervisor 不会因为 scheduler 失败而停止
        # (RuntimeCore.tick() 内部 try/except 后,supervisor 仍认为 tick 完成)
        assert status["running"] is False  # 已 stop
        # 恢复
        rc.autonomous_scheduler.on_tick = original_on_tick


# ============================================================
# 6. 向后兼容
# ============================================================

class TestBackwardCompatibility:
    """无 B.1 flag 时,scheduler 应为 None,行为与 B.1 之前一致"""

    def test_no_b1_flag_means_no_scheduler(self, temp_workspace):
        from src.runtime.runtime_core import RuntimeCore

        # 不调用 apply_phase_b1_config,直接使用最小 config
        cfg = {
            "memory_store_path": os.path.join(temp_workspace, "mem.json"),
            "growth_proposals_path": os.path.join(temp_workspace, "gp.json"),
            "state_file": os.path.join(temp_workspace, "rt.json"),
            "autonomous_scheduler_enabled": False,
        }

        rc = RuntimeCore(config=cfg)
        try:
            assert rc.autonomous_scheduler is None, (
                "未启用 B.1 时,autonomous_scheduler 应为 None"
            )
        finally:
            try:
                rc.save_lifecycle()
            except Exception:
                pass

    def test_bridge_handles_no_scheduler(self, temp_workspace, b1_module):
        from src.runtime.runtime_core import RuntimeCore

        cfg = {
            "memory_store_path": os.path.join(temp_workspace, "mem.json"),
            "growth_proposals_path": os.path.join(temp_workspace, "gp.json"),
            "state_file": os.path.join(temp_workspace, "rt.json"),
            "autonomous_scheduler_enabled": False,
        }

        rc = RuntimeCore(config=cfg)
        try:
            bridge = b1_module["create"](rc)
            status = bridge.get_status()
            assert status["phase_b1_enabled"] is False
            assert status["scheduler_available"] is False
            assert status["scheduler"] is None
        finally:
            try:
                rc.save_lifecycle()
            except Exception:
                pass


# ============================================================
# 7. 黑盒:不修改业务模块(import 完整性)
# ============================================================

class TestNoCoreModuleModification:
    """B.1 不得影响核心模块的公开接口"""

    def test_runtime_core_public_api_unchanged(self):
        """RuntimeCore 关键方法签名必须存在"""
        from src.runtime.runtime_core import RuntimeCore
        for method in ("tick", "save_lifecycle", "get_autonomous_scheduler_history",
                       "get_autonomous_scheduler_snapshot"):
            assert hasattr(RuntimeCore, method), f"RuntimeCore 缺少 {method}"

    def test_autonomous_scheduler_public_api_unchanged(self):
        """AutonomousScheduler 关键方法签名必须存在"""
        from src.runtime.autonomous_scheduler import (
            AutonomousScheduler,
            AutonomousSchedulerConfig,
        )
        for method in ("on_tick", "get_history", "get_snapshot"):
            assert hasattr(AutonomousScheduler, method), (
                f"AutonomousScheduler 缺少 {method}"
            )
        # config 字段
        for field in (
            "enabled",
            "memory_maintenance_interval_ticks",
            "identity_check_interval_ticks",
            "growth_evaluation_interval_ticks",
            "reflection_check_interval_ticks",
        ):
            assert hasattr(AutonomousSchedulerConfig, field), (
                f"AutonomousSchedulerConfig 缺少 {field}"
            )

    def test_supervisor_unchanged(self):
        """RuntimeSupervisor 必须保留 B.1 之前的所有公开接口"""
        from src.runtime.supervisor import RuntimeSupervisor
        for method in (
            "start", "stop", "is_running", "get_status",
            "get_instance", "reset_instance",
        ):
            assert hasattr(RuntimeSupervisor, method), (
                f"RuntimeSupervisor 缺少 {method}"
            )
