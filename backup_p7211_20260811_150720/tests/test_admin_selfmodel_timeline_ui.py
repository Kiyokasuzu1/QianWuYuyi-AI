# -*- coding: utf-8 -*-
"""
tests/test_admin_selfmodel_timeline_ui.py

Phase 6.5 Step 5.3: Admin SelfModel Evolution Timeline 前端 UI 测试。

验证：
1. Evolution Timeline JS / CSS 文件存在
2. HTML 中包含 timeline section / nav 入口 / CSS+JS 引用
3. JS 请求正确的 API 端点（/api/admin/selfmodel/evolution_timeline）
4. JS 严格只读（不允许 POST/PUT/DELETE/api_post；不允许修改 store / 触发 PCR apply）
5. JS 暴露全局对象 YuyiSelfModelTimeline
6. CSS 包含必要 token（玻璃态、状态色、来源色）
7. app.js 集成 view route（switchView + showSelfModelTimelineView）
8. 不修改 RuntimeCore / SelfModelAdapter（仅 UI 集成）
9. 不修改已有 selfmodel_dashboard.js / .css
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATIC_DIR = PROJECT_ROOT / "static" / "admin"
JS_FILE = STATIC_DIR / "js" / "selfmodel_timeline.js"
CSS_FILE = STATIC_DIR / "css" / "selfmodel_timeline.css"
HTML_FILE = STATIC_DIR / "index.html"
APP_JS_FILE = STATIC_DIR / "js" / "app.js"

# 已有的 selfmodel dashboard 文件（必须不被修改）
EXISTING_JS_FILE = STATIC_DIR / "js" / "selfmodel_dashboard.js"
EXISTING_CSS_FILE = STATIC_DIR / "css" / "selfmodel_dashboard.css"


# =====================================================================
# 工具函数
# =====================================================================
def _strip_js_comments(text: str) -> str:
    """
    粗略剥离 JS 注释（行内 / 块级），避免注释中的禁用词污染测试。
    """
    # 块级注释
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    # 行内注释
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _extract_function_body(text: str, fn_name: str) -> str:
    """
    通过大括号匹配提取指定函数的函数体（不含大括号本身）。
    失败返回空串。
    """
    # 找到函数声明起始
    m = re.search(
        r"function\s+" + re.escape(fn_name) + r"\s*\([^)]*\)\s*\{",
        text,
    )
    if not m:
        return ""
    start = m.end()  # 大括号之后
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    if depth != 0:
        return ""
    return text[start:i - 1]


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


@pytest.fixture(scope="module")
def existing_js_text():
    """保护已有 selfmodel_dashboard.js 不被修改。"""
    return EXISTING_JS_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def existing_css_text():
    """保护已有 selfmodel_dashboard.css 不被修改。"""
    return EXISTING_CSS_FILE.read_text(encoding="utf-8")


# =====================================================================
# A. 文件存在
# =====================================================================
class TestFilesExist:

    def test_js_file_exists(self):
        assert JS_FILE.exists(), f"selfmodel_timeline.js 不存在: {JS_FILE}"

    def test_css_file_exists(self):
        assert CSS_FILE.exists(), f"selfmodel_timeline.css 不存在: {CSS_FILE}"

    def test_html_file_exists(self):
        assert HTML_FILE.exists()

    def test_existing_selfmodel_dashboard_untouched(self, existing_js_text, existing_css_text):
        """已有 selfmodel_dashboard 文件不能被改坏。"""
        # 关键 API 端点应仍然存在
        assert "/admin/api/admin/selfmodel/identity" in existing_js_text
        assert "YuyiSelfModelDashboard" in existing_js_text
        # 关键 CSS 类应仍然存在
        assert ".sm-brief-card" in existing_css_text
        assert ".sm-timeline" in existing_css_text


# =====================================================================
# B. HTML 结构
# =====================================================================
class TestHtmlStructure:

    def test_html_has_timeline_section(self, html_text):
        """HTML 必须包含 selfmodel-timeline-section。"""
        assert 'id="selfmodel-timeline-section"' in html_text

    def test_timeline_section_initially_hidden(self, html_text):
        """timeline section 初始应为 display:none。"""
        m = re.search(r'<section[^>]*id="selfmodel-timeline-section"[^>]*>', html_text)
        assert m, "selfmodel-timeline-section 标签缺失"
        assert 'display:none' in m.group(0), "timeline section 必须初始隐藏"

    def test_html_has_timeline_nav_item(self, html_text):
        """侧栏必须有 selfmodel-timeline nav 入口。"""
        assert 'data-view="selfmodel-timeline"' in html_text
        assert "演化时间线" in html_text

    def test_html_has_smtl_containers(self, html_text):
        """timeline section 必须包含所有必要子节点。"""
        for sid in (
            "smtl-stats",
            "smtl-toolbar",
            "smtl-list",
            "smtl-refresh-btn",
            "smtl-last-updated",
        ):
            assert f'id="{sid}"' in html_text, f"缺少 {sid}"

    def test_html_references_css(self, html_text):
        assert "/admin/css/selfmodel_timeline.css" in html_text

    def test_html_references_js(self, html_text):
        assert "/admin/js/selfmodel_timeline.js" in html_text

    def test_html_does_not_break_existing_selfmodel_section(self, html_text):
        """原有的 selfmodel-section 必须保持完整。"""
        assert 'id="selfmodel-section"' in html_text
        assert "data-tab=\"overview\"" in html_text
        assert "data-tab=\"identity\"" in html_text
        assert "data-tab=\"beliefs\"" in html_text


# =====================================================================
# C. JS — 正确请求 Evolution Timeline API
# =====================================================================
class TestJsApiEndpoints:

    def test_js_requests_evolution_timeline_endpoint(self, js_text):
        """必须请求 /api/admin/selfmodel/evolution_timeline。"""
        assert "/admin/api/admin/selfmodel/evolution_timeline" in js_text

    def test_js_uses_fetch(self, js_text):
        """使用现代 fetch API。"""
        assert "fetch(" in js_text

    def test_js_supports_limit_param(self, js_text):
        """支持 limit 参数。"""
        assert "limit" in js_text

    def test_js_supports_sources_filter_param(self, js_text):
        """支持 sources 过滤参数。"""
        assert "sources" in js_text

    def test_js_supports_start_time_param(self, js_text):
        """支持 start 时间参数。"""
        assert "start" in js_text


# =====================================================================
# D. JS — 严格只读（不允许写入）
# =====================================================================
class TestJsReadOnly:

    def test_no_api_post_call(self, js_text):
        """禁止调用 app.js 的 api_post（防止写入）。"""
        assert "api_post" not in js_text, "selfmodel_timeline.js 不应调用 api_post"

    def test_no_method_post(self, js_text):
        """selfmodel_timeline.js 不应使用 POST（只读视图）。"""
        assert "method: \"POST\"" not in js_text
        assert "method:'POST'" not in js_text

    def test_no_method_put(self, js_text):
        assert "method: \"PUT\"" not in js_text
        assert "method:'PUT'" not in js_text

    def test_no_method_delete(self, js_text):
        assert "method: \"DELETE\"" not in js_text
        assert "method:'DELETE'" not in js_text

    def test_no_method_patch(self, js_text):
        assert "method: \"PATCH\"" not in js_text
        assert "method:'PATCH'" not in js_text

    def test_no_pcr_apply(self, js_text):
        """禁止触发人格修改 / PCR apply。"""
        for forbidden in (
            "apply_pcr",
            "update_state",
            "submitProposal",
            "save_pcr",
            "write_state",
            "submit_proposal",
            "execute_pcr",
            "modify_state",
        ):
            assert forbidden not in js_text, f"selfmodel_timeline.js 不应触发 {forbidden}"

    def test_no_enforce_retention(self, js_text):
        """禁止执行真实 retention（Step 5.4 dry-run 在 health.js 中）。"""
        # Step 5.3 仅展示 timeline，不做 retention
        assert "enforce_all" not in js_text
        assert "enforce_retention" not in js_text


# =====================================================================
# E. JS — 全局对象 & 渲染 API
# =====================================================================
class TestJsGlobalApi:

    def test_exposes_global(self, js_text):
        assert "window.YuyiSelfModelTimeline" in js_text

    def test_global_has_load(self, js_text):
        """全局对象应暴露 load 方法。"""
        assert "load: load" in js_text

    def test_global_has_state(self, js_text):
        """全局对象应暴露内部 state（用于测试）。"""
        assert "_state" in js_text

    def test_init_called_on_dom_ready(self, js_text):
        """必须在 DOMContentLoaded 或 readyState 时 init。"""
        assert "DOMContentLoaded" in js_text or "readyState" in js_text


# =====================================================================
# F. JS — 渲染逻辑
# =====================================================================
class TestJsRenderLogic:

    def test_renders_sources(self, js_text):
        """必须支持 4 种来源：belief / history / reflection / audit。"""
        for s in ("belief", "history", "reflection", "audit"):
            assert s in js_text, f"缺少来源 {s}"

    def test_renders_event_types(self, js_text):
        assert "event_type" in js_text

    def test_renders_timestamp(self, js_text):
        """必须展示时间。"""
        assert "timestamp" in js_text

    def test_renders_proposal_id(self, js_text):
        """必须展示关联 proposal_id。"""
        assert "proposal_id" in js_text

    def test_supports_source_filter(self, js_text):
        """必须支持来源过滤。"""
        assert "sourceFilter" in js_text

    def test_supports_time_range(self, js_text):
        """必须支持时间范围过滤。"""
        assert "rangeFilter" in js_text

    def test_supports_sort(self, js_text):
        """必须支持排序（最近事件优先 / 时间正序）。"""
        assert "sortOrder" in js_text

    def test_supports_detail_expand(self, js_text):
        """必须支持点击事件查看详情。"""
        assert "expandedId" in js_text

    def test_supports_chain_view(self, js_text):
        """必须展示链路（history → proposal → PCR → belief）。"""
        assert "smtl-chain" in js_text

    def test_has_offline_fallback(self, js_text):
        """必须实现 Offline 回退。"""
        assert "smtl-offline" in js_text

    def test_has_loading_state(self, js_text):
        assert "smtl-loading" in js_text

    def test_has_empty_state(self, js_text):
        assert "smtl-empty" in js_text

    def test_has_readonly_tag(self, js_text):
        """只读标识。"""
        assert "smtl-readonly-tag" in js_text


# =====================================================================
# G. CSS — 必要 token
# =====================================================================
class TestCssTokens:

    def test_css_uses_glassmorphism(self, css_text):
        assert "backdrop-filter" in css_text or "glass" in css_text.lower()

    def test_css_has_soft_palette(self, css_text):
        assert "--c-accent" in css_text or "var(--c-" in css_text

    def test_css_has_source_icon_classes(self, css_text):
        """必须为 4 个来源定义 icon 样式。"""
        for cls in (
            ".smtl-item-icon--history",
            ".smtl-item-icon--belief",
            ".smtl-item-icon--reflection",
            ".smtl-item-icon--audit",
        ):
            assert cls in css_text, f"缺少 {cls}"

    def test_css_has_toolbar(self, css_text):
        assert ".smtl-toolbar" in css_text

    def test_css_has_offline_state(self, css_text):
        assert ".smtl-offline" in css_text

    def test_css_has_loading_state(self, css_text):
        assert ".smtl-loading" in css_text

    def test_css_has_empty_state(self, css_text):
        assert ".smtl-empty" in css_text

    def test_css_has_detail_panel(self, css_text):
        assert ".smtl-detail" in css_text

    def test_css_has_chain_view(self, css_text):
        assert ".smtl-chain" in css_text

    def test_css_has_readonly_tag(self, css_text):
        assert ".smtl-readonly-tag" in css_text


# =====================================================================
# H. app.js 集成
# =====================================================================
class TestAppJsIntegration:

    def test_switchview_routes_selfmodel_timeline(self, app_js_text):
        """switchView 必须识别 'selfmodel-timeline'。"""
        assert "view === 'selfmodel-timeline'" in app_js_text

    def test_show_selfmodel_timeline_view_function(self, app_js_text):
        """必须存在 showSelfModelTimelineView 函数。"""
        assert "function showSelfModelTimelineView" in app_js_text

    def test_show_selfmodel_timeline_triggers_load(self, app_js_text):
        """showSelfModelTimelineView 应触发 YuyiSelfModelTimeline.load。"""
        body = _extract_function_body(app_js_text, "showSelfModelTimelineView")
        assert body, "showSelfModelTimelineView 函数体缺失"
        assert "YuyiSelfModelTimeline" in body
        assert ".load" in body

    def test_hide_dashboard_view_hides_smtl(self, app_js_text):
        """hideDashboardView 应隐藏 smtl-view。"""
        m = re.search(
            r"function hideDashboardView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smtl-view" in m.group(1)

    def test_show_dashboard_view_hides_smtl(self, app_js_text):
        """showDashboardView 应隐藏 smtl-view。"""
        m = re.search(
            r"function showDashboardView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smtl-view" in m.group(1)

    def test_show_selfmodel_view_hides_smtl(self, app_js_text):
        """showSelfModelView 应隐藏 smtl-view。"""
        m = re.search(
            r"function showSelfModelView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smtl-view" in m.group(1)

    def test_app_js_does_not_break_existing(self, app_js_text):
        """app.js 仍应支持所有已有视图。"""
        for view in (
            "view === 'dashboard'",
            "view === 'modules'",
            "view === 'config'",
            "view === 'audit'",
            "view === 'cognitive'",
            "view === 'selfmodel'",
            "view === 'autonomous'",
            "view === 'remote'",
            "view === 'agents'",
        ):
            assert view in app_js_text, f"已有视图路由丢失: {view}"


# =====================================================================
# I. 不影响 Runtime（无核心模块引用）
# =====================================================================
class TestNoRuntimeImpact:

    def test_no_runtimecore_import_in_js(self, js_text):
        """前端 JS 不能 import RuntimeCore（剥离注释后检查）。"""
        stripped = _strip_js_comments(js_text)
        assert "RuntimeCore" not in stripped
        assert "runtime_core" not in stripped

    def test_no_self_model_adapter_in_js(self, js_text):
        """前端 JS 不能直接读 SelfModelAdapter（剥离注释后检查）。"""
        stripped = _strip_js_comments(js_text)
        assert "SelfModelAdapter" not in stripped
        assert "self_model_adapter" not in stripped

    def test_no_belief_store_in_js(self, js_text):
        """前端 JS 不能直接读 SelfBeliefStore。"""
        stripped = _strip_js_comments(js_text)
        assert "SelfBeliefStore" not in stripped
        assert "self_belief_store" not in stripped

    def test_only_api_path_access(self, js_text):
        """所有数据访问必须通过 /admin/api/selfmodel/* 路径。"""
        assert "/admin/api" in js_text


# =====================================================================
# J. 向后兼容（已有 selfmodel_dashboard.js 仍可工作）
# =====================================================================
class TestBackwardCompatibility:

    def test_selfmodel_dashboard_js_intact(self, existing_js_text):
        """已有 selfmodel_dashboard.js 不能被破坏。"""
        # 关键全局对象
        assert "YuyiSelfModelDashboard" in existing_js_text
        # 关键 API
        assert "/admin/api/admin/selfmodel/status" in existing_js_text
        assert "/admin/api/admin/selfmodel/identity" in existing_js_text
        assert "/admin/api/admin/selfmodel/beliefs" in existing_js_text

    def test_selfmodel_dashboard_css_intact(self, existing_css_text):
        """已有 selfmodel_dashboard.css 不能被破坏。"""
        assert ".selfmodel-view" in existing_css_text
        assert ".sm-tabs" in existing_css_text
        assert ".sm-brief-card" in existing_css_text
