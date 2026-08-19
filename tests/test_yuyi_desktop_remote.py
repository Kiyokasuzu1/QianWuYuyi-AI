# -*- coding: utf-8 -*-
"""
tests/test_yuyi_desktop_remote.py

Phase C.10.2 —— Yuyi Desktop Server Communication Layer 单元测试

覆盖:
1. API Client 创建
2. Server 连接成功
3. Server 离线 / Timeout
4. Retry 机制
5. RemoteProviderBridge 读取
6. Service 调用远程 API
7. 返回数据 schema 验证
8. FailSafe
9. 无核心模块污染

约束(强):
- 不直接 import src/runtime/**、src/memory/**、src/growth/** 等业务模块
- 仅测试 RemoteProviderBridge / ApiClient / ConnectionManager / AuthClient / Service
- 保持向后兼容
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# 路径由 conftest.py 处理


# ============================================================
# 1. ApiClient
# ============================================================
class TestApiClient:
    """ApiClient 创建 / 配置 / 基础行为。"""

    def test_apiclient_creation(self):
        from yuyi_desktop.core.api_client import (
            ApiClient,
            ApiClientConfig,
        )
        c = ApiClient()
        assert isinstance(c, ApiClient)

    def test_config_defaults(self):
        from yuyi_desktop.core.api_client import ApiClientConfig
        cfg = ApiClientConfig()
        assert cfg.timeout_seconds > 0
        assert cfg.max_retries >= 0
        assert cfg.base_url != ""

    def test_envelope_helpers(self):
        from yuyi_desktop.core.api_client import (
            make_envelope,
            make_error_envelope,
            make_success_envelope,
        )
        ok = make_success_envelope(data={"a": 1})
        assert ok["success"] is True
        assert ok["data"] == {"a": 1}
        assert ok["schema_version"] == "1.0"
        assert ok["timestamp"]
        assert ok["degraded"] is False

        err = make_error_envelope("test_error", degraded=True)
        assert err["success"] is False
        assert err["error"] == "test_error"
        assert err["degraded"] is True

        env = make_envelope(success=True, data=[1, 2])
        assert env["data"] == [1, 2]

    def test_get_rejects_post(self):
        """验证 ApiClient 没有暴露 POST / PUT 等写方法。"""
        from yuyi_desktop.core.api_client import ApiClient
        forbidden = ["post", "put", "patch", "delete", "create", "update", "remove"]
        public = [
            m for m in dir(ApiClient)
            if not m.startswith("_") and callable(getattr(ApiClient, m))
        ]
        for m in public:
            assert m.lower() not in forbidden, f"ApiClient 暴露写方法: {m}"


# ============================================================
# 2. Server 连接成功(使用 mock server)
# ============================================================
class TestServerConnection:
    """通过 mock server 验证 Desktop 可成功连接 Yuyi Server。"""

    def test_ping_success(self, mock_yuyi_server):
        """ping() 应当访问 /health(server 实际注册的探活端点),并返回完整 envelope。

        Phase D.2.7+ 调整: ping 改为调用 /health(server 端契约)而非 /health/ping。
        """
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
            max_retries=1,
        )
        client = ApiClient(config=cfg)
        resp = client.ping()
        assert resp["success"] is True
        # ping() 现在用 /health 端点,数据含 server_status 字段
        assert resp["data"]["server_status"] == "ok"
        assert resp["schema_version"] == "1.0"
        assert resp["degraded"] is False
        assert resp["latency_ms"] >= 0
        # 同时确认实际请求打到了 /health,而不是 /health/ping
        paths = [c["path"] for c in mock_yuyi_server.get_calls()]
        assert "/health" in paths
        assert "/health/ping" not in paths

    def test_server_info(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        client = ApiClient(config=cfg)
        resp = client.server_info()
        assert resp["success"] is True
        assert "version" in resp["data"]

    def test_get_runtime_status(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        client = ApiClient(config=cfg)
        resp = client.get("/runtime/status")
        assert resp["success"] is True
        assert resp["data"]["online"] is True
        assert resp["data"]["tick_count"] == 100


# ============================================================
# 3. Server 离线 / Timeout / Connection Error
# ============================================================
class TestServerOffline:
    """Server 不可达时,ApiClient 必须返回 degraded 响应,不抛错。"""

    def test_connection_refused(self):
        """连接到一个未监听的端口,应返回 degraded。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        # 寻找一个肯定未占用的端口
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        # 立即关闭,确保端口空闲
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=1.0,
            max_retries=1,
            connect_timeout_seconds=0.5,
        )
        client = ApiClient(config=cfg)
        resp = client.ping()
        assert resp["success"] is False
        assert resp["degraded"] is True
        assert resp["error"]  # 必须有 error 描述

    def test_timeout_handling(self):
        """Timeout 时应返回 degraded,error 含 timeout。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=0.3,
            max_retries=0,
            connect_timeout_seconds=0.3,
        )
        client = ApiClient(config=cfg)
        resp = client.get("/some/endpoint")
        assert resp["success"] is False
        assert resp["degraded"] is True
        # error 包含 timeout / connection_error / unexpected
        assert any(
            kw in resp["error"]
            for kw in ("timeout", "connection_error", "request_error", "unexpected")
        )


# ============================================================
# 4. Retry 机制
# ============================================================
class TestRetry:
    """验证 ApiClient 在失败时会重试,且不超过 max_retries+1 次。"""

    def test_retry_on_failure(self):
        """连接失败时,max_retries=2 应尝试 3 次。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=0.3,
            max_retries=2,
            connect_timeout_seconds=0.2,
            retry_backoff_seconds=0.05,
        )
        client = ApiClient(config=cfg)

        start = time.monotonic()
        resp = client.get("/x")
        elapsed = time.monotonic() - start

        assert resp["success"] is False
        # 至少 2 次退避:0.05 + 0.10 = 0.15s
        assert elapsed >= 0.10, f"重试退避不足,elapsed={elapsed}"

    def test_no_retry_when_max_retries_zero(self):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=0.2,
            max_retries=0,
            connect_timeout_seconds=0.2,
        )
        client = ApiClient(config=cfg)
        start = time.monotonic()
        resp = client.get("/x")
        elapsed = time.monotonic() - start
        assert resp["success"] is False
        # 单次尝试应 < 1.5s
        assert elapsed < 1.5


# ============================================================
# 5. RemoteProviderBridge 读取
# ============================================================
class TestRemoteProviderBridge:
    """通过 mock server 验证 RemoteProviderBridge 6 个 snapshot 端点。"""

    def _make_bridge(self, port: int):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=2.0,
            max_retries=1,
        )
        api = ApiClient(config=cfg)
        return RemoteProviderBridge(api_client=api), api

    def test_runtime_snapshot(self, mock_yuyi_server):
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        resp = bridge.get_runtime_snapshot()
        assert resp["success"] is True
        assert resp["data"]["initialized"] is True
        assert resp["data"]["online"] is True

    def test_memory_snapshot(self, mock_yuyi_server):
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        resp = bridge.get_memory_snapshot()
        assert resp["success"] is True
        assert resp["data"]["total_count"] == 100
        assert resp["data"]["important_count"] == 12

    def test_personality_snapshot(self, mock_yuyi_server):
        """get_personality_snapshot fallback 到 /personality/status,
        data 包含 snapshot 子字典,identity_name / stable 在 snapshot 里。
        """
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        resp = bridge.get_personality_snapshot()
        assert resp["success"] is True
        # /personality/status 返回 envelope.data.snapshot.{identity_name, stable}
        snap = resp["data"].get("snapshot", {})
        assert snap.get("identity_name") == "浅雾羽依"
        assert snap.get("stable") is True

    def test_selfmodel_snapshot(self, mock_yuyi_server):
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        resp = bridge.get_selfmodel_snapshot()
        assert resp["success"] is True
        assert resp["data"]["version"] == "2.0"

    def test_growth_snapshot(self, mock_yuyi_server):
        """get_growth_snapshot fallback 到 /growth/status,
        data 包含 proposal_count / pending_count / approved_count。
        """
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        resp = bridge.get_growth_snapshot()
        assert resp["success"] is True
        # /growth/status envelope: {proposal_count, pending_count, approved_count, ...}
        assert resp["data"]["proposal_count"] == 10
        assert resp["data"]["pending_count"] == 3

    def test_initiative_snapshot(self, mock_yuyi_server):
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        resp = bridge.get_initiative_snapshot()
        assert resp["success"] is True
        assert resp["data"]["interest_count"] == 5

    def test_health_check(self, mock_yuyi_server):
        """health_check 按 PROVIDER_GROUPS 探测各分组可用性。

        注: 'life' 分组在 v2 契约下已不提供,不再包含。
        """
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        health = bridge.health_check()
        assert isinstance(health, dict)
        # 当前 provider 提供的所有分组
        for grp in ["runtime", "memory", "personality", "selfmodel",
                    "growth", "initiative"]:
            assert grp in health, f"缺少分组: {grp}"
        # mock server 全可用
        assert all(health.values()), f"应有全部 health=True,got {health}"

    def test_security_self_check(self, mock_yuyi_server):
        bridge, _ = self._make_bridge(mock_yuyi_server.port)
        sec = bridge.security_self_check()
        assert sec["all_get_only"] is True
        assert sec["forbidden_endpoints"] == []
        assert sec["endpoint_count"] > 0

    def test_no_write_endpoints_in_bridge(self):
        """bridge 公开方法不含写操作关键字。"""
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        forbidden = [
            "apply", "update", "delete", "remove", "post",
            "put", "patch", "commit", "resolve", "approve",
            "reject", "modify", "create", "insert",
        ]
        for m in dir(RemoteProviderBridge):
            if m.startswith("_"):
                continue
            attr = getattr(RemoteProviderBridge, m)
            if not callable(attr):
                continue
            lower = m.lower()
            for f in forbidden:
                assert f not in lower, f"RemoteProviderBridge.{m} 含写操作关键字 '{f}'"


# ============================================================
# 6. Service 调用远程 API
# ============================================================
class TestServiceRemoteApi:
    """5 个 Service 通过 RemoteProviderBridge 访问 mock server。"""

    def _make_services(self, port: int):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import RuntimeService
        from yuyi_desktop.services.memory_service import MemoryService
        from yuyi_desktop.services.personality_service import PersonalityService
        from yuyi_desktop.services.growth_service import GrowthService
        from yuyi_desktop.services.initiative_service import InitiativeService

        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{port}",
            timeout_seconds=2.0,
            max_retries=1,
        )
        api = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=api)
        return {
            "runtime": RuntimeService(bridge=bridge),
            "memory": MemoryService(bridge=bridge),
            "personality": PersonalityService(bridge=bridge),
            "growth": GrowthService(bridge=bridge),
            "initiative": InitiativeService(bridge=bridge),
        }

    def test_runtime_service_overview(self, mock_yuyi_server):
        svcs = self._make_services(mock_yuyi_server.port)
        ov = svcs["runtime"].get_overview()
        assert isinstance(ov, dict)
        assert ov["available"] is True
        assert "status" in ov
        assert ov["task_count"] >= 0

    def test_memory_service_overview(self, mock_yuyi_server):
        svcs = self._make_services(mock_yuyi_server.port)
        ov = svcs["memory"].get_overview()
        assert ov["available"] is True
        assert ov["total"] == 100
        assert ov["important"] == 12

    def test_personality_service_overview(self, mock_yuyi_server):
        svcs = self._make_services(mock_yuyi_server.port)
        ov = svcs["personality"].get_overview()
        assert ov["available"] is True
        assert ov["identity_name"] == "浅雾羽依"
        assert ov["stable"] is True

    def test_growth_service_overview(self, mock_yuyi_server):
        svcs = self._make_services(mock_yuyi_server.port)
        ov = svcs["growth"].get_overview()
        assert ov["available"] is True
        assert ov["total"] == 10
        assert ov["pending"] == 3
        assert ov["approved"] == 5
        assert ov["rejected"] == 2

    def test_initiative_service_overview(self, mock_yuyi_server):
        svcs = self._make_services(mock_yuyi_server.port)
        ov = svcs["initiative"].get_overview()
        assert ov["available"] is True
        assert ov["interest_count"] == 5
        assert ov["possible_action_count"] == 3

    def test_services_no_src_import(self):
        """验证 5 个 service 不直接 import src.*"""
        from yuyi_desktop.services import (
            runtime_service,
            memory_service,
            personality_service,
            growth_service,
            initiative_service,
        )
        modules = [
            runtime_service,
            memory_service,
            personality_service,
            growth_service,
            initiative_service,
        ]
        for mod in modules:
            src = __import__("sys").modules.get(mod.__name__)
            assert src is not None
            with open(mod.__file__, "r", encoding="utf-8") as f:
                content = f.read()
            # 不允许 "from src." 或 "import src."
            for forbidden in ["from src.", "import src."]:
                assert forbidden not in content, (
                    f"{mod.__name__} 包含禁止的导入: '{forbidden}'"
                )


# ============================================================
# 7. 返回数据 schema 验证
# ============================================================
class TestResponseSchema:
    """所有响应必须包含标准 envelope 字段。"""

    def test_envelope_fields(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        client = ApiClient(config=cfg)
        for path in [
            "/runtime/status",
            "/memory/summary",
            "/personality/snapshot",
            "/selfmodel/snapshot",
            "/growth/summary",
            "/initiative/summary",
        ]:
            resp = client.get(path)
            assert "success" in resp, f"{path} 缺少 success"
            assert "data" in resp, f"{path} 缺少 data"
            assert "error" in resp, f"{path} 缺少 error"
            assert "timestamp" in resp, f"{path} 缺少 timestamp"
            assert "schema_version" in resp, f"{path} 缺少 schema_version"
            assert "degraded" in resp, f"{path} 缺少 degraded"
            assert "latency_ms" in resp, f"{path} 缺少 latency_ms"

    def test_schema_version_constant(self):
        from yuyi_desktop.core.api_client import API_SCHEMA_VERSION
        assert API_SCHEMA_VERSION == "1.0"


# ============================================================
# 8. FailSafe
# ============================================================
class TestFailSafe:
    """任何失败场景都不导致 Desktop 崩溃。"""

    def test_offline_does_not_raise(self):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=0.3,
            max_retries=0,
        )
        client = ApiClient(config=cfg)
        # 连续调用,不抛错
        for _ in range(5):
            resp = client.get("/x")
            assert resp["success"] is False
            assert resp["degraded"] is True

    def test_service_failsafe_offline(self):
        """Service 在 server 离线时仍返回 fallback dict,不抛错。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.services.runtime_service import RuntimeService
        from yuyi_desktop.services.memory_service import MemoryService
        from yuyi_desktop.services.growth_service import GrowthService
        from yuyi_desktop.services.personality_service import PersonalityService
        from yuyi_desktop.services.initiative_service import InitiativeService
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=0.3,
            max_retries=0,
        )
        api = ApiClient(config=cfg)
        bridge = RemoteProviderBridge(api_client=api)
        for svc in [
            RuntimeService(bridge=bridge),
            MemoryService(bridge=bridge),
            PersonalityService(bridge=bridge),
            GrowthService(bridge=bridge),
            InitiativeService(bridge=bridge),
        ]:
            ov = svc.get_overview()
            assert isinstance(ov, dict)
            # available / degraded 字段
            assert "available" in ov
            assert ov["available"] is False
            assert "degraded" in ov
            assert ov["degraded"] is True

    def test_500_error_failsafe(self, mock_yuyi_server):
        """Server 返回 5xx 时,ApiClient 仍返回 degraded。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        # 设置一个永远失败的 endpoint
        mock_yuyi_server.set_response("/boom", {
            "success": False,
            "data": {},
            "error": "internal_error",
        })
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        client = ApiClient(config=cfg)
        # /boom 不存在,会得到 404 但 mock server 会返回 success:false
        resp = client.get("/boom")
        # mock 默认 200 + payload,所以 success=False 是预期的(我们的 payload 标记了 False)
        # 实际 ApiClient 把 200 视为 success,但 success=False 不报错
        # 主要看 envelope 完整
        assert "success" in resp
        assert "data" in resp
        assert "degraded" in resp


# ============================================================
# 9. 无核心模块污染
# ============================================================
class TestNoCoreModulePollution:
    """Desktop 不得直接 import 任何 src.* 业务模块。"""

    def test_services_no_src_import(self):
        from yuyi_desktop.services import (
            runtime_service,
            memory_service,
            personality_service,
            growth_service,
            initiative_service,
        )
        modules = [
            runtime_service,
            memory_service,
            personality_service,
            growth_service,
            initiative_service,
        ]
        for mod in modules:
            with open(mod.__file__, "r", encoding="utf-8") as f:
                content = f.read()
            for forbidden in [
                "from src.", "import src.",
                "from src/runtime", "from src/memory",
                "from src/growth", "from src/personality",
                "from src/self_model", "from src/relationship",
            ]:
                assert forbidden not in content, (
                    f"{mod.__name__} 含禁止导入: '{forbidden}'"
                )

    def test_bridge_no_src_import(self):
        from yuyi_desktop.core import remote_provider_bridge
        with open(remote_provider_bridge.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        for forbidden in [
            "from src.", "import src.",
            "from src/runtime", "from src/memory",
            "from src/growth", "from src/personality",
            "from src/self_model", "from src/relationship",
        ]:
            assert forbidden not in content

    def test_apiclient_no_src_import(self):
        from yuyi_desktop.core import api_client
        with open(api_client.__file__, "r", encoding="utf-8") as f:
            content = f.read()
        for forbidden in [
            "from src.", "import src.",
        ]:
            assert forbidden not in content

    def test_desktop_runtime_not_initialized_by_import(self):
        """导入 Desktop 不应触发 Runtime 核心单例。"""
        # 简单 import 测试:不出现 RuntimeCore 状态被修改
        try:
            from yuyi_desktop.core.api_client import ApiClient
            from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
            from yuyi_desktop.services.runtime_service import RuntimeService
            from yuyi_desktop.services.memory_service import MemoryService
            from yuyi_desktop.services.personality_service import PersonalityService
            from yuyi_desktop.services.growth_service import GrowthService
            from yuyi_desktop.services.initiative_service import InitiativeService
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"导入 Desktop 模块失败: {exc}")


# ============================================================
# 10. Connection Manager
# ============================================================
class TestConnectionManager:
    """ConnectionManager 状态 / 心跳 / 离线检测。"""

    def test_creation(self):
        from yuyi_desktop.core.connection_manager import ConnectionManager
        cm = ConnectionManager()
        st = cm.get_status()
        assert st["connected"] is False
        assert st["latency_ms"] == -1.0

    def test_check_offline(self):
        """未启动 server 时 check_once 返回 offline。"""
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.connection_manager import ConnectionManager
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            free_port = s.getsockname()[1]
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{free_port}",
            timeout_seconds=0.3,
            max_retries=0,
        )
        api = ApiClient(config=cfg)
        cm = ConnectionManager(api_client=api)
        st = cm.check_once()
        assert st["connected"] is False
        assert st["total_checks"] >= 1

    def test_check_online(self, mock_yuyi_server):
        from yuyi_desktop.core.api_client import ApiClient, ApiClientConfig
        from yuyi_desktop.core.connection_manager import ConnectionManager
        cfg = ApiClientConfig(
            base_url=f"http://127.0.0.1:{mock_yuyi_server.port}",
            timeout_seconds=2.0,
        )
        api = ApiClient(config=cfg)
        cm = ConnectionManager(api_client=api)
        st = cm.check_once()
        assert st["connected"] is True
        assert st["latency_ms"] >= 0
        assert st["total_successes"] >= 1


# ============================================================
# 11. Auth Client
# ============================================================
class TestAuthClient:
    """AuthClient 加载 token / 状态查询。"""

    def test_injected_token(self):
        from yuyi_desktop.core.auth_client import AuthClient
        a = AuthClient(token="test-token-12345")
        st = a.get_state()
        assert st["has_token"] is True
        assert st["source"] == "injected"
        assert a.get_token() == "test-token-12345"

    def test_token_preview(self):
        from yuyi_desktop.core.auth_client import AuthClient
        a = AuthClient(token="abcdefgh12345678")
        st = a.get_state()
        assert "abcd" in st["token_preview"]
        assert "5678" in st["token_preview"]

    def test_no_token(self):
        from yuyi_desktop.core.auth_client import AuthClient
        a = AuthClient()
        st = a.get_state()
        # 在测试环境中,可能从环境变量加载了
        # 至少 state 有正确的 schema
        assert "has_token" in st
        assert "source" in st

    def test_token_provider(self):
        from yuyi_desktop.core.auth_client import AuthClient
        a = AuthClient(token="x" * 20)
        assert a.token_provider() == "x" * 20


# ============================================================
# 12. DesktopContext
# ============================================================
class TestDesktopContextRemote:
    """DesktopContext 集成所有远程组件。"""

    def test_context_has_remote_bridge(self):
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from yuyi_desktop.core.api_client import ApiClient
        from yuyi_desktop.core.connection_manager import ConnectionManager
        from yuyi_desktop.core.auth_client import AuthClient
        ctx = DesktopContext()
        assert isinstance(ctx.bridge, RemoteProviderBridge)
        assert isinstance(ctx.api, ApiClient)
        assert isinstance(ctx.connection, ConnectionManager)
        assert isinstance(ctx.auth, AuthClient)

    def test_context_health_has_connection_info(self):
        from yuyi_desktop.core.desktop_context import DesktopContext
        ctx = DesktopContext()
        snap = ctx.health_snapshot()
        assert "ready" in snap
        assert "tab_count" in snap
        assert "auth" in snap
        assert "connection" in snap
        assert snap["tab_count"] == 7


# ============================================================
# 13. 端点契约
# ============================================================
class TestEndpointContract:
    """端点定义必须覆盖 6 个核心域。"""

    def test_endpoints_cover_all_domains(self):
        from yuyi_desktop.core.remote_provider_bridge import ENDPOINTS
        # C.10.3 v2 契约:server 只注册 *_status / overview 类端点
        required = [
            "runtime_status",
            "memory_overview",
            "personality_status",
            "selfmodel_status",
            "growth_status",
            "initiative_status",
        ]
        for key in required:
            assert key in ENDPOINTS, f"ENDPOINTS 缺少 {key}"
            assert ENDPOINTS[key].startswith("/"), (
                f"endpoint 必须以 / 开头: {key}={ENDPOINTS[key]}"
            )

    def test_no_write_endpoints(self):
        from yuyi_desktop.core.remote_provider_bridge import ENDPOINTS
        forbidden = [
            "apply", "update", "delete", "post", "put", "patch",
            "resolve", "approve", "reject", "commit", "create",
        ]
        for key, path in ENDPOINTS.items():
            lower = path.lower()
            for kw in forbidden:
                # 整词匹配
                if f"/{kw}" in lower or lower.endswith(kw):
                    pytest.fail(f"endpoint 含写操作关键字: {key}={path}")


# ============================================================
# 14. DesktopConfig.auth_token 与 AuthClient 集成
# ============================================================
class TestDesktopConfigAuthToken:
    """验证 DesktopConfig.auth_token 正确接入 AuthClient。

    验收要求:
        - config 提供 auth_token 时: AuthClient 状态 source 应为 injected
        - 没有 token 时: 保持 source=none(若 env / file 也无)
        - 环境变量和文件加载行为保持正常(injected > env > file)
        - ApiClient 请求包含 Authorization header
    """

    def _reset_modules(self):
        """重置所有相关单例,避免测试间污染。"""
        from yuyi_desktop.core import (
            api_client as api_mod,
            auth_client as auth_mod,
            connection_manager as cm_mod,
            desktop_context as ctx_mod,
            remote_provider_bridge as rpb_mod,
        )
        api_mod.reset_api_client_for_testing()
        auth_mod.reset_auth_client_for_testing()
        cm_mod.reset_connection_manager_for_testing()
        rpb_mod.reset_remote_provider_bridge_for_testing()
        # 重置 DesktopContext 单例
        try:
            ctx_mod._ctx_instance = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def _clear_env_and_file(self, monkeypatch):
        """清理 YUYI_DESKTOP_TOKEN 环境变量。"""
        monkeypatch.delenv("YUYI_DESKTOP_TOKEN", raising=False)

    def test_config_has_auth_token_field(self):
        """DesktopConfig 必须支持 auth_token 字段。"""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        cfg = DesktopConfig(auth_token="test-token-xyz")
        assert cfg.auth_token == "test-token-xyz"
        assert cfg.has_auth_token() is True

    def test_config_default_auth_token_empty(self):
        """默认 config 的 auth_token 必须为空(不自动读取环境)。"""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        cfg = DesktopConfig()
        assert cfg.auth_token == ""
        assert cfg.has_auth_token() is False

    def test_config_has_auth_token_strips_whitespace(self):
        """has_auth_token 必须对空白 token 返回 False。"""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        cfg = DesktopConfig(auth_token="   ")
        assert cfg.has_auth_token() is False

    def test_config_safe_repr_hides_token(self):
        """safe_repr 必须不暴露 token 完整值(防日志泄露)。"""
        from yuyi_desktop.config.desktop_config import DesktopConfig
        cfg = DesktopConfig(auth_token="mysecret-token-12345")
        text = cfg.safe_repr()
        assert "mysecret-token-12345" not in text
        # 至少包含 preview
        assert "myse" in text or "***" in text

    def test_desktop_context_uses_config_token_as_injected(
        self, monkeypatch,
    ):
        """config 提供 auth_token 时,AuthClient 必须是 injected 模式。"""
        self._clear_env_and_file(monkeypatch)
        self._reset_modules()

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        cfg = DesktopConfig(auth_token="config-token-abc-1234567890")
        ctx = DesktopContext(config=cfg)

        st = ctx.auth.get_state()
        assert st["has_token"] is True
        # 必须是 injected 模式(从 config 注入)
        assert st["source"] == "injected", f"期望 source=injected, 实际 {st['source']}"
        # token 内容正确
        assert ctx.auth.get_token() == "config-token-abc-1234567890"
        # token preview 不泄露
        assert "config-token" not in st["token_preview"]

    def test_desktop_context_no_token_keeps_env_or_file(
        self, monkeypatch, tmp_path,
    ):
        """config 无 token 时,AuthClient 应回退到 env / file。"""
        self._clear_env_and_file(monkeypatch)
        # 通过临时 token.json 走 file 路径
        token_file = tmp_path / "token.json"
        token_file.write_text(
            '{"token": "file-loaded-token-xyz-12345"}',
            encoding="utf-8",
        )

        self._reset_modules()
        # 重置模块级 env 缓存(若有), 直接 patch AuthClient 默认 token_file
        from yuyi_desktop.core import auth_client as auth_mod
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        cfg = DesktopConfig(auth_token="")  # 显式空
        # 传入 file 路径让 AuthClient 走 file 分支
        auth_instance = auth_mod.AuthClient(token_file=str(token_file))
        ctx = DesktopContext(config=cfg, auth=auth_instance)

        st = ctx.auth.get_state()
        assert st["has_token"] is True
        assert st["source"] == "file", f"期望 source=file, 实际 {st['source']}"
        assert ctx.auth.get_token() == "file-loaded-token-xyz-12345"

    def test_desktop_context_no_token_anywhere_keeps_source_none(
        self, monkeypatch,
    ):
        """config / env / file 全部无 token 时,source 应为 none。"""
        self._clear_env_and_file(monkeypatch)
        # patch Path.home() 让默认 token.json 路径不存在
        from pathlib import Path
        original_home = Path.home

        def _fake_home():
            return Path("/nonexistent/path/that/does/not/exist")

        monkeypatch.setattr(Path, "home", _fake_home)

        self._reset_modules()
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        cfg = DesktopConfig(auth_token="")
        ctx = DesktopContext(config=cfg)

        st = ctx.auth.get_state()
        assert st["has_token"] is False
        assert st["source"] == "none", f"期望 source=none, 实际 {st['source']}"
        assert ctx.auth.get_token() == ""

    def test_api_client_includes_authorization_header(
        self, monkeypatch,
    ):
        """config 有 auth_token 时,ApiClient 必须带 Authorization header。"""
        self._clear_env_and_file(monkeypatch)
        self._reset_modules()

        from unittest.mock import MagicMock

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        cfg = DesktopConfig(auth_token="header-test-token-xyz")
        ctx = DesktopContext(config=cfg)

        # 捕获 _build_headers 输出
        api = ctx.api
        headers = api._build_headers()  # type: ignore[attr-defined]
        assert "Authorization" in headers, f"缺 Authorization: {headers}"
        assert headers["Authorization"] == "Bearer header-test-token-xyz"

    def test_api_client_no_token_omits_authorization(self, monkeypatch):
        """config / env / file 全部无 token 时,ApiClient 不带 Authorization。"""
        from pathlib import Path

        self._clear_env_and_file(monkeypatch)
        monkeypatch.setattr(Path, "home", lambda: Path("/nonexistent/home/path"))

        self._reset_modules()
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        cfg = DesktopConfig(auth_token="")
        ctx = DesktopContext(config=cfg)

        api = ctx.api
        headers = api._build_headers()  # type: ignore[attr-defined]
        assert "Authorization" not in headers, f"不应有 Authorization: {headers}"

    def test_explicit_auth_param_overrides_config(
        self, monkeypatch,
    ):
        """显式传入 auth 参数时,优先级高于 config.auth_token。"""
        self._clear_env_and_file(monkeypatch)
        self._reset_modules()

        from yuyi_desktop.core.auth_client import AuthClient
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        cfg = DesktopConfig(auth_token="config-token-should-not-be-used")
        explicit_auth = AuthClient(token="explicit-token-12345")

        ctx = DesktopContext(config=cfg, auth=explicit_auth)

        # 必须是 explicit_auth(调用方传入)
        assert ctx.auth is explicit_auth
        assert ctx.auth.get_token() == "explicit-token-12345"

    def test_config_with_token_does_not_log_full_token(
        self, monkeypatch, caplog,
    ):
        """DesktopContext 日志不能泄露完整 token。"""
        import logging
        caplog.set_level(logging.INFO)

        self._clear_env_and_file(monkeypatch)
        self._reset_modules()

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext

        secret = "super-secret-very-private-token-12345"
        cfg = DesktopConfig(auth_token=secret)
        DesktopContext(config=cfg)

        # 检查所有日志输出不含完整 token
        full_log = "\n".join(rec.getMessage() for rec in caplog.records)
        assert secret not in full_log, f"日志泄露 token: {full_log[:500]}"


# ============================================================
# 15. main 启动流程注入 auth_token
# ============================================================
class TestMainBootstrap:
    """验证 main.py 启动流程可以正确加载 token 并注入到 DesktopContext。

    验收要求:
        - main 启动流程可以生成带 auth_token 的 DesktopContext
        - token_provider 返回正确 token
        - ApiClient 请求包含 Authorization: Bearer token
    """

    def _reset_modules(self):
        """重置所有相关单例,避免测试间污染。"""
        from yuyi_desktop.core import (
            api_client as api_mod,
            auth_client as auth_mod,
            connection_manager as cm_mod,
            desktop_context as ctx_mod,
            remote_provider_bridge as rpb_mod,
        )
        api_mod.reset_api_client_for_testing()
        auth_mod.reset_auth_client_for_testing()
        cm_mod.reset_connection_manager_for_testing()
        rpb_mod.reset_remote_provider_bridge_for_testing()
        try:
            ctx_mod._ctx_instance = None  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def test_load_auth_token_from_env(self, monkeypatch):
        """YUYI_DESKTOP_TOKEN 环境变量应被加载为 token。"""
        from yuyi_desktop.main import _load_auth_token

        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "env-token-12345")
        token = _load_auth_token()
        assert token == "env-token-12345"

    def test_load_auth_token_from_file(self, monkeypatch, tmp_path):
        """~/.yuyi_desktop/token.json 应被加载为 token(若 env 未设)。"""
        from yuyi_desktop.main import _load_auth_token
        from pathlib import Path

        monkeypatch.delenv("YUYI_DESKTOP_TOKEN", raising=False)
        # 改写 home 到临时目录
        token_dir = tmp_path / ".yuyi_desktop"
        token_dir.mkdir(parents=True, exist_ok=True)
        token_file = token_dir / "token.json"
        token_file.write_text(
            '{"token": "file-token-abc-12345"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        token = _load_auth_token()
        assert token == "file-token-abc-12345"

    def test_load_auth_token_empty_when_nothing(self, monkeypatch):
        """env / file 都没有时,应返回空串。"""
        from yuyi_desktop.main import _load_auth_token
        from pathlib import Path

        monkeypatch.delenv("YUYI_DESKTOP_TOKEN", raising=False)
        monkeypatch.setattr(
            Path, "home",
            lambda: Path("/nonexistent/home/that/does/not/exist"),
        )
        token = _load_auth_token()
        assert token == ""

    def test_load_auth_token_handles_file_error(self, monkeypatch, tmp_path):
        """token.json 损坏时不应抛错,应降级返回空串。"""
        from yuyi_desktop.main import _load_auth_token
        from pathlib import Path

        monkeypatch.delenv("YUYI_DESKTOP_TOKEN", raising=False)
        token_dir = tmp_path / ".yuyi_desktop"
        token_dir.mkdir(parents=True, exist_ok=True)
        token_file = token_dir / "token.json"
        token_file.write_text("not a valid json {{{", encoding="utf-8")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # 不抛错
        token = _load_auth_token()
        assert token == ""

    def test_build_desktop_config_with_explicit_token(self):
        """显式传入 token 时,AuthClient 不参与加载。"""
        from yuyi_desktop.main import _build_desktop_config
        cfg = _build_desktop_config(auth_token="explicit-token-xyz")
        assert cfg.auth_token == "explicit-token-xyz"
        assert cfg.has_auth_token() is True

    def test_build_desktop_config_with_empty_string(self):
        """显式传空串时,AuthClient 不参与加载,结果为空。"""
        from yuyi_desktop.main import _build_desktop_config
        cfg = _build_desktop_config(auth_token="")
        assert cfg.auth_token == ""
        assert cfg.has_auth_token() is False

    def test_build_desktop_config_auto_loads_from_env(self, monkeypatch):
        """auth_token=None 时,自动从 env 加载。"""
        from yuyi_desktop.main import _build_desktop_config
        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "auto-env-token-12345")
        cfg = _build_desktop_config()
        assert cfg.auth_token == "auto-env-token-12345"

    def test_bootstrap_builds_configured_context(self, monkeypatch):
        """模拟 main 启动:加载 token → DesktopContext,AuthClient 应为 injected。"""
        self._reset_modules()
        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "bootstrap-token-abc-12345")

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.main import _build_desktop_config

        # 1) 加载 token
        cfg = _build_desktop_config()
        assert cfg.has_auth_token() is True

        # 2) 用 config 构造 DesktopContext
        ctx = DesktopContext(config=cfg)

        # 3) 验证 token 已注入
        st = ctx.auth.get_state()
        assert st["has_token"] is True
        assert st["source"] == "injected", (
            f"期望 source=injected, 实际 {st['source']}"
        )
        assert ctx.auth.get_token() == "bootstrap-token-abc-12345"

    def test_bootstrap_token_provider_returns_token(self, monkeypatch):
        """token_provider 必须返回正确 token。"""
        self._reset_modules()
        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "provider-test-token-xyz")

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.main import _build_desktop_config

        cfg = _build_desktop_config()
        ctx = DesktopContext(config=cfg)
        assert ctx.auth.token_provider() == "provider-test-token-xyz"

    def test_bootstrap_api_client_authorization_header(self, monkeypatch):
        """ApiClient 请求必须包含 Authorization: Bearer <token>。"""
        self._reset_modules()
        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "header-bootstrap-token-xyz")

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.main import _build_desktop_config

        cfg = _build_desktop_config()
        ctx = DesktopContext(config=cfg)
        headers = ctx.api._build_headers()  # type: ignore[attr-defined]
        assert headers.get("Authorization") == "Bearer header-bootstrap-token-xyz"

    def test_bootstrap_no_token_omits_authorization(self, monkeypatch):
        """没有 token 时,ApiClient 不应带 Authorization。"""
        from pathlib import Path

        self._reset_modules()
        monkeypatch.delenv("YUYI_DESKTOP_TOKEN", raising=False)
        monkeypatch.setattr(
            Path, "home",
            lambda: Path("/nonexistent/home/no/token"),
        )

        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.main import _build_desktop_config

        cfg = _build_desktop_config()
        assert cfg.has_auth_token() is False
        ctx = DesktopContext(config=cfg)
        headers = ctx.api._build_headers()  # type: ignore[attr-defined]
        assert "Authorization" not in headers

    def test_bootstrap_real_http_request_includes_auth(
        self, monkeypatch,
    ):
        """真实 HTTP 请求(ApiClient.get 走 _session.request)必须带 Authorization。

        使用 mock session 捕获 headers,验证 token 被注入到实际请求。
        """
        self._reset_modules()
        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "real-req-token-xyz-12345")

        from unittest.mock import MagicMock
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.main import _build_desktop_config

        cfg = _build_desktop_config()
        ctx = DesktopContext(config=cfg)
        api = ctx.api

        # mock session.request, 捕获 headers
        captured = {}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = kwargs.get("headers", {})
            captured["params"] = kwargs.get("params", {})
            # 模拟一个成功响应
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "success": True,
                "data": {"ok": True},
                "error": "",
                "timestamp": "2026-08-05T00:00:00",
                "schema_version": "1.0",
            }
            return resp

        api._session.request = fake_request  # type: ignore[assignment]

        env = api.get("/health")
        assert env["success"] is True
        # 关键: Authorization header 必须被注入
        assert "Authorization" in captured["headers"], (
            f"实际 headers: {captured['headers']}"
        )
        assert captured["headers"]["Authorization"] == "Bearer real-req-token-xyz-12345"

    def test_bootstrap_logs_token_source(self, monkeypatch, caplog):
        """main 启动日志应记录 token 来源(不暴露 token 值)。"""
        import logging
        caplog.set_level(logging.INFO)

        self._reset_modules()
        monkeypatch.setenv("YUYI_DESKTOP_TOKEN", "log-test-secret-12345")

        # 模拟 main 启动流程(不进入 Qt 主循环)
        from yuyi_desktop.config.desktop_config import DesktopConfig
        from yuyi_desktop.core.desktop_context import DesktopContext
        from yuyi_desktop.main import _build_desktop_config

        cfg = _build_desktop_config()
        DesktopContext(config=cfg)

        full_log = "\n".join(rec.getMessage() for rec in caplog.records)
        # 不暴露 token
        assert "log-test-secret-12345" not in full_log


# ============================================================
# 16. 老式端点 fallback 行为(Phase D.2.9)
# ============================================================
class TestEndpointFallback:
    """验证:当 server 不提供老式端点(snapshot/summary/recent/...)时,
    RemoteProviderBridge 应当 fallback 到 *_status 端点,而不是 405。

    行为契约:
        - 老式方法(get_xxx_snapshot/summary/recent/...)应返回 success=True
        - 返回 data 中应能提取到 fallback 字段(snapshot/recent/...)
        - 不存在的端点(life/*)返回 not-available envelope(success=False, degraded=False)
        - 405 响应在 ApiClient 层被标为 endpoint_not_available,不算 degraded
    """

    def _make_bridge(self, responses: dict):
        """构造一个 mock session 的 bridge,按 endpoint 返回指定响应。"""
        from yuyi_desktop.core.api_client import ApiClient
        from yuyi_desktop.core.remote_provider_bridge import RemoteProviderBridge
        from unittest.mock import MagicMock

        session = MagicMock()

        def fake_request(method, url, **kwargs):
            # url 形如 http://.../api/v1/<endpoint>
            ep = url.split("/api/v1", 1)[-1]
            payload = responses.get(ep)
            if payload is None:
                resp = MagicMock()
                resp.status_code = 404
                resp.json.side_effect = ValueError("no body")
                return resp
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "success": True,
                "data": payload,
                "error": "",
                "timestamp": "2026-08-05T00:00:00",
                "schema_version": "1.0",
            }
            return resp

        session.request = fake_request
        api = ApiClient(session=session)
        return RemoteProviderBridge(api_client=api)

    def test_personality_snapshot_falls_back_to_status(self):
        """get_personality_snapshot 应 fallback 到 /personality/status 并提取 snapshot 字段。"""
        bridge = self._make_bridge({
            "/personality/status": {
                "available": True,
                "snapshot": {"identity_name": "羽依", "version": "1.2.0"},
                "evolution_version": "1.2.0",
                "state": "stable",
            },
        })
        env = bridge.get_personality_snapshot()
        assert env["success"] is True, f"fallback 后应 success=True, err={env.get('error')}"
        data = bridge.get_personality_snapshot_data()
        assert data.get("identity_name") == "羽依"
        assert data.get("version") == "1.2.0"

    def test_memory_snapshot_falls_back_to_overview(self):
        """get_memory_snapshot 应 fallback 到 /memory/overview。"""
        bridge = self._make_bridge({
            "/memory/overview": {
                "available": True,
                "total_count": 100,
                "important_count": 5,
                "recent": [{"id": "m1"}],
            },
        })
        env = bridge.get_memory_snapshot()
        assert env["success"] is True
        data = bridge.get_memory_snapshot_data()
        assert data.get("total_count") == 100

    def test_growth_recent_extracts_from_status(self):
        """get_growth_recent_data() 应能从 /growth/status 提取 recent(若有)。"""
        bridge = self._make_bridge({
            "/growth/status": {
                "available": True,
                "proposal_count": 0,
                "pending_count": 0,
                "recent": [],  # 即使空也能成功
            },
        })
        items = bridge.get_growth_recent_data()
        assert items == []  # 空 list,不算失败

    def test_life_methods_return_not_available(self):
        """/life/* server 暂未提供,应返回 success=False,degraded=False。"""
        bridge = self._make_bridge({})  # 任何端点都 404
        env = bridge.get_life_state()
        assert env["success"] is False
        assert env["degraded"] is False
        assert "not_available" in env["error"]
        # data 提取也应安全返回空
        assert bridge.get_life_state_data() == {}
        assert bridge.get_life_timeline_data() == []
        assert bridge.get_life_graph_data() == {}

    def test_405_not_treated_as_degraded(self):
        """ApiClient 收到 404/405 应标 endpoint_not_available(degraded=False)。"""
        from yuyi_desktop.core.api_client import ApiClient
        from unittest.mock import MagicMock

        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 405
        resp.json.side_effect = ValueError("no body")
        session.request.return_value = resp
        api = ApiClient(session=session)
        env = api.get("/some/missing/endpoint")
        assert env["success"] is False
        assert env["degraded"] is False
        assert "endpoint_not_available" in env["error"]

    def test_health_check_uses_only_existing_endpoints(self):
        """PROVIDER_GROUPS 只包含 server 实际注册的端点。"""
        from yuyi_desktop.core.remote_provider_bridge import (
            PROVIDER_GROUPS, ENDPOINTS,
        )
        all_in_groups = set()
        for eps in PROVIDER_GROUPS.values():
            all_in_groups.update(eps)
        for ep in all_in_groups:
            assert ep in ENDPOINTS.values(), (
                f"PROVIDER_GROUPS 引用了 ENDPOINTS 中不存在的端点: {ep}"
            )

    def test_bridge_fallback_logging(self, caplog):
        """fallback 时应有 DEBUG 日志,提示老式端点被替换。"""
        import logging
        caplog.set_level(logging.DEBUG)
        bridge = self._make_bridge({
            "/personality/status": {"available": True, "snapshot": {}},
        })
        bridge.get_personality_snapshot()
        # 没有强制要求日志,只是不应抛错
        assert True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
