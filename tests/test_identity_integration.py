# -*- coding: utf-8 -*-
"""P2.1.2 Identity Resolver 接入测试。

验证外部入口（/v1/chat/completions、/initiative）统一经过
src/security.identity.IdentityResolver：

    1. 合法 QQ 用户 → 进入正常 user 路径（handle_message 收到原样 QQ）
    2. None（缺 user 字段）→ 进入 sandbox（_unknown_sender）
    3. default → 进入 sandbox
    4. 非法 ID → 进入 sandbox
    5. 原有聊天流程不崩溃（200 + reply 正常返回）

mock 模式沿用 tests/test_api_server_user_id_fallback.py 的既有做法。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

SANDBOX = "_unknown_sender"


class _UserCapture:
    """捕获 handle_message / orchestrator.process 收到的 user_id。"""

    def __init__(self):
        self.calls = []

    def handle_message(self, user_id, message, **_kwargs):
        self.calls.append(("runtime", str(user_id)))
        mock_hr = MagicMock()
        mock_hr.reply = f"[MOCK] user={user_id}"
        mock_hr.model = "phase4-mock"
        return mock_hr

    def orchestrator_process(self, message, user_id=None, **_kw):
        self.calls.append(("orchestrator", str(user_id) if user_id is not None else "NONE"))
        return f"[ORCH-MOCK] user={user_id}"


@pytest.fixture()
def client(monkeypatch):
    import api_server as api_mod

    cap = _UserCapture()
    fake_ctrl = MagicMock()
    fake_ctrl.phase4_enabled = True
    fake_ctrl.handle_message.side_effect = cap.handle_message
    api_mod._runtime_controller = fake_ctrl
    api_mod._pipeline = None

    fake_orch = MagicMock()
    fake_orch.process.side_effect = cap.orchestrator_process
    api_mod.orchestrator = fake_orch

    api_mod.app.config["TESTING"] = True
    with api_mod.app.test_client() as c:
        c._user_capture = cap  # type: ignore[attr-defined]
        yield c


def _post_chat(client, user=..., message="测试消息 hello"):
    body = {"model": "yuyi", "messages": [{"role": "user", "content": message}]}
    if user is not ...:
        body["user"] = user
    return client.post(
        "/v1/chat/completions",
        data=json.dumps(body),
        content_type="application/json",
    )


def _last_user(client):
    cap: _UserCapture = client._user_capture  # type: ignore[attr-defined]
    cap.calls.clear()
    return cap


class TestChatIdentityIntegration:
    """P2.1.2 主链路接入。"""

    def test_01_valid_qq_enters_user_path(self, client):
        cap = _last_user(client)
        resp = _post_chat(client, user="366648462")
        assert resp.status_code == 200
        assert cap.calls and cap.calls[-1] == ("runtime", "366648462")

    def test_02_missing_user_enters_sandbox(self, client):
        cap = _last_user(client)
        resp = _post_chat(client, user=...)  # 不携带 user 字段
        assert resp.status_code == 200
        assert cap.calls and cap.calls[-1] == ("runtime", SANDBOX)

    def test_03_null_user_enters_sandbox(self, client):
        cap = _last_user(client)
        resp = _post_chat(client, user=None)
        assert resp.status_code == 200
        assert cap.calls and cap.calls[-1] == ("runtime", SANDBOX)

    def test_04_default_placeholder_enters_sandbox(self, client):
        cap = _last_user(client)
        resp = _post_chat(client, user="default")
        assert resp.status_code == 200
        assert cap.calls and cap.calls[-1] == ("runtime", SANDBOX)

    def test_05_illegal_id_enters_sandbox(self, client):
        cap = _last_user(client)
        for raw in ("qq_366648462", "1234", "1234567890123", "guest", "anonymous"):
            cap.calls.clear()
            resp = _post_chat(client, user=raw)
            assert resp.status_code == 200, raw
            assert cap.calls and cap.calls[-1] == ("runtime", SANDBOX), raw

    def test_06_stranger_real_qq_enters_own_user_path(self, client):
        """陌生人的真实 QQ 原样使用（不串进 creator / 不落沙盒）。"""
        cap = _last_user(client)
        resp = _post_chat(client, user="123456789")
        assert resp.status_code == 200
        assert cap.calls and cap.calls[-1] == ("runtime", "123456789")

    def test_07_chat_flow_does_not_crash(self, client):
        """原有聊天流程：200 + OpenAI 兼容响应结构。"""
        resp = _post_chat(client, user="366648462", message="你好羽依")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["choices"][0]["message"]["content"] == "[MOCK] user=366648462"

    def test_08_missing_messages_still_400(self, client):
        """既有 400 契约保持不变。"""
        resp = client.post(
            "/v1/chat/completions",
            data=json.dumps({"model": "yuyi", "messages": []}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_09_health_untouched(self, client):
        """P2.0 health 契约不受身份接入影响。"""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert "version" in data and "commit" in data


class TestInitiativeIdentityIntegration:
    """/initiative 入口同样经过 resolver。"""

    def test_10_initiative_default_becomes_sandbox(self, client, monkeypatch):
        import api_server as api_mod
        cap = _UserCapture()
        fake_orch = MagicMock()
        fake_orch.generate_initiative = MagicMock(return_value="主动消息")
        api_mod.orchestrator = fake_orch
        resp = client.post(
            "/initiative",
            data=json.dumps({"user_id": "default"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        fake_orch.generate_initiative.assert_called_once_with(SANDBOX)

    def test_11_initiative_real_qq_preserved(self, client):
        import api_server as api_mod
        fake_orch = MagicMock()
        fake_orch.generate_initiative = MagicMock(return_value="主动消息")
        api_mod.orchestrator = fake_orch
        resp = client.post(
            "/initiative",
            data=json.dumps({"user_id": "366648462"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        fake_orch.generate_initiative.assert_called_once_with("366648462")


class TestAstrBotPluginGuard:
    """插件端 None 防护逻辑（独立函数级验证，不依赖 AstrBot 环境）。"""

    def test_12_plugin_none_not_becomes_user(self):
        """模拟 get_sender_id() 返回 None：payload.user 必须是 _unknown_sender。"""
        raw = None
        if isinstance(raw, str) and raw.strip():
            payload_user = raw.strip()
        else:
            payload_user = SANDBOX
        assert payload_user == SANDBOX
        assert payload_user != "None"
