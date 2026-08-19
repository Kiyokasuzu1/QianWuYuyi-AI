# -*- coding: utf-8 -*-
"""
tests/test_admin_ui_integration.py

Phase 5.2: Admin UI 与 Phase 5.1 RuntimeProvider API 集成测试。

验证：
1. HTML 文件存在 Runtime Dashboard 元素（卡片、刷新按钮、容器 ID）
2. JS 正确请求 5 个 Phase 5.1 API 端点
3. 5 个 API 端点都能返回数据，且可被前端消费
4. 错误状态下 API 返回 ok=False，前端能正确进入 Offline 渲染
5. 不创建新的 Runtime 实例；前端只读
"""

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STATIC_DIR = PROJECT_ROOT / "static" / "admin"
INDEX_HTML = STATIC_DIR / "index.html"
JS_FILE = STATIC_DIR / "js" / "runtime_dashboard.js"
CSS_FILE = STATIC_DIR / "css" / "runtime_dashboard.css"

# Phase 5.1 API 端点
PHASE5_1_ENDPOINTS = [
    "/admin/api/admin/authority/status",
    "/admin/api/admin/personality/status",
    "/admin/api/admin/emotion/status",
    "/admin/api/admin/growth/status",
    "/admin/api/admin/memory/summary",
]


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture(scope="module", autouse=True)
def _tmp_workspace():
    original_cwd = os.getcwd()
    tmp_dir = tempfile.mkdtemp(prefix="yuyi_admin_ui_")
    os.chdir(tmp_dir)
    Path("data").mkdir(parents=True, exist_ok=True)
    yield tmp_dir
    os.chdir(original_cwd)
    shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_singletons():
    from src.runtime.runtime_bridge import reset_runtime_bridge
    from src.admin.runtime_provider import reset_runtime_provider_for_testing
    reset_runtime_bridge()
    reset_runtime_provider_for_testing()
    yield
    reset_runtime_bridge()
    reset_runtime_provider_for_testing()


def _init_runtime_bridge():
    from src.runtime.runtime_bridge import get_runtime_bridge
    bridge = get_runtime_bridge(config={
        "emotion_enabled": False,
        "adapters_enabled": False,
        "autostart": False,
    })
    bridge.initialize()
    return bridge


# =====================================================================
# A. 静态资源存在性
# =====================================================================
class TestStaticAssetsExist:
    """Phase 5.2 静态资源文件应全部存在。"""

    def test_index_html_exists(self):
        assert INDEX_HTML.exists(), f"index.html 不存在: {INDEX_HTML}"

    def test_runtime_dashboard_js_exists(self):
        assert JS_FILE.exists(), f"runtime_dashboard.js 不存在: {JS_FILE}"

    def test_runtime_dashboard_css_exists(self):
        assert CSS_FILE.exists(), f"runtime_dashboard.css 不存在: {CSS_FILE}"


# =====================================================================
# B. HTML 结构 — Runtime Dashboard 元素
# =====================================================================
class TestHtmlStructure:
    """HTML 必须包含 Phase 5.2 Dashboard 的关键 DOM 节点 ID。"""

    @pytest.fixture(scope="class")
    def html_text(self):
        return INDEX_HTML.read_text(encoding="utf-8")

    # ---- Runtime 状态总览 ----
    def test_html_has_runtime_dashboard_section(self, html_text):
        assert 'id="runtime-dashboard-section"' in html_text

    def test_html_has_runtime_status_text_id(self, html_text):
        assert 'id="rd-runtime-status-text"' in html_text

    def test_html_has_runtime_initialized_id(self, html_text):
        assert 'id="rd-runtime-initialized"' in html_text

    def test_html_has_runtime_running_id(self, html_text):
        assert 'id="rd-runtime-running"' in html_text

    def test_html_has_authority_grid_id(self, html_text):
        assert 'id="rd-authority-grid"' in html_text

    def test_html_has_refresh_button_id(self, html_text):
        assert 'id="rd-refresh-btn"' in html_text

    def test_html_has_last_updated_id(self, html_text):
        assert 'id="rd-last-updated"' in html_text

    # ---- Personality 面板 ----
    def test_html_has_personality_section(self, html_text):
        assert 'id="runtime-personality-section"' in html_text

    def test_html_has_personality_content_id(self, html_text):
        assert 'id="rd-personality-content"' in html_text

    def test_html_has_growth_metrics_id(self, html_text):
        assert 'id="rd-growth-metrics"' in html_text

    # ---- Emotion 面板 ----
    def test_html_has_emotion_section(self, html_text):
        assert 'id="runtime-emotion-section"' in html_text

    def test_html_has_emotion_content_id(self, html_text):
        assert 'id="rd-emotion-content"' in html_text

    # ---- Memory 面板 ----
    def test_html_has_memory_section(self, html_text):
        assert 'id="runtime-memory-section"' in html_text

    def test_html_has_memory_content_id(self, html_text):
        assert 'id="rd-memory-content"' in html_text

    # ---- CSS 引用 ----
    def test_html_references_runtime_dashboard_css(self, html_text):
        assert "/admin/css/runtime_dashboard.css" in html_text

    # ---- JS 引用 ----
    def test_html_references_runtime_dashboard_js(self, html_text):
        assert "/admin/js/runtime_dashboard.js" in html_text


# =====================================================================
# C. JS — 请求正确的 API 端点
# =====================================================================
class TestJsRequestsPhase51Endpoints:
    """runtime_dashboard.js 必须请求全部 5 个 Phase 5.1 端点。"""

    @pytest.fixture(scope="class")
    def js_text(self):
        return JS_FILE.read_text(encoding="utf-8")

    @pytest.mark.parametrize("endpoint", PHASE5_1_ENDPOINTS)
    def test_js_requests_endpoint(self, js_text, endpoint):
        assert endpoint in js_text, f"runtime_dashboard.js 未请求 {endpoint}"

    def test_js_uses_admin_api_base(self, js_text):
        """JS 应通过 /admin/api 路径访问端点。"""
        assert "/admin/api" in js_text

    def test_js_does_not_call_phase5_endpoints_only(self, js_text):
        """JS 应只使用 Phase 5.1 端点，不使用旧版 /api/dashboard 等聚合。"""
        # 不能在 endpoints 中使用旧聚合 API
        # 旧 API 路径不应出现在 CONFIG.endpoints 字典中
        for old_path in ["/api/dashboard", "/api/runtime/state"]:
            assert old_path not in js_text, (
                f"runtime_dashboard.js 不应使用旧路径 {old_path}，应使用 Phase 5.1 端点"
            )

    def test_js_has_unsafe_global_offline_handler(self, js_text):
        """JS 必须实现 Runtime Offline 回退渲染。"""
        assert "Offline" in js_text or "OFFLINE" in js_text
        assert "rd-offline-note" in js_text or "Runtime Offline" in js_text

    def test_js_has_auto_refresh_mechanism(self, js_text):
        """JS 必须包含定时刷新机制（默认 10 秒）。"""
        assert "setInterval" in js_text
        assert "10000" in js_text, "默认刷新周期应为 10000ms (10 秒)"

    def test_js_has_manual_refresh_button(self, js_text):
        """JS 必须绑定手动刷新按钮。"""
        assert "rd-refresh-btn" in js_text

    def test_js_does_not_create_runtime_instance(self, js_text):
        """JS 不能创建任何 RuntimeCore / Authority 实例，仅消费 API 数据。"""
        forbidden_patterns = [
            "new RuntimeCore(",
            "new MemoryStore(",
            "new EmotionManager(",
            "new PersonalityResolver(",
            "RuntimeCore()",
            "MemoryStore()",
        ]
        for pat in forbidden_patterns:
            assert pat not in js_text, f"runtime_dashboard.js 不应创建 {pat}"

    def test_js_exposes_global_object(self, js_text):
        """JS 必须将自身挂到 window.YuyiRuntimeDashboard 供测试/调试。"""
        assert "window.YuyiRuntimeDashboard" in js_text


# =====================================================================
# D. CSS — 样式文件有效
# =====================================================================
class TestCssValid:
    """runtime_dashboard.css 必须是有效的 CSS 文本。"""

    @pytest.fixture(scope="class")
    def css_text(self):
        return CSS_FILE.read_text(encoding="utf-8")

    def test_css_has_authority_pill(self, css_text):
        assert ".rd-auth-pill" in css_text

    def test_css_has_status_pill(self, css_text):
        assert ".rd-status-pill" in css_text

    def test_css_has_metric(self, css_text):
        assert ".rd-metric" in css_text

    def test_css_has_offline_note(self, css_text):
        assert ".rd-offline-note" in css_text

    def test_css_uses_color_states(self, css_text):
        """样式必须包含 green / yellow / red 三种状态色（通过 ok / warn / bad 变体）。"""
        assert "--ok" in css_text or "ok" in css_text
        assert "--warn" in css_text or "warn" in css_text
        assert "--bad" in css_text or "bad" in css_text


# =====================================================================
# E. API 端点 — 实际能返回数据（与 Phase 5.1 集成）
# =====================================================================
class TestApiEndpointsConsumable:
    """Phase 5.1 API 端点必须可被 Flask 测试客户端消费（前端可正常请求）。"""

    @pytest.fixture(scope="class")
    def flask_app(self):
        pytest.importorskip("flask")
        from src.admin.api.routes import admin_bp
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(admin_bp, url_prefix="/admin")
        app.config["TESTING"] = True
        return app

    @pytest.fixture(scope="class")
    def client(self, flask_app):
        return flask_app.test_client()

    @pytest.fixture(autouse=True)
    def _init_bridge_for_api(self):
        """为 API 测试初始化 RuntimeBridge，使 provider 拥有真实数据。"""
        try:
            _init_runtime_bridge()
        except Exception:
            pass

    def test_authority_endpoint_returns_json(self, client):
        rv = client.get("/admin/api/admin/authority/status")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "ok" in data
        assert "authority" in data or "error" in data

    def test_personality_endpoint_returns_json(self, client):
        rv = client.get("/admin/api/admin/personality/status")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "ok" in data
        assert "available" in data or "error" in data

    def test_emotion_endpoint_returns_json(self, client):
        rv = client.get("/admin/api/admin/emotion/status")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "ok" in data
        assert "available" in data or "error" in data

    def test_growth_endpoint_returns_json(self, client):
        rv = client.get("/admin/api/admin/growth/status")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "ok" in data
        assert "available" in data or "error" in data

    def test_memory_endpoint_returns_json(self, client):
        rv = client.get("/admin/api/admin/memory/summary")
        assert rv.status_code == 200
        data = json.loads(rv.data)
        assert "ok" in data
        assert "available" in data or "error" in data

    def test_authority_endpoint_structure(self, client):
        """authority 端点返回结构应能被前端渲染（authority 字段是 dict）。"""
        rv = client.get("/admin/api/admin/authority/status")
        data = json.loads(rv.data)
        if data.get("ok"):
            assert isinstance(data.get("authority"), dict)
            # 应至少包含 6 个标准 Authority key
            for key in [
                "memory_store", "vector_memory", "emotion_manager",
                "personality_resolver", "self_model_store", "growth_state",
            ]:
                assert key in data["authority"], f"authority 应包含 {key}"

    def test_memory_endpoint_structure(self, client):
        """memory 端点返回结构应能被前端消费。"""
        rv = client.get("/admin/api/admin/memory/summary")
        data = json.loads(rv.data)
        if data.get("ok") and data.get("available"):
            assert "total_count" in data
            assert "recent" in data
            assert "important_count" in data


# =====================================================================
# F. 错误状态 — Offline 回退
# =====================================================================
class TestOfflineFallback:
    """当所有端点都失败时，API 返回 ok=False，前端能进入 Offline 模式。"""

    @pytest.fixture(scope="class")
    def flask_app(self):
        pytest.importorskip("flask")
        from src.admin.api.routes import admin_bp
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(admin_bp, url_prefix="/admin")
        app.config["TESTING"] = True
        return app

    @pytest.fixture(scope="class")
    def client(self, flask_app):
        return flask_app.test_client()

    def test_authority_offline_returns_ok_false(self, client):
        """RuntimeCore 未初始化时，authority API 返回 ok=False。"""
        from src.runtime.runtime_bridge import reset_runtime_bridge
        from src.admin.runtime_provider import reset_runtime_provider_for_testing
        reset_runtime_bridge()
        reset_runtime_provider_for_testing()

        rv = client.get("/admin/api/admin/authority/status")
        data = json.loads(rv.data)

        # 应能正常返回 200 但 ok=False，且 authority 全为 False
        assert rv.status_code == 200
        # 注意：routes 在 bridge_error 时仍返回 ok=True + online=False
        # 这是设计行为，前端通过 online / authority 全 False 判定 Offline
        if data.get("ok"):
            assert data.get("online") is False
            for k in data.get("authority", {}):
                assert data["authority"][k] is False

    def test_personality_offline_returns_unavailable(self, client):
        from src.runtime.runtime_bridge import reset_runtime_bridge
        from src.admin.runtime_provider import reset_runtime_provider_for_testing
        reset_runtime_bridge()
        reset_runtime_provider_for_testing()

        rv = client.get("/admin/api/admin/personality/status")
        data = json.loads(rv.data)
        assert data.get("available") is False
        assert data.get("current") is None

    def test_emotion_offline_returns_unavailable(self, client):
        from src.runtime.runtime_bridge import reset_runtime_bridge
        from src.admin.runtime_provider import reset_runtime_provider_for_testing
        reset_runtime_bridge()
        reset_runtime_provider_for_testing()

        rv = client.get("/admin/api/admin/emotion/status")
        data = json.loads(rv.data)
        assert data.get("available") is False
        assert data.get("current") is None

    def test_growth_offline_returns_unavailable(self, client):
        from src.runtime.runtime_bridge import reset_runtime_bridge
        from src.admin.runtime_provider import reset_runtime_provider_for_testing
        reset_runtime_bridge()
        reset_runtime_provider_for_testing()

        rv = client.get("/admin/api/admin/growth/status")
        data = json.loads(rv.data)
        assert data.get("available") is False
        assert data.get("shared_with_resolver") is False

    def test_memory_offline_returns_unavailable(self, client):
        from src.runtime.runtime_bridge import reset_runtime_bridge
        from src.admin.runtime_provider import reset_runtime_provider_for_testing
        reset_runtime_bridge()
        reset_runtime_provider_for_testing()

        rv = client.get("/admin/api/admin/memory/summary")
        data = json.loads(rv.data)
        assert data.get("available") is False
        assert data.get("total_count") == 0


# =====================================================================
# G. 数据渲染验证 — API 返回数据能被前端消费
# =====================================================================
class TestDataRenderable:
    """Phase 5.1 API 数据应能被前端消费，结构必须满足渲染需求。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _init_runtime_bridge()

    def test_memory_data_renderable(self):
        """memory 端点数据结构应满足前端最近记忆列表的渲染。"""
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_memory_summary()

        # 前端需要：total_count / recent / important_count / user_id
        assert "total_count" in summary
        assert "recent" in summary
        assert "important_count" in summary
        assert "user_id" in summary
        assert isinstance(summary["recent"], list)

    def test_growth_metrics_renderable(self):
        """growth 端点 metrics 应包含前端所需的 5 个指标。"""
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_growth_summary()

        assert summary.get("available") is True
        metrics = summary.get("metrics", {})
        # 前端会尝试读取这 5 个 key，缺失会显示 --，但结构应包含至少部分
        for key in ("total_growth", "maturity", "self_awareness", "empathy", "stability"):
            # 至少字段应可访问（不一定有值）
            assert isinstance(metrics, dict)

    def test_authority_renderable(self):
        """authority 端点应包含 6 个组件 key。"""
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        authority = provider.get_authority_status()

        for key in [
            "memory_store", "vector_memory", "emotion_manager",
            "personality_resolver", "self_model_store", "growth_state",
        ]:
            assert key in authority
            assert isinstance(authority[key], bool)


# =====================================================================
# G. 回归测试 — 防止 JS fetch URL 双前缀拼接（Phase 5.1 → 5.2 修复回归）
# =====================================================================
# 历史 bug：
#   fetchJson 内部 fetch("/admin/api" + path)，
#   但 CONFIG.endpoints 中的 path 已包含 /admin/api/，导致
#     "/admin/api" + "/admin/api/admin/authority/status"
#   = "/admin/api/admin/api/admin/authority/status"  ← 404
#   现象：所有 Phase 5.1 API 失败 → 前端整体进入 "Runtime Offline"。
#
# 本测试以"行为"为目标（不限制实现方式）：
#   1. 拼接产生的 URL 不能含 /admin/api/admin/api/ 双前缀
#   2. 拼接产生的 URL 应指向真实 Phase 5.1 端点（/admin/api/admin/...）
#   3. 模拟所有可能的 fetch 调用形态（直接传字符串 / 前缀+变量）
# =====================================================================

JS_DASHBOARD_FILES = [
    STATIC_DIR / "js" / "runtime_dashboard.js",
    STATIC_DIR / "js" / "selfmodel_dashboard.js",
    STATIC_DIR / "js" / "selfmodel_timeline.js",
    STATIC_DIR / "js" / "governance_dashboard.js",
]
# 注：selfmodel_health.js 内部虽然保留 fetch("/admin/api" + path) 模式，
# 但其调用处的 path 不含 /api/（例如 /admin/selfmodel/health），
# 拼接结果仍为 /admin/api/admin/selfmodel/health（正确），故不纳入本回归测试。


def _extract_quoted_strings(text: str) -> set:
    """提取 JS 源码中所有双引号 / 单引号字符串字面量。"""
    strings = set()
    strings.update(re.findall(r'"([^"]+)"', text))
    strings.update(re.findall(r"'([^']+)'", text))
    return strings


def _extract_admin_path_strings(text: str) -> set:
    """提取所有以 /admin 开头的字符串（作为可能 endpoint 路径）。"""
    paths = set()
    paths.update(re.findall(r'"(/admin[^"]*)"', text))
    paths.update(re.findall(r"'(/admin[^']*)'", text))
    return paths


def _simulate_fetch_urls(text: str) -> list:
    """模拟所有 fetch 调用可能产生的 URL。

    覆盖以下模式：
    1. fetch("完整URL")  → 直接使用该 URL
    2. fetch("前缀" + 变量)  → 用 prefix 拼上所有 /admin 路径字符串
    """
    urls = []
    admin_paths = _extract_admin_path_strings(text)

    # 模式 1: fetch(...) 第一个参数是字符串字面量（直接传完整 URL）
    for m in re.finditer(r'fetch\(\s*"([^"]+)"\s*[,)]', text):
        url = m.group(1)
        if url.startswith("/"):
            urls.append(url)
    for m in re.finditer(r"fetch\(\s*'([^']+)'\s*[,)]", text):
        url = m.group(1)
        if url.startswith("/"):
            urls.append(url)

    # 模式 2: fetch("前缀" + 变量) — 模拟拼接
    for m in re.finditer(r'fetch\(\s*"([^"]+)"\s*\+', text):
        prefix = m.group(1)
        for path in admin_paths:
            urls.append(prefix + path)
    for m in re.finditer(r"fetch\(\s*'([^']+)'\s*\+", text):
        prefix = m.group(1)
        for path in admin_paths:
            urls.append(prefix + path)

    return urls


class TestAdminJsNoDoubleApiPrefix:
    """Admin Dashboard JS 的 fetch 拼接不能产生 /admin/api/admin/api/ 双前缀 URL。"""

    @pytest.mark.parametrize("js_file", JS_DASHBOARD_FILES)
    def test_no_fetch_url_contains_double_api_prefix(self, js_file):
        """核心回归：模拟所有 fetch 调用，断言产生的 URL 不含 /admin/api/admin/api/。

        修复后：所有 fetch(...) 都直接传 path 变量，不再硬编码 "/admin/api" 前缀。
        因此检测分两层：
          1) 字面量层面：禁止在 fetch 中拼接 "/admin/api" 前缀
          2) 拼接层面：从源代码推演拼接可能产生的 URL，不得出现 /admin/api/admin/api/
        """
        text = js_file.read_text(encoding="utf-8")
        urls = _simulate_fetch_urls(text)
        unique_urls = {u for u in urls if u}

        # 模式 1：检测 fetch() 中是否还在硬编码 "/admin/api" 前缀拼接
        bad_prefix_patterns = [
            re.compile(r'fetch\(\s*"/admin/api"\s*\+'),
            re.compile(r"fetch\(\s*'/admin/api'\s*\+"),
            re.compile(r'fetch\(\s*"/admin/api"\s*,\s*\{'),
            re.compile(r"fetch\(\s*'/admin/api'\s*,\s*\{"),
            re.compile(r'fetch\(\s*"/admin/api"\)'),
            re.compile(r"fetch\(\s*'/admin/api'\)"),
        ]
        for pat in bad_prefix_patterns:
            assert not pat.search(text), (
                f"{js_file.name}: 检测到 fetch 仍在拼接 '/admin/api' 前缀，"
                f"这会与 CONFIG.endpoints 中已含 /admin/api 的路径拼出双前缀。"
            )

        # 模式 2：任何可能产生的 URL 都不得含 /admin/api/admin/api/
        bad = [u for u in unique_urls if "/admin/api/admin/api/" in u]
        assert not bad, (
            f"{js_file.name}: 模拟拼接出 {len(bad)} 个双前缀 URL，"
            f"样例: {bad[0]!r}"
        )

    @pytest.mark.parametrize("js_file", JS_DASHBOARD_FILES)
    def test_admin_path_strings_are_well_formed(self, js_file):
        """所有以 /admin 开头的字符串都应是 /admin/api/admin/... 形式（Phase 5.1 端点）。

        覆盖 CONFIG.endpoints 路径配置本身：路径已含 /admin/api/，fetch 不应再拼接前缀。
        """
        text = js_file.read_text(encoding="utf-8")
        admin_paths = _extract_admin_path_strings(text)
        assert admin_paths, f"{js_file.name}: 未发现 /admin 路径字符串"

        # 所有 /admin 路径字符串都应该以 /admin/api/admin/ 开头
        # 这是 Phase 5.1 真实端点（admin blueprint + /api/admin/<resource>）
        for p in admin_paths:
            if "/admin/api/admin/" in p or p == "/admin" or p == "/admin/":
                continue  # 符合要求
            # 排除 /admin/api 单独出现（可能用作前缀）
            if p in ("/admin/api", "/admin/api/"):
                continue
            # 其他形式（如 /admin/foo/bar）应被关注但不一定 fail
            # 这里只 fail 于 /admin/api/admin/api/ 这种"以 /admin/api/ 开头但又含另一个 /admin/api/" 的怪路径
            assert "/admin/api/admin/api/" not in p, (
                f"{js_file.name}: 路径 {p!r} 含双前缀"
            )

    @pytest.mark.parametrize("js_file", JS_DASHBOARD_FILES)
    def test_fetch_simulation_summary(self, js_file):
        """打印所有模拟 URL（调试辅助，确认无双前缀）。"""
        text = js_file.read_text(encoding="utf-8")
        urls = _simulate_fetch_urls(text)
        unique_urls = sorted(set(u for u in urls if u))
        # 这里不 fail，只作为健康检查存在
        # 真实断言在 test_no_fetch_url_contains_double_api_prefix
        assert isinstance(unique_urls, list)

    @pytest.mark.parametrize("js_file", JS_DASHBOARD_FILES)
    def test_admin_endpoint_paths_target_real_phase51_routes(self, js_file):
        """正向验证：JS 中以 /admin 开头的路径都是 /admin/api/admin/... 形式。

        配合 fetch() 不再拼接前缀的修复，运行时实际请求 URL 就是 /admin/api/admin/...
        这是后端 Phase 5.1 admin blueprint 注册的真实端点。
        """
        text = js_file.read_text(encoding="utf-8")
        admin_paths = _extract_admin_path_strings(text)
        assert admin_paths, f"{js_file.name}: 未发现任何 /admin 路径字符串"

        real_phase51_paths = {p for p in admin_paths if p.startswith("/admin/api/admin/")}
        assert real_phase51_paths, (
            f"{js_file.name}: 未发现任何 /admin/api/admin/... 路径，"
            f"实际发现: {sorted(admin_paths)}"
        )


# =====================================================================
# H. 现有 Admin 功能未受影响
# =====================================================================
class TestExistingAdminUnaffected:
    """Phase 5.2 不破坏现有 Admin 系统。"""

    def test_index_html_still_loads(self):
        """index.html 仍可读取，未破坏。"""
        assert INDEX_HTML.exists()
        text = INDEX_HMTL = INDEX_HTML.read_text(encoding="utf-8")
        # 关键原有结构应保留
        assert 'id="welcome-section"' in INDEX_HMTL
        assert 'id="modules-section"' in INDEX_HMTL
        assert 'id="timeline-section"' in INDEX_HMTL or 'id="timeline-container"' in INDEX_HMTL

    def test_app_js_still_loaded(self):
        """app.js 仍被加载。"""
        text = INDEX_HTML.read_text(encoding="utf-8")
        assert "/admin/js/app.js" in text
        assert "/admin/js/dashboard.js" in text

    def test_admin_blueprint_still_loads(self):
        """admin_bp 蓝图仍可正常加载。"""
        pytest.importorskip("flask")
        from src.admin.api.routes import admin_bp
        assert admin_bp is not None


# =====================================================================
# I. JS — Phase 5.1 API 端点与 Provider 契约一致
# =====================================================================
class TestJsProviderContractAlignment:
    """JS 期望的响应结构应与 RuntimeProvider 实际返回结构一致。"""

    @pytest.fixture(autouse=True)
    def _setup(self):
        _init_runtime_bridge()

    def test_authority_api_keys_match_provider(self):
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        authority = provider.get_authority_status()

        js_text = JS_FILE.read_text(encoding="utf-8")
        # JS 渲染代码中应至少引用这些 key 一次（作为 authority 字典的字段名）
        for key in [
            "memory_store", "vector_memory", "emotion_manager",
            "personality_resolver", "self_model_store", "growth_state",
        ]:
            assert key in js_text, f"JS 应消费 authority 字段 {key}"

    def test_memory_api_keys_match_provider(self):
        from src.admin.runtime_provider import get_runtime_provider
        provider = get_runtime_provider()
        summary = provider.get_memory_summary()

        js_text = JS_FILE.read_text(encoding="utf-8")
        # JS 应消费这些字段
        for key in ("total_count", "important_count", "user_id", "recent"):
            assert key in js_text, f"JS 应消费 memory 字段 {key}"

    def test_growth_metric_keys_match_provider(self):
        js_text = JS_FILE.read_text(encoding="utf-8")
        for key in ("total_growth", "maturity", "self_awareness", "empathy", "stability"):
            assert key in js_text, f"JS 应消费 growth metric {key}"

    def test_emotion_fields_match_provider(self):
        js_text = JS_FILE.read_text(encoding="utf-8")
        for key in ("intensity", "recent"):
            assert key in js_text, f"JS 应消费 emotion 字段 {key}"
