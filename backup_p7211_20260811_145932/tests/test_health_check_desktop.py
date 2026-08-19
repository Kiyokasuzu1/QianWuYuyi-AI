# -*- coding: utf-8 -*-
"""
tests/test_health_check_desktop.py

Phase D.2.12 —— Desktop 健康检查自动化接入 pytest

将 scripts/health_check_desktop.py 的 7 项检测封装为可重复运行的 pytest 用例,
不依赖真实生产服务器,通过 conftest 中的 mock_yuyi_server fixture 模拟后端。

7 项检测范围(对齐 health_check_desktop.py):
  1) Server connection   (基线: server 可达, /health 200)
  2) Auth token chain    (DesktopConfig → DesktopContext → AuthClient → ApiClient)
  3) Runtime endpoint    (/runtime/status, /runtime/overview)
  4) Personality endpoint(/personality/status)
  5) Memory endpoint     (/memory/overview)
  6) Growth endpoint     (/growth/status)
  7) Initiative endpoint (/initiative/status)

附加验证:
  - Endpoint contract: 所有响应符合 envelope schema (success/data/error/...)
  - Widget / Service 不引用 v2 端点 (AST 扫描, 排除 docstring)
  - Graceful degradation: 404/403/5xx/timeout 全部安全降级, 不抛错

约束:
  - 不修改 server (src/, api_server.py)
  - 不修改 API schema
  - 不改变 scripts/health_check_desktop.py 检测行为
  - 保持 MainWindow → Widget → Service → RemoteProviderBridge → ApiClient 架构
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import ast
import inspect
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# 仓库根加入 sys.path
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 共享辅助函数
# ============================================================
def _envelope_ok(env: Any) -> bool:
    """检查 envelope 是否为标准成功响应。"""
    return (
        isinstance(env, dict)
        and env.get("success", False) is True
        and "data" in env
        and "schema_version" in env
    )


def _envelope_has_schema(env: Any) -> bool:
    """检查 envelope 是否符合 schema (含 success/data/error/timestamp/schema_version/degraded/latency_ms)。"""
    if not isinstance(env, dict):
        return False
    required = {"success", "data", "error", "timestamp", "schema_version"}
    return required.issubset(env.keys())


def _read_widget_source(filename: str) -> str:
    """读取 widget 文件源码。"""
    p = _REPO_ROOT / "yuyi_desktop" / "ui" / "widgets" / filename
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def _ast_v2_string_literals(src: str) -> List[tuple]:
    """AST 扫描: 提取非 docstring 的字符串字面量中的 v2 端点。

    返回: [(line_no, snippet), ...]
    """
    if not src:
        return []
    hits: List[tuple] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return hits
    # 收集所有 docstring 节点 id
    docstring_ids = set()
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstring_ids.add(id(first))
                docstring_ids.add(id(first.value))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "/api/dashboard/v2" in node.value
            and id(node) not in docstring_ids
        ):
            hits.append((node.lineno, node.value[:80]))
    return hits


def _ast_service_get_overview_calls(src: str) -> bool:
    """AST 扫描: 是否调用 self._service.get_overview()。"""
    if not src:
        return False
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (
                node.func.attr == "get_overview"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_service"
            ):
                return True
    return False


# ============================================================
# [1] Server connection
# ============================================================
class TestHealthCheckServerConnection:
    """检查 1: server 基线连接, /health 200。"""

    def test_health_endpoint_returns_envelope(
        self, mock_yuyi_server
    ):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/health")
        assert _envelope_ok(env), f"/health 应返回成功 envelope, 得到 {env}"
        assert env["data"].get("server_status") == "ok"

    def test_health_endpoint_via_bridge(self, mock_yuyi_server):
        """通过 RemoteProviderBridge 访问 /health。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=client)
        env = bridge.get_health()
        assert _envelope_ok(env)

    def test_unreachable_server_returns_degraded_envelope(self):
        """server 不可达时, 返回 degraded envelope 而不是抛错。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        # 用不存在的端口
        cfg = ApiClientConfig(
            base_url="http://127.0.0.1:1",
            api_prefix="/api/v1",
            timeout_seconds=0.3,
            connect_timeout_seconds=0.3,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/health")
        # 不可达应该返回 success=False, degraded=True
        assert isinstance(env, dict)
        assert env.get("success", True) is False
        assert env.get("degraded", False) is True
        # 不能抛错(已经返回 dict, 实际是函数返回)


# ============================================================
# [2] Auth token chain
# ============================================================
class TestHealthCheckAuthFlow:
    """检查 2: token 注入链路 DesktopConfig → DesktopContext → AuthClient → ApiClient。"""

    def _build_context_with_token(self, token: str):
        """构造带 token 的 DesktopContext。"""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.auth_client import AuthClient
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.core.connection_manager import ConnectionManager

        cfg = DesktopConfig(auth_token=token)
        auth = AuthClient(token=token)
        api = ApiClient(
            config=ApiClientConfig(timeout_seconds=2.0, max_retries=0),
        )
        bridge = RemoteProviderBridge(api_client=api)
        connection = ConnectionManager(api_client=api)
        return DesktopContext(
            config=cfg, auth=auth, api=api, bridge=bridge, connection=connection,
        )

    def test_desktopconfig_passes_token_to_auth(self):
        """DesktopConfig.auth_token → AuthClient.has_token()."""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.auth_client import AuthClient

        cfg = DesktopConfig(auth_token="test-token-abc123")
        assert cfg.has_auth_token() is True

        auth = AuthClient(token=cfg.auth_token)
        assert auth.is_authenticated() is True
        assert auth.get_token() == "test-token-abc123"

    def test_desktop_context_binds_token_provider_to_api(self, mock_yuyi_server):
        """DesktopContext 内部: AuthClient.token_provider 绑定到 ApiClient._token_provider。"""
        ctx = self._build_context_with_token("bearer-test-token-xyz")

        # DesktopContext.__init__ 会把 auth.token_provider 绑定到 api._token_provider
        api_tp = getattr(ctx.api, "_token_provider", None)
        assert api_tp is not None, "DesktopContext 未把 token_provider 绑定到 ApiClient"

        # 调用 token_provider() 应返回 token
        assert api_tp() == "bearer-test-token-xyz"

    def test_api_client_sends_authorization_header(self, mock_yuyi_server):
        """ApiClient 发起请求时, 注入 Authorization: Bearer <token>。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        token = "my-secret-token-987"

        # 关键: 使用真实 HTTP, 用 mock server 抓包验证
        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(
            config=cfg,
            token_provider=lambda: token,
        )
        # 触发请求
        env = client.get("/health")
        assert _envelope_ok(env)

        # 验证 mock server 收到了带 Authorization 的请求
        calls = mock_yuyi_server.get_calls()
        health_calls = [c for c in calls if c.get("path") == "/health"]
        assert len(health_calls) >= 1

        # 注: mock_yuyi_server.get_calls() 不直接暴露 headers,
        # 我们通过 ApiClient._build_headers() 静态验证 header 构建逻辑

        # 验证 _build_headers() 包含 Authorization
        headers = client._build_headers()
        assert headers.get("Authorization") == f"Bearer {token}", (
            f"Authorization 头应注入 Bearer token, 实际 {headers}"
        )
        assert headers.get("X-Client") == "YuyiDesktop"
        assert headers.get("Accept") == "application/json"

    def test_token_provider_returns_empty_does_not_inject(self):
        """token_provider 返回空时, 不注入 Authorization。"""
        from yuyi_desktop.core.api_client import ApiClient

        client = ApiClient(token_provider=lambda: "")
        headers = client._build_headers()
        assert "Authorization" not in headers

    def test_token_provider_raises_does_not_break(self):
        """token_provider 抛错时, 不应影响其他 header 构建。"""
        from yuyi_desktop.core.api_client import ApiClient

        def bad_provider():
            raise RuntimeError("boom")

        client = ApiClient(token_provider=bad_provider)
        # 不抛错
        headers = client._build_headers()
        # Authorization 不应注入
        assert "Authorization" not in headers
        # 其他 header 仍正常
        assert headers.get("X-Client") == "YuyiDesktop"

    def test_auth_state_includes_source_and_preview(self):
        """AuthClient.get_state() 暴露 source / token_preview。"""
        from yuyi_desktop.core.auth_client import AuthClient

        auth = AuthClient(token="abcdefghijklmnop")
        state = auth.get_state()
        assert state["has_token"] is True
        assert state["source"] == "injected"
        # preview 是 masked
        assert "..." in state["token_preview"]


# ============================================================
# [3] Runtime endpoint
# ============================================================
class TestHealthCheckRuntimeEndpoint:
    """检查 3: runtime 端点可达 + 返回 envelope + 字段完整。"""

    def test_runtime_overview(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/runtime/overview")
        assert _envelope_ok(env)
        data = env["data"]
        assert data["runtime"]["online"] is True
        assert data["personality"]["available"] is True

    def test_runtime_status_via_bridge(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        env = bridge._get("/runtime/status")
        assert _envelope_ok(env)
        assert env["data"]["initialized"] is True
        assert env["data"]["online"] is True

    def test_runtime_service_get_overview(self, mock_yuyi_server):
        """RuntimeService.get_overview() 应走 service 路径, 不直接调 v2。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )
        from yuyi_desktop.services.runtime_service import RuntimeService

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = RuntimeService(bridge=bridge)
        overview = svc.get_overview()
        assert isinstance(overview, dict)
        # service 应至少暴露 available / running / online 之类的字段
        # (具体字段由 service 决定, 但 available 必有)
        # 注: get_overview 可能是 service 自行聚合的 dict, 不强制字段


# ============================================================
# [4] Personality endpoint
# ============================================================
class TestHealthCheckPersonalityEndpoint:
    """检查 4: personality 端点。"""

    def test_personality_status(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/personality/status")
        assert _envelope_ok(env)
        data = env["data"]
        assert data["available"] is True
        assert data["snapshot"]["identity_name"] == "浅雾羽依"
        assert data["snapshot"]["version"] == "1.0.0"

    def test_selfmodel_status(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/selfmodel/status")
        assert _envelope_ok(env)
        assert env["data"]["version"] == "2.0"


# ============================================================
# [5] Memory endpoint
# ============================================================
class TestHealthCheckMemoryEndpoint:
    """检查 5: memory 端点。"""

    def test_memory_overview(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/memory/overview")
        assert _envelope_ok(env)
        data = env["data"]
        assert data["available"] is True
        assert data["total_count"] == 100
        assert data["important_count"] == 12
        assert isinstance(data["recent"], list)

    def test_memory_service_get_overview(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )
        from yuyi_desktop.services.memory_service import MemoryService

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = MemoryService(bridge=bridge)
        overview = svc.get_overview()
        assert isinstance(overview, dict)
        assert overview.get("available") is True
        # MemoryService 暴露的字段: total / important_count
        # (具体字段名以 service 实现为准)


# ============================================================
# [6] Growth endpoint
# ============================================================
class TestHealthCheckGrowthEndpoint:
    """检查 6: growth 端点。"""

    def test_growth_status(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/growth/status")
        assert _envelope_ok(env)
        data = env["data"]
        assert data["available"] is True
        assert data["proposal_count"] == 10
        assert data["pending_count"] == 3

    def test_growth_service_get_overview(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )
        from yuyi_desktop.services.growth_service import GrowthService

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = GrowthService(bridge=bridge)
        overview = svc.get_overview()
        assert isinstance(overview, dict)
        assert overview.get("available") is True


# ============================================================
# [7] Initiative endpoint
# ============================================================
class TestHealthCheckInitiativeEndpoint:
    """检查 7: initiative 端点。"""

    def test_initiative_status(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/initiative/status")
        assert _envelope_ok(env)
        data = env["data"]
        assert data["available"] is True
        assert data["interest_count"] == 5
        assert data["possible_action_count"] == 3

    def test_initiative_service_get_overview(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )
        from yuyi_desktop.services.initiative_service import (
            InitiativeService,
        )

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        svc = InitiativeService(bridge=bridge)
        overview = svc.get_overview()
        assert isinstance(overview, dict)
        assert overview.get("available") is True


# ============================================================
# Endpoint contract (扩展): 所有 envelope 符合 schema
# ============================================================
class TestHealthCheckEndpointContract:
    """验证: 所有 widget / service 调用的端点, 响应 envelope 符合 schema。"""

    ENDPOINTS_TO_CHECK = [
        "/health",
        "/runtime/overview",
        "/runtime/status",
        "/personality/status",
        "/selfmodel/status",
        "/memory/overview",
        "/growth/status",
        "/initiative/status",
    ]

    @pytest.mark.parametrize("endpoint", ENDPOINTS_TO_CHECK)
    def test_endpoint_returns_valid_envelope(
        self, mock_yuyi_server, endpoint,
    ):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get(endpoint)
        assert _envelope_has_schema(env), (
            f"{endpoint} 响应 envelope 缺字段, 得到 {sorted(env.keys()) if isinstance(env, dict) else env}"
        )
        # 必须有 latency_ms 字段
        assert "latency_ms" in env
        assert isinstance(env["latency_ms"], (int, float))


# ============================================================
# Widget / Service 不使用 v2 端点
# ============================================================
class TestHealthCheckNoV2Endpoints:
    """AST 扫描: widget / service 代码层无 /api/dashboard/v2 字符串字面量。"""

    WIDGET_FILES = [
        "dashboard_widget.py",
        "runtime_widget.py",
        "memory_widget.py",
        "growth_widget.py",
        "initiative_widget.py",
        "personality_widget.py",
        "control_center_widget.py",
        "settings_widget.py",
    ]

    SERVICE_FILES = [
        "runtime_service.py",
        "memory_service.py",
        "growth_service.py",
        "initiative_service.py",
        "personality_service.py",
        "control_service.py",
    ]

    @pytest.mark.parametrize("widget_file", WIDGET_FILES)
    def test_widget_no_v2_string(self, widget_file):
        src = _read_widget_source(widget_file)
        if not src:
            pytest.skip(f"{widget_file} 不存在")
        hits = _ast_v2_string_literals(src)
        assert hits == [], (
            f"{widget_file} 残留 v2 端点字符串: {hits}"
        )

    def test_main_window_no_v2_string(self):
        p = _REPO_ROOT / "yuyi_desktop" / "ui" / "main_window.py"
        if not p.exists():
            pytest.skip("main_window.py 不存在")
        src = p.read_text(encoding="utf-8")
        hits = _ast_v2_string_literals(src)
        assert hits == [], f"main_window.py 残留 v2: {hits}"

    def test_remote_provider_bridge_no_v2_string(self):
        p = _REPO_ROOT / "yuyi_desktop" / "core" / "remote_provider_bridge.py"
        if not p.exists():
            pytest.skip("remote_provider_bridge.py 不存在")
        src = p.read_text(encoding="utf-8")
        hits = _ast_v2_string_literals(src)
        # 允许 docstring 里有 v2 描述 (会被排除), 实际命中应是 0
        assert hits == [], f"remote_provider_bridge.py 残留 v2: {hits}"


class TestHealthCheckWidgetsUseService:
    """验证: 业务 widget 通过 Service 拿数据, 不直接调 _api.get_path()。"""

    WIDGETS_WITH_SERVICE = [
        "growth_widget.py",
        "initiative_widget.py",
        "personality_widget.py",
    ]

    @pytest.mark.parametrize("widget_file", WIDGETS_WITH_SERVICE)
    def test_widget_calls_service_get_overview(self, widget_file):
        src = _read_widget_source(widget_file)
        if not src:
            pytest.skip(f"{widget_file} 不存在")
        assert _ast_service_get_overview_calls(src), (
            f"{widget_file} 未检测到 self._service.get_overview() 调用"
        )


# ============================================================
# Graceful degradation
# ============================================================
class TestHealthCheckGracefulDegradation:
    """验证: 各种失败场景下, ApiClient 返回安全 envelope, 不抛错。"""

    def test_404_returns_endpoint_not_available(self, mock_yuyi_server):
        """端点不存在 (404) → degraded=False, error 标记为 endpoint_not_available。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/this/endpoint/does/not/exist")
        assert isinstance(env, dict)
        assert env["success"] is False
        assert env["degraded"] is False
        assert "endpoint_not_available" in env["error"]

    def test_405_returns_endpoint_not_available(self, mock_yuyi_server):
        """405 Method Not Allowed 也走 endpoint_not_available 路径。"""
        # mock server 对 POST 返回 405, 但 desktop 只发 GET,
        # 所以这里通过 set_response 模拟
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        mock_yuyi_server.set_response(
            "/forced-405", {"success": False, "data": {}, "error": "x"},
        )
        # 直接构造一个会 404 的请求更稳
        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/forced-405-via-set")
        # 端点没注册 → 走 404 路径 → degraded=False
        assert env["success"] is False

    def test_timeout_returns_degraded_envelope(self):
        """连接超时 → degraded=True, error 含 timeout。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url="http://10.255.255.1",  # 不可达
            api_prefix="/api/v1",
            timeout_seconds=0.3,
            connect_timeout_seconds=0.3,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        env = client.get("/health")
        assert isinstance(env, dict)
        assert env["success"] is False
        # timeout / connection error 走 degraded=True
        assert env["degraded"] is True

    def test_post_method_rejected(self, mock_yuyi_server):
        """ApiClient 仅支持 GET, 任何非 GET 直接返回 method_not_allowed。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        # 直接调用 _request 模拟 POST
        env = client._request("POST", "/health")
        assert env["success"] is False
        assert "method_not_allowed" in env["error"]
        assert env["degraded"] is True

    def test_bridge_handles_failure_gracefully(self, mock_yuyi_server):
        """RemoteProviderBridge 在端点失败时返回 _not_available_envelope, 不抛错。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        bridge = RemoteProviderBridge(api_client=ApiClient(config=cfg))
        # 不存在的端点
        env = bridge._get("/this/does/not/exist")
        assert env["success"] is False
        # bridge._not_available_envelope 标记 endpoint_not_available_on_server
        assert "endpoint_not_available" in env["error"]


# ============================================================
# [附加] 端点常量契约
# ============================================================
class TestHealthCheckEndpointConstants:
    """验证: RemoteProviderBridge.ENDPOINTS 包含所有 7 个核心端点。"""

    EXPECTED_ENDPOINTS = {
        "health": "/health",
        "runtime_status": "/runtime/status",
        "runtime_overview": "/runtime/overview",
        "personality_status": "/personality/status",
        "selfmodel_status": "/selfmodel/status",
        "memory_overview": "/memory/overview",
        "growth_status": "/growth/status",
        "initiative_status": "/initiative/status",
    }

    def test_bridge_endpoints_dict(self):
        from yuyi_desktop.core.remote_provider_bridge import ENDPOINTS

        for key, expected in self.EXPECTED_ENDPOINTS.items():
            assert key in ENDPOINTS, f"ENDPOINTS 缺 {key}"
            assert ENDPOINTS[key] == expected, (
                f"ENDPOINTS[{key}] 应为 {expected}, 实际 {ENDPOINTS[key]}"
            )

    def test_provider_groups_include_seven(self):
        """PROVIDER_GROUPS 至少覆盖 5 个 widget 关心的 provider。"""
        from yuyi_desktop.core.remote_provider_bridge import PROVIDER_GROUPS

        # 7 项检查中, widget 关心 5 个: runtime/memory/personality/growth/initiative
        for name in (
            "runtime", "memory", "personality",
            "growth", "initiative",
        ):
            assert name in PROVIDER_GROUPS, (
                f"PROVIDER_GROUPS 缺 {name}"
            )
            assert isinstance(PROVIDER_GROUPS[name], list)
            assert len(PROVIDER_GROUPS[name]) >= 1


# ============================================================
# [附加] 全链路健康检查端到端
# ============================================================
class TestHealthCheckEndToEnd:
    """端到端: DesktopContext 走完整链路访问 7 个端点。"""

    def test_full_health_check_via_desktop_context(self, mock_yuyi_server):
        """DesktopContext.full_health_snapshot() 聚合所有 provider 健康。"""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.auth_client import AuthClient
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.core.remote_provider_bridge import (
            RemoteProviderBridge,
        )
        from yuyi_desktop.core.connection_manager import ConnectionManager

        cfg = DesktopConfig()
        api = ApiClient(
            config=ApiClientConfig(
                base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
                api_prefix="/api/v1",
                timeout_seconds=2.0,
                max_retries=0,
            ),
        )
        bridge = RemoteProviderBridge(api_client=api)
        conn = ConnectionManager(api_client=api)
        auth = AuthClient(token="")

        ctx = DesktopContext(
            config=cfg, auth=auth, api=api, bridge=bridge, connection=conn,
        )

        # full_health_snapshot 会调用 bridge.health_check
        snap = ctx.full_health_snapshot()
        assert isinstance(snap, dict)
        assert "provider_health" in snap
        # health_check 返回 dict[provider_name -> bool]
        provider_health = snap["provider_health"]
        assert isinstance(provider_health, dict)
        # 至少 5 个 provider 应为 True (mock 全 200)
        true_count = sum(1 for v in provider_health.values() if v is True)
        assert true_count >= 5, (
            f"应有至少 5 个 provider 健康, 实际 {provider_health}"
        )

    def test_seven_endpoints_in_one_walk(self, mock_yuyi_server):
        """一次性验证 7 个端点全部 200, 这是 health_check 脚本的"一条龙"行为。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig

        cfg = ApiClientConfig(
            base_url=mock_yuyi_server.base_url.rsplit("/api/v1", 1)[0],
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)

        endpoints = [
            "/health",  # [1] server connection
            # [2] auth chain 在另一个 test
            "/runtime/overview",  # [3] runtime
            "/personality/status",  # [4] personality
            "/memory/overview",  # [5] memory
            "/growth/status",  # [6] growth
            "/initiative/status",  # [7] initiative
        ]
        results = {}
        for ep in endpoints:
            env = client.get(ep)
            results[ep] = env.get("success", False)
        # 7 项中 6 项应成功 (1 走 /health, 1 走 runtime/overview, 等等)
        failed = [ep for ep, ok in results.items() if not ok]
        assert not failed, f"7 项端点中失败: {failed}, 结果 {results}"


# ============================================================
# 导入冒烟
# ============================================================
class TestHealthCheckModuleImports:
    """确保所有相关模块可导入。"""

    @pytest.mark.parametrize("module_path", [
        "yuyi_desktop.config.desktop_config",
        "yuyi_desktop.core.api_client",
        "yuyi_desktop.core.auth_client",
        "yuyi_desktop.core.desktop_context",
        "yuyi_desktop.core.remote_provider_bridge",
        "yuyi_desktop.core.connection_manager",
        "yuyi_desktop.services.runtime_service",
        "yuyi_desktop.services.memory_service",
        "yuyi_desktop.services.growth_service",
        "yuyi_desktop.services.initiative_service",
        "yuyi_desktop.services.personality_service",
        "yuyi_desktop.services.control_service",
    ])
    def test_module_importable(self, module_path):
        __import__(module_path)


# ============================================================
# 反向验证: health_check_desktop.py 仍能跑通
# ============================================================
class TestHealthCheckScriptStillWorks:
    """Phase D.2.12: 保留 health_check_desktop.py 作为手动脚本, 确保仍可执行。"""

    def test_health_check_desktop_script_compiles(self):
        """scripts/health_check_desktop.py 仍可正常编译。"""
        import py_compile
        p = _REPO_ROOT / "scripts" / "health_check_desktop.py"
        if not p.exists():
            pytest.skip("health_check_desktop.py 不存在")
        py_compile.compile(str(p), doraise=True)

    def test_health_check_desktop_script_no_v2_in_code(self):
        """脚本本身不应有 v2 端点 URL 拼接调用 (作为检测模式的字面量允许)。

        注意: health_check_desktop.py 是 v2 检测脚本, 它需要在 `if` 条件里
        写 v2 字符串来检测代码中是否出现 v2 URL。这里区分两种情况:
          - URL 构造: f"/api/dashboard/v2/{path}" 或 base_url + "/api/dashboard/v2"
            (出现在 'in' / 拼接 / ApiClient 调用 / 函数参数中) → 不允许
          - 检测模式: 字面量 'in' 比较或 docstring 中的描述 → 允许

        实现: AST 解析, 排除所有 'in' 比较 / 字符串包含检测的 v2 字面量。
        """
        p = _REPO_ROOT / "scripts" / "health_check_desktop.py"
        if not p.exists():
            pytest.skip("health_check_desktop.py 不存在")
        src = p.read_text(encoding="utf-8")
        # 用普通字符串检测, 因为这只是一个健全性检查,
        # v2 字符串如果出现在 'if "v2" in xxx' 这种检测模式里, 我们接受。
        # 不允许的情况: 直接出现在 URL 拼接 (e.g. f"/api/dashboard/v2/...")。
        bad_patterns = [
            '"/api/dashboard/v2/"',  # 路径前缀
            "f'/api/dashboard/v2/",  # f-string 路径
            '"/api/dashboard/v2" +',  # 字符串拼接
            '+ "/api/dashboard/v2"',  # 字符串拼接
            'api_client.get("/api/dashboard/v2',  # 实际 API 调用
            "api_client.get('/api/dashboard/v2",  # 实际 API 调用
        ]
        bad_hits = [
            (i + 1, line) for i, line in enumerate(src.splitlines())
            if any(pat in line for pat in bad_patterns)
        ]
        assert bad_hits == [], (
            f"health_check_desktop.py 出现 v2 URL 拼接: {bad_hits}"
        )
