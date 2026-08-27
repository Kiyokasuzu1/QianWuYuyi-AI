# -*- coding: utf-8 -*-
"""Governance Console Phase 2B.1 ConnectionState 测试（T1-T17，offscreen）。

覆盖（任务书）：
  1. token source：DPAPI / ENV / MANUAL / NONE
  2. 401 → UNAUTHORIZED 且凭据未删除
  3. timeout → FAILED
  4. 异常信息截断（≤120）
  5. 状态显示文本（徽标）
  6. 已有 T1-T10 回归（另文件，单独运行）

安全：全部 dummy token（TEST_TOKEN_NOT_REAL），凭据路径注入临时目录。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TOKEN = "TEST_TOKEN_NOT_REAL_42"
TEST_TOKEN_2 = "TEST_TOKEN_NOT_REAL_43"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.connection_state import ConnectionState  # noqa: E402
from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def qbox(monkeypatch):
    """mock 模态弹窗，避免真实弹窗阻塞。"""
    calls = {"question": [], "critical": []}

    def fake_question(parent, title, text, *args, **kwargs):
        calls["question"].append((title, text))
        return QMessageBox.Yes

    def fake_critical(parent, title, text, *args, **kwargs):
        calls["critical"].append((title, text))

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(fake_critical))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    return calls


def _fake_api(self, path, method="GET", body=None):
    """类级 _api mock（避免网络；实例化时的 refresh_all 也走它）。"""
    if path.startswith("/candidates"):
        return {"count": 1, "candidates": []}
    if path.startswith("/patterns"):
        return {"patterns": []}
    if path.startswith("/relationship-core"):
        return {"facts": []}
    if path.startswith("/self-model-statements"):
        return {"statements": []}
    if "proposals" in path or "growth" in path:
        return {"proposals": [], "pending": 0, "approved": 0, "applied": 0}
    if path.startswith("/audit-log"):
        return {"entries": []}
    if "personality" in path:
        return {"available": False, "data": {"current": {}}}
    return {}


def _new_panel(monkeypatch, tmp_path, qapp):
    monkeypatch.setattr(GovernancePanel, "_api", _fake_api)
    return _real_panel(monkeypatch, tmp_path, qapp)


def _real_panel(monkeypatch, tmp_path, qapp):
    """真实 _api 路径的面板实例（requests 由各测试 mock）。

    凭据强制隔离：_cred 注入临时路径 + 清空 _stored_token，
    任何场景都禁止 _prompt_save_credential 触碰本机默认凭据路径。
    """
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""  # 隔离本机真实凭据（%LOCALAPPDATA% 下用户已保存的凭据）
    return panel


# ============ 纯 ConnectionState 类 ============

def test_t1_initial_state():
    c = ConnectionState()
    assert c.status is None, "初始 = 尚未探测（≠ 健康）"
    assert c.consecutive_failures == 0
    assert c.last_success_at is None
    assert c.last_error is None
    assert c.is_stale is False


def test_t2_success_records_time_and_resets():
    c = ConnectionState()
    c.record_failure("network", "x")
    c.record_success()
    assert c.status == ConnectionState.CONNECTED
    assert c.last_success_at, "刷新成功必须记录数据时间"
    assert c.consecutive_failures == 0
    assert c.last_error is None
    assert c.is_stale is False


def test_t3_unauthorized_from_401():
    c = ConnectionState()
    c.record_failure("unauthorized", "HTTP 401")
    assert c.status == ConnectionState.UNAUTHORIZED
    assert c.is_stale is False  # 单次失败未达阈值


def test_t4_network_failure_failed():
    c = ConnectionState()
    c.record_failure("network", "connect timed out")
    assert c.status == ConnectionState.FAILED


def test_t5_non_auth_http_failure_failed():
    c = ConnectionState()
    c.record_failure("http", "HTTP 500: boom")
    assert c.status == ConnectionState.FAILED


def test_t6_error_detail_truncated():
    c = ConnectionState()
    c.record_failure("network", "E" * 500)
    assert len(c.last_error["detail"]) <= 120, "异常信息必须截断"
    assert c.last_error["kind"] == "network"


def test_t7_fail_threshold_fixed():
    c = ConnectionState()
    assert ConnectionState.FAIL_THRESHOLD == 3, "失败阈值必须为固定常量"
    c.record_failure("network", "1")
    c.record_failure("network", "2")
    assert c.is_stale is False, "2 次失败未达阈值"
    c.record_failure("network", "3")
    assert c.is_stale is True, "连续 3 次失败 → 数据可能陈旧"
    assert c.consecutive_failures == 3


def test_t8_success_clears_stale():
    c = ConnectionState()
    for _ in range(3):
        c.record_failure("network", "x")
    assert c.is_stale is True
    c.record_success()
    assert c.is_stale is False


# ============ 面板集成：token source ============

def test_t9_token_source_manual(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.delenv("YUYI_ADMIN_TOKEN", raising=False)
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    assert panel._token_source() == "MANUAL"


def test_t10_token_source_env(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.setenv("YUYI_ADMIN_TOKEN", TEST_TOKEN)
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText("")
    assert panel._token_source() == "ENV"


def test_t11_token_source_dpapi(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.delenv("YUYI_ADMIN_TOKEN", raising=False)
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    assert panel._cred.save(TEST_TOKEN) is True
    panel._load_stored_credential()
    panel.ed_token.setText("")
    assert panel._token_source() == "DPAPI"


def test_t12_token_source_none(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.delenv("YUYI_ADMIN_TOKEN", raising=False)
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText("")
    assert panel._token_source() == "NONE"


# ============ 面板集成：状态驱动 ============

def _requests_get_mock(resp_factory):
    def fake_get(url, headers=None, timeout=None):
        return resp_factory()
    return fake_get


def test_t13_401_shows_unauthorized_keeps_credential(monkeypatch, tmp_path, qapp, qbox):
    class Resp401:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"error": "unauthorized"}

    monkeypatch.setattr("requests.get", _requests_get_mock(lambda: Resp401()))
    monkeypatch.setattr("requests.post", _requests_get_mock(lambda: Resp401()))
    panel = _real_panel(monkeypatch, tmp_path, qapp)
    assert panel._cred.save(TEST_TOKEN) is True
    panel.ed_token.setText(TEST_TOKEN)
    panel.test_connect()
    assert panel._conn.status == ConnectionState.UNAUTHORIZED, "401 必须显示 UNAUTHORIZED"
    assert panel._cred.exists(), "401 禁止自动删除凭据"
    assert panel._cred.load() == TEST_TOKEN, "凭据内容不得被修改"
    assert "未授权" in panel.lbl_conn.text()


def test_t14_timeout_shows_failed(monkeypatch, tmp_path, qapp, qbox):
    import requests

    def boom(url, headers=None, timeout=None):
        raise requests.Timeout("connect timed out")

    monkeypatch.setattr("requests.get", boom)
    monkeypatch.setattr("requests.post", boom)
    panel = _real_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    with pytest.raises(requests.RequestException):
        panel._api("/candidates")
    assert panel._conn.status == ConnectionState.FAILED, "超时必须显示 FAILED"
    assert panel._conn.last_error["kind"] == "network"
    assert len(panel._conn.last_error["detail"]) <= 120
    assert "连接失败" in panel.lbl_conn.text()


def test_t15_success_shows_connected(monkeypatch, tmp_path, qapp, qbox):
    class RespOK:
        status_code = 200
        text = ""

        def json(self):
            return {"count": 1, "candidates": []}

    monkeypatch.setattr("requests.get", _requests_get_mock(lambda: RespOK()))
    monkeypatch.setattr("requests.post", _requests_get_mock(lambda: RespOK()))
    panel = _real_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    panel.test_connect()
    assert panel._conn.status == ConnectionState.CONNECTED
    assert "已连接" in panel.lbl_conn.text()
    assert panel._conn.last_success_at, "成功必须记录数据时间"


def test_t16_consecutive_failures_stale_label(monkeypatch, tmp_path, qapp, qbox):
    import requests

    def boom(url, headers=None, timeout=None):
        raise requests.Timeout("down")

    monkeypatch.setattr("requests.get", boom)
    monkeypatch.setattr("requests.post", boom)
    panel = _real_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    for _ in range(ConnectionState.FAIL_THRESHOLD):
        with pytest.raises(requests.RequestException):
            panel._api("/candidates")
    assert panel._conn.is_stale is True
    assert "数据可能陈旧" in panel.lbl_conn.text(), "连续失败后徽标必须标注陈旧"
    assert "数据截至" in panel.lbl_conn.text()


def test_t17_label_shows_token_source_and_tail_only(monkeypatch, tmp_path, qapp, qbox):
    class RespOK:
        status_code = 200
        text = ""

        def json(self):
            return {"count": 1, "candidates": []}

    monkeypatch.setattr("requests.get", _requests_get_mock(lambda: RespOK()))
    monkeypatch.setattr("requests.post", _requests_get_mock(lambda: RespOK()))
    panel = _real_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    panel.test_connect()
    text = panel.lbl_conn.text()
    assert "🔑 手动输入" in text, "徽标必须显示 token 来源"
    assert TEST_TOKEN not in text, "禁止显示完整 token"
    assert "42)" in text, "仅允许显示尾 4 位"
