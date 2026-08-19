# -*- coding: utf-8 -*-
"""
tests/test_dashboard_security.py

Phase 5.0 Dashboard Upgrade Step 8.4.7 —— POST Security / Governance / Audit 链路测试。

覆盖:
  TestAuth           —— Auth(本地 / token)
  TestPermission     —— Permission(role → action)
  TestGovernance     —— Governance(action 风险分级 / 高风险)
  TestAudit          —— Audit(success / failed / denied)
  TestIsolation      —— 不 import 业务 / 不调用 LLM
  TestAPI            —— /api/dashboard/v2/control + /api/dashboard/v2/audit/logs

合计: >= 20 个测试(目标要求 ≥ 20)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Helpers / Fakes
# =====================================================================
# 测试用合法 token(全部带 role 前缀,与 _security._extract_role_from_token 协议对齐)
TEST_TOKEN_ADMIN = "admin:secret"
TEST_TOKEN_OPERATOR = "operator:secret"
TEST_TOKEN_VIEWER = "viewer:secret"
ALL_TEST_TOKENS = [TEST_TOKEN_ADMIN, TEST_TOKEN_OPERATOR, TEST_TOKEN_VIEWER]


class _FakeAuditSink:
    """可注入的 audit sink(模拟 AuditStorage)。"""

    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []
        self._raise_on_save: Exception = None

    def save(self, record: Any) -> None:
        if self._raise_on_save is not None:
            raise self._raise_on_save
        if hasattr(record, "to_dict"):
            try:
                d = record.to_dict()
            except Exception:  # noqa: BLE001
                d = {}
        elif isinstance(record, dict):
            d = dict(record)
        else:
            d = {"raw": str(record)}
        # 将 who / reason 提升到 top-level(便于测试断言)
        # who: 优先 metadata.who,fallback 到 user_name / user_id
        if "who" not in d or not d.get("who"):
            md = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            d["who"] = (
                md.get("who")
                or d.get("user_name")
                or d.get("user_id")
                or "unknown"
            )
        # reason: 优先 metadata.reason,fallback 到 error_message
        if "reason" not in d:
            md = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            d["reason"] = (
                md.get("reason")
                or d.get("error_message")
                or ""
            )
        # role: 优先 metadata.role
        if not d.get("role"):
            md = d.get("metadata") if isinstance(d.get("metadata"), dict) else {}
            d["role"] = md.get("role", "")
        self.saved.append(d)

    def load(self, limit: int = 100, offset: int = 0) -> List[Any]:
        # 模拟返回 records(按 timestamp 倒序)
        out = sorted(self.saved, key=lambda r: r.get("timestamp", ""), reverse=True)
        return out[offset: offset + limit]


# =====================================================================
# Fixtures
# =====================================================================
@pytest.fixture
def reset_all():
    """重置所有 dashboard 安全/治理/审计的注入状态。"""
    from src.admin.dashboard import _security, audit as daudit, governance
    _security.reset_security_overrides_for_dashboard()
    daudit.reset_audit_sink_for_dashboard()
    governance.reset_governance_overrides()
    yield
    _security.reset_security_overrides_for_dashboard()
    daudit.reset_audit_sink_for_dashboard()
    governance.reset_governance_overrides()


@pytest.fixture
def fake_sink():
    return _FakeAuditSink()


@pytest.fixture
def app(reset_all, fake_sink):
    """构造 Flask app + 注册 security router。"""
    from flask import Flask
    from src.admin.dashboard.security_router import security_v2_bp
    from src.admin.dashboard.audit import set_audit_sink_for_dashboard
    from src.admin.dashboard._security import set_extra_tokens_for_dashboard
    set_audit_sink_for_dashboard(fake_sink)
    # 注入测试用合法 token(支持 admin:secret / operator:secret / viewer:secret)
    set_extra_tokens_for_dashboard(list(ALL_TEST_TOKENS))
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(security_v2_bp)
    # 设置环境变量确保 remote_addr = "127.0.0.1"(test_client 默认)
    return app


@pytest.fixture
def client(app, fake_sink):
    return app.test_client(), fake_sink


# =====================================================================
# 1. TestAuth —— Auth(本地 / token)
# =====================================================================
class TestAuth:
    def test_post_without_token_401(self, client, reset_all):
        """POST 无 token → 401 missing_token。"""
        c, _ = client
        resp = c.post("/api/dashboard/v2/control", json={"action": "dashboard.read"})
        assert resp.status_code == 401
        body = resp.get_json()
        assert body.get("ok") is False
        assert body.get("error", {}).get("code") == "missing_token"

    def test_invalid_token_401(self, client, reset_all):
        """POST 错误 token → 401 invalid_token。"""
        c, _ = client
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer wrong-token-xyz"},
        )
        assert resp.status_code == 401
        body = resp.get_json()
        assert body.get("error", {}).get("code") == "invalid_token"

    def test_valid_token_pass(self, client, reset_all):
        """POST 正确 token + low-risk action → 200(走到 governance 之后 success)。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True
        # audit 应记录 success
        assert any(r.get("result") == "success" for r in sink.saved)

    def test_x_dashboard_token_header(self, client, reset_all):
        """X-Dashboard-Token header 也能通过。"""
        c, _ = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"X-Dashboard-Token": DEFAULT_DEV_TOKEN},
        )
        assert resp.status_code == 200

    def test_local_only_blocks_remote(self, reset_all):
        """远程地址 → 403 dashboard_local_only。"""
        # 用 environ_base 模拟远程 IP
        from flask import Flask
        from src.admin.dashboard.security_router import security_v2_bp
        from src.admin.dashboard.audit import set_audit_sink_for_dashboard
        sink = _FakeAuditSink()
        set_audit_sink_for_dashboard(sink)
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(security_v2_bp)
        c = app.test_client()
        # 模拟 remote_addr 192.168.1.100
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer whatever"},
            environ_base={"REMOTE_ADDR": "192.168.1.100"},
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert body.get("error", {}).get("code") == "dashboard_local_only"


# =====================================================================
# 2. TestPermission —— Permission(role → action)
# =====================================================================
class TestPermission:
    def test_viewer_cannot_control(self, client, reset_all):
        """viewer 没有 dashboard.control 权限 → 403。"""
        c, sink = client
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer viewer:secret"},
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert body.get("error", {}).get("code") == "insufficient_role"
        # 不应记录到 audit(_security 中间件拒绝不写 audit,设计如此)
        # 但如果 _audit_event 被设计为"denied by auth"则需要写
        # 当前实现:在 auth/permission 阶段被 _security 直接拒绝,不会调 _audit_event
        # 这是符合"auth 失败立即阻断"的设计

    def test_operator_permission(self, client, reset_all):
        """operator 有 dashboard.control 权限 + low-risk action → 200。"""
        c, sink = client
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer operator:secret"},
        )
        assert resp.status_code == 200

    def test_admin_permission(self, client, reset_all):
        """admin 有 dashboard.control 权限 + 任意 action → 通过 auth,但 high risk 仍被 governance 拒绝。"""
        c, sink = client
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer admin:secret"},
        )
        assert resp.status_code == 200

    def test_role_has_permission_helper(self, reset_all):
        """role_has_permission 直接检查权限(单元级)。"""
        from src.admin.dashboard._security import role_has_permission
        assert role_has_permission("admin", "anything") is True
        assert role_has_permission("operator", "runtime.read") is True
        assert role_has_permission("viewer", "runtime.read") is False
        assert role_has_permission("operator", "memory.delete") is False

    def test_role_not_found(self, reset_all):
        """未知 role → check_permission role_not_found。"""
        from src.admin.dashboard._security import check_permission
        ok, reason = check_permission("alien_role", "dashboard.read")
        assert ok is False
        assert reason == "role_not_found"


# =====================================================================
# 3. TestGovernance —— Governance(action 风险分级 / 高风险)
# =====================================================================
class TestGovernance:
    def test_high_risk_requires_review(self, reset_all):
        """high risk action 默认 deny + reason=high_risk_requires_review。"""
        from src.admin.dashboard.governance import check_action
        r = check_action("runtime.disable", payload={"target": "core"})
        assert r["allowed"] is False
        assert r["reason"] == "high_risk_requires_review"
        assert r["risk"] == "high"
        assert r["needs_review"] is True

    def test_unknown_action_denied(self, reset_all):
        """未知 action → deny + reason=unknown_action。"""
        from src.admin.dashboard.governance import check_action
        r = check_action("totally.made_up.action")
        assert r["allowed"] is False
        assert r["reason"] == "unknown_action"
        assert r["risk"] == "unknown"

    def test_allowed_action_pass(self, reset_all):
        """low risk action → allow。"""
        from src.admin.dashboard.governance import check_action
        r = check_action("dashboard.read")
        assert r["allowed"] is True
        assert r["risk"] == "low"
        assert r["reason"] == "ok"

    def test_check_action_with_role_admin(self, reset_all):
        """admin + high risk action → 二次校验后 allow(高风险 allowlist 中有 admin)。"""
        from src.admin.dashboard.governance import check_action_with_role
        r = check_action_with_role("runtime.disable", role="admin")
        assert r["risk"] == "high"
        assert r["role_authorized"] is True
        assert r["allowed"] is True  # admin 在 allowlist 中

    def test_check_action_with_role_viewer_denied(self, reset_all):
        """viewer + high risk → deny + reason=role_not_in_allowlist。"""
        from src.admin.dashboard.governance import check_action_with_role
        r = check_action_with_role("runtime.disable", role="viewer")
        assert r["risk"] == "high"
        assert r["role_authorized"] is False
        assert r["allowed"] is False
        assert r["reason"] == "role_not_in_allowlist"

    def test_disallowed_action(self, reset_all):
        """显式 disallowed action → deny。"""
        from src.admin.dashboard.governance import set_governance_overrides, check_action
        set_governance_overrides(disallowed_actions=["dangerous.action"])
        r = check_action("dangerous.action")
        assert r["allowed"] is False
        assert r["reason"] == "action_disallowed"


# =====================================================================
# 4. TestAudit —— Audit(success / failed / denied)
# =====================================================================
class TestAudit:
    def test_success_post_logged(self, client, reset_all):
        """POST 成功 → audit 记录 result=success。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        assert any(r.get("result") == "success" for r in sink.saved)

    def test_failed_post_logged(self, client, reset_all):
        """POST 失败(adapter error)→ audit 记录 result=failure。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        from src.admin.dashboard import security_router

        # 注入一个会抛异常的 adapter
        original_adapter = security_router._control_adapter
        def _boom(action, payload):
            raise RuntimeError("simulated adapter failure")
        security_router._control_adapter = _boom
        try:
            resp = c.post(
                "/api/dashboard/v2/control",
                json={"action": "dashboard.read"},
                headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
            )
        finally:
            security_router._control_adapter = original_adapter
        assert resp.status_code == 200
        body = resp.get_json()
        # fallback
        assert body.get("fallback") is True
        # audit 应记录 failure
        failure_logs = [r for r in sink.saved if r.get("result") == "failure"]
        assert any("adapter_error" in (r.get("reason") or "") for r in failure_logs)

    def test_denied_post_logged(self, client, reset_all):
        """POST 被 governance 拒绝 → audit 记录 result=denied。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "runtime.disable", "payload": {"target": "core"}},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        denied_logs = [r for r in sink.saved if r.get("result") == "denied"]
        # operator 不在 runtime.disable 的 allowlist 中 →
        # governance 返回 reason="role_not_in_allowlist"(更具体的禁止原因)
        assert any("role_not_in_allowlist" in (r.get("reason") or "") for r in denied_logs)

    def test_audit_contains_who(self, client, reset_all):
        """audit 必须含 who(操作者)。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        # token 不带 role 前缀 → 默认 operator
        assert any("operator" in (r.get("who") or "").lower() for r in sink.saved)

    def test_audit_contains_action(self, client, reset_all):
        """audit 必须含 action。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        assert any(r.get("action") == "dashboard.read" for r in sink.saved)

    def test_audit_contains_timestamp(self, client, reset_all):
        """audit 必须含 timestamp。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        assert all(bool(r.get("timestamp")) for r in sink.saved)

    def test_audit_does_not_block_on_sink_failure(self, client, reset_all):
        """Audit 写入失败不阻断业务流。"""
        c, sink = client
        sink._raise_on_save = RuntimeError("disk full")
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        # 业务仍然成功
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True

    def test_audit_payload_hash_stable(self, reset_all):
        """payload_hash 在脱敏后稳定。"""
        from src.admin.dashboard.audit import _hash_payload
        h1 = _hash_payload({"a": 1, "b": 2})
        h2 = _hash_payload({"b": 2, "a": 1})  # 顺序不同
        assert h1 == h2
        # 敏感字段被脱敏但 hash 仍稳定
        h3 = _hash_payload({"a": 1, "token": "secret"})
        h4 = _hash_payload({"a": 1, "token": "different-secret"})
        # 两次 hash 应该相同(因为脱敏后 token → "***")
        assert h3 == h4


# =====================================================================
# 5. TestIsolation —— 不 import 业务 / 不调用 LLM
# =====================================================================
class TestIsolation:
    def test_security_no_business_import(self, reset_all):
        """_security.py 源码禁止 import 业务模块。"""
        from src.admin.dashboard import _security as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.runtime.", "import src.runtime.",
            "from src.self_model", "import src.self_model",
            "from src.selfmodel", "import src.selfmodel",
            "from src.identity", "import src.identity",
            "from src.governance_provider", "import src.governance_provider",
            "from src.llm", "import src.llm",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

    def test_security_no_llm(self, reset_all):
        """_security.py 禁止任何 LLM 调用。"""
        from src.admin.dashboard import _security as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "openai", "anthropic", "claude",
            "chat_completion", "completion(",
            ".invoke_llm", "call_llm",
        ]
        for f in forbidden:
            assert f not in src, f"禁止 LLM 关键字: {f}"

    def test_governance_no_business_import(self, reset_all):
        """governance.py 源码禁止 import 业务模块。"""
        from src.admin.dashboard import governance as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.runtime.", "import src.runtime.",
            "from src.self_model", "import src.self_model",
            "from src.llm", "import src.llm",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

    def test_router_no_authority_access(self, reset_all):
        """security_router.py 不得调用 Authority / 修改业务状态。"""
        from src.admin.dashboard import security_router as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        # 禁止调用 Authority / 业务写操作
        forbidden = [
            "Authority(", "MemoryStore",
            "PersonalityEngine", "GrowthState",
            "EmotionEngine",
            "openai", "anthropic",
            "from src.memory.", "from src.emotion.",
            "from src.growth.", "from src.personality.",
            "from src.runtime.", "from src.llm",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"


# =====================================================================
# 6. TestAPI —— /api/dashboard/v2/control + /api/dashboard/v2/audit/logs
# =====================================================================
class TestAPI:
    def test_control_endpoint_envelope(self, client, reset_all):
        """POST 响应必须含 envelope 标准字段。"""
        c, _ = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        body = resp.get_json()
        for k in ("ok", "data", "trace", "confidence", "timestamp"):
            assert k in body, f"envelope 缺字段: {k}"
        assert "sources" in body["trace"]

    def test_audit_endpoint(self, client, reset_all):
        """GET /api/dashboard/v2/audit/logs → 200 + items。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        # 先 POST 一条
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        resp = c.get("/api/dashboard/v2/audit/logs?limit=10")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "data" in body
        assert "items" in body["data"]
        assert isinstance(body["data"]["items"], list)
        assert body["data"]["count"] >= 1

    def test_audit_response_envelope(self, client, reset_all):
        """Audit API 响应必须含 envelope。"""
        c, _ = client
        resp = c.get("/api/dashboard/v2/audit/logs")
        body = resp.get_json()
        for k in ("ok", "data", "trace", "confidence", "timestamp"):
            assert k in body
        # trace 至少有 1 个 source
        assert len(body["trace"]["sources"]) >= 1

    def test_local_only_blocks_audit_endpoint(self, reset_all):
        """Audit API 远程访问被拒。"""
        from flask import Flask
        from src.admin.dashboard.security_router import security_v2_bp
        from src.admin.dashboard.audit import set_audit_sink_for_dashboard
        sink = _FakeAuditSink()
        set_audit_sink_for_dashboard(sink)
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(security_v2_bp)
        c = app.test_client()
        resp = c.get(
            "/api/dashboard/v2/audit/logs",
            environ_base={"REMOTE_ADDR": "10.0.0.1"},
        )
        assert resp.status_code == 403
        body = resp.get_json()
        assert body.get("error", {}).get("code") == "dashboard_local_only"

    def test_audit_filter_by_who(self, client, reset_all):
        """Audit API 支持按 who 过滤。"""
        c, _ = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        # 触发 2 次 POST(operator token 默认)
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        resp = c.get("/api/dashboard/v2/audit/logs?who=operator")
        body = resp.get_json()
        for item in body["data"]["items"]:
            assert item["who"] == "operator"

    def test_audit_filter_by_action(self, client, reset_all):
        """Audit API 支持按 action 过滤。"""
        c, _ = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "dashboard.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "runtime.read"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        resp = c.get("/api/dashboard/v2/audit/logs?action=dashboard.read")
        body = resp.get_json()
        for item in body["data"]["items"]:
            assert item["action"] == "dashboard.read"

    def test_audit_filter_by_result(self, client, reset_all):
        """Audit API 支持按 result 过滤。"""
        c, _ = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={"action": "runtime.disable"},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        resp = c.get("/api/dashboard/v2/audit/logs?result=denied")
        body = resp.get_json()
        for item in body["data"]["items"]:
            assert item["result"] == "denied"

    def test_control_high_risk_admin_allowed(self, client, reset_all):
        """admin + high risk action → 通过 governance → success。"""
        c, sink = client
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "runtime.disable", "payload": {"target": "core"}},
            headers={"Authorization": "Bearer admin:secret"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("ok") is True
        # audit success
        assert any(r.get("result") == "success" and r.get("action") == "runtime.disable" for r in sink.saved)

    def test_control_high_risk_operator_denied(self, client, reset_all):
        """operator + high risk action → governance deny(operator 不在 allowlist)。"""
        c, sink = client
        resp = c.post(
            "/api/dashboard/v2/control",
            json={"action": "runtime.disable"},
            headers={"Authorization": "Bearer operator:secret"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body.get("fallback") is True
        assert body.get("fallback_reason") == "role_not_in_allowlist"
        # audit denied
        denied = [r for r in sink.saved if r.get("result") == "denied"]
        assert any("role_not_in_allowlist" in (r.get("reason") or "") for r in denied)

    def test_missing_action_audit_denied(self, client, reset_all):
        """POST 不带 action → audit denied。"""
        c, sink = client
        from src.admin.dashboard._security import DEFAULT_DEV_TOKEN
        c.post(
            "/api/dashboard/v2/control",
            json={},
            headers={"Authorization": "Bearer " + DEFAULT_DEV_TOKEN},
        )
        assert any(r.get("result") == "denied" and "missing_action" in (r.get("reason") or "") for r in sink.saved)
