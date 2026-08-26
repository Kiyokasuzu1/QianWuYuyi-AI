# -*- coding: utf-8 -*-
"""Governance Credential Store（Phase 2A）测试 T1-T10。

全部使用 dummy token（TEST_TOKEN_NOT_REAL），绝不使用生产 token。
DPAPI 为真实本机调用；路径全部注入临时目录，不触碰正式凭据。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_TOKEN = "TEST_TOKEN_NOT_REAL_42"
from tools.governance_credential_store import (  # noqa: E402
    CredentialStore, is_available,
)


@pytest.fixture
def store(tmp_path):
    return CredentialStore(tmp_path / "cred.bin")


# ---------- T1 DPAPI round-trip ----------
def test_t1_dpapi_round_trip(store):
    if not is_available():
        pytest.skip("非 Windows / DPAPI 不可用")
    assert store.save(TEST_TOKEN) is True
    assert store.load() == TEST_TOKEN


# ---------- T2 磁盘不含明文 ----------
def test_t2_no_plaintext_on_disk(store):
    if not is_available():
        pytest.skip("DPAPI 不可用")
    store.save(TEST_TOKEN)
    raw = store.path.read_bytes()
    assert TEST_TOKEN.encode() not in raw, "磁盘不得包含明文 token"
    assert TEST_TOKEN not in store.path.read_text(encoding="ascii", errors="replace")


# ---------- T3 restart simulation（Store A save → Store B load） ----------
def test_t3_restart_simulation(tmp_path):
    if not is_available():
        pytest.skip("DPAPI 不可用")
    p = tmp_path / "cred.bin"
    CredentialStore(p).save(TEST_TOKEN)
    assert CredentialStore(p).load() == TEST_TOKEN


# ---------- T4 clear ----------
def test_t4_clear(store):
    if not is_available():
        pytest.skip("DPAPI 不可用")
    store.save(TEST_TOKEN)
    assert store.exists()
    assert store.clear() is True
    assert store.exists() is False
    assert store.load() is None


# ---------- T5 corrupted credential ----------
def test_t5_corrupted(store):
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(b"not-a-dpapi-blob\x00\x01")
    assert store.load() is None  # 不崩溃、不返回随机值
    assert store.exists() is True  # 不覆盖/不删除损坏文件


# ---------- T6 401 不自动删凭据（面板层） ----------
def test_t6_401_does_not_auto_clear(monkeypatch, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    app = QApplication.instance() or QApplication([])

    from tools.governance_panel import GovernancePanel
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._cred.save(TEST_TOKEN)
    panel._stored_token = TEST_TOKEN

    class _FakeResp:
        status_code = 401
        def json(self): return {"error": "未授权"}
        @property
        def text(self): return "401"
    import requests as _req
    orig_get = _req.get
    _req.get = lambda url, headers=None, **kw: _FakeResp()
    try:
        with pytest.raises(RuntimeError) as ei:
            panel._api("/candidates")
        assert "401" in str(ei.value)
        assert "已保存的管理凭据可能已失效" in str(ei.value)
    finally:
        _req.get = orig_get
    # 凭据未被自动删除
    assert panel._cred.exists()
    assert panel._cred.load() == TEST_TOKEN


# ---------- T7 perfect token → 200 → save → next session auto load ----------
def test_t7_save_then_auto_load(monkeypatch, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: None)
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    app = QApplication.instance() or QApplication([])

    from tools.governance_panel import GovernancePanel
    p1 = GovernancePanel()
    p1._cred = CredentialStore(tmp_path / "cred.bin")
    assert p1._cred.save(TEST_TOKEN) is True

    # 第二次启动（新实例）模拟：自动读取
    p2 = GovernancePanel()
    p2._cred = CredentialStore(tmp_path / "cred.bin")
    p2._load_stored_credential()
    assert p2._stored_token == TEST_TOKEN
    assert "已保存凭据" in p2.ed_token.placeholderText()


# ---------- T8 invalid token → 401 → 不保存错误 token ----------
def test_t8_invalid_not_saved(monkeypatch, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    app = QApplication.instance() or QApplication([])

    from tools.governance_panel import GovernancePanel
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")

    class _FakeResp:
        status_code = 401
        def json(self): return {"error": "未授权"}
        @property
        def text(self): return "401"
    import requests as _req
    orig_get = _req.get
    _req.get = lambda url, headers=None, **kw: _FakeResp()
    try:
        with pytest.raises(RuntimeError):
            panel._api("/candidates")
    finally:
        _req.get = orig_get
    assert panel._cred.exists() is False  # 错误 token 未保存


# ---------- T9 DPAPI unavailable → 不写明文 ----------
def test_t9_dpapi_unavailable_no_plaintext(monkeypatch, tmp_path):
    from tools import governance_credential_store as cs
    monkeypatch.setattr(cs, "is_available", lambda: False)
    store = CredentialStore(tmp_path / "cred.bin")
    assert store.save(TEST_TOKEN) is False
    assert not store.path.exists()


# ---------- T10 token leak：错误信息不含完整 token ----------
def test_t10_no_token_leak(monkeypatch, tmp_path):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication, QMessageBox
    QMessageBox.critical = staticmethod(lambda *a, **k: None)
    app = QApplication.instance() or QApplication([])

    import requests as _req

    class _FakeResp:
        status_code = 401
        def json(self): return {"error": "未授权：缺失或无效的管理 token"}
        @property
        def text(self): return "401"

    orig_get = _req.get
    _req.get = lambda url, headers=None, **kw: _FakeResp()
    try:
        from tools.governance_panel import GovernancePanel
        panel = GovernancePanel()
        panel.ed_token.setText(TEST_TOKEN)
        try:
            panel._api("/candidates")
            raised = False
        except RuntimeError as exc:
            raised = True
            msg = str(exc)
            assert TEST_TOKEN not in msg, f"错误信息泄漏 token: {msg}"
            assert "401" in msg
        assert raised
        # 保存路径不打印完整 token（placeholder 只显示尾4位或占位）
        assert TEST_TOKEN not in panel.ed_token.placeholderText()
    finally:
        _req.get = orig_get
