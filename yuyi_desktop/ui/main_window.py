# -*- coding: utf-8 -*-
"""
yuyi_desktop/ui/main_window.py

Phase C.10.1 —— Yuyi Desktop 主窗口

包含:
- 标题栏
- 7 个 Tab 占位(QTabWidget)
- Status bar(显示 Provider 健康状态)

约束:
- Phase C.10.1 阶段:每个 Tab 只显示一个 QLabel 占位
- 禁止在 UI 层调用任何 Provider
- 禁止在 UI 层调用任何写接口
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from yuyi_desktop.config.desktop_config import (
    DesktopConfig,
    TabSpec,
    get_desktop_config,
)
from yuyi_desktop.core.desktop_context import (
    DesktopContext,
    get_desktop_context,
)
from yuyi_desktop.ui.widgets.archive_tab import ArchiveTab
from yuyi_desktop.ui.widgets.dashboard_widget import DashboardWidget
from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
from yuyi_desktop.ui.widgets.initiative_widget import InitiativeWidget
from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
from yuyi_desktop.ui.widgets.runtime_widget import RuntimeWidget
# Phase D.2.11 新增: Settings + ControlCenter
from yuyi_desktop.ui.widgets.control_center_widget import ControlCenterWidget
from yuyi_desktop.ui.widgets.settings_widget import SettingsWidget

logger = logging.getLogger(__name__)


# ============================================================
# 单个 Tab 占位
# ============================================================
class _PlaceholderTab(QWidget):
    """单个 Tab 的占位页面。"""

    def __init__(
        self,
        tab_spec: TabSpec,
        ctx: Optional[DesktopContext] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._spec = tab_spec
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # 标题
        title = QLabel(self._spec.title)
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        # 描述
        desc = QLabel(self._spec.description)
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #555;")
        layout.addWidget(desc)

        # 占位提示
        placeholder = QLabel(f"占位:此 Tab 将在 Phase C.10.{self._spec.index + 2} 实现")
        placeholder.setStyleSheet("color: #999; font-style: italic; padding: 12px;")
        layout.addWidget(placeholder)

        layout.addStretch(1)
        self.setLayout(layout)

    def tab_key(self) -> str:
        return self._spec.key


# ============================================================
# Scroll Area 包装器(Phase D.4: 修复模块重叠)
# ============================================================
def _wrap_in_scroll_area(widget: QWidget) -> QScrollArea:
    """将 widget 包装到 QScrollArea 中, 防止内容溢出压缩。

    Phase D.4 修复: 之前各 Tab 直接放入 QTabWidget, 当内容超过窗口高度时,
    QVBoxLayout 会压缩子项导致卡片/QGroupBox 互相堆叠(用户视觉上看到
    "模块重叠")。本包装器提供:
      1) 垂直滚动: 内容超出窗口时, 用户可向下滚动查看
      2) 水平禁用: 保持 Tab 单列布局, 避免横向压缩
      3) 自适应: widgetResizable=True, 内容宽度跟 Tab 宽度走
      4) 深色风格: 与 QGroupBox 折叠态区分(滚动条明显可见)

    Args:
        widget: 原始 Tab widget(已经构建好布局)

    Returns:
        配置好的 QScrollArea, 已 setWidget(widget)
    """
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    scroll.setStyleSheet(
        "QScrollArea { background-color: transparent; border: none; }"
        "QScrollBar:vertical {"
        "    background: #181818; width: 10px; margin: 0;"
        "}"
        "QScrollBar::handle:vertical {"
        "    background: #3a3a3a; border-radius: 4px; min-height: 24px;"
        "}"
        "QScrollBar::handle:vertical:hover {"
        "    background: #4a4a4a;"
        "}"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
        "    height: 0; background: none;"
        "}"
    )
    scroll.setWidget(widget)
    return scroll


# ============================================================
# Settings Tab 组合容器(Phase D.2.11)
# ============================================================
class _SettingsTab(QWidget):
    """Settings Tab 组合页面。

    上半部: SettingsWidget(客户端自身信息, 完全本地, 只读)
    下半部: ControlCenterWidget(模块 ON/OFF 控制, 通过 ControlService 走 /api/v1/control/*)

    布局: 垂直 QVBoxLayout, 中间加 QSplitter 让用户可调整上下比例。
    """

    def __init__(
        self,
        ctx: Optional[DesktopContext] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._build_ui()

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QSplitter

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._splitter = QSplitter(Qt.Vertical, self)
        self._splitter.setHandleWidth(4)
        self._splitter.setChildrenCollapsible(False)

        # 上: SettingsWidget
        self._settings = SettingsWidget(
            ctx=self._ctx, parent=self._splitter,
        )
        self._splitter.addWidget(self._settings)

        # 下: ControlCenterWidget
        self._control = ControlCenterWidget(
            ctx=self._ctx, parent=self._splitter,
        )
        self._splitter.addWidget(self._control)

        # 初始比例 1 : 1
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([400, 400])

        layout.addWidget(self._splitter)
        self.setLayout(layout)

    def tab_key(self) -> str:
        return "settings"

    def refresh(self) -> None:
        """手动触发刷新(转发到两个子 widget)。"""
        try:
            self._settings.refresh()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_SettingsTab.settings.refresh 异常: %s", exc)
        # ControlCenterWidget 自己有 AsyncRefresher, 不需要手动触发


# ============================================================
# 主窗口
# ============================================================
class YuyiMainWindow(QMainWindow):
    """Yuyi Desktop 主窗口。"""

    def __init__(
        self,
        config: Optional[DesktopConfig] = None,
        ctx: Optional[DesktopContext] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config if config is not None else get_desktop_config()
        self._ctx = ctx if ctx is not None else get_desktop_context()
        self._tabs: dict = {}
        self._build_ui()
        self._refresh_status()

    def _build_ui(self) -> None:
        # 窗口属性
        self.setWindowTitle(self._config.window_title)
        self.resize(
            self._config.window_width,
            self._config.window_height,
        )
        self.setMinimumSize(
            self._config.window_min_width,
            self._config.window_min_height,
        )

        # 中央 Tab Widget
        self._tab_widget = QTabWidget(self)
        self._tab_widget.setTabPosition(QTabWidget.North)
        self._tab_widget.setMovable(False)
        self._tab_widget.setDocumentMode(True)

        # 依次创建 7 个 Tab
        # Phase D.1.1: Dashboard Tab 替换为真实数据组件
        # Phase D.1.2: Runtime Tab 替换为真实数据组件
        # Phase D.1.3: Memory Tab 替换为真实数据组件
        # Phase D.1.4: Growth Tab 替换为真实数据组件
        # Phase D.1.5: Initiative Tab 替换为真实数据组件
        # Phase D.2.0: Personality Tab 替换为真实数据组件
        # Phase D.2.11: Settings Tab 替换为 SettingsWidget + ControlCenterWidget 组合
        # Phase D.4: 所有非 Settings Tab 包装到 QScrollArea 防止内容溢出压缩
        for spec in self._config.tab_specs:
            if spec.key == "dashboard":
                tab = DashboardWidget(ctx=self._ctx, parent=self._tab_widget)
            elif spec.key == "runtime":
                tab = RuntimeWidget(ctx=self._ctx, parent=self._tab_widget)
            elif spec.key == "memory":
                tab = MemoryWidget(ctx=self._ctx, parent=self._tab_widget)
            elif spec.key == "growth":
                tab = GrowthWidget(ctx=self._ctx, parent=self._tab_widget)
            elif spec.key == "initiative":
                tab = InitiativeWidget(ctx=self._ctx, parent=self._tab_widget)
            elif spec.key == "personality":
                tab = PersonalityWidget(ctx=self._ctx, parent=self._tab_widget)
            elif spec.key == "archive":
                # Phase D.5: 羽依档案(基础版:诞生时间/计数/版本/最近里程碑)
                tab = ArchiveTab(parent=self._tab_widget)
            elif spec.key == "settings":
                # Phase D.2.11: 组合 SettingsWidget(客户端自身信息,只读)
                # + ControlCenterWidget(模块 ON/OFF 控制)
                # Settings Tab 内部已用 QSplitter, 不再额外包 QScrollArea
                tab = _SettingsTab(
                    ctx=self._ctx, parent=self._tab_widget,
                )
            else:
                tab = _PlaceholderTab(spec, ctx=self._ctx, parent=self._tab_widget)
            # Phase D.4: 包装到 QScrollArea 防止内容溢出压缩
            # Settings Tab 已是 QSplitter 自带滚动, 跳过
            if spec.key != "settings":
                container = _wrap_in_scroll_area(tab)
                self._tab_widget.addTab(container, spec.title)
            else:
                self._tab_widget.addTab(tab, spec.title)
            self._tabs[spec.key] = tab

        self.setCentralWidget(self._tab_widget)

        # Status bar
        sb = QStatusBar(self)
        self.setStatusBar(sb)
        self._status_label = QLabel("Provider 健康状态:未检测")
        sb.addWidget(self._status_label, 1)

        self._refresh_button = QPushButton("刷新")
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        sb.addPermanentWidget(self._refresh_button)

    def _on_refresh_clicked(self) -> None:
        self._refresh_status()

    def _refresh_status(self) -> None:
        """刷新 StatusBar 显示 Provider 健康状态。"""
        try:
            health = self._ctx.health_snapshot()
            provider_health = health.get("provider_health", {})
            available_count = sum(1 for v in provider_health.values() if v)
            total = len(provider_health)
            tab_count = health.get("tab_count", 0)
            self._status_label.setText(
                f"Provider: {available_count}/{total} | Tab: {tab_count} | 模式: 只读"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MainWindow._refresh_status 异常: %s", exc)
            self._status_label.setText("Provider: 不可用")

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def tab_count(self) -> int:
        return self._tab_widget.count()

    def get_tab_keys(self) -> list:
        return [self._tab_widget.tabText(i) for i in range(self._tab_widget.count())]

    def get_tab_widget_keys(self) -> list:
        """返回 tab 内部 key 列表。

        Phase D.2.11 修复: 之前依赖每个 widget 自带 tab_key() 方法,
        但真实 widget (Dashboard/Runtime/Memory/...) 没有这个方法,
        会导致 AttributeError. 改为直接返回 _tabs 的 key 列表(按 spec 顺序).
        """
        return list(self._tabs.keys())


__all__ = [
    "YuyiMainWindow",
]
