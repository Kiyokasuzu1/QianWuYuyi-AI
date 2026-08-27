# -*- coding: utf-8 -*-
"""Governance Console Overview growth 字段契约测试（Phase 2C 前置清理，T1-T5，offscreen）。

背景：/growth 计数位于 data.{pending,approved,applied}（列表），
既有 Overview 曾用顶层 g.get('pending') 读取（错误路径）。
本测试验证修正后行为。

覆盖（任务书）：
  T1 正确读取 data.pending / data.approved / data.applied
  T2 顶层不存在字段时不会读取错误路径
  T3 缺少 data 时安全降级（空列表默认，不抛）
  T4 输出仅包含数量事实（无 recommendation/suggestion/risk/interpretation/meaning）
  T5 全量 2B 测试回归由执行命令验证（本文件含契约渲染断言）

安全：dummy 数据；凭据隔离临时路径。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _fake_api_with(growth_resp):
    def fake_api(self, path, method="GET", body=None):
        if path.startswith("/candidates"):
            return {"count": 0, "candidates": []}
        if path.startswith("/patterns"):
            return {"patterns": []}
        if path.startswith("/relationship-core"):
            return {"facts": []}
        if path.startswith("/self-model-statements"):
            return {"statements": []}
        if "proposals" in path or "growth" in path:
            return growth_resp
        if path.startswith("/audit-log"):
            return {"entries": []}
        if "personality" in path:
            return {"available": False, "data": {"current": {}}}
        return {}
    return fake_api


def _overview_html(monkeypatch, tmp_path, qapp, growth_resp):
    monkeypatch.setattr(GovernancePanel, "_api", _fake_api_with(growth_resp))
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    return panel.tab_overview.toHtml()


# ============ T1: 正确读取 data 内计数 ============
def test_t1_reads_data_counts(monkeypatch, tmp_path, qapp):
    html = _overview_html(monkeypatch, tmp_path, qapp,
                          {"data": {"pending": [1, 2], "approved": [3], "applied": []}})
    assert "pending 2 · approved 1 · applied 0" in html, \
        "计数必须来自 data.{pending,approved,applied} 列表长度"


# ============ T2: 顶层不存在字段时不读取错误路径 ============
def test_t2_no_top_level_read(monkeypatch, tmp_path, qapp):
    # 响应只含 data（真实契约无顶层 pending/approved/applied）
    html = _overview_html(monkeypatch, tmp_path, qapp,
                          {"data": {"pending": [1], "approved": [], "applied": [2, 3]}})
    assert "None" not in html, "不得读取不存在的顶层字段（显示 None 即错误路径残留）"
    assert "pending 1 · approved 0 · applied 2" in html


# ============ T3: 缺少 data 时安全降级 ============
def test_t3_missing_data_safe(monkeypatch, tmp_path, qapp):
    html = _overview_html(monkeypatch, tmp_path, qapp, {})  # 无 data 键
    assert "pending 0 · approved 0 · applied 0" in html, "缺 data 必须空列表默认，不抛"


# ============ T4: 输出仅数量事实 ============
def test_t4_counts_only_no_judgement(monkeypatch, tmp_path, qapp):
    html = _overview_html(monkeypatch, tmp_path, qapp,
                          {"data": {"pending": [1], "approved": [2], "applied": [3]}})
    assert "pending 1 · approved 1 · applied 1" in html
    for token in ("recommendation", "suggestion", "risk", "interpretation",
                  "meaning", "系统认为", "建议"):
        assert token not in html.lower(), f"Overview growth 输出禁止: {token}"


# ============ T5: 契约渲染回归（全量 2B 回归由执行命令验证） ============
def test_t5_contract_render_regression(monkeypatch, tmp_path, qapp):
    # 多种返回形态均不抛、不显示 None
    for resp in ({"data": {}}, {"data": None}, {}, {"ok": False, "error": "x"}):
        html = _overview_html(monkeypatch, tmp_path, qapp, resp)
        assert "None" not in html
        assert "pending" in html or "读取失败" in html
