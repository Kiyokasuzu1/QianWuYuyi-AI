# -*- coding: utf-8 -*-
"""
tests/test_admin_selfmodel_ui_integration.py

Phase 6.5: Admin SelfModel 前端 UI 集成测试。

验证：
1. SelfModel Dashboard JS / CSS 文件存在
2. HTML 中包含 selfmodel-section / nav 入口
3. JS 请求正确的 API 端点（/api/admin/selfmodel/*）
4. JS 严格只读（不允许 POST/PUT/DELETE/api_post）
5. JS 暴露全局对象 YuyiSelfModelDashboard
6. CSS 包含必要的视觉 token（玻璃态、状态色）
7. 不修改 RuntimeCore / SelfModelAdapter（仅 UI 集成）
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATIC_DIR = PROJECT_ROOT / "static" / "admin"
JS_FILE = STATIC_DIR / "js" / "selfmodel_dashboard.js"
CSS_FILE = STATIC_DIR / "css" / "selfmodel_dashboard.css"
HTML_FILE = STATIC_DIR / "index.html"
APP_JS_FILE = STATIC_DIR / "js" / "app.js"


# Phase 6.5 API 端点（11 个 selfmodel 端点）
PHASE6_5_SELFMODEL_ENDPOINTS = [
    "/admin/api/admin/selfmodel/status",
    "/admin/api/admin/selfmodel/identity",
    "/admin/api/admin/selfmodel/beliefs",
    "/admin/api/admin/selfmodel/belief",          # /<id>/why
    "/admin/api/admin/selfmodel/history",
    "/admin/api/admin/selfmodel/reflections",
    "/admin/api/admin/selfmodel/evolution_timeline",
    "/admin/api/admin/selfmodel/pcr",             # /<id>/related
    "/admin/api/admin/selfmodel/health",
    "/admin/api/admin/selfmodel/retention",
]


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture(scope="module")
def html_text():
    return HTML_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def js_text():
    return JS_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_text():
    return CSS_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js_text():
    return APP_JS_FILE.read_text(encoding="utf-8")


# =====================================================================
# A. 文件存在
# =====================================================================
class TestFilesExist:

    def test_js_file_exists(self):
        assert JS_FILE.exists(), f"selfmodel_dashboard.js 不存在: {JS_FILE}"

    def test_css_file_exists(self):
        assert CSS_FILE.exists(), f"selfmodel_dashboard.css 不存在: {CSS_FILE}"

    def test_html_file_exists(self):
        assert HTML_FILE.exists()


# =====================================================================
# B. HTML 结构
# =====================================================================
class TestHtmlStructure:

    def test_html_has_selfmodel_section(self, html_text):
        assert 'id="selfmodel-section"' in html_text

    def test_html_has_selfmodel_nav_item(self, html_text):
        """侧栏必须有 selfmodel nav 入口。"""
        assert 'data-view="selfmodel"' in html_text
        assert "自我模型" in html_text

    def test_html_has_subview_containers(self, html_text):
        for sid in ("sm-view-overview", "sm-view-identity", "sm-view-beliefs"):
            assert f'id="{sid}"' in html_text, f"缺少 {sid}"

    def test_html_has_tabs(self, html_text):
        for tab in ("overview", "identity", "beliefs"):
            assert f'data-tab="{tab}"' in html_text, f"缺少 tab: {tab}"

    def test_html_has_refresh_button(self, html_text):
        assert 'id="sm-refresh-btn"' in html_text

    def test_html_references_css(self, html_text):
        assert "/admin/css/selfmodel_dashboard.css" in html_text

    def test_html_references_js(self, html_text):
        assert "/admin/js/selfmodel_dashboard.js" in html_text

    def test_selfmodel_section_initially_hidden(self, html_text):
        """selfmodel-section 初始应为 display:none。"""
        # 找到包含 id="selfmodel-section" 的标签
        m = re.search(r'<section[^>]*id="selfmodel-section"[^>]*>', html_text)
        assert m, "selfmodel-section 标签缺失"
        assert 'display:none' in m.group(0), "selfmodel-section 必须初始隐藏"


# =====================================================================
# C. JS — 正确请求 Phase 6.5 API
# =====================================================================
class TestJsApiEndpoints:

    @pytest.mark.parametrize("endpoint", PHASE6_5_SELFMODEL_ENDPOINTS)
    def test_js_requests_endpoint(self, js_text, endpoint):
        assert endpoint in js_text, f"selfmodel_dashboard.js 未请求 {endpoint}"

    def test_js_uses_admin_api_base(self, js_text):
        assert "/admin/api" in js_text

    def test_js_uses_fetch(self, js_text):
        """现代 fetch API（不是 XMLHttpRequest）。"""
        assert "fetch(" in js_text


# =====================================================================
# D. JS — 严格只读（不允许写入）
# =====================================================================
class TestJsReadOnly:

    def test_no_api_post_call(self, js_text):
        """禁止调用 app.js 的 api_post（防止写入）。"""
        assert "api_post" not in js_text, "selfmodel_dashboard.js 不应调用 api_post"

    def test_no_direct_method_post(self, js_text):
        """selfmodel_dashboard.js 不应使用 method=POST（除了 dry_run via fetchJson）。"""
        # dry_run 是 Step 5.4 范畴，本阶段无 POST
        assert "method: \"POST\"" not in js_text and "method:'POST'" not in js_text, \
            "selfmodel_dashboard.js 不应使用 POST"

    def test_no_method_put(self, js_text):
        assert "method: \"PUT\"" not in js_text and "method:'PUT'" not in js_text

    def test_no_method_delete(self, js_text):
        assert "method: \"DELETE\"" not in js_text and "method:'DELETE'" not in js_text

    def test_no_apply_pcr(self, js_text):
        """禁止触发人格修改 / PCR apply。"""
        for forbidden in ("apply_pcr", "update_state", "submitProposal", "save_pcr", "write_state"):
            assert forbidden not in js_text, f"selfmodel_dashboard.js 不应触发 {forbidden}"


# =====================================================================
# E. JS — 全局对象 & 渲染 API
# =====================================================================
class TestJsGlobalApi:

    def test_exposes_global(self, js_text):
        assert "window.YuyiSelfModelDashboard" in js_text

    def test_global_has_render(self, js_text):
        assert "render: render" in js_text

    def test_global_has_show_why(self, js_text):
        assert "showWhy" in js_text

    def test_global_has_activate_tab(self, js_text):
        assert "activateTab" in js_text

    def test_has_offline_fallback(self, js_text):
        """必须实现 Offline 回退渲染（bridge 不可用时不抛错）。"""
        assert "sm-offline" in js_text
        assert "不可用" in js_text or "Offline" in js_text or "offline" in js_text

    def test_has_loading_state(self, js_text):
        """必须实现 loading 状态。"""
        assert "sm-loading" in js_text

    def test_init_called_on_dom_ready(self, js_text):
        """必须在 DOMContentLoaded 或 script-end 时 init。"""
        assert "DOMContentLoaded" in js_text or "readyState" in js_text


# =====================================================================
# F. CSS — 必要 token
# =====================================================================
class TestCssTokens:

    def test_css_uses_glassmorphism(self, css_text):
        assert "backdrop-filter" in css_text or "glass" in css_text.lower()

    def test_css_has_soft_palette(self, css_text):
        """使用现有 design tokens（紫色 / 蓝色 / 粉色系）。"""
        # 至少引用一个 design-tokens 变量
        assert "--c-accent" in css_text or "--c-accent-2" in css_text or "var(--c-" in css_text

    def test_css_has_status_pills(self, css_text):
        for cls in ("sm-status-pill--ok", "sm-status-pill--warn", "sm-status-pill--bad", "sm-status-pill--unknown"):
            assert cls in css_text, f"缺少 {cls}"

    def test_css_has_offline_state(self, css_text):
        assert ".sm-offline" in css_text

    def test_css_has_loading_state(self, css_text):
        assert ".sm-loading" in css_text

    def test_css_has_belief_card(self, css_text):
        assert ".sm-brief-card" in css_text

    def test_css_has_why_panel(self, css_text):
        assert ".sm-why-panel" in css_text or ".sm-why-block" in css_text


# =====================================================================
# G. app.js 集成
# =====================================================================
class TestAppJsIntegration:

    def test_switchview_routes_selfmodel(self, app_js_text):
        assert "view === 'selfmodel'" in app_js_text

    def test_show_selfmodel_view_function(self, app_js_text):
        assert "function showSelfModelView" in app_js_text

    def test_show_selfmodel_view_triggers_render(self, app_js_text):
        """切换到 selfmodel 视图应触发 YuyiSelfModelDashboard.render。"""
        assert "YuyiSelfModelDashboard.render" in app_js_text

    def test_show_dashboard_view_hides_selfmodel(self, app_js_text):
        """回到首页时 selfmodel 视图应隐藏。"""
        # showDashboardView 中应包含 selfmodel-view
        m = re.search(r"function showDashboardView\(\)\s*\{(.*?)\n\}", app_js_text, re.DOTALL)
        assert m
        assert "selfmodel-view" in m.group(1), "showDashboardView 应隐藏 selfmodel-view"

    def test_hide_dashboard_view_hides_selfmodel(self, app_js_text):
        m = re.search(r"function hideDashboardView\(\)\s*\{(.*?)\n\}", app_js_text, re.DOTALL)
        assert m
        assert "selfmodel-view" in m.group(1), "hideDashboardView 应隐藏 selfmodel-view"


# =====================================================================
# H. 渲染逻辑（粗略静态检查）
# =====================================================================
class TestRenderLogic:

    def test_overview_renders_status(self, js_text):
        assert "renderOverview" in js_text
        # 涉及 Bootstrap 字段
        assert "persistence_attached" in js_text
        assert "data_dir" in js_text

    def test_overview_renders_health(self, js_text):
        assert "overall_status" in js_text

    def test_overview_renders_timeline(self, js_text):
        assert "renderTimelineItem" in js_text

    def test_identity_renders_name(self, js_text):
        assert "identity_name" in js_text
        assert "core_identity" in js_text or "current_personality" in js_text

    def test_beliefs_renders_card(self, js_text):
        assert "renderBeliefCard" in js_text
        assert "sm-brief-domain" in js_text

    def test_why_renders_sources(self, js_text):
        assert "sm-why-source-item" in js_text
        assert "sm-why-step" in js_text

    def test_why_calls_correct_endpoint(self, js_text):
        """Why 追溯应调用 /belief/<id>/why。"""
        # 应使用模板字符串拼接
        assert "${" in js_text or "+ " in js_text
        # 或显式构造 /why
        assert "/why" in js_text


# =====================================================================
# I. 不影响 Runtime（无核心模块 import 路径）
# =====================================================================
class TestNoRuntimeImpact:

    def test_no_runtimecore_import_in_js(self, js_text):
        """前端 JS 不能 import RuntimeCore。"""
        assert "RuntimeCore" not in js_text
        assert "runtime_core" not in js_text

    def test_no_self_model_adapter_in_js(self, js_text):
        """前端 JS 不能直接读 SelfModelAdapter。"""
        assert "SelfModelAdapter" not in js_text
        assert "self_model_adapter" not in js_text

    def test_only_api_path_access(self, js_text):
        """所有数据访问必须通过 /admin/api/selfmodel/* 路径。"""
        # 必须包含 /admin/api
        assert "/admin/api" in js_text
