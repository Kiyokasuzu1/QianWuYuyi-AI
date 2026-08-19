# -*- coding: utf-8 -*-
"""
yuyi_desktop/app.py

Phase C.10.1 —— QApplication 工厂

负责:
- 创建 QApplication 实例
- 注入 QApplication name/org(便于 QSettings)
- 提供 build_window() 工厂方法

约束:
- 不创建 main window,只提供 QApplication 单例
- 不修改全局 Qt 行为
- 不连接任何外部资源
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from yuyi_desktop.config.desktop_config import (
    DesktopConfig,
    get_desktop_config,
)
from yuyi_desktop.core.desktop_context import (
    DesktopContext,
    get_desktop_context,
)
from yuyi_desktop.ui.main_window import YuyiMainWindow

logger = logging.getLogger(__name__)

APP_NAME = "YuyiDesktop"
ORG_NAME = "QianWuYuyi"
ORG_DOMAIN = "qianwuyuyi.ai"


def _set_app_metadata() -> None:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setOrganizationDomain(ORG_DOMAIN)
    QCoreApplication.setApplicationName(APP_NAME)


def get_or_create_qapp(argv: Optional[list] = None) -> QApplication:
    """
    获取或创建 QApplication 单例。

    Args:
        argv: 命令行参数列表(可选)

    Returns:
        QApplication 实例
    """
    existing = QApplication.instance()
    if existing is not None:
        if isinstance(existing, QApplication):
            return existing
        # 如果存在但不是 QApplication(罕见),转为强类型
        # 这种情况在测试中可能出现
    if argv is None:
        argv = sys.argv[:1] if sys.argv else ["yuyi_desktop"]
    _set_app_metadata()
    app = QApplication(argv)
    # 允许在无头环境(测试)运行
    if os.environ.get("QT_QPA_PLATFORM") is None and not _has_display():
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        # 重新创建
        app = QApplication(argv)
        _set_app_metadata()
    return app


def _has_display() -> bool:
    """检测是否有可用显示设备。Windows 总是 True,Linux 检查 DISPLAY。"""
    if sys.platform.startswith("win") or sys.platform.startswith("darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def build_window(
    config: Optional[DesktopConfig] = None,
    ctx: Optional[DesktopContext] = None,
) -> YuyiMainWindow:
    """
    构建主窗口(不调用 app.exec())。

    Args:
        config: DesktopConfig(默认使用全局)
        ctx: DesktopContext(默认使用全局)

    Returns:
        YuyiMainWindow 实例
    """
    if config is None:
        config = get_desktop_config()
    if ctx is None:
        ctx = get_desktop_context()
    window = YuyiMainWindow(config=config, ctx=ctx)
    ctx.mark_ready()
    return window


def run_desktop(
    config: Optional[DesktopConfig] = None,
    ctx: Optional[DesktopContext] = None,
) -> int:
    """
    启动 Desktop 主循环(阻塞,直到窗口关闭)。

    Args:
        config: DesktopConfig
        ctx: DesktopContext

    Returns:
        QApplication.exec() 的退出码
    """
    app = get_or_create_qapp()
    window = build_window(config=config, ctx=ctx)
    window.show()
    return app.exec()


__all__ = [
    "APP_NAME",
    "ORG_NAME",
    "ORG_DOMAIN",
    "get_or_create_qapp",
    "build_window",
    "run_desktop",
]
