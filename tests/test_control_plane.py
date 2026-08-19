# -*- coding: utf-8 -*-
"""
tests/test_control_plane.py

Phase C.10.5 —— Yuyi Control Plane 端到端测试

覆盖:
1. ControlState (Server)
   - 默认状态
   - 修改状态
   - 持久化(save/restore)
   - 审计(append-only)
   - 未知字段/类型

2. ModuleRegistry
   - 内置 7 个模块
   - 状态查询
   - 自定义注册/注销
   - readonly/controllable 标记

3. ControlManager
   - enable_module / disable_module / toggle
   - enter_safe_mode / exit_safe_mode
   - readonly 模块不能被 disable
   - 每次操作都追加 audit

4. Control API (Server, Flask test client)
   - GET /api/v1/control/status
   - GET /api/v1/control/modules
   - GET /api/v1/control/audit
   - POST /api/v1/control/module/<name>/enable
   - POST /api/v1/control/module/<name>/disable
   - POST /api/v1/control/safe_mode/enable
   - 401 (无 token)
   - 403 (readonly 不能 disable)
   - 405 (方法不允许)

5. Desktop ControlService
   - 调用 ControlApiClient
   - 状态解析(get_overview)
   - 失败 fallback
   - 事件发布

6. Desktop ControlCenterWidget
   - 启动无异常
   - 模块行 + 系统模式行
   - 事件驱动刷新

约束(强):
- 不修改 src/runtime / src/memory / src/growth / src/personality / src/self_model
- 不删除任何已有测试
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

# 测试期间强制 Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# 路径由 conftest.py 处理


# ============================================================
# 工具
# ============================================================
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_temp_dir(prefix: str = "yuyi_control_test_") -> str:
    return tempfile.mkdtemp(prefix=prefix)


# ============================================================
# 1. ControlState (Server)
# ============================================================
class TestControlState:
    """ControlState + Persistence 单元测试。"""

    def _new_persistence(self):
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        d = _make_temp_dir()
        return reset_control_state_persistence_for_testing(data_dir=d), d

    def test_default_state(self):
        from src.control.state.control_state import ControlState
        s = ControlState.default()
        # 所有模块默认启用
        for f in [
            "runtime_enabled", "memory_enabled", "emotion_enabled",
            "growth_enabled", "initiative_enabled", "dream_enabled",
            "live2d_enabled",
        ]:
            assert getattr(s, f) is True, f"{f} should default to True"
        # 系统模式默认关闭
        for f in ["maintenance_mode", "safe_mode"]:
            assert getattr(s, f) is False, f"{f} should default to False"

    def test_set_field_success(self):
        p, _ = self._new_persistence()
        change = p.set_field("growth_enabled", False, operator="desktop", reason="test")
        assert change.old_value is True
        assert change.new_value is False
        assert change.field == "growth_enabled"
        assert change.operator == "desktop"
        assert p.get_field("growth_enabled") is False

    def test_set_field_no_change(self):
        p, _ = self._new_persistence()
        change = p.set_field("growth_enabled", True, operator="desktop")
        assert change.old_value == change.new_value
        # 即使无变化也应记录 audit
        assert p.audit_count() == 1

    def test_set_unknown_field(self):
        p, _ = self._new_persistence()
        from src.control.state.control_state import ControlStateError
        with pytest.raises(ControlStateError):
            p.set_field("not_exists", True)

    def test_persistence_save_restore(self):
        p, d = self._new_persistence()
        p.set_field("growth_enabled", False, operator="alice", reason="r1")
        p.set_field("safe_mode", True, operator="bob", reason="r2")
        # 重新构造(从磁盘)
        from src.control.state.control_state import ControlStatePersistence
        p2 = ControlStatePersistence(data_dir=d)
        assert p2.get_field("growth_enabled") is False
        assert p2.get_field("safe_mode") is True
        # 恢复后 audit 也存在(append-only)
        # 新实例的 audit 文件独立,不会自动恢复(仅状态恢复)
        # 状态恢复是必须的
        s2 = p2.get_state()
        assert s2.growth_enabled is False
        assert s2.safe_mode is True

    def test_audit_append_only(self):
        p, _ = self._new_persistence()
        for i in range(5):
            p.set_field("growth_enabled", bool(i % 2), operator=f"op{i}")
        audit = p.list_audit(limit=10)
        assert len(audit) == 5
        # 倒序
        assert audit[0]["field"] == "growth_enabled"
        # 字段过滤
        filtered = p.list_audit(limit=10, field="memory_enabled")
        assert len(filtered) == 0

    def test_state_to_from_dict(self):
        from src.control.state.control_state import ControlState
        s1 = ControlState.default()
        s1.growth_enabled = False
        s1.safe_mode = True
        d = s1.to_dict()
        s2 = ControlState.from_dict(d)
        assert s2.growth_enabled is False
        assert s2.safe_mode is True
        # 缺失字段时使用默认
        s3 = ControlState.from_dict({})
        assert s3.growth_enabled is True
        assert s3.safe_mode is False

    def test_get_modules_and_system(self):
        p, _ = self._new_persistence()
        modules = p.get_modules()
        assert "growth_enabled" in modules
        assert "memory_enabled" in modules
        system = p.get_system()
        assert "safe_mode" in system
        assert "maintenance_mode" in system


# ============================================================
# 2. ModuleRegistry
# ============================================================
class TestModuleRegistry:
    """ModuleRegistry 单元测试。"""

    def test_builtin_modules(self):
        from src.control.registry.module_registry import (
            BUILTIN_MODULES,
            get_module_registry,
        )
        reg = get_module_registry()
        names = reg.list_names()
        expected = {
            "runtime", "memory", "emotion", "growth",
            "initiative", "dream", "live2d",
        }
        assert expected.issubset(set(names))
        assert len(BUILTIN_MODULES) == 7

    def test_module_metadata(self):
        from src.control.registry.module_registry import get_module_registry
        reg = get_module_registry()
        m = reg.get_module("growth")
        assert m is not None
        assert m.state_field == "growth_enabled"
        assert m.controllable is True
        assert m.readonly is False
        assert m.display == "Growth"

    def test_runtime_readonly(self):
        from src.control.registry.module_registry import get_module_registry
        reg = get_module_registry()
        m = reg.get_module("runtime")
        assert m is not None
        assert m.readonly is True
        assert m.controllable is False
        assert reg.is_readonly("runtime") is True
        assert reg.is_controllable("runtime") is False

    def test_is_enabled_with_state(self):
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        d = _make_temp_dir()
        state = reset_control_state_persistence_for_testing(data_dir=d)
        reg = reset_module_registry_for_testing()

        # 默认 enabled=True
        assert reg.is_enabled("growth", state_provider=state) is True
        # 修改状态
        state.set_field("growth_enabled", False, operator="test")
        assert reg.is_enabled("growth", state_provider=state) is False
        # 不存在的模块
        assert reg.is_enabled("not_exists", state_provider=state) is False

    def test_get_status(self):
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        d = _make_temp_dir()
        state = reset_control_state_persistence_for_testing(data_dir=d)
        reg = reset_module_registry_for_testing()
        status = reg.get_status(state_provider=state)
        assert isinstance(status, list)
        assert len(status) == 7
        # 每项都有 enabled 字段
        for item in status:
            assert "name" in item
            assert "enabled" in item
            assert "readonly" in item
            assert "controllable" in item

    def test_custom_register(self):
        from src.control.registry.module_registry import (
            ModuleInfo,
            reset_module_registry_for_testing,
        )
        reg = reset_module_registry_for_testing()
        m = ModuleInfo(
            name="custom",
            display="Custom",
            version="0.1",
            description="test",
            state_field="growth_enabled",  # 复用现有字段
            controllable=True,
            readonly=False,
        )
        ok = reg.register(m, replace=False)
        assert ok is True
        assert reg.has_module("custom")
        # 重复注册应失败
        ok2 = reg.register(m, replace=False)
        assert ok2 is False
        # 替换
        ok3 = reg.register(m, replace=True)
        assert ok3 is True
        # 注销
        reg.unregister("custom")
        assert not reg.has_module("custom")


# ============================================================
# 3. ControlManager
# ============================================================
class TestControlManager:
    """ControlManager 单元测试。"""

    def _fresh_manager(self):
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.manager.control_manager import (
            reset_control_manager_for_testing,
        )
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        d = _make_temp_dir()
        state = reset_control_state_persistence_for_testing(data_dir=d)
        reg = reset_module_registry_for_testing()
        mgr = reset_control_manager_for_testing(
            state_persistence=state,
            registry=reg,
        )
        return mgr, state, reg

    def test_enable_module(self):
        mgr, state, _ = self._fresh_manager()
        # 先 disable 再 enable
        mgr.disable_module("growth", operator="test", reason="r1")
        assert state.get_field("growth_enabled") is False
        result = mgr.enable_module("growth", operator="test", reason="r2")
        assert result.success is True
        assert result.action == "enable"
        assert result.new_value is True
        assert state.get_field("growth_enabled") is True

    def test_disable_module(self):
        mgr, state, _ = self._fresh_manager()
        result = mgr.disable_module("memory", operator="test", reason="r1")
        assert result.success is True
        assert state.get_field("memory_enabled") is False

    def test_toggle_module(self):
        mgr, state, _ = self._fresh_manager()
        r1 = mgr.toggle_module("emotion", operator="test")
        assert r1.success is True
        assert state.get_field("emotion_enabled") is False
        r2 = mgr.toggle_module("emotion", operator="test")
        assert r2.success is True
        assert state.get_field("emotion_enabled") is True

    def test_readonly_cannot_disable(self):
        mgr, state, _ = self._fresh_manager()
        result = mgr.disable_module("runtime", operator="test")
        assert result.success is False
        # runtime 既 readonly 也 not_controllable,接受任一关键字
        assert (
            "readonly" in result.error
            or "not_controllable" in result.error
        )
        # 状态未变
        assert state.get_field("runtime_enabled") is True

    def test_unknown_module(self):
        mgr, _, _ = self._fresh_manager()
        result = mgr.enable_module("not_exists", operator="test")
        assert result.success is False
        assert "not_found" in result.error

    def test_safe_mode(self):
        mgr, state, _ = self._fresh_manager()
        r1 = mgr.enter_safe_mode(operator="test", reason="emergency")
        assert r1.success is True
        assert state.get_field("safe_mode") is True
        r2 = mgr.exit_safe_mode(operator="test")
        assert r2.success is True
        assert state.get_field("safe_mode") is False

    def test_maintenance(self):
        mgr, state, _ = self._fresh_manager()
        r1 = mgr.enter_maintenance(operator="test")
        assert r1.success is True
        assert state.get_field("maintenance_mode") is True
        r2 = mgr.exit_maintenance(operator="test")
        assert r2.success is True
        assert state.get_field("maintenance_mode") is False

    def test_audit_per_action(self):
        mgr, state, _ = self._fresh_manager()
        before = state.audit_count()
        mgr.enable_module("growth", operator="alice", reason="a")
        mgr.disable_module("memory", operator="bob", reason="b")
        mgr.enter_safe_mode(operator="carol", reason="c")
        after = state.audit_count()
        assert after - before == 3
        audit = state.list_audit(limit=10)
        operators = [a.get("operator") for a in audit]
        assert "carol" in operators
        assert "bob" in operators
        assert "alice" in operators

    def test_overview(self):
        mgr, _, _ = self._fresh_manager()
        ov = mgr.get_overview()
        assert "state" in ov
        assert "modules" in ov
        assert "recent_audit" in ov
        assert "audit_count" in ov
        assert len(ov["modules"]) == 7


# ============================================================
# 4. Control API (Flask test client)
# ============================================================
class TestControlAPI:
    """Control API 集成测试(Flask test client + in-process state)。"""

    def _build_app(self):
        from flask import Flask
        from src.control.api.control_routes import register_control_api
        from src.control.api.envelope import API_SCHEMA_VERSION
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.manager.control_manager import (
            reset_control_manager_for_testing,
        )
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )

        d = _make_temp_dir()
        reset_control_state_persistence_for_testing(data_dir=d)
        reset_module_registry_for_testing()
        reset_control_manager_for_testing()
        # 强制 dev 模式认证(token 可缺省)
        from src.control.api.config import GatewayAuthConfig
        from src.control.api.auth import set_auth_override
        set_auth_override(GatewayAuthConfig(mode="disabled"))

        app = Flask("control_api_test_app")
        app.config["TESTING"] = True
        register_control_api(app)
        return app

    def test_get_status(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.get("/api/v1/control/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "data" in body
        assert "modules" in body["data"]
        assert "state" in body["data"]

    def test_get_modules(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.get("/api/v1/control/modules")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        mods = body["data"]["modules"]
        names = {m["name"] for m in mods}
        assert "growth" in names
        assert "memory" in names

    def test_get_audit(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.get("/api/v1/control/audit?limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert "items" in body["data"]

    def test_enable_module(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.post(
            "/api/v1/control/module/growth/enable",
            json={"operator": "alice", "reason": "test"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["module"] == "growth"
        assert body["data"]["new_value"] is True

    def test_disable_module(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.post(
            "/api/v1/control/module/memory/disable",
            json={"operator": "bob", "reason": "test"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["new_value"] is False

    def test_toggle_module(self):
        app = self._build_app()
        client = app.test_client()
        # 第一次 toggle: ON -> OFF
        resp = client.post("/api/v1/control/module/emotion/toggle")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["new_value"] is False
        # 第二次 toggle: OFF -> ON
        resp = client.post("/api/v1/control/module/emotion/toggle")
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["new_value"] is True

    def test_safe_mode_endpoints(self):
        app = self._build_app()
        client = app.test_client()
        # enable
        resp = client.post("/api/v1/control/safe_mode/enable")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["new_value"] is True
        # disable
        resp = client.post("/api/v1/control/safe_mode/disable")
        body = resp.get_json()
        assert body["success"] is True
        assert body["data"]["new_value"] is False

    def test_readonly_module_disable_403(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.post("/api/v1/control/module/runtime/disable")
        assert resp.status_code == 403
        body = resp.get_json()
        assert body["success"] is False
        # runtime 既 readonly 也 not_controllable
        assert (
            "readonly" in body["error"]
            or "not_controllable" in body["error"]
        )

    def test_unknown_module_404(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.post("/api/v1/control/module/not_exists/enable")
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["success"] is False

    def test_method_not_allowed_405(self):
        app = self._build_app()
        client = app.test_client()
        resp = client.delete("/api/v1/control/status")
        assert resp.status_code == 405

    def test_auth_required_in_production(self):
        """
        production 模式下无 token 应返回 401。
        """
        from flask import Flask
        from src.control.api.control_routes import register_control_api
        from src.control.api.config import GatewayAuthConfig
        from src.control.api.auth import set_auth_override
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.manager.control_manager import (
            reset_control_manager_for_testing,
        )

        d = _make_temp_dir()
        reset_control_state_persistence_for_testing(data_dir=d)
        reset_module_registry_for_testing()
        reset_control_manager_for_testing()
        set_auth_override(
            GatewayAuthConfig(mode="production", token="secret-token", required=True)
        )
        app = Flask("control_api_prod_test")
        app.config["TESTING"] = True
        register_control_api(app)
        client = app.test_client()
        # 无 token
        resp = client.get("/api/v1/control/status")
        assert resp.status_code == 401
        # 错误 token
        resp = client.get(
            "/api/v1/control/status",
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 401
        # 正确 token
        resp = client.get(
            "/api/v1/control/status",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # 还原
        set_auth_override(GatewayAuthConfig(mode="disabled"))

    def test_audit_after_actions(self):
        app = self._build_app()
        client = app.test_client()
        # 多个操作
        client.post("/api/v1/control/module/growth/disable", json={"operator": "x"})
        client.post("/api/v1/control/module/memory/disable", json={"operator": "x"})
        client.post("/api/v1/control/safe_mode/enable", json={"operator": "x"})
        resp = client.get("/api/v1/control/audit?limit=20")
        body = resp.get_json()
        assert body["success"] is True
        items = body["data"]["items"]
        # 至少 3 条
        assert len(items) >= 3
        # 包含 safe_mode
        fields = [i.get("field") for i in items]
        assert "safe_mode" in fields


# ============================================================
# 5. Desktop ControlApiClient
# ============================================================
class TestDesktopControlApiClient:
    """Desktop ControlApiClient 单元测试(使用 mock session)。"""

    def test_make_envelope(self):
        from yuyi_desktop.core.control_api_client import (
            make_envelope,
            make_error_envelope,
            make_success_envelope,
        )
        e = make_envelope(success=True, data={"x": 1}, latency_ms=1.0)
        assert e["success"] is True
        assert e["data"] == {"x": 1}
        assert e["schema_version"] == "1.0"
        err = make_error_envelope("bad", degraded=False)
        assert err["success"] is False
        assert err["degraded"] is False
        ok = make_success_envelope({"a": 1}, latency_ms=2.0)
        assert ok["success"] is True

    def test_get_status_with_mock(self):
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
            ControlApiConfig,
        )
        from yuyi_desktop.core.control_api_client import CONTROL_SCHEMA_VERSION

        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {
                "modules": [
                    {"name": "growth", "enabled": True, "controllable": True},
                ],
                "state": {"growth_enabled": True, "safe_mode": False},
                "audit_count": 0,
            },
            "schema_version": CONTROL_SCHEMA_VERSION,
        }
        session.request.return_value = resp

        client = ControlApiClient(
            config=ControlApiConfig(),
            session=session,
        )
        env = client.get_status()
        assert env["success"] is True
        assert env["data"]["modules"][0]["name"] == "growth"
        # 验证 request 被调用
        assert session.request.called
        # 验证 method 是 GET
        kwargs = session.request.call_args.kwargs
        assert kwargs["method"] == "GET"

    def test_post_enable(self):
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
            ControlApiConfig,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {
                "module": "growth",
                "new_value": True,
                "old_value": False,
                "action": "enable",
            },
            "schema_version": "1.0",
        }
        session.request.return_value = resp
        client = ControlApiClient(config=ControlApiConfig(), session=session)
        env = client.enable_module("growth", operator="alice", reason="r")
        assert env["success"] is True
        assert env["data"]["module"] == "growth"
        # 验证 method 是 POST
        kwargs = session.request.call_args.kwargs
        assert kwargs["method"] == "POST"
        assert kwargs["json"]["operator"] == "alice"

    def test_auth_error(self):
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
            ControlApiConfig,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 401
        resp.json.return_value = {"error": "unauthorized"}
        session.request.return_value = resp
        client = ControlApiClient(config=ControlApiConfig(), session=session)
        env = client.get_status()
        assert env["success"] is False
        assert env["error"].startswith("auth_error")
        assert env["degraded"] is False

    def test_token_provider_injection(self):
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
            ControlApiConfig,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "data": {}, "schema_version": "1.0"}
        session.request.return_value = resp
        token_provider = lambda: "abc-token"
        client = ControlApiClient(
            config=ControlApiConfig(),
            session=session,
            token_provider=token_provider,
        )
        client.get_status()
        headers = session.request.call_args.kwargs["headers"]
        assert headers.get("Authorization") == "Bearer abc-token"


# ============================================================
# 6. Desktop ControlService
# ============================================================
class TestControlService:
    """Desktop ControlService 单元测试(注入 mock client)。"""

    def _make_service(self):
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
        )
        from yuyi_desktop.services.control_service import (
            ControlService,
        )
        from yuyi_desktop.core.events.event_bus import (
            reset_event_bus_for_testing,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {
                "modules": [
                    {
                        "name": "growth", "display": "Growth",
                        "version": "1.0", "description": "...",
                        "state_field": "growth_enabled",
                        "enabled": True, "readonly": False,
                        "controllable": True, "category": "evolution",
                    },
                ],
                "state": {
                    "growth_enabled": True, "memory_enabled": True,
                    "safe_mode": False, "maintenance_mode": False,
                },
                "audit_count": 2,
                "recent_audit": [
                    {"field": "growth_enabled", "operator": "alice"},
                ],
            },
            "schema_version": "1.0",
        }
        session.request.return_value = resp
        api = ControlApiClient(session=session)
        bus = reset_event_bus_for_testing()
        svc = ControlService(api_client=api, event_bus=bus, enable_events=True)
        return svc, bus, session

    def test_get_overview(self):
        svc, _, _ = self._make_service()
        ov = svc.get_overview()
        assert ov["available"] is True
        assert len(ov["modules"]) == 1
        assert ov["modules"][0]["name"] == "growth"
        assert ov["state"]["growth_enabled"] is True
        assert ov["audit_count"] == 2

    def test_get_overview_failure(self):
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
            ControlApiConfig,
        )
        from yuyi_desktop.services.control_service import (
            ControlService,
        )
        from yuyi_desktop.core.events.event_bus import (
            reset_event_bus_for_testing,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 500
        session.request.return_value = resp
        api = ControlApiClient(config=ControlApiConfig(), session=session)
        bus = reset_event_bus_for_testing()
        svc = ControlService(api_client=api, event_bus=bus, enable_events=False)
        ov = svc.get_overview()
        assert ov["available"] is False
        assert "error" in ov

    def test_list_modules(self):
        svc, _, _ = self._make_service()
        mods = svc.list_modules()
        assert len(mods) == 1
        assert mods[0]["name"] == "growth"

    def test_enable_module_emits_event(self):
        from yuyi_desktop.services.control_service import (
            ControlService,
            CONTROL_UPDATED,
        )
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
        )
        from yuyi_desktop.core.events.event_bus import (
            reset_event_bus_for_testing,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {
                "module": "growth",
                "old_value": False,
                "new_value": True,
                "action": "enable",
            },
            "schema_version": "1.0",
        }
        session.request.return_value = resp
        api = ControlApiClient(session=session)
        bus = reset_event_bus_for_testing()
        received: List[Any] = []
        bus.subscribe(CONTROL_UPDATED, lambda e: received.append(e))
        svc = ControlService(api_client=api, event_bus=bus, enable_events=True)
        result = svc.enable_module("growth", reason="test")
        assert result["success"] is True
        assert len(received) == 1
        assert received[0].data["module"] == "growth"
        assert received[0].data["action"] == "enable"

    def test_safe_mode_roundtrip(self):
        from yuyi_desktop.services.control_service import ControlService
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
        )
        from yuyi_desktop.core.events.event_bus import (
            reset_event_bus_for_testing,
        )
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {
                "old_value": False,
                "new_value": True,
                "action": "enter_safe_mode",
            },
            "schema_version": "1.0",
        }
        session.request.return_value = resp
        api = ControlApiClient(session=session)
        bus = reset_event_bus_for_testing()
        svc = ControlService(api_client=api, event_bus=bus, enable_events=False)
        r1 = svc.enter_safe_mode(reason="emergency")
        assert r1["success"] is True
        # 通过 kwargs['url'] 获取 URL
        call_kwargs = session.request.call_args.kwargs
        called_url = call_kwargs.get("url", "")
        assert "/control/safe_mode/enable" in called_url
        # method 是 POST
        assert call_kwargs.get("method") == "POST"


# ============================================================
# 7. Desktop ControlCenterWidget
# ============================================================
class TestControlCenterWidget:
    """Desktop Control Tab 骨架单元测试。"""

    def test_widget_creation(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            pytest.skip("PySide6 未安装")
        from yuyi_desktop.ui.widgets.control_center_widget import (
            ControlCenterWidget,
        )
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
        )
        from yuyi_desktop.services.control_service import (
            ControlService,
        )
        from yuyi_desktop.core.events.event_bus import (
            reset_event_bus_for_testing,
        )

        # 确保 QApplication 存在
        app = QApplication.instance() or QApplication([])

        # Mock client 永远返回 offline
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 500
        session.request.return_value = resp
        api = ControlApiClient(session=session)
        bus = reset_event_bus_for_testing()
        svc = ControlService(api_client=api, event_bus=bus, enable_events=False)
        widget = ControlCenterWidget(service=svc)
        # 关闭定时器,避免测试卡住
        try:
            widget._timer.stop()  # noqa: SLF001
        except Exception:
            pass
        # 标题存在
        assert widget.findChild(type(widget).__bases__[0]) is not None
        # 触发刷新(不会抛错)
        widget._refresh()  # noqa: SLF001
        widget.deleteLater()

    def test_widget_with_modules(self):
        try:
            from PySide6.QtWidgets import QApplication
        except ImportError:
            pytest.skip("PySide6 未安装")
        from yuyi_desktop.ui.widgets.control_center_widget import (
            ControlCenterWidget,
        )
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
        )
        from yuyi_desktop.services.control_service import (
            ControlService,
        )
        from yuyi_desktop.core.events.event_bus import (
            reset_event_bus_for_testing,
        )

        app = QApplication.instance() or QApplication([])

        # Mock client 返回成功数据
        session = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {
                "modules": [
                    {
                        "name": "growth", "display": "Growth",
                        "version": "1.0", "description": "Growth engine",
                        "state_field": "growth_enabled",
                        "enabled": True, "readonly": False,
                        "controllable": True, "category": "evolution",
                    },
                    {
                        "name": "memory", "display": "Memory",
                        "version": "1.0", "description": "Memory",
                        "state_field": "memory_enabled",
                        "enabled": False, "readonly": False,
                        "controllable": True, "category": "core",
                    },
                    {
                        "name": "runtime", "display": "Runtime",
                        "version": "1.0", "description": "Runtime core",
                        "state_field": "runtime_enabled",
                        "enabled": True, "readonly": True,
                        "controllable": False, "category": "core",
                    },
                ],
                "state": {
                    "growth_enabled": True,
                    "memory_enabled": False,
                    "runtime_enabled": True,
                    "safe_mode": False,
                    "maintenance_mode": False,
                },
                "audit_count": 5,
                "server_status": "ok",
                "version": "0.10.5",
                "uptime_seconds": 12.0,
            },
            "schema_version": "1.0",
        }
        session.request.return_value = resp
        api = ControlApiClient(session=session)
        bus = reset_event_bus_for_testing()
        svc = ControlService(api_client=api, event_bus=bus, enable_events=False)
        widget = ControlCenterWidget(service=svc)
        try:
            widget._timer.stop()  # noqa: SLF001
        except Exception:
            pass
        # 触发现有刷新
        widget._refresh()  # noqa: SLF001
        # 至少 3 个 module row
        assert len(widget._module_rows) == 3  # noqa: SLF001
        # runtime 是 readonly
        runtime_row = widget._module_rows.get("runtime")  # noqa: SLF001
        assert runtime_row is not None
        assert runtime_row._readonly is True  # noqa: SLF001
        assert not runtime_row._enable_btn.isEnabled()  # noqa: SLF001
        assert not runtime_row._disable_btn.isEnabled()  # noqa: SLF001
        # memory enabled=False
        memory_row = widget._module_rows.get("memory")  # noqa: SLF001
        assert memory_row is not None
        assert memory_row._enabled is False  # noqa: SLF001
        # system mode row
        assert widget._safe_mode_row is not None  # noqa: SLF001
        assert widget._maintenance_row is not None  # noqa: SLF001
        widget.deleteLater()


# ============================================================
# 8. 集成测试:ControlService 通过真实 Flask test client
# ============================================================
class TestControlPlaneIntegration:
    """端到端:Flask API + Desktop Service 协同。"""

    def test_full_flow(self):
        try:
            from flask import Flask
        except ImportError:
            pytest.skip("Flask 未安装")
        from src.control.api.control_routes import register_control_api
        from src.control.api.config import GatewayAuthConfig
        from src.control.api.auth import set_auth_override
        from src.control.state.control_state import (
            reset_control_state_persistence_for_testing,
        )
        from src.control.registry.module_registry import (
            reset_module_registry_for_testing,
        )
        from src.control.manager.control_manager import (
            reset_control_manager_for_testing,
        )
        from yuyi_desktop.core.control_api_client import (
            ControlApiClient,
        )
        from yuyi_desktop.services.control_service import (
            ControlService,
        )
        import threading

        d = _make_temp_dir()
        reset_control_state_persistence_for_testing(data_dir=d)
        reset_module_registry_for_testing()
        reset_control_manager_for_testing()
        set_auth_override(GatewayAuthConfig(mode="disabled"))

        app = Flask("control_plane_integration_test")
        app.config["TESTING"] = True
        register_control_api(app)
        client = app.test_client()

        # 1. 初始状态
        resp = client.get("/api/v1/control/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["success"] is True
        # 7 个模块
        assert len(body["data"]["modules"]) == 7

        # 2. disable growth
        resp = client.post(
            "/api/v1/control/module/growth/disable",
            json={"operator": "desktop", "reason": "user requested"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["new_value"] is False

        # 3. status 现在 growth 应该是 OFF
        resp = client.get("/api/v1/control/status")
        body = resp.get_json()
        mods = {m["name"]: m for m in body["data"]["modules"]}
        assert mods["growth"]["enabled"] is False
        # 至少有一个模块还是 enabled=True
        assert any(m["enabled"] for m in mods.values())

        # 4. enable safe mode
        resp = client.post("/api/v1/control/safe_mode/enable")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["data"]["new_value"] is True

        # 5. audit 至少 2 条
        resp = client.get("/api/v1/control/audit?limit=20")
        body = resp.get_json()
        assert body["data"]["total"] >= 2

        # 6. 启动一个真实 HTTP server 让 Desktop ApiClient 走真实 HTTP
        # 使用 threading + werkzeug
        from werkzeug.serving import make_server
        port = _free_port()
        server = make_server("127.0.0.1", port, app, threaded=True)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        # 等待就绪
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.05)

        try:
            # 7. Desktop ApiClient 拉取 overview
            from yuyi_desktop.core.control_api_client import (
                ControlApiConfig,
            )
            api = ControlApiClient(
                config=ControlApiConfig(
                    base_url=f"http://127.0.0.1:{port}",
                ),
            )
            env = api.get_status()
            assert env["success"] is True
            data = env["data"]
            mods = {m["name"]: m for m in data["modules"]}
            assert mods["growth"]["enabled"] is False
            assert data["state"]["safe_mode"] is True
        finally:
            server.shutdown()
            # 还原
            set_auth_override(GatewayAuthConfig(mode="disabled"))


# ============================================================
# 9. 安全测试:不修改任何业务模块
# ============================================================
class TestNoBusinessModuleTouched:
    """验证 Control Plane 没有修改任何业务核心。"""

    def test_no_import_of_business_core(self):
        """
        静态检查:Control Plane 的核心模块不应 import 业务核心。
        """
        # 禁止 import 的业务核心
        forbidden_prefixes = [
            "src.runtime",
            "src.memory",
            "src.growth",
            "src.personality",
            "src.self_model",
        ]
        files_to_check = [
            "src/control/state/control_state.py",
            "src/control/registry/module_registry.py",
            "src/control/manager/control_manager.py",
            "src/control/api/control_routes.py",
        ]
        import ast
        for fpath in files_to_check:
            full = Path(fpath)
            if not full.exists():
                continue
            content = full.read_text(encoding="utf-8")
            try:
                tree = ast.parse(content, filename=fpath)
            except SyntaxError:
                # 解析失败 -> 跳过(让其它测试发现)
                continue
            for node in ast.walk(tree):
                # 处理 import x.y.z
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name
                        for prefix in forbidden_prefixes:
                            if name == prefix or name.startswith(prefix + "."):
                                pytest.fail(
                                    f"{fpath} 不应 import 业务核心: {name}"
                                )
                # 处理 from x.y.z import a
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for prefix in forbidden_prefixes:
                        if mod == prefix or mod.startswith(prefix + "."):
                            pytest.fail(
                                f"{fpath} 不应 import 业务核心: {mod}"
                            )
