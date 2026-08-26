# -*- coding: utf-8 -*-
"""Governance Panel Phase 2A UI Bugfix 测试（T1-T10，offscreen）。

覆盖任务书（YUI_GOVERNANCE_CONSOLE_PHASE2A_UI_BUGFIX）：
- T1/T2: 首次连接保存弹窗（无凭据 + 用户输入 + 200 → 弹窗 → 保存）
- T3: 已存在凭据 → 不重复询问
- T4: 重新输入新 token + 200 → 可覆盖旧凭据
- T5: 401 → 不删除凭据
- T6: 环境变量 token → 既有优先级（env > input > stored）
- T7: Personality URL 与真实 route 一致（无前缀重复）
- T8: mock 200 → Overview 正常显示人格
- T9: 404 → Overview 明确显示读取失败
- T10: 人格失败不影响其他 Overview sections

安全：全部使用 dummy token（TEST_TOKEN_NOT_REAL），凭据路径全部注入临时目录。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TOKEN = "TEST_TOKEN_NOT_REAL_42"
TEST_TOKEN_2 = "TEST_TOKEN_NOT_REAL_43"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def qbox(monkeypatch):
    """mock 所有模态弹窗，记录调用，question 默认返回 Yes。"""
    calls = {"question": [], "critical": [], "info": []}

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


def _make_fake_api(personality="ok"):
    """类级 _api mock：按 path 分发，personality 可 ok / 404。"""
    def fake_api(self, path, method="GET", body=None):
        if "personality" in path:
            if personality == "ok":
                return {"available": True,
                        "data": {"current": {"warmth": 0.6, "playfulness": 0.5}}}
            raise RuntimeError("HTTP 404: Not Found")
        if path.startswith("/candidates"):
            return {"count": 1, "candidates": []}
        if path.startswith("/patterns"):
            return {"patterns": []}
        if path.startswith("/relationship-core"):
            return {"facts": []}
        if path.startswith("/self-model-statements"):
            return {"statements": []}
        if "proposals" in path:
            return {"proposals": []}
        if "growth" in path:
            return {"pending": 0, "approved": 0, "applied": 0}
        if path.startswith("/audit-log"):
            return {"entries": []}
        return {}
    return fake_api


def _new_panel(monkeypatch, tmp_path, qapp):
    """实例化面板（_api 为 fake；凭据注入临时路径）。"""
    monkeypatch.setattr(GovernancePanel, "_api", _make_fake_api())
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    return panel


# ---------- T1: 无凭据 + 用户输入 + 200 → 保存弹窗出现 ----------
def test_t1_save_prompt_appears_on_first_connect(monkeypatch, tmp_path, qapp, qbox):
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    panel.test_connect()
    assert qbox["question"], "首次连接（无凭据）应弹出保存凭据询问"
    assert panel._cred.exists(), "选择保存后凭据文件应被创建"


# ---------- T2: 选择保存 → credential.save() ----------
def test_t2_save_writes_credential(monkeypatch, tmp_path, qapp, qbox):
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    panel.ed_token.setText(TEST_TOKEN)
    panel.test_connect()
    assert panel._cred.load() == TEST_TOKEN
    assert panel._stored_token == TEST_TOKEN


# ---------- T3: 已存在凭据 → 不重复询问 ----------
def test_t3_existing_credential_no_requery(monkeypatch, tmp_path, qapp, qbox):
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    assert panel._cred.save(TEST_TOKEN) is True
    panel._load_stored_credential()
    assert panel._stored_token == TEST_TOKEN
    # 输入框为空（占位符提示已保存）→ 连接测试不弹保存
    panel.test_connect()
    assert not qbox["question"], "已存在凭据且未输入新 token 时不应弹保存询问"
    # 直接调用保存路径：存在凭据 → 跳过询问直接覆盖
    assert panel._prompt_save_credential(TEST_TOKEN) is True
    assert not qbox["question"]


# ---------- T4: 重新输入新 token + 200 → 可覆盖旧凭据 ----------
def test_t4_reenter_token_overwrites(monkeypatch, tmp_path, qapp, qbox):
    panel = _new_panel(monkeypatch, tmp_path, qapp)
    assert panel._cred.save(TEST_TOKEN) is True
    panel._load_stored_credential()
    panel.ed_token.setText(TEST_TOKEN_2)
    panel.test_connect()
    # 已有凭据时不重复询问（T3 语义），但新 token 必须静默覆盖旧凭据
    assert panel._cred.load() == TEST_TOKEN_2, "重新输入与已保存不同的 token 应覆盖旧凭据"


# ---------- T5: 401 → 不删除凭据 ----------
def test_t5_401_keeps_credential(monkeypatch, tmp_path, qapp, qbox):
    captured = {"urls": []}

    class Resp401:
        status_code = 401
        text = "unauthorized"

        def json(self):
            return {"error": "unauthorized"}

    def fake_get(url, headers=None, timeout=None):
        captured["urls"].append(url)
        return Resp401()

    monkeypatch.setattr("requests.get", fake_get)
    panel = GovernancePanel()  # 真实 _api（requests 层 mock 401）
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    assert panel._cred.save(TEST_TOKEN) is True
    panel.ed_token.setText(TEST_TOKEN)
    panel.test_connect()
    assert qbox["critical"], "401 应提示连接失败"
    assert panel._cred.exists(), "401 不应删除本机凭据"
    assert panel._cred.load() == TEST_TOKEN


# ---------- T6: 环境变量 token → 符合既有优先级 ----------
def test_t6_env_token_priority(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.setenv("YUYI_ADMIN_TOKEN", TEST_TOKEN)
    captured = {"headers": []}

    class RespOK:
        status_code = 200
        text = ""

        def json(self):
            return {"count": 1, "candidates": []}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"].append(dict(headers or {}))
        return RespOK()

    monkeypatch.setattr("requests.get", fake_get)
    panel = GovernancePanel()  # 真实 _api
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel.ed_token.setText("")  # 输入框为空 → 应回退到环境变量
    panel.test_connect()
    assert captured["headers"], "请求应携带 X-Admin-Token"
    assert captured["headers"][-1].get("X-Admin-Token") == TEST_TOKEN
    assert not qbox["question"], "环境变量 token 来源不应弹保存询问"


# ---------- T7: Personality URL 与真实 route 一致（无前缀重复） ----------
def test_t7_personality_url_matches_route(monkeypatch, tmp_path, qapp, qbox):
    captured = {"urls": []}

    class RespOK:
        status_code = 200
        text = ""

        def json(self):
            return {"available": True, "data": {"current": {}}}

    def fake_get(url, headers=None, timeout=None):
        captured["urls"].append(url)
        return RespOK()

    monkeypatch.setattr("requests.get", fake_get)
    panel = GovernancePanel()
    panel.ed_token.setText(TEST_TOKEN)
    panel._api("/admin/api/admin/governance/personality")
    panel._api("/candidates")
    base = panel.ed_base.text().strip().rstrip("/")
    assert captured["urls"][0] == base + "/admin/api/admin/governance/personality", \
        "Personality URL 必须与 admin_bp 真实 route 一致（无前缀重复）"
    assert captured["urls"][1] == base + "/admin/api/governance/candidates", \
        "gov_bp 端点仍应使用 /admin/api/governance 前缀"


# ---------- T8: mock 200 → Overview 正常显示人格 ----------
def test_t8_overview_shows_personality_ok(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.setattr(GovernancePanel, "_api", _make_fake_api(personality="ok"))
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    html = panel.tab_overview.toHtml()
    assert "人格（DERIVED" in html, "Overview 应显示人格区块"
    assert "warmth=0.6" in html, "Overview 应显示人格 trait 值"


# ---------- T9: 404 → 明确显示读取失败 ----------
def test_t9_overview_shows_personality_failure(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.setattr(GovernancePanel, "_api", _make_fake_api(personality="404"))
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    html = panel.tab_overview.toHtml()
    assert "读取失败" in html, "404 时 Overview 应明确显示读取失败"
    assert "HTTP 404" in html


# ---------- T10: 人格失败不影响其他 Overview sections ----------
def test_t10_personality_failure_does_not_break_overview(monkeypatch, tmp_path, qapp, qbox):
    monkeypatch.setattr(GovernancePanel, "_api", _make_fake_api(personality="404"))
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    html = panel.tab_overview.toHtml()
    assert "读取失败" in html
    assert "关系核心" in html, "人格失败不应影响关系核心区块"
    assert "候选" in html, "人格失败不应影响待审区块"
