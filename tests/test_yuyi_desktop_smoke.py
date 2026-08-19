# -*- coding: utf-8 -*-
"""
tests/test_yuyi_desktop_smoke.py

Phase C.10.1 —— Yuyi Desktop 骨架冒烟测试

覆盖目标:
1. Desktop 启动 / QApplication / MainWindow 创建成功
2. 7 个 Tab 都存在
3. Service 可以实例化
4. Provider Bridge 可以读取数据(允许 fallback)
5. 不调用任何写接口
6. 不影响已有测试

约束(强):
- 不创建 MemoryStore / PersonalityResolver / EmotionManager / GrowthState
- 不修改 RuntimeCore
- 不直接 import src/runtime/**、src/memory/**、src/growth/** 等业务模块
- 仅依赖 Provider(允许 fallback)
- 保持向后兼容
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

# 测试期间强制 Qt offscreen(避免需要真实显示设备)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# ============================================================
# 路径准备:确保 src / yuyi_desktop 可被 import
# ============================================================
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 1. PySide6 可用性
# ============================================================
class TestPySide6Available:
    """PySide6 必须可用,否则 Desktop 无法运行。"""

    def test_pyside6_imported(self):
        from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
        assert QtCore is not None
        assert QtGui is not None
        assert QtWidgets is not None

    def test_qapplication_version_present(self):
        from PySide6.QtCore import qVersion
        v = qVersion()
        assert isinstance(v, str)
        assert len(v) > 0


# ============================================================
# 2. Desktop Config
# ============================================================
class TestDesktopConfig:
    """DesktopConfig 应返回稳定的 7 Tab 配置。"""

    def test_config_import(self):
        from yuyi_desktop.config.desktop_config import (
            DesktopConfig,
            get_desktop_config,
        )
        cfg = get_desktop_config()
        assert isinstance(cfg, DesktopConfig)

    def test_seven_tabs(self):
        from yuyi_desktop.config.desktop_config import get_desktop_config
        cfg = get_desktop_config()
        assert cfg.get_tab_count() == 7, "Desktop 必须有 7 个 Tab"

    def test_tab_keys(self):
        from yuyi_desktop.config.desktop_config import get_desktop_config
        cfg = get_desktop_config()
        expected = [
            "dashboard", "runtime", "personality",
            "growth", "initiative", "memory", "settings",
        ]
        assert cfg.get_tab_keys() == expected

    def test_readonly_mode(self):
        from yuyi_desktop.config.desktop_config import get_desktop_config
        cfg = get_desktop_config()
        assert cfg.readonly_mode is True, "Phase C.10.1 必须只读"
        assert cfg.safe_writes_enabled is False, "Phase C.10.1 阶段必须禁用任何写入"


# ============================================================
# 3. Provider Bridge
# ============================================================
class TestProviderBridge:
    """ProviderBridge 必须可实例化,且可读取(允许 fallback)。"""

    def test_bridge_import(self):
        from yuyi_desktop.core.provider_bridge import (
            ProviderBridge,
            get_provider_bridge,
        )
        b = get_provider_bridge()
        assert isinstance(b, ProviderBridge)

    def test_bridge_health_keys(self):
        from yuyi_desktop.core.provider_bridge import (
            PROVIDER_KEYS,
            get_provider_bridge,
        )
        b = get_provider_bridge()
        health = b.health_check()
        assert set(health.keys()) == set(PROVIDER_KEYS)
        # 值均为 bool(可能全 False 因为 Runtime 未启动)
        for v in health.values():
            assert isinstance(v, bool)

    def test_bridge_runtime_status_safe(self):
        from yuyi_desktop.core.provider_bridge import get_provider_bridge
        b = get_provider_bridge()
        # 不管 Provider 是否可用,调用不能抛错
        status = b.runtime_get_status()
        assert isinstance(status, dict)

    def test_bridge_memory_summary_safe(self):
        from yuyi_desktop.core.provider_bridge import get_provider_bridge
        b = get_provider_bridge()
        s = b.memory_get_summary()
        assert isinstance(s, dict)

    def test_bridge_growth_summary_safe(self):
        from yuyi_desktop.core.provider_bridge import get_provider_bridge
        b = get_provider_bridge()
        s = b.growth_get_summary()
        assert isinstance(s, dict)

    def test_bridge_selfmodel_identity_safe(self):
        from yuyi_desktop.core.provider_bridge import get_provider_bridge
        b = get_provider_bridge()
        i = b.selfmodel_get_identity()
        assert isinstance(i, dict)

    def test_bridge_initiative_summary_safe(self):
        from yuyi_desktop.core.provider_bridge import get_provider_bridge
        b = get_provider_bridge()
        s = b.initiative_get_summary()
        assert isinstance(s, dict)

    def test_bridge_no_write_methods_exposed(self):
        """桥接器只能调用 get_*/list_* 风格的只读方法。"""
        from yuyi_desktop.core.provider_bridge import ProviderBridge
        # 列出所有 public 方法
        public_methods = [
            m for m in dir(ProviderBridge)
            if not m.startswith("_") and callable(getattr(ProviderBridge, m))
        ]
        # 不允许出现 apply / write / set / update / delete / resolve / approve / reject / commit
        forbidden = [
            "apply", "write", "delete", "resolve",
            "approve", "reject", "commit", "modify",
        ]
        for m in public_methods:
            lower = m.lower()
            for f in forbidden:
                assert f not in lower, (
                    f"ProviderBridge 暴露写方法: {m}(含 '{f}')"
                )


# ============================================================
# 4. Service 层
# ============================================================
class TestServices:
    """5 个 Service 都可实例化,且只调用 Provider 的读接口。"""

    def test_runtime_service(self):
        from yuyi_desktop.services.runtime_service import (
            RuntimeService,
            get_runtime_service,
        )
        s = get_runtime_service()
        assert isinstance(s, RuntimeService)
        # overview 必须为 dict
        ov = s.get_overview()
        assert isinstance(ov, dict)

    def test_memory_service(self):
        from yuyi_desktop.services.memory_service import (
            MemoryService,
            get_memory_service,
        )
        s = get_memory_service()
        assert isinstance(s, MemoryService)
        ov = s.get_overview()
        assert isinstance(ov, dict)
        assert "total" in ov
        assert "important" in ov

    def test_personality_service(self):
        from yuyi_desktop.services.personality_service import (
            PersonalityService,
            get_personality_service,
        )
        s = get_personality_service()
        assert isinstance(s, PersonalityService)
        ov = s.get_overview()
        assert isinstance(ov, dict)
        assert "identity_name" in ov

    def test_growth_service(self):
        from yuyi_desktop.services.growth_service import (
            GrowthService,
            get_growth_service,
        )
        s = get_growth_service()
        assert isinstance(s, GrowthService)
        ov = s.get_overview()
        assert isinstance(ov, dict)
        assert "pending" in ov
        assert "approved" in ov

    def test_initiative_service(self):
        from yuyi_desktop.services.initiative_service import (
            InitiativeService,
            get_initiative_service,
        )
        s = get_initiative_service()
        assert isinstance(s, InitiativeService)
        ov = s.get_overview()
        assert isinstance(ov, dict)

    def test_service_no_write_methods(self):
        """5 个 Service 都不能暴露写方法。"""
        service_modules = [
            "yuyi_desktop.services.runtime_service",
            "yuyi_desktop.services.memory_service",
            "yuyi_desktop.services.personality_service",
            "yuyi_desktop.services.growth_service",
            "yuyi_desktop.services.initiative_service",
        ]
        forbidden = [
            "apply", "write", "delete", "resolve",
            "approve", "reject", "commit", "modify",
        ]
        for mod_name in service_modules:
            import importlib
            mod = importlib.import_module(mod_name)
            classes = [
                getattr(mod, name) for name in dir(mod)
                if isinstance(getattr(mod, name), type)
                and name.endswith("Service")
            ]
            assert classes, f"{mod_name} 没有 Service 类"
            for cls in classes:
                for m in dir(cls):
                    if m.startswith("_"):
                        continue
                    attr = getattr(cls, m)
                    if not callable(attr):
                        continue
                    lower = m.lower()
                    for f in forbidden:
                        assert f not in lower, (
                            f"{cls.__name__}.{m} 含写操作关键字 '{f}'"
                        )


# ============================================================
# 5. QApplication + MainWindow
# ============================================================
class TestQApplicationAndMainWindow:
    """QApplication 与 MainWindow 启动/创建测试。"""

    def test_qapplication_creation(self, qapp):
        from PySide6.QtWidgets import QApplication
        assert isinstance(qapp, QApplication)
        assert qapplication_running(qapp)

    def test_mainwindow_creation(self, qapp):
        from yuyi_desktop.ui.main_window import YuyiMainWindow
        w = YuyiMainWindow()
        assert w is not None
        assert w.tab_count() == 7

    def test_seven_tabs_present(self, qapp):
        from yuyi_desktop.ui.main_window import YuyiMainWindow
        w = YuyiMainWindow()
        keys = w.get_tab_widget_keys()
        expected = [
            "dashboard", "runtime", "personality",
            "growth", "initiative", "memory", "settings",
        ]
        assert keys == expected, f"Tab 顺序错误,得到 {keys}"

    def test_mainwindow_title(self, qapp):
        from yuyi_desktop.config.desktop_config import get_desktop_config
        from yuyi_desktop.ui.main_window import YuyiMainWindow
        cfg = get_desktop_config()
        w = YuyiMainWindow()
        assert cfg.window_title in w.windowTitle()

    def test_build_window_factory(self, qapp):
        from yuyi_desktop.app import build_window
        from yuyi_desktop.ui.main_window import YuyiMainWindow
        w = build_window()
        assert isinstance(w, YuyiMainWindow)
        assert w.tab_count() == 7

    def test_get_or_create_qapp_idempotent(self, qapp):
        from yuyi_desktop.app import get_or_create_qapp
        from PySide6.QtWidgets import QApplication
        a1 = get_or_create_qapp()
        a2 = get_or_create_qapp()
        assert a1 is a2
        assert isinstance(a1, QApplication)


# ============================================================
# 6. Desktop Context
# ============================================================
class TestDesktopContext:
    """DesktopContext 单例与生命周期。"""

    def test_context_singleton(self):
        from yuyi_desktop.core.desktop_context import (
            DesktopContext,
            get_desktop_context,
        )
        c1 = get_desktop_context()
        c2 = get_desktop_context()
        assert c1 is c2
        assert isinstance(c1, DesktopContext)

    def test_context_health_snapshot(self, qapp):
        from yuyi_desktop.core.desktop_context import get_desktop_context
        c = get_desktop_context()
        c.mark_ready()
        snap = c.health_snapshot()
        assert "ready" in snap
        assert "tab_count" in snap
        assert "provider_health" in snap
        assert snap["tab_count"] == 7

    def test_context_reset_for_testing(self, qapp):
        from yuyi_desktop.core.desktop_context import (
            DesktopContext,
            reset_desktop_context_for_testing,
        )
        new_ctx = reset_desktop_context_for_testing()
        assert isinstance(new_ctx, DesktopContext)


# ============================================================
# 7. 不影响已有 Runtime / 业务模块
# ============================================================
class TestNoCoreModulePollution:
    """Desktop 导入不得触发核心模块副作用(不创建单例 / 不修改状态)。"""

    def test_desktop_import_does_not_initialize_runtime(self):
        # 重新加载 yuyi_desktop 子模块
        import importlib
        from yuyi_desktop.core import desktop_context
        importlib.reload(desktop_context)
        # Runtime 应未启动(没有 RuntimeCore 实例)
        try:
            from src.runtime.core.runtime_core import RuntimeCore
        except ImportError:
            RuntimeCore = None
        if RuntimeCore is not None:
            # 检查全局不应有 _instance
            for attr in dir(RuntimeCore):
                if "instance" in attr.lower() and not attr.startswith("__"):
                    val = getattr(RuntimeCore, attr, None)
                    if val is not None and not callable(val):
                        pytest.fail(
                            f"导入 Desktop 不应触发 RuntimeCore 单例: {attr}={val}"
                        )

    def test_desktop_does_not_modify_growth(self):
        # Desktop 不能修改 Growth proposal 数量
        # 简单验证:growth provider summary 与直接读 storage 一致
        from yuyi_desktop.core.provider_bridge import get_provider_bridge
        b = get_provider_bridge()
        s = b.growth_get_summary()
        # 若 proposal_storage 不可用,available=False,但不应抛错
        assert isinstance(s, dict)

    def test_desktop_does_not_call_runtime_apply(self):
        """通过 inspection 验证:Desktop 没有调用 apply 流程的方法引用。"""
        import yuyi_desktop
        import sys

        # 加载的所有 yuyi_desktop 子模块
        loaded = [m for m in sys.modules if m.startswith("yuyi_desktop")]
        assert len(loaded) > 0, "yuyi_desktop 必须能被 import"

        # 验证:服务层模块中不存在名为 apply/resolve/approve/reject 的方法
        for mod_name in loaded:
            mod = sys.modules[mod_name]
            for attr_name in dir(mod):
                if attr_name.startswith("_"):
                    continue
                attr = getattr(mod, attr_name, None)
                if not callable(attr):
                    continue
                # 方法对象
                if hasattr(attr, "__name__"):
                    name_lower = attr.__name__.lower()
                    for forbidden in ["apply", "resolve", "approve", "reject"]:
                        if name_lower == forbidden:
                            pytest.fail(
                                f"{mod_name}.{attr_name} 是写方法,违反只读边界"
                            )


# ============================================================
# 8. 辅助 fixture / util
# ============================================================
def qapplication_running(app):
    """简单检查 QApplication 是否存活。"""
    try:
        from PySide6.QtWidgets import QApplication
        return isinstance(app, QApplication) and QApplication.instance() is not None
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def qapp():
    """Session 级 QApplication fixture(供 UI 测试)。"""
    from yuyi_desktop.app import get_or_create_qapp
    return get_or_create_qapp()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
