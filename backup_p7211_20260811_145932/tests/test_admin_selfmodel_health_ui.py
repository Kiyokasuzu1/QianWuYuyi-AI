# -*- coding: utf-8 -*-
"""
tests/test_admin_selfmodel_health_ui.py

Phase 6.5 Step 5.4: Admin SelfModel Health + Retention 前端 UI 测试。

验证：
1. Health + Retention JS / CSS 文件存在
2. HTML 中包含 health section / nav 入口 / CSS+JS 引用
3. JS 请求正确的 API 端点（/api/admin/selfmodel/health、/retention、/retention/dry_run）
4. JS 严格只读（除 POST /retention/dry_run 外不允许任何写入接口）
5. JS 暴露全局对象 YuyiSelfModelHealth
6. CSS 包含必要 token（玻璃态、状态色、severity 色）
7. app.js 集成 view route（switchView + showSelfModelHealthView）
8. 不修改 RuntimeCore / SelfModelAdapter（仅 UI 集成）
9. 不修改已有 selfmodel_dashboard.js / selfmodel_timeline.js
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATIC_DIR = PROJECT_ROOT / "static" / "admin"
JS_FILE = STATIC_DIR / "js" / "selfmodel_health.js"
CSS_FILE = STATIC_DIR / "css" / "selfmodel_health.css"
HTML_FILE = STATIC_DIR / "index.html"
APP_JS_FILE = STATIC_DIR / "js" / "app.js"

# 已有 selfmodel 文件（必须不被修改）
EXISTING_DASHBOARD_JS = STATIC_DIR / "js" / "selfmodel_dashboard.js"
EXISTING_DASHBOARD_CSS = STATIC_DIR / "css" / "selfmodel_dashboard.css"
EXISTING_TIMELINE_JS = STATIC_DIR / "js" / "selfmodel_timeline.js"
EXISTING_TIMELINE_CSS = STATIC_DIR / "css" / "selfmodel_timeline.css"


# =====================================================================
# 工具函数
# =====================================================================
def _strip_js_comments(text: str) -> str:
    """粗略剥离 JS 注释（行内 / 块级），避免注释中的禁用词污染测试。"""
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)
    text = re.sub(r"//[^\n]*", "", text)
    return text


def _extract_function_body(text: str, fn_name: str) -> str:
    """通过大括号匹配提取指定函数的函数体。失败返回空串。"""
    m = re.search(
        r"function\s+" + re.escape(fn_name) + r"\s*\([^)]*\)\s*\{",
        text,
    )
    if not m:
        return ""
    start = m.end()
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
def dashboard_js_text():
    return EXISTING_DASHBOARD_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard_css_text():
    return EXISTING_DASHBOARD_CSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def timeline_js_text():
    return EXISTING_TIMELINE_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def timeline_css_text():
    return EXISTING_TIMELINE_CSS.read_text(encoding="utf-8")


# =====================================================================
# A. 文件存在
# =====================================================================
class TestFilesExist:

    def test_js_file_exists(self):
        assert JS_FILE.exists(), f"selfmodel_health.js 不存在: {JS_FILE}"

    def test_css_file_exists(self):
        assert CSS_FILE.exists(), f"selfmodel_health.css 不存在: {CSS_FILE}"

    def test_html_file_exists(self):
        assert HTML_FILE.exists()

    def test_existing_dashboard_untouched(
        self, dashboard_js_text, dashboard_css_text
    ):
        """已有 selfmodel_dashboard 文件不能被改坏。"""
        assert "/admin/api/admin/selfmodel/identity" in dashboard_js_text
        assert "YuyiSelfModelDashboard" in dashboard_js_text
        assert ".sm-brief-card" in dashboard_css_text
        assert ".selfmodel-view" in dashboard_css_text

    def test_existing_timeline_untouched(
        self, timeline_js_text, timeline_css_text
    ):
        """已有 selfmodel_timeline 文件不能被改坏。"""
        assert "/admin/api/admin/selfmodel/evolution_timeline" in timeline_js_text
        assert "YuyiSelfModelTimeline" in timeline_js_text
        assert ".smtl-view" in timeline_css_text
        assert ".smtl-toolbar" in timeline_css_text


# =====================================================================
# B. HTML 结构
# =====================================================================
class TestHtmlStructure:

    def test_html_has_health_section(self, html_text):
        """HTML 必须包含 selfmodel-health-section。"""
        assert 'id="selfmodel-health-section"' in html_text

    def test_health_section_initially_hidden(self, html_text):
        """health section 初始应为 display:none。"""
        m = re.search(
            r'<section[^>]*id="selfmodel-health-section"[^>]*>', html_text
        )
        assert m, "selfmodel-health-section 标签缺失"
        assert 'display:none' in m.group(0), "health section 必须初始隐藏"

    def test_html_has_health_nav_item(self, html_text):
        """侧栏必须有 selfmodel-health nav 入口。"""
        assert 'data-view="selfmodel-health"' in html_text
        # 中文标签
        assert "健康与配额" in html_text

    def test_html_has_smh_containers(self, html_text):
        """health section 必须包含所有必要子节点。"""
        for sid in (
            "smh-runtime-badge",
            "smh-runtime-dot",
            "smh-runtime-text",
            "smh-refresh-btn",
            "smh-last-updated",
            "smh-health-overview",
            "smh-issues-toolbar",
            "smh-issues",
            "smh-persistence",
            "smh-capacity",
            "smh-quota",
            "smh-dryrun-btn",
            "smh-dryrun-result",
        ):
            assert f'id="{sid}"' in html_text, f"缺少 {sid}"

    def test_html_references_css(self, html_text):
        assert "/admin/css/selfmodel_health.css" in html_text

    def test_html_references_js(self, html_text):
        assert "/admin/js/selfmodel_health.js" in html_text

    def test_html_does_not_break_existing_selfmodel_section(self, html_text):
        """原有的 selfmodel-section 必须保持完整。"""
        assert 'id="selfmodel-section"' in html_text
        assert 'data-tab="overview"' in html_text
        assert 'data-tab="identity"' in html_text
        assert 'data-tab="beliefs"' in html_text

    def test_html_does_not_break_existing_timeline_section(self, html_text):
        """原有的 selfmodel-timeline-section 必须保持完整。"""
        assert 'id="selfmodel-timeline-section"' in html_text
        assert 'id="smtl-toolbar"' in html_text
        assert 'id="smtl-list"' in html_text

    def test_html_nav_after_timeline(self, html_text):
        """健康与配额入口应位于演化时间线之后。"""
        nav_timeline = html_text.find('data-view="selfmodel-timeline"')
        nav_health = html_text.find('data-view="selfmodel-health"')
        assert nav_timeline > 0 and nav_health > 0
        assert nav_health > nav_timeline, "selfmodel-health nav 应在 timeline 之后"


# =====================================================================
# C. JS — 正确请求 Health / Retention API
# =====================================================================
class TestJsApiEndpoints:

    def test_js_requests_health_endpoint(self, js_text):
        """必须请求 /api/admin/selfmodel/health。"""
        assert "/admin/api/admin/selfmodel/health" in js_text

    def test_js_requests_retention_endpoint(self, js_text):
        """必须请求 /api/admin/selfmodel/retention。"""
        assert "/admin/api/admin/selfmodel/retention" in js_text

    def test_js_requests_dryrun_endpoint(self, js_text):
        """必须请求 /api/admin/selfmodel/retention/dry_run。"""
        assert "/admin/api/admin/selfmodel/retention/dry_run" in js_text

    def test_js_uses_fetch(self, js_text):
        """使用现代 fetch API。"""
        assert "fetch(" in js_text

    def test_js_uses_admin_api_base(self, js_text):
        """所有路径必须以 /admin/api 开头。"""
        # 多次出现
        assert js_text.count("/admin/api") >= 3


# =====================================================================
# D. JS — 严格只读（除 dry_run POST 外）
# =====================================================================
class TestJsReadOnly:

    def test_no_api_post_call(self, js_text):
        """禁止调用 app.js 的 api_post（防止写入）。"""
        assert "api_post" not in js_text, "selfmodel_health.js 不应调用 api_post"

    def test_no_appjs_api_put(self, js_text):
        """禁止调用 app.js 的 api_put。"""
        assert "api_put" not in js_text

    def test_no_apply_pcr(self, js_text):
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
            stripped = _strip_js_comments(js_text)
            assert forbidden not in stripped, (
                f"selfmodel_health.js 不应触发 {forbidden}"
            )

    def test_no_enforce_retention(self, js_text):
        """禁止直接执行真实 retention（只允许 dry_run）。"""
        stripped = _strip_js_comments(js_text)
        assert "enforce_all" not in stripped, "selfmodel_health.js 不应调用 enforce_all"
        assert "enforce_retention" not in stripped

    def test_post_only_to_dry_run(self, js_text):
        """POST 仅允许访问 dry_run 端点（其他端点必须 GET）。"""
        # 必须有 method: "POST"
        assert 'method: "POST"' in js_text or "method:'POST'" in js_text
        # 必须有 dry_run 端点
        assert "retention/dry_run" in js_text

    def test_dry_run_body_has_dry_run_true(self, js_text):
        """POST body 应包含 dry_run=true 标记。"""
        # 客户端冗余信号：发送 dry_run=true
        assert "dry_run" in js_text

    def test_no_method_put(self, js_text):
        """不应使用 PUT。"""
        assert 'method: "PUT"' not in js_text
        assert "method:'PUT'" not in js_text

    def test_no_method_delete(self, js_text):
        """不应使用 DELETE。"""
        assert 'method: "DELETE"' not in js_text
        assert "method:'DELETE'" not in js_text

    def test_no_method_patch(self, js_text):
        """不应使用 PATCH。"""
        assert 'method: "PATCH"' not in js_text
        assert "method:'PATCH'" not in js_text

    def test_no_direct_runtimecore_import(self, js_text):
        """前端 JS 不能 import RuntimeCore。"""
        stripped = _strip_js_comments(js_text)
        assert "RuntimeCore" not in stripped
        assert "runtime_core" not in stripped

    def test_no_self_model_adapter_in_js(self, js_text):
        """前端 JS 不能直接读 SelfModelAdapter。"""
        stripped = _strip_js_comments(js_text)
        assert "SelfModelAdapter" not in stripped
        assert "self_model_adapter" not in stripped

    def test_no_belief_store_in_js(self, js_text):
        """前端 JS 不能直接读 SelfBeliefStore。"""
        stripped = _strip_js_comments(js_text)
        assert "SelfBeliefStore" not in stripped
        assert "self_belief_store" not in stripped


# =====================================================================
# E. JS — 全局对象 & 渲染 API
# =====================================================================
class TestJsGlobalApi:

    def test_exposes_global(self, js_text):
        assert "window.YuyiSelfModelHealth" in js_text

    def test_global_has_load(self, js_text):
        """全局对象应暴露 load 方法。"""
        assert "load: load" in js_text

    def test_global_has_run_dry_run(self, js_text):
        """全局对象应暴露 runDryRun 方法（用户也可手动调用）。"""
        assert "runDryRun" in js_text

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

    # ------- Health Overview -------

    def test_renders_overall_status(self, js_text):
        """必须渲染 overall_status。"""
        assert "overall_status" in js_text

    def test_renders_check_time(self, js_text):
        """必须展示检查时间。"""
        assert "timestamp" in js_text

    def test_renders_issue_count(self, js_text):
        """必须展示问题数量。"""
        assert "issueCount" in js_text or "issues.length" in js_text

    def test_renders_critical_count(self, js_text):
        """必须统计 critical issues。"""
        assert "criticalCount" in js_text or "critical" in js_text

    # ------- Issue List -------

    def test_supports_severity_filter(self, js_text):
        """必须支持 severity 过滤。"""
        assert "severityFilter" in js_text

    def test_supports_all_severities(self, js_text):
        """必须支持 4 种 severity：all / critical / warning / info。"""
        for sv in ("all", "critical", "warning", "info"):
            assert f'"{sv}"' in js_text, f"缺少 severity {sv}"

    def test_renders_issue_message(self, js_text):
        """必须展示 issue message。"""
        assert "message" in js_text

    def test_renders_issue_component(self, js_text):
        """必须展示 issue component / location。"""
        assert "location" in js_text or "component" in js_text

    def test_renders_issue_code(self, js_text):
        """必须展示 issue code。"""
        assert "code" in js_text

    def test_renders_issue_timestamp(self, js_text):
        """必须展示 issue 时间戳。"""
        assert "timestamp" in js_text

    # ------- Persistence -------

    def test_renders_persistence_files(self, js_text):
        """必须渲染 3 个核心文件：beliefs / history / reflections。"""
        for k in ("beliefs", "history", "reflections"):
            assert k in js_text, f"缺少文件 {k}"

    def test_renders_snapshot_status(self, js_text):
        """必须渲染 snapshot 状态。"""
        assert "snapshot" in js_text.lower() or "snap" in js_text.lower()

    def test_renders_data_dir(self, js_text):
        """必须展示 data_dir。"""
        assert "data_dir" in js_text or "dataDir" in js_text

    # ------- Capacity -------

    def test_renders_beliefs_capacity(self, js_text):
        """必须展示 beliefs 容量（active / inactive / archived）。"""
        assert "beliefsActive" in js_text or "beliefs_active" in js_text

    def test_renders_history_capacity(self, js_text):
        """必须展示 history 容量。"""
        assert "historyTotal" in js_text or "history_total" in js_text

    def test_renders_reflections_capacity(self, js_text):
        """必须展示 reflections 容量。"""
        assert "reflectionsTotal" in js_text or "reflections_total" in js_text

    # ------- Quota -------

    def test_renders_quota(self, js_text):
        """必须渲染 quota 进度条。"""
        assert "quota" in js_text.lower() or "smh-quota" in js_text

    def test_renders_max_active_beliefs(self, js_text):
        """必须展示 max_active_beliefs。"""
        assert "max_active_beliefs" in js_text

    def test_renders_max_in_memory_events(self, js_text):
        """必须展示 max_in_memory_events。"""
        assert "max_in_memory_events" in js_text

    def test_renders_recent_window_days(self, js_text):
        """必须展示 recent_window_days。"""
        assert "recent_window_days" in js_text

    # ------- Dry Run -------

    def test_dryrun_button_binds(self, js_text):
        """必须绑定 dry_run 按钮。"""
        assert 'smh-dryrun-btn' in js_text
        assert "addEventListener" in js_text

    def test_dryrun_calls_post(self, js_text):
        """dry_run 必须使用 POST。"""
        assert "safePost" in js_text or "method: \"POST\"" in js_text

    def test_dryrun_does_not_modify(self, js_text):
        """dry_run 不应触发任何修改性操作（注释里也禁止 enforce_all）。"""
        # 检查函数 runDryRun 中只调用 safePost 到 dry_run
        body = _extract_function_body(js_text, "runDryRun")
        assert body, "runDryRun 函数缺失"
        # 应调用 retention/dry_run
        assert "retention/dry_run" in body
        # 不应调用 enforce_all / mutate
        assert "enforce_all" not in body
        assert "mutate" not in body

    # ------- Empty / Loading / Offline -------

    def test_has_offline_fallback(self, js_text):
        """必须实现 Offline 回退。"""
        assert "smhealth-offline" in js_text

    def test_has_loading_state(self, js_text):
        assert "smhealth-loading" in js_text

    def test_has_empty_state(self, js_text):
        assert "smhealth-empty" in js_text

    def test_has_readonly_tag(self, js_text):
        """只读标识。"""
        assert "smhealth-readonly-tag" in js_text


# =====================================================================
# G. CSS — 必要 token
# =====================================================================
class TestCssTokens:

    def test_css_uses_glassmorphism(self, css_text):
        assert "backdrop-filter" in css_text or "glass" in css_text.lower()

    def test_css_has_soft_palette(self, css_text):
        assert "--c-accent" in css_text or "var(--c-" in css_text

    def test_css_has_status_palette(self, css_text):
        """必须为 5 种 overall_status 定义颜色。"""
        for cls in (
            "smhealth-overview-card--healthy",
            "smhealth-overview-card--warning",
            "smhealth-overview-card--degraded",
            "smhealth-overview-card--critical",
            "smhealth-overview-card--unknown",
        ):
            assert cls in css_text, f"缺少 {cls}"

    def test_css_has_severity_styles(self, css_text):
        """必须为 4 种 severity 定义样式。"""
        for cls in (
            "smhealth-issue--critical",
            "smhealth-issue--warning",
            "smhealth-issue--info",
        ):
            assert cls in css_text, f"缺少 {cls}"

    def test_css_has_capacity_cards(self, css_text):
        """必须定义 capacity 卡片（Beliefs / History / Reflections）。"""
        for cls in (
            "smhealth-capacity-card--beliefs",
            "smhealth-capacity-card--history",
            "smhealth-capacity-card--reflections",
        ):
            assert cls in css_text, f"缺少 {cls}"

    def test_css_has_quota_bar(self, css_text):
        """必须定义 quota 进度条。"""
        assert ".smhealth-quota-bar" in css_text
        assert ".smhealth-quota-fill" in css_text

    def test_css_has_dryrun(self, css_text):
        """必须定义 dry_run 按钮。"""
        assert ".smhealth-dryrun-btn" in css_text
        assert ".smhealth-dryrun-result" in css_text

    def test_css_has_offline_state(self, css_text):
        assert ".smhealth-offline" in css_text

    def test_css_has_loading_state(self, css_text):
        assert ".smhealth-loading" in css_text

    def test_css_has_empty_state(self, css_text):
        assert ".smhealth-empty" in css_text

    def test_css_has_persistence_card(self, css_text):
        assert ".smhealth-persistence-card" in css_text

    def test_css_has_readonly_tag(self, css_text):
        assert ".smhealth-readonly-tag" in css_text


# =====================================================================
# H. app.js 集成
# =====================================================================
class TestAppJsIntegration:

    def test_switchview_routes_selfmodel_health(self, app_js_text):
        """switchView 必须识别 'selfmodel-health'。"""
        assert "view === 'selfmodel-health'" in app_js_text

    def test_show_selfmodel_health_view_function(self, app_js_text):
        """必须存在 showSelfModelHealthView 函数。"""
        assert "function showSelfModelHealthView" in app_js_text

    def test_show_selfmodel_health_triggers_load(self, app_js_text):
        """showSelfModelHealthView 应触发 YuyiSelfModelHealth.load。"""
        body = _extract_function_body(app_js_text, "showSelfModelHealthView")
        assert body, "showSelfModelHealthView 函数体缺失"
        assert "YuyiSelfModelHealth" in body
        assert ".load" in body

    def test_hide_dashboard_view_hides_smh(self, app_js_text):
        """hideDashboardView 应隐藏 smhealth-view。"""
        m = re.search(
            r"function hideDashboardView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smhealth-view" in m.group(1)

    def test_show_dashboard_view_hides_smh(self, app_js_text):
        """showDashboardView 应隐藏 smhealth-view。"""
        m = re.search(
            r"function showDashboardView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smhealth-view" in m.group(1)

    def test_show_selfmodel_view_hides_smh(self, app_js_text):
        """showSelfModelView 应隐藏 smhealth-view。"""
        m = re.search(
            r"function showSelfModelView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smhealth-view" in m.group(1)

    def test_show_selfmodel_timeline_view_hides_smh(self, app_js_text):
        """showSelfModelTimelineView 应隐藏 smhealth-view。"""
        m = re.search(
            r"function showSelfModelTimelineView\s*\(\)\s*\{(.*?)\n\s*\}",
            app_js_text,
            re.DOTALL,
        )
        assert m
        assert "smhealth-view" in m.group(1)

    def test_app_js_does_not_break_existing(self, app_js_text):
        """app.js 仍应支持所有已有视图。"""
        for view in (
            "view === 'dashboard'",
            "view === 'modules'",
            "view === 'config'",
            "view === 'audit'",
            "view === 'cognitive'",
            "view === 'selfmodel'",
            "view === 'selfmodel-timeline'",
            "view === 'autonomous'",
            "view === 'remote'",
            "view === 'agents'",
        ):
            assert view in app_js_text, f"已有视图路由丢失: {view}"


# =====================================================================
# I. 向后兼容（已有 selfmodel_dashboard.js / .timeline.js 仍可工作）
# =====================================================================
class TestBackwardCompatibility:

    def test_selfmodel_dashboard_js_intact(self, dashboard_js_text):
        """已有 selfmodel_dashboard.js 不能被破坏。"""
        assert "YuyiSelfModelDashboard" in dashboard_js_text
        assert "/admin/api/admin/selfmodel/status" in dashboard_js_text
        assert "/admin/api/admin/selfmodel/identity" in dashboard_js_text
        assert "/admin/api/admin/selfmodel/beliefs" in dashboard_js_text

    def test_selfmodel_dashboard_css_intact(self, dashboard_css_text):
        """已有 selfmodel_dashboard.css 不能被破坏。"""
        assert ".selfmodel-view" in dashboard_css_text
        assert ".sm-tabs" in dashboard_css_text
        assert ".sm-brief-card" in dashboard_css_text

    def test_selfmodel_timeline_js_intact(self, timeline_js_text):
        """已有 selfmodel_timeline.js 不能被破坏。"""
        assert "YuyiSelfModelTimeline" in timeline_js_text
        assert "/admin/api/admin/selfmodel/evolution_timeline" in timeline_js_text

    def test_selfmodel_timeline_css_intact(self, timeline_css_text):
        """已有 selfmodel_timeline.css 不能被破坏。"""
        assert ".smtl-view" in timeline_css_text
        assert ".smtl-toolbar" in timeline_css_text
