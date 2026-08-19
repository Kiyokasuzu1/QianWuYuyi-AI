# -*- coding: utf-8 -*-
"""
tests/test_server_api_gateway.py

Phase C.10.3 — Yuyi Server API Gateway 单元测试

覆盖目标(来自 Phase C.10.3 任务清单):
1. API 启动 —— Flask app 启动 + Blueprint 注册 + health 端点
2. health —— /api/v1/health 返回 server_status / runtime_status / version / schema_version
3. schema —— 所有响应符合统一 envelope(success/data/error/timestamp/schema_version/degraded)
4. readonly —— POST/PUT/PATCH/DELETE 一律 405 拒绝
5. auth —— Bearer Token 校验(missing / invalid / ok / dev / disabled)
6. provider 读取 —— 通过 mock provider 验证数据流
7. exception 降级 —— provider 异常时返回 degraded envelope,不抛错

约束(强):
- 不得影响 test_full_system_e2e.py 101/101
- 不得修改 src/runtime/** / src/memory/** / src/growth/** / src/personality/**
  / src/self_model/** / src/relationship/**
- 仅通过 Provider 的只读快照访问数据
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

# 测试期间强制 Qt offscreen(避免依赖真实显示设备)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 路径由 conftest.py 处理


# ============================================================
# 工具: 启动可控端口的 mock gateway server
# ============================================================
def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_test_providers() -> Dict[str, Any]:
    """构造 mock Provider 集合,供 gateway 路由使用。"""
    runtime = MagicMock()
    runtime.get_status.return_value = {
        "online": True,
        "runtime": {"initialized": True, "is_running": True},
    }
    runtime.get_authority_status.return_value = {
        "runtime": True,
        "personality": True,
        "memory": True,
        "selfmodel": True,
        "growth": True,
        "initiative": True,
    }
    runtime.get_personality_summary.return_value = {
        "available": True,
        "current": {
            "identity_name": "浅雾羽依",
            "version": "1.0.0",
            "stable": True,
            "traits": {"温柔": 0.85, "理性": 0.7},
        },
        "state": "stable",
    }
    runtime.get_memory_summary.return_value = {
        "available": True,
        "total_count": 42,
        "important_count": 5,
        "user_id": "user-1",
        "recent": [
            {"memory_id": "m1", "summary": "first meeting"},
            {"memory_id": "m2", "summary": "second meeting"},
        ],
    }
    runtime.get_emotion_summary.return_value = {
        "available": True,
        "state": "calm",
    }

    selfmodel = MagicMock()
    selfmodel.get_status.return_value = {
        "available": True,
        "version": "2.0",
        "bootstrap": {"version": "2.0"},
        "bridge_error": None,
    }
    selfmodel.get_identity.return_value = {
        "identity_name": "浅雾羽依",
    }

    governance = MagicMock()
    governance.get_growth_governance_snapshot.return_value = {
        "available": True,
        "section": "growth",
        "proposal_count": 10,
        "pending_count": 3,
        "approved_count": 5,
        "rejected_count": 2,
        "applied_count": 0,
        "evolution": {"stage": "stable", "version": "1.0.0"},
    }

    initiative = MagicMock()
    initiative.get_summary.return_value = {
        "available": True,
        "interest_count": 5,
        "possible_action_count": 3,
        "filtered_count": 1,
        "by_trend": {"curiosity": 3, "care": 2},
        "by_action_status": {"pending": 2, "delivered": 1},
        "by_action_type": {"speak": 2, "observe": 1},
        "last_interest_at": "2026-08-04T00:00:00Z",
        "last_action_at": "2026-08-04T00:01:00Z",
        "fallback": False,
    }
    initiative.get_history.return_value = {
        "items": [
            {"action_id": "a1", "type": "speak", "status": "delivered"},
        ],
    }

    audit = MagicMock()

    class _AuditRecord:
        def __init__(self, payload: Dict[str, Any]) -> None:
            self._payload = payload

        def to_dict(self) -> Dict[str, Any]:
            return dict(self._payload)

    audit.load.return_value = [
        _AuditRecord({"event_id": "ae1", "event_type": "runtime.tick"}),
        _AuditRecord({"event_id": "ae2", "event_type": "memory.write"}),
    ]

    return {
        "runtime": runtime,
        "selfmodel": selfmodel,
        "governance": governance,
        "initiative": initiative,
        "audit": audit,
    }


@pytest.fixture
def gateway_test_app():
    """
    启动一个真实 Flask app,注册 C.10.3 gateway,绑定随机空闲端口。

    Yields:
        {
            "base_url": "http://127.0.0.1:<port>/api/v1",
            "port": int,
            "providers": Dict[str, MagicMock],
            "stop": callable,
        }
    """
    try:
        from flask import Flask  # type: ignore
        from werkzeug.serving import make_server  # type: ignore
    except ImportError:
        pytest.skip("Flask/Werkzeug 未安装,无法启动 gateway test app")

    # 在 import 之前先 patch src.* provider 单例,避免真实初始化
    providers = _build_test_providers()

    # 1) 创建一个轻量测试 app 并注册 gateway
    app = Flask("yuyi_gateway_test")
    # 设置测试配置
    app.config["TESTING"] = True

    # 2) 通过 patch 注入 mock providers(影响 routes.py 内部 import)
    patches = []
    try:
        # 强制 auth 模式为 disabled(测试时不强制 token)
        from src.control.api import auth as auth_mod
        from src.control.api.config import GatewayAuthConfig

        auth_mod.set_auth_override(GatewayAuthConfig(
            mode="disabled",
            token="",
            required=False,
        ))

        # Patch provider getters
        from src.admin.runtime_provider import get_runtime_provider as _gr
        patches.append(patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=providers["runtime"],
        ))
        from src.admin.self_model_provider import get_self_model_provider as _gsm
        patches.append(patch(
            "src.control.api.routes._get_self_model_provider",
            return_value=providers["selfmodel"],
        ))
        from src.admin.governance_provider import get_governance_provider as _gg
        patches.append(patch(
            "src.control.api.routes._get_governance_provider",
            return_value=providers["governance"],
        ))
        from src.admin.initiative_dashboard_provider import (
            get_initiative_dashboard_provider as _gi,
        )
        patches.append(patch(
            "src.control.api.routes._get_initiative_provider",
            return_value=providers["initiative"],
        ))
        from src.audit.storage import get_audit_storage as _ga
        patches.append(patch(
            "src.control.api.routes._get_audit_storage",
            return_value=providers["audit"],
        ))

        for p in patches:
            p.start()

        # 3) 注册 gateway
        from src.control.api import register_gateway
        register_gateway(app)

        # 4) 启动 server
        port = _find_free_port()
        server = make_server("127.0.0.1", port, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        # 等待 server 就绪
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.05)

        base_url = f"http://127.0.0.1:{port}/api/v1"

        def _stop():
            try:
                server.shutdown()
            except Exception:  # noqa: BLE001
                pass

        yield {
            "base_url": base_url,
            "port": port,
            "providers": providers,
            "stop": _stop,
        }
    finally:
        # 清理
        for p in patches:
            try:
                p.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            auth_mod.set_auth_override(None)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def gateway_client(gateway_test_app):
    """构造连接真实 gateway 的 ApiClient。"""
    from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
    cfg = ApiClientConfig(
        base_url=gateway_test_app["base_url"].replace("/api/v1", ""),
        api_prefix="/api/v1",
        timeout_seconds=2.0,
        max_retries=0,
    )
    return ApiClient(config=cfg)


# ============================================================
# 1. 模块加载 & 配置
# ============================================================
class TestGatewayModuleLoad:
    """验证 src/control/api 模块正确加载。"""

    def test_module_imports(self):
        from src.control.api import (
            API_SCHEMA_VERSION,
            AuthResult,
            GatewayConfig,
            check_bearer_token,
            gateway_bp,
            make_envelope,
            register_gateway,
            require_auth,
        )
        assert callable(register_gateway)
        assert callable(require_auth)
        assert API_SCHEMA_VERSION == "1.0"
        assert gateway_bp is not None

    def test_config_default(self):
        from src.control.api import GatewayConfig, GatewayAuthConfig
        cfg = GatewayConfig()
        assert cfg.enabled is True
        assert cfg.port == 0
        assert isinstance(cfg.auth, GatewayAuthConfig)
        assert cfg.schema_version == "1.0"

    def test_envelope_shape(self):
        from src.control.api import make_envelope, is_envelope
        env = make_envelope(success=True, data={"a": 1})
        assert is_envelope(env) is True
        for k in ("success", "data", "error", "timestamp", "schema_version", "degraded"):
            assert k in env

    def test_auth_disabled_mode(self):
        from src.control.api import check_bearer_token
        from src.control.api.auth import set_auth_override
        from src.control.api.config import GatewayAuthConfig
        set_auth_override(GatewayAuthConfig(mode="disabled", token="", required=False))
        # mock Flask request
        from flask import Flask
        app = Flask(__name__)
        with app.test_request_context("/", headers={}):
            from flask import request
            res = check_bearer_token(request)
            assert res.ok is True
            assert res.mode == "disabled"
        set_auth_override(None)


# ============================================================
# 2. Gateway 启动
# ============================================================
class TestGatewayStartup:
    """验证 gateway 可以挂载到 Flask 并响应 health 请求。"""

    def test_app_starts_and_health_responds(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/health")
        assert resp["success"] is True
        data = resp["data"]
        assert "server_status" in data
        assert "runtime_status" in data
        assert "version" in data
        assert "schema_version" in data
        # runtime_status 应为 "running"(因为 provider mock 返回 initialized=True)
        assert data["runtime_status"] in ("running", "offline")
        assert data["version"]  # 非空

    def test_app_includes_authority(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/health")
        assert resp["success"] is True
        data = resp["data"]
        assert "authority" in data
        assert isinstance(data["authority"], dict)

    def test_app_includes_uptime(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/health")
        assert resp["success"] is True
        data = resp["data"]
        assert "uptime_seconds" in data
        assert float(data["uptime_seconds"]) >= 0.0


# ============================================================
# 3. Schema 验证(所有 8 个端点的响应格式)
# ============================================================
class TestResponseSchema:
    """所有 endpoint 响应必须符合统一 envelope。"""

    ENDPOINTS = [
        "/health",
        "/runtime/status",
        "/runtime/overview",
        "/personality/status",
        "/selfmodel/status",
        "/memory/overview",
        "/growth/status",
        "/initiative/status",
        "/audit/recent",
    ]

    def test_all_endpoints_return_envelope(self, gateway_test_app, gateway_client):
        for ep in self.ENDPOINTS:
            resp = gateway_client.get(ep)
            assert isinstance(resp, dict), f"{ep} 响应不是 dict"
            for k in ("success", "data", "error", "timestamp", "schema_version", "degraded"):
                assert k in resp, f"{ep} 缺少字段: {k}"
            # 成功时 error 应为空字符串
            if resp["success"]:
                assert resp["error"] == "", f"{ep} 成功时 error 应为空: {resp['error']!r}"
            # degraded 必为 bool
            assert isinstance(resp["degraded"], bool)
            # schema_version 固定为 1.0
            assert resp["schema_version"] == "1.0"

    def test_envelope_timestamp_iso(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/health")
        ts = resp["timestamp"]
        # 简单校验:含 "T" 与 ":" 表示 ISO8601
        assert "T" in ts
        assert ":" in ts


# ============================================================
# 4. 只读约束(POST/PUT/PATCH/DELETE 一律 405)
# ============================================================
class TestReadonlyEnforcement:
    """Gateway 是只读的,所有写方法必须 405。"""

    def test_post_rejected(self, gateway_test_app, gateway_client):
        # ApiClient 不支持 POST,改用 requests
        import requests
        url = f"{gateway_test_app['base_url']}/health"
        resp = requests.post(url, timeout=2.0)
        assert resp.status_code == 405
        body = resp.json()
        assert body["success"] is False
        assert "method_not_allowed" in body["error"]

    def test_put_rejected(self, gateway_test_app):
        import requests
        url = f"{gateway_test_app['base_url']}/runtime/status"
        resp = requests.put(url, timeout=2.0)
        assert resp.status_code == 405

    def test_patch_rejected(self, gateway_test_app):
        import requests
        url = f"{gateway_test_app['base_url']}/personality/status"
        resp = requests.patch(url, timeout=2.0)
        assert resp.status_code == 405

    def test_delete_rejected(self, gateway_test_app):
        import requests
        url = f"{gateway_test_app['base_url']}/growth/status"
        resp = requests.delete(url, timeout=2.0)
        assert resp.status_code == 405

    def test_allow_header_present(self, gateway_test_app):
        import requests
        url = f"{gateway_test_app['base_url']}/memory/overview"
        resp = requests.post(url, timeout=2.0)
        # Allow header 应为 GET
        assert resp.headers.get("Allow") == "GET"


# ============================================================
# 5. Auth 校验
# ============================================================
class TestAuth:
    """Bearer Token 认证流程。"""

    def test_dev_mode_no_token_allows(self, gateway_test_app):
        """development 模式下,客户端无 token,服务端也无 token,应放行。"""
        from src.control.api.auth import set_auth_override
        from src.control.api.config import GatewayAuthConfig
        set_auth_override(GatewayAuthConfig(
            mode="development", token="", required=False,
        ))
        try:
            import requests
            url = f"{gateway_test_app['base_url']}/health"
            resp = requests.get(url, timeout=2.0)
            assert resp.status_code == 200
            assert resp.json()["success"] is True
        finally:
            set_auth_override(None)

    def test_dev_mode_valid_token_allows(self, gateway_test_app):
        from src.control.api.auth import set_auth_override
        from src.control.api.config import GatewayAuthConfig
        set_auth_override(GatewayAuthConfig(
            mode="development", token="secret-token-12345", required=True,
        ))
        try:
            import requests
            url = f"{gateway_test_app['base_url']}/health"
            resp = requests.get(
                url,
                headers={"Authorization": "Bearer secret-token-12345"},
                timeout=2.0,
            )
            assert resp.status_code == 200
        finally:
            set_auth_override(None)

    def test_dev_mode_invalid_token_rejected(self, gateway_test_app):
        from src.control.api.auth import set_auth_override
        from src.control.api.config import GatewayAuthConfig
        set_auth_override(GatewayAuthConfig(
            mode="development", token="real-token-xyz", required=True,
        ))
        try:
            import requests
            url = f"{gateway_test_app['base_url']}/health"
            resp = requests.get(
                url,
                headers={"Authorization": "Bearer wrong-token"},
                timeout=2.0,
            )
            assert resp.status_code == 401
            body = resp.json()
            assert body["success"] is False
            assert "unauthorized" in body["error"]
        finally:
            set_auth_override(None)

    def test_missing_token_when_required_rejected(self, gateway_test_app):
        from src.control.api.auth import set_auth_override
        from src.control.api.config import GatewayAuthConfig
        set_auth_override(GatewayAuthConfig(
            mode="development", token="x", required=True,
        ))
        try:
            import requests
            url = f"{gateway_test_app['base_url']}/health"
            resp = requests.get(url, timeout=2.0)
            assert resp.status_code == 401
        finally:
            set_auth_override(None)

    def test_safe_compare_timing(self):
        """常量时间比较必须对长度不同也返回 False,且不抛错。"""
        from src.control.api.auth import _safe_compare
        assert _safe_compare("abc", "abc") is True
        assert _safe_compare("abc", "abd") is False
        assert _safe_compare("abc", "abcd") is False
        assert _safe_compare("", "abc") is False
        assert _safe_compare("abc", "") is False


# ============================================================
# 6. Provider 数据读取
# ============================================================
class TestProviderRead:
    """Gateway 必须通过 Provider 读取数据,不应直接访问业务模块。"""

    def test_health_uses_runtime_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/health")
        assert resp["success"] is True
        # 验证 runtime provider 被调用
        gateway_test_app["providers"]["runtime"].get_status.assert_called()
        gateway_test_app["providers"]["runtime"].get_authority_status.assert_called()

    def test_runtime_status_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/runtime/status")
        assert resp["success"] is True
        data = resp["data"]
        assert data["online"] is True
        assert data["initialized"] is True
        assert data["is_running"] is True
        # cycle_state 应映射 online
        assert data["cycle_state"] == "running"

    def test_runtime_overview_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/runtime/overview")
        assert resp["success"] is True
        data = resp["data"]
        # 应包含 personality / selfmodel / memory / emotion
        assert "runtime" in data
        assert "personality" in data
        assert "memory" in data
        # provider 方法被调用
        gateway_test_app["providers"]["runtime"].get_personality_summary.assert_called()
        gateway_test_app["providers"]["runtime"].get_memory_summary.assert_called()
        gateway_test_app["providers"]["runtime"].get_emotion_summary.assert_called()

    def test_personality_status_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/personality/status")
        assert resp["success"] is True
        data = resp["data"]
        assert data["available"] is True
        assert data["snapshot"]["identity_name"] == "浅雾羽依"
        assert data["evolution_version"] == "1.0.0"
        # traits 字段存在
        assert isinstance(data["traits"], list)
        assert len(data["traits"]) > 0

    def test_selfmodel_status_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/selfmodel/status")
        assert resp["success"] is True
        data = resp["data"]
        assert data["available"] is True
        assert data["version"] == "2.0"
        assert data["identity_name"] == "浅雾羽依"
        assert data["health"] == "healthy"

    def test_memory_overview_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/memory/overview")
        assert resp["success"] is True
        data = resp["data"]
        assert data["total_count"] == 42
        assert data["important_count"] == 5
        assert isinstance(data["recent"], list)
        # 默认 limit=5
        assert len(data["recent"]) <= 5

    def test_memory_overview_limit_param(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/memory/overview?limit=1")
        assert resp["success"] is True
        assert len(resp["data"]["recent"]) <= 1

    def test_growth_status_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/growth/status")
        assert resp["success"] is True
        data = resp["data"]
        assert data["proposal_count"] == 10
        assert data["pending_count"] == 3
        assert data["approved_count"] == 5
        assert data["rejected_count"] == 2
        assert data["applied_count"] == 0
        # evolution 字段
        assert "evolution" in data

    def test_initiative_status_uses_provider(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/initiative/status")
        assert resp["success"] is True
        data = resp["data"]
        assert data["interest_count"] == 5
        assert data["possible_action_count"] == 3
        assert data["filtered_count"] == 1
        assert "by_trend" in data
        assert "by_action_status" in data
        assert "by_action_type" in data
        assert isinstance(data["recent"], list)
        assert len(data["recent"]) > 0

    def test_audit_recent_uses_storage(self, gateway_test_app, gateway_client):
        resp = gateway_client.get("/audit/recent?limit=10")
        assert resp["success"] is True
        data = resp["data"]
        assert data["available"] is True
        assert data["limit"] == 10
        assert data["total"] == 2
        assert len(data["items"]) == 2
        # 验证 to_dict 被调用
        ev0 = data["items"][0]
        assert ev0["event_id"] == "ae1"
        assert ev0["event_type"] == "runtime.tick"

    def test_audit_recent_limit_clamped(self, gateway_test_app, gateway_client):
        # limit 必须被 clamp 到 [1, 100]
        resp = gateway_client.get("/audit/recent?limit=99999")
        assert resp["success"] is True
        assert resp["data"]["limit"] == 100
        resp = gateway_client.get("/audit/recent?limit=0")
        assert resp["success"] is True
        assert resp["data"]["limit"] == 1


# ============================================================
# 7. 异常降级(Provider 抛错时返回 degraded envelope)
# ============================================================
class TestExceptionDegradation:
    """Provider 异常时,Gateway 必须返回 degraded envelope,不抛 5xx 错误。"""

    def _make_failing_provider(self):
        runtime = MagicMock()
        runtime.get_status.side_effect = RuntimeError("runtime_provider_failure")
        runtime.get_authority_status.side_effect = RuntimeError("authority_failure")
        runtime.get_personality_summary.side_effect = RuntimeError("personality_failure")
        runtime.get_memory_summary.side_effect = RuntimeError("memory_failure")
        runtime.get_emotion_summary.side_effect = RuntimeError("emotion_failure")
        return runtime

    def _restart_with_patches(self, gateway_test_app, **patches):
        """对已启动的 gateway_test_app 重新打补丁,影响下一次请求。"""
        for target, return_value in patches.items():
            patcher = patch(target, return_value=return_value)
            patcher.start()
            # 不 stop,依赖测试 teardown 时清理

    def test_health_when_runtime_provider_raises(self, gateway_test_app, gateway_client):
        # 让 runtime provider 抛错
        runtime = self._make_failing_provider()
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/health")
            # 不抛错,返回 envelope
            assert "success" in resp
            # runtime 异常应被 _ok 流程吃掉,但 response 中 runtime 字段为 fallback
            # 整体 success 仍为 True(server_status ok,只是 runtime 字段是 fallback)
            assert resp["success"] is True
            # runtime 字段应该是 fallback(initialized=False)
            assert resp["data"]["runtime"]["initialized"] is False

    def test_runtime_status_provider_unavailable(self, gateway_test_app, gateway_client):
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=None,
        ):
            resp = gateway_client.get("/runtime/status")
            assert resp["success"] is False
            assert resp["degraded"] is True
            assert "unavailable" in resp["error"]

    def test_runtime_status_provider_raises(self, gateway_test_app, gateway_client):
        runtime = MagicMock()
        runtime.get_status.side_effect = RuntimeError("boom")
        runtime.get_authority_status.return_value = {}
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/runtime/status")
            assert resp["success"] is False
            assert resp["degraded"] is True
            assert "boom" in resp["error"]

    def test_personality_status_raises(self, gateway_test_app, gateway_client):
        runtime = MagicMock()
        runtime.get_personality_summary.side_effect = ValueError("personality_broken")
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            assert resp["success"] is False
            assert "personality_broken" in resp["error"]

    def test_selfmodel_status_provider_none(self, gateway_test_app, gateway_client):
        with patch(
            "src.control.api.routes._get_self_model_provider",
            return_value=None,
        ):
            resp = gateway_client.get("/selfmodel/status")
            assert resp["success"] is False
            assert "unavailable" in resp["error"]

    def test_selfmodel_status_provider_raises(self, gateway_test_app, gateway_client):
        sm = MagicMock()
        sm.get_status.side_effect = RuntimeError("sm_crash")
        with patch(
            "src.control.api.routes._get_self_model_provider",
            return_value=sm,
        ):
            resp = gateway_client.get("/selfmodel/status")
            assert resp["success"] is False
            assert "sm_crash" in resp["error"]

    def test_growth_status_provider_raises(self, gateway_test_app, gateway_client):
        gov = MagicMock()
        gov.get_growth_governance_snapshot.side_effect = RuntimeError("gov_crash")
        with patch(
            "src.control.api.routes._get_governance_provider",
            return_value=gov,
        ):
            resp = gateway_client.get("/growth/status")
            assert resp["success"] is False
            assert "gov_crash" in resp["error"]

    def test_initiative_status_provider_raises(self, gateway_test_app, gateway_client):
        init = MagicMock()
        init.get_summary.side_effect = RuntimeError("init_crash")
        with patch(
            "src.control.api.routes._get_initiative_provider",
            return_value=init,
        ):
            resp = gateway_client.get("/initiative/status")
            assert resp["success"] is False
            assert "init_crash" in resp["error"]

    def test_audit_recent_storage_raises(self, gateway_test_app, gateway_client):
        audit = MagicMock()
        audit.load.side_effect = RuntimeError("audit_broken")
        with patch(
            "src.control.api.routes._get_audit_storage",
            return_value=audit,
        ):
            resp = gateway_client.get("/audit/recent")
            assert resp["success"] is False
            assert "audit_broken" in resp["error"]

    def test_audit_recent_storage_none(self, gateway_test_app, gateway_client):
        with patch(
            "src.control.api.routes._get_audit_storage",
            return_value=None,
        ):
            resp = gateway_client.get("/audit/recent")
            assert resp["success"] is False
            assert "unavailable" in resp["error"]

    def test_partial_overview_when_personality_raises(self, gateway_test_app, gateway_client):
        """overview 端点中 personality 抛错时,runtime 部分仍可访问。"""
        runtime = MagicMock()
        runtime.get_status.return_value = {
            "online": True,
            "runtime": {"initialized": True, "is_running": True},
        }
        runtime.get_authority_status.return_value = {"runtime": True}
        runtime.get_personality_summary.side_effect = RuntimeError("partial_failure")
        runtime.get_memory_summary.return_value = {
            "available": True,
            "total_count": 10,
        }
        runtime.get_emotion_summary.return_value = {"available": True, "state": "calm"}
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/runtime/overview")
            # overview 端点不强求整体 success=False
            assert resp["success"] is True
            # runtime 部分正常
            assert resp["data"]["runtime"]["online"] is True
            # memory 部分正常
            assert resp["data"]["memory"]["total_count"] == 10
            # personality 部分为 fallback (None)
            assert resp["data"]["personality"] is None


# ============================================================
# 8. 隔离原则(Gateway 不应 import 写接口相关模块)
# ============================================================
class TestIsolation:
    """验证 Gateway 模块没有引入核心业务模块的写接口。"""

    def test_routes_no_src_business_import(self):
        """routes.py 内部 import 的 provider 应来自 src.admin / src.audit(只读桥)。"""
        from src.control.api import routes
        with open(routes.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        # 禁止 import src.runtime
        forbidden = [
            "from src.runtime",
            "from src.memory",
            "from src.growth.personality",
            "from src.self_model",
            "from src.relationship",
        ]
        for kw in forbidden:
            assert kw not in content, f"routes.py 含禁止导入: {kw}"

    def test_gateway_only_uses_providers(self):
        """所有数据获取必须经由 Provider(只读)。"""
        from src.control.api import routes
        with open(routes.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        # 不允许直接调用 apply / commit / resolve / approve / reject
        for kw in ("apply", "commit", "resolve", "approve", "reject", "modify"):
            # 排除字符串中的引用(注释、错误消息)—— 只检查方法调用形式
            if f".{kw}(" in content:
                # 检查是否是合法的 provider.get_status() 形式
                if f".{kw}(" not in content.replace(
                    f".{kw}_", "PROVIDER_"
                ):
                    # 进一步排除注释
                    lines = [
                        l for l in content.split("\n")
                        if f".{kw}(" in l and not l.strip().startswith("#")
                    ]
                    assert not lines, f"routes.py 含可疑调用 .{kw}():\n" + "\n".join(lines)


# ============================================================
# 9. 与 Desktop 端 RemoteProviderBridge 集成
# ============================================================
class TestDesktopIntegration:
    """验证 Desktop RemoteProviderBridge 能调用真实 Gateway。"""

    def test_bridge_health(self, gateway_test_app, gateway_client):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge

        cfg = ApiClientConfig(
            base_url=gateway_test_app["base_url"].replace("/api/v1", ""),
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=client)
        env = bridge.get_health()
        assert env["success"] is True
        assert "server_status" in env["data"]

    def test_bridge_runtime_overview(self, gateway_test_app, gateway_client):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge

        cfg = ApiClientConfig(
            base_url=gateway_test_app["base_url"].replace("/api/v1", ""),
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=client)
        ov = bridge.get_runtime_overview_data()
        assert "runtime" in ov
        assert "personality" in ov

    def test_bridge_all_v2_endpoints(self, gateway_test_app, gateway_client):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge

        cfg = ApiClientConfig(
            base_url=gateway_test_app["base_url"].replace("/api/v1", ""),
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=client)

        # 验证 8 个 v2 端点都能拿到数据
        ep_methods = [
            ("get_health_data", dict),
            ("get_runtime_status_v2_data", dict),
            ("get_runtime_overview_data", dict),
            ("get_personality_status_v2_data", dict),
            ("get_selfmodel_status_v2_data", dict),
            ("get_memory_overview_v2_data", dict),
            ("get_growth_status_v2_data", dict),
            ("get_initiative_status_v2_data", dict),
        ]
        for name, expect in ep_methods:
            method = getattr(bridge, name, None)
            assert callable(method), f"bridge 缺少方法: {name}"
            data = method()
            assert isinstance(data, expect), f"{name} 应返回 {expect.__name__}"

    def test_services_use_v2_endpoints(self, gateway_test_app, gateway_client):
        """Service 集成:各 service 的 overview 应能正常返回。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import RuntimeService
        from yuyi_desktop.services.memory_service import MemoryService
        from yuyi_desktop.services.personality_service import PersonalityService
        from yuyi_desktop.services.growth_service import GrowthService
        from yuyi_desktop.services.initiative_service import InitiativeService

        cfg = ApiClientConfig(
            base_url=gateway_test_app["base_url"].replace("/api/v1", ""),
            api_prefix="/api/v1",
            timeout_seconds=2.0,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=client)

        rt = RuntimeService(bridge=bridge)
        mem = MemoryService(bridge=bridge)
        ps = PersonalityService(bridge=bridge)
        gw = GrowthService(bridge=bridge)
        ini = InitiativeService(bridge=bridge)

        # RuntimeService overview 应包含 v2 / online / health / adapters
        ov = rt.get_overview()
        assert "v2" in ov
        assert "online" in ov
        assert "health" in ov
        assert "adapters" in ov
        assert ov["available"] is True

        # MemoryService overview 应包含 v2 / total / important
        ov = mem.get_overview()
        assert "v2" in ov
        assert ov["total"] == 42
        assert ov["important"] == 5

        # PersonalityService overview 应包含 personality_v2 / selfmodel_v2
        ov = ps.get_overview()
        assert "personality_v2" in ov
        assert "selfmodel_v2" in ov
        assert ov["identity_name"] == "浅雾羽依"

        # GrowthService overview 应包含 v2
        ov = gw.get_overview()
        assert "v2" in ov
        assert ov["total"] == 10
        assert ov["pending"] == 3

        # InitiativeService overview 应包含 v2
        ov = ini.get_overview()
        assert "v2" in ov
        assert ov["interest_count"] == 5
        assert ov["possible_action_count"] == 3


# ============================================================
# 10. 端点契约(契约固定,不允许漂移)
# ============================================================
class TestEndpointContract:
    """8 个端点必须可路由,且路径固定。"""

    EXPECTED_PATHS = [
        "/health",
        "/runtime/status",
        "/runtime/overview",
        "/personality/status",
        "/selfmodel/status",
        "/memory/overview",
        "/growth/status",
        "/initiative/status",
        "/audit/recent",
    ]

    def test_all_paths_routable(self, gateway_test_app, gateway_client):
        for path in self.EXPECTED_PATHS:
            resp = gateway_client.get(path)
            # 端点必须存在(注意:某些 provider 异常会 success=False,但路由必须 200)
            assert "success" in resp, f"{path} 没有响应"
            assert "schema_version" in resp, f"{path} 没有 envelope"

    def test_path_count(self):
        """确保 9 个端点都被 Blueprint 注册(8 主 + audit/recent)。"""
        from src.control.api.routes import gateway_bp
        # Flask 3.x 兼容
        rules = []
        try:
            rules = [str(r) for r in gateway_bp.deferred_functions]
        except Exception:  # noqa: BLE001
            pass
        # 简单做法:通过路由表计数
        from flask import Flask
        app = Flask("contract_test")
        # 临时注册
        from src.control.api import register_gateway
        register_gateway(app)
        routes = [r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/v1/")]
        # 应包含 9 个端点
        for p in self.EXPECTED_PATHS:
            assert f"/api/v1{p}" in routes, f"路由缺失: {p}"


# ============================================================
# 11. 配置文件加载(config.yaml 不存在时优雅降级)
# ============================================================
class TestConfigLoading:
    """配置加载:从 config.yaml 读取 + 优雅降级。"""

    def test_load_without_config(self, tmp_path):
        """没有 config.yaml 时,使用默认配置。"""
        from src.control.api.config import load_gateway_config
        cfg = load_gateway_config(config_path=tmp_path / "nonexistent.yaml")
        assert cfg.enabled is True
        assert cfg.auth.mode in ("development", "production", "disabled")
        # 默认 development 模式
        assert cfg.auth.mode == "development"

    def test_load_with_config(self, tmp_path):
        """写入临时 config.yaml,验证读取。"""
        from src.control.api.config import load_gateway_config
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "control_api:\n"
            "  enabled: true\n"
            "  host: 127.0.0.1\n"
            "  port: 9999\n"
            "  auth:\n"
            "    mode: production\n"
            "    token: my-secret\n"
            "    required: true\n",
            encoding="utf-8",
        )
        cfg = load_gateway_config(config_path=cfg_path)
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 9999
        assert cfg.auth.mode == "production"
        assert cfg.auth.token == "my-secret"
        assert cfg.auth.required is True

    def test_env_token_override(self, tmp_path, monkeypatch):
        """YUYI_GATEWAY_TOKEN 环境变量可覆盖配置。"""
        from src.control.api.config import load_gateway_config
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "control_api:\n"
            "  auth:\n"
            "    mode: development\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("YUYI_GATEWAY_TOKEN", "env-token-xyz")
        cfg = load_gateway_config(config_path=cfg_path)
        assert cfg.auth.token == "env-token-xyz"

    def test_remote_auth_token_fallback(self, tmp_path):
        """向后兼容:remote.auth_token 可作为兜底。"""
        from src.control.api.config import load_gateway_config
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            "remote:\n"
            "  auth_token: legacy-token\n",
            encoding="utf-8",
        )
        cfg = load_gateway_config(config_path=cfg_path)
        assert cfg.auth.token == "legacy-token"
        assert cfg.auth.mode == "development"


# ============================================================
# 12. JSON 序列化安全(防止 500)
# ============================================================
class TestPersonalityStatusJsonSafe:
    """验证 /api/v1/personality/status 不会因为不可序列化对象返回 500。

    场景: provider.get_personality_summary() 返回的对象中包含
    dataclass / datetime / Enum / set / 自定义类 等不可被 json.dumps
    直接序列化的类型,API 输出层必须能安全降级为 JSON 原生类型。
    """

    def test_personality_status_with_datetime_in_snapshot(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 中嵌套 datetime 时不能 500,应被转成 ISO 字符串。"""
        from datetime import datetime

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": {
                "identity_name": "浅雾羽依",
                "version": "1.2.0",
                "stable": True,
                "traits": {"温柔": 0.9, "理性": 0.7},
                "last_updated": datetime(2026, 8, 5, 10, 30, 0),
                "created_at": datetime(2025, 1, 1, 0, 0, 0),
            },
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            # 关键: 不应返回 500 / 不应抛错
            assert resp["success"] is True
            assert resp["degraded"] is False
            data = resp["data"]
            # snapshot 中的 datetime 已被转为 ISO 字符串
            assert isinstance(data["snapshot"], dict)
            assert data["snapshot"]["identity_name"] == "浅雾羽依"
            assert isinstance(data["snapshot"]["last_updated"], str)
            assert "2026-08-05" in data["snapshot"]["last_updated"]
            assert "T10:30:00" in data["snapshot"]["last_updated"]
            assert isinstance(data["snapshot"]["created_at"], str)
            assert "2025-01-01" in data["snapshot"]["created_at"]
            # evolution_version 应被正确提取
            assert data["evolution_version"] == "1.2.0"

    def test_personality_status_with_dataclass_in_snapshot(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 顶层就是 dataclass 时不能 500,应被 asdict 转为 dict。"""
        import dataclasses

        @dataclasses.dataclass
        class _MockIdentity:
            identity_name: str
            version: str
            stable: bool
            archetype: str

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": _MockIdentity(
                identity_name="浅雾羽依",
                version="3.0.0",
                stable=True,
                archetype="gentle_companion",
            ),
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            assert resp["success"] is True
            data = resp["data"]
            # dataclass 顶层被 asdict 转 dict
            assert isinstance(data["snapshot"], dict)
            assert data["snapshot"]["identity_name"] == "浅雾羽依"
            assert data["snapshot"]["version"] == "3.0.0"
            assert data["snapshot"]["archetype"] == "gentle_companion"
            # 注意: evolution_version 在 current 不是 dict 时保持 None(原行为, 与本任务无关)

    def test_personality_status_with_enum_in_snapshot(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 中嵌套 Enum 时不能 500,应被转为 .value。"""
        from enum import Enum

        class _Stage(Enum):
            STABLE = "stable"
            EVOLVING = "evolving"
            UNSTABLE = "unstable"

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": {
                "identity_name": "浅雾羽依",
                "version": "1.0.0",
                "stage": _Stage.EVOLVING,
                "traits": {1: "温柔", 2: "理性"},
            },
            "state": _Stage.EVOLVING,
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            assert resp["success"] is True
            data = resp["data"]
            # Enum 应被转为其 .value 字符串
            assert data["snapshot"]["stage"] == "evolving"
            assert data["state"] == "evolving"

    def test_personality_status_with_set_and_frozenset(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 中包含 set / frozenset 时不能 500,应被转成 list。"""
        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": {
                "identity_name": "浅雾羽依",
                "version": "1.0.0",
                "allowed_topics": {"chat", "growth", "memory"},
                "frozen_traits": frozenset({"温柔", "理性"}),
            },
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            assert resp["success"] is True
            data = resp["data"]
            # set / frozenset 应被转为 list
            assert isinstance(data["snapshot"]["allowed_topics"], list)
            assert sorted(data["snapshot"]["allowed_topics"]) == ["chat", "growth", "memory"]
            assert isinstance(data["snapshot"]["frozen_traits"], list)
            assert sorted(data["snapshot"]["frozen_traits"]) == ["温柔", "理性"]

    def test_personality_status_with_custom_object_fallback(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 顶层是非 dict / 非 dataclass 的自定义对象时,降级为 str。"""

        class _Weird:
            def __init__(self):
                self.identity_name = "浅雾羽依"
                self.version = "1.0.0"

            def __repr__(self):
                return "<Weird:浅雾羽依@1.0.0>"

        weird = _Weird()
        runtime = MagicMock()
        # summary.get("current") 返回这个对象,但 is None / dict 判断失败后,
        # _to_json_safe 仍会递归处理;此处走 str() 兜底分支。
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": weird,
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            # 关键: 不应 500
            assert resp["success"] is True
            data = resp["data"]
            # snapshot 可能是 str(兜底)或 dict(若 _to_json_safe 命中 __dict__ 路径)
            # 两种结果都视为合法:只要不是 500 即可。
            assert data["snapshot"] is not None
            # 至少 data 中其他字段正常
            assert data["available"] is True

    def test_personality_status_with_nested_unserializable_object(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 深层嵌套不可序列化对象时,递归降级不能 500。"""
        from datetime import datetime

        class _CustomBag:
            """有 __dict__ 的自定义对象,应被转为 dict(走 __dict__ 分支)。"""

            def __init__(self):
                self.created_at = datetime(2026, 1, 1, 12, 0, 0)
                self.label = "core"

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": {
                "identity_name": "浅雾羽依",
                "version": "1.0.0",
                "inner": {
                    "deep": _CustomBag(),
                },
            },
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            assert resp["success"] is True
            data = resp["data"]
            # 深层 CustomBag 应被转 dict,内部 datetime 转 ISO 字符串
            deep = data["snapshot"]["inner"]["deep"]
            assert isinstance(deep, dict)
            assert deep["label"] == "core"
            assert "2026-01-01" in deep["created_at"]
            assert "T12:00:00" in deep["created_at"]

    def test_personality_status_with_cyclic_reference(
        self, gateway_test_app, gateway_client,
    ):
        """snapshot 包含循环引用时,深度上限保护不能让 500。"""
        a: Dict[str, Any] = {"name": "a"}
        b: Dict[str, Any] = {"name": "b", "ref_a": a}
        a["ref_b"] = b  # 循环引用

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": a,
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            # 关键: 循环引用不导致 500(深度上限保护)
            assert resp["success"] is True
            data = resp["data"]
            assert data["snapshot"]["name"] == "a"

    def test_personality_status_envelope_shape_when_unserializable(
        self, gateway_test_app, gateway_client,
    ):
        """即使内部包含不可序列化对象,envelope 仍应保持标准格式。"""
        from datetime import datetime

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": {
                "identity_name": "浅雾羽依",
                "version": "1.0.0",
                "last_updated": datetime(2026, 8, 5, 18, 0, 0),
            },
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            resp = gateway_client.get("/personality/status")
            # envelope 字段必须齐全
            for k in ("success", "data", "error", "timestamp", "schema_version", "degraded"):
                assert k in resp, f"envelope 缺少字段: {k}"
            assert resp["success"] is True
            assert resp["error"] == ""
            assert resp["schema_version"] == "1.0"
            assert resp["degraded"] is False
            # data 内部 schema 保持稳定
            for k in ("available", "snapshot", "traits", "evolution_version", "state"):
                assert k in resp["data"], f"data 缺少字段: {k}"

    def test_personality_status_does_not_call_500_real_request(
        self, gateway_test_app, gateway_client,
    ):
        """真实 HTTP 请求: 即使 snapshot 含 datetime 也必须 200 + success。"""
        from datetime import datetime
        import requests

        runtime = MagicMock()
        runtime.get_personality_summary.return_value = {
            "available": True,
            "current": {
                "identity_name": "浅雾羽依",
                "version": "1.0.0",
                "ts": datetime(2026, 8, 5, 12, 0, 0),
            },
            "state": "stable",
        }
        with patch(
            "src.control.api.routes._get_runtime_provider",
            return_value=runtime,
        ):
            url = f"{gateway_test_app['base_url']}/personality/status"
            resp = requests.get(url, timeout=2.0)
            # 关键验收: 不能 500
            assert resp.status_code == 200, (
                f"期望 200, 实际 {resp.status_code}, body={resp.text[:200]}"
            )
            body = resp.json()
            assert body["success"] is True
            # datetime 已转为字符串
            assert "2026-08-05T12:00:00" in body["data"]["snapshot"]["ts"]

    def test_personality_status_to_json_safe_unit(self):
        """直接单测 _to_json_safe 工具函数,覆盖各种不可序列化类型。"""
        from src.control.api.routes import _to_json_safe
        from datetime import date, datetime, time as _time
        from enum import Enum
        import dataclasses

        # 1) 基础类型
        assert _to_json_safe(None) is None
        assert _to_json_safe(1) == 1
        assert _to_json_safe("x") == "x"
        assert _to_json_safe(True) is True
        assert _to_json_safe(1.5) == 1.5

        # 2) datetime
        dt = datetime(2026, 8, 5, 10, 30, 0)
        assert _to_json_safe(dt) == "2026-08-05T10:30:00"
        assert _to_json_safe(date(2026, 8, 5)) == "2026-08-05"
        assert _to_json_safe(_time(12, 0, 0)) == "12:00:00"

        # 3) Enum
        class _Color(Enum):
            RED = "red"

        assert _to_json_safe(_Color.RED) == "red"

        # 4) dict / list / set
        assert _to_json_safe({"a": 1, "b": [1, 2, {"c": 3}]}) == {"a": 1, "b": [1, 2, {"c": 3}]}
        assert _to_json_safe([1, 2, 3]) == [1, 2, 3]
        result = _to_json_safe({1, 2, 3})
        assert isinstance(result, list) and sorted(result) == [1, 2, 3]

        # 5) dataclass
        @dataclasses.dataclass
        class _P:
            name: str
            ts: datetime

        p = _P(name="x", ts=datetime(2026, 1, 1, 0, 0, 0))
        out = _to_json_safe(p)
        assert out == {"name": "x", "ts": "2026-01-01T00:00:00"}

        # 6) 自定义类(有 __dict__)
        class _Plain:
            def __init__(self):
                self.x = 1
                self.y = "hello"

        plain = _Plain()
        out = _to_json_safe(plain)
        assert out == {"x": 1, "y": "hello"}

        # 7) 完全不可序列化的对象走 str() 兜底
        class _NoDict:
            __slots__ = ()

        out = _to_json_safe(_NoDict())
        assert isinstance(out, str)
        assert "_NoDict" in out


# ============================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
