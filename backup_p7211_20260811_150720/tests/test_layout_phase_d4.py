# -*- coding: utf-8 -*-
"""
tests/test_layout_phase_d4.py

Phase D.4 —— 布局修复(模块重叠 / 内容溢出压缩)回归测试

覆盖:
- yuyi_desktop.ui.main_window._wrap_in_scroll_area 包装器
- 主窗口对非 Settings Tab 启用 QScrollArea
- Settings Tab 保留 QSplitter 行为
- QGroupBox 新样式 (_DETAIL_GROUP_STYLESHEET) 存在并应用到所有 detail group
- PersonalityWidget 4 个 detail group 都使用新样式
- Tab widget container 实际类型为 QScrollArea

约束:
- 不直接 import src.*
- 不发起任何网络请求
- 不调用任何写接口
- 完全离线的 offscreen 渲染

这些测试确保 Phase D.4 修复有效, 且不会回归:
  - 之前的 D.2.x 测试 (tab_key / widgets)
  - 之前的 D.3 测试 (state_visualization)
"""
from __future__ import annotations

import os

# 强制 Qt offscreen, 避免 CI/无显示器环境失败
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QScrollArea, QSplitter, QTabWidget, QWidget


# ============================================================
# _wrap_in_scroll_area 单元测试
# ============================================================
class TestWrapInScrollArea:
    """测试 main_window._wrap_in_scroll_area 包装器的契约。"""

    def test_wrap_returns_qscrollarea(self, qt_app):
        """包装器必须返回 QScrollArea 实例。"""
        from yuyi_desktop.ui.main_window import _wrap_in_scroll_area

        child = QWidget()
        result = _wrap_in_scroll_area(child)
        try:
            assert isinstance(result, QScrollArea)
        finally:
            result.setParent(None)
            child.setParent(None)

    def test_wrap_widget_set_as_content(self, qt_app):
        """被包装的 widget 必须作为 QScrollArea 的内容子控件。"""
        from yuyi_desktop.ui.main_window import _wrap_in_scroll_area

        child = QWidget()
        child.setObjectName("wrapped_child_marker_d4")
        result = _wrap_in_scroll_area(child)
        try:
            assert result.widget() is child
            assert result.widgetResizable() is True
        finally:
            result.setParent(None)
            child.setParent(None)

    def test_wrap_horizontal_disabled(self, qt_app):
        """水平滚动条必须禁用, 防止横向压缩。"""
        from yuyi_desktop.ui.main_window import _wrap_in_scroll_area

        child = QWidget()
        result = _wrap_in_scroll_area(child)
        try:
            assert result.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
        finally:
            result.setParent(None)
            child.setParent(None)

    def test_wrap_vertical_as_needed(self, qt_app):
        """垂直滚动条按需出现, 默认 AsNeeded。"""
        from yuyi_desktop.ui.main_window import _wrap_in_scroll_area

        child = QWidget()
        result = _wrap_in_scroll_area(child)
        try:
            assert result.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
        finally:
            result.setParent(None)
            child.setParent(None)

    def test_wrap_no_frame(self, qt_app):
        """QScrollArea 必须无边框, 保持 Tab 视觉一致性。"""
        from yuyi_desktop.ui.main_window import _wrap_in_scroll_area

        child = QWidget()
        result = _wrap_in_scroll_area(child)
        try:
            assert result.frameShape() == QScrollArea.NoFrame
        finally:
            result.setParent(None)
            child.setParent(None)

    def test_wrap_expanding_size_policy(self, qt_app):
        """QScrollArea 必须声明 Expanding 尺寸策略。"""
        from yuyi_desktop.ui.main_window import _wrap_in_scroll_area

        child = QWidget()
        result = _wrap_in_scroll_area(child)
        try:
            from PySide6.QtWidgets import QSizePolicy

            sp = result.sizePolicy()
            assert sp.horizontalPolicy() == QSizePolicy.Expanding
            assert sp.verticalPolicy() == QSizePolicy.Expanding
        finally:
            result.setParent(None)
            child.setParent(None)


# ============================================================
# 主窗口 Tab 包装测试
# ============================================================
@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def main_window(qt_app):
    from yuyi_desktop.config.desktop_config import get_desktop_config
    from yuyi_desktop.core.desktop_context import get_desktop_context
    from yuyi_desktop.ui.main_window import YuyiMainWindow

    w = YuyiMainWindow(get_desktop_config(), get_desktop_context())
    yield w
    w.deleteLater()
    qt_app.processEvents()


class TestMainWindowTabWrapping:
    """测试主窗口的 Tab 包装行为 (Phase D.4)。"""

    def test_main_window_has_tabs(self, main_window):
        """主窗口必须有 Tab widget。"""
        assert main_window.tab_count() >= 1

    def test_all_non_settings_tabs_wrapped_in_scroll_area(self, main_window):
        """所有非 Settings Tab 必须被 QScrollArea 包装, 防止溢出压缩。"""
        from yuyi_desktop.config.desktop_config import get_desktop_config

        config = get_desktop_config()
        spec_keys = [s.key for s in config.tab_specs]
        tab_widget = main_window.findChild(QTabWidget)
        assert tab_widget is not None

        for idx, key in enumerate(spec_keys):
            tab_page = tab_widget.widget(idx)
            assert tab_page is not None, f"Tab {key} not found"
            if key == "settings":
                # Settings Tab 内部已用 QSplitter, 不再额外包
                # tab_page 本身可能是 _SettingsTab (含 QSplitter)
                # 不强求顶层是 QSplitter, 因为 _SettingsTab 才是 parent
                continue
            # 其他 Tab: 顶层必须是 QScrollArea
            assert isinstance(tab_page, QScrollArea), (
                f"Tab '{key}' 应被 QScrollArea 包装, 实际是 {type(tab_page).__name__}"
            )

    def test_settings_tab_not_wrapped_in_scroll_area(self, main_window):
        """Settings Tab 顶层不应是 QScrollArea(它使用 QSplitter)。"""
        tab_widget = main_window.findChild(QTabWidget)
        if tab_widget is None:
            pytest.skip("no tab widget")
        for i in range(tab_widget.count()):
            text = tab_widget.tabText(i)
            if "System" in text or "设置" in text or "Settings" in text:
                tab_page = tab_widget.widget(i)
                assert not isinstance(tab_page, QScrollArea), (
                    "Settings Tab 不应被 QScrollArea 包装(内部已用 QSplitter)"
                )
                return
        pytest.skip("settings tab not found")


# ============================================================
# QGroupBox 新样式测试
# ============================================================
class TestDetailGroupStylesheet:
    """测试 _DETAIL_GROUP_STYLESHEET 存在并被正确应用。"""

    def test_stylesheet_constant_exists(self):
        """_DETAIL_GROUP_STYLESHEET 必须在 personality_widget 中定义。"""
        from yuyi_desktop.ui.widgets.personality_widget import (
            _DETAIL_GROUP_STYLESHEET,
        )

        assert isinstance(_DETAIL_GROUP_STYLESHEET, str)
        assert len(_DETAIL_GROUP_STYLESHEET) > 0
        # 关键样式片段
        assert "QGroupBox" in _DETAIL_GROUP_STYLESHEET
        assert "QGroupBox::title" in _DETAIL_GROUP_STYLESHEET
        assert "QGroupBox::indicator" in _DETAIL_GROUP_STYLESHEET
        # 关键颜色: title 背景不应与 QFrame 背景完全相同
        # 旧版 #181818 (与 Section 同色, 看起来像黑条)
        # 新版应使用 #1f1f1f + 边框
        assert "#1f1f1f" in _DETAIL_GROUP_STYLESHEET
        assert "#3a3a3a" in _DETAIL_GROUP_STYLESHEET

    def test_stylesheet_uses_title_background(self):
        """title 样式必须使用 background-color 让标题区域有底色, 区别于 QFrame 卡片。"""
        from yuyi_desktop.ui.widgets.personality_widget import (
            _DETAIL_GROUP_STYLESHEET,
        )

        # 提取 QGroupBox::title 块
        title_idx = _DETAIL_GROUP_STYLESHEET.find("QGroupBox::title")
        assert title_idx > 0
        title_block = _DETAIL_GROUP_STYLESHEET[title_idx:]
        assert "background-color" in title_block
        assert "border" in title_block
        assert "border-radius" in title_block
        assert "color" in title_block


class TestPersonalityWidgetGroupStyles:
    """测试 PersonalityWidget 的 4 个 detail group 都使用新样式。"""

    def test_all_groups_use_detail_stylesheet(self, qt_app):
        from yuyi_desktop.core.desktop_context import get_desktop_context
        from yuyi_desktop.ui.widgets.personality_widget import (
            PersonalityWidget,
            _DETAIL_GROUP_STYLESHEET,
        )

        ctx = get_desktop_context()
        w = PersonalityWidget(ctx=ctx)
        try:
            for attr in (
                "_traits_group",
                "_beliefs_group",
                "_evolution_group",
                "_growth_group",
            ):
                grp = getattr(w, attr, None)
                assert grp is not None, f"{attr} not found"
                assert isinstance(grp, QGroupBox), (
                    f"{attr} 应为 QGroupBox 实例, 实际是 {type(grp).__name__}"
                )
                # 验证实际样式应用: 取出当前样式
                style = grp.styleSheet()
                # 注: PySide6 中 QGroupBox.styleSheet() 会返回自身 set 的样式
                assert style == _DETAIL_GROUP_STYLESHEET, (
                    f"{attr} 样式与 _DETAIL_GROUP_STYLESHEET 不一致"
                )
        finally:
            w.setParent(None)
            qt_app.processEvents()

    def test_group_titles_contain_expand_hint(self, qt_app):
        """detail group 标题必须包含"点击展开"提示, 让用户知道可点击。"""
        from yuyi_desktop.core.desktop_context import get_desktop_context
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget

        ctx = get_desktop_context()
        w = PersonalityWidget(ctx=ctx)
        try:
            expected_keys = (
                "Trait 详情",
                "Beliefs 详情",
                "最近演化",
                "Growth 详情",
            )
            for attr in (
                "_traits_group",
                "_beliefs_group",
                "_evolution_group",
                "_growth_group",
            ):
                grp = getattr(w, attr, None)
                assert grp is not None, f"{attr} not found"
                title = grp.title()
                assert any(k in title for k in expected_keys), (
                    f"{attr} 标题 '{title}' 不在期望列表中"
                )
                # 强调"点击展开"提示
                assert "点击展开" in title, (
                    f"{attr} 标题应包含 '点击展开' 提示, 实际: '{title}'"
                )
        finally:
            w.setParent(None)
            qt_app.processEvents()


# ============================================================
# 关键回归保护: D.2.11 / D.3 功能不被破坏
# ============================================================
class TestRegressionProtection:
    """Phase D.4 不能破坏 Phase D.2.x / D.3 已有的功能。"""

    def test_settings_widget_still_works(self, qt_app):
        """Settings Tab 内容应继续工作。"""
        from yuyi_desktop.core.desktop_context import get_desktop_context
        from yuyi_desktop.config.desktop_config import get_desktop_config
        from yuyi_desktop.ui.widgets.settings_widget import SettingsWidget

        ctx = get_desktop_context()
        w = SettingsWidget(ctx=ctx, config=get_desktop_config())
        try:
            snap = w.get_snapshot()
            assert isinstance(snap, dict)
            assert "client" in snap
            assert "server" in snap
            assert "auth" in snap
            assert "runtime" in snap
        finally:
            w.deleteLater()
            qt_app.processEvents()

    def test_main_window_can_be_constructed(self, qt_app):
        """主窗口必须能正常构造(QScrollArea 包装不能破坏整体流程)。"""
        from yuyi_desktop.config.desktop_config import get_desktop_config
        from yuyi_desktop.core.desktop_context import get_desktop_context
        from yuyi_desktop.ui.main_window import YuyiMainWindow

        w = YuyiMainWindow(get_desktop_config(), get_desktop_context())
        try:
            assert w.tab_count() >= 1
            keys = w.get_tab_widget_keys()
            assert isinstance(keys, list)
            assert len(keys) >= 1
        finally:
            w.deleteLater()
            qt_app.processEvents()

    def test_state_visualization_module_still_works(self):
        """state_visualization 模块(D.3)功能必须正常。"""
        from yuyi_desktop.ui.widgets.state_visualization import (
            TimelineEntry,
            parse_evolution_entries,
            parse_growth_entries,
            parse_memory_entries,
            sort_entries_by_time_desc,
            has_meaningful_data,
        )

        # 简单烟雾测试
        evos = [{"timestamp": "2026-08-04T10:00:00Z", "changed_traits": ["温柔"]}]
        out = parse_evolution_entries(evos)
        assert len(out) == 1
        assert isinstance(out[0], TimelineEntry)

        growth = [{"timestamp": "2026-08-04T10:00:00Z", "signal": "test"}]
        out = parse_growth_entries(growth)
        assert len(out) == 1

        mems = [{"timestamp": "2026-08-04T10:00:00Z", "summary": "重要记忆"}]
        out = parse_memory_entries(mems)
        assert len(out) == 1

        # 排序
        evos = [
            {"timestamp": "2026-08-04T10:00:00Z"},
            {"timestamp": "2026-08-04T11:00:00Z"},
        ]
        entries = parse_evolution_entries(evos)
        sorted_entries = sort_entries_by_time_desc(entries)
        # 较新的 (11:00) 排在前面
        assert sorted_entries[0].sort_key > sorted_entries[1].sort_key
        assert has_meaningful_data(sorted_entries)
