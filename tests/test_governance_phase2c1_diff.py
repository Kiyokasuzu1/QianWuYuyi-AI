# -*- coding: utf-8 -*-
"""Governance Console Phase 2C.1 Fact Differences 测试（T1-T8，offscreen）。

覆盖（任务书）：
  T1 equal 正确输出
  T2 contains 正确输出
  T3 contained 正确输出
  T4 无匹配不输出
  T5 空输入安全
  T6 输出字段严格限制（仅五字段，无 score/confidence/similarity/reason/decision）
  T7 源码扫描禁止（llm/openai/recommendation/suggestion/risk/interpretation/meaning）
  T8 最大 200 条截断

安全：dummy 数据；凭据隔离临时路径。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_diff import MAX_RESULTS, compare_facts  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _cand(cid, fact):
    return {"candidate_id": cid, "fact": fact, "current_status": "candidate"}


# ============ T1: equal ============
def test_t1_equal():
    rows = compare_facts([_cand("RC-100", "羽依喜欢安静")],
                         [("RC:RC-001", "羽依喜欢安静")])
    assert len(rows) == 1
    assert rows[0]["relation"] == "equal"
    assert rows[0]["candidate_id"] == "RC-100"
    assert rows[0]["matched_id"] == "RC:RC-001"


# ============ T2: contains ============
def test_t2_contains():
    rows = compare_facts([_cand("RC-101", "羽依喜欢安静的环境里读书")],
                         [("SM:SM-001", "喜欢安静")])
    assert len(rows) == 1
    assert rows[0]["relation"] == "contains"


# ============ T3: contained ============
def test_t3_contained():
    rows = compare_facts([_cand("RC-102", "喜欢安静")],
                         [("SM:SM-001", "羽依喜欢安静的环境里读书")])
    assert len(rows) == 1
    assert rows[0]["relation"] == "contained"


# ============ T4: 无匹配不输出 ============
def test_t4_no_match():
    rows = compare_facts([_cand("RC-103", "完全无关的文本")],
                         [("RC:RC-001", "另一段文本")])
    assert rows == []


# ============ T5: 空输入安全 ============
def test_t5_empty_inputs():
    assert compare_facts([], []) == []
    assert compare_facts(None, None) == []
    assert compare_facts([_cand("RC-1", "")], [("RC:RC-9", "x")]) == [], "空文本不比较"


# ============ T6: 输出字段严格限制 ============
def test_t6_strict_fields():
    rows = compare_facts([_cand("RC-200", "共同文本")], [("RC:RC-001", "共同文本")])
    assert rows
    assert set(rows[0].keys()) == {"candidate_id", "candidate_text", "matched_id",
                                   "matched_text", "relation"}
    for banned in ("score", "confidence", "similarity", "reason",
                   "recommendation", "decision"):
        assert banned not in rows[0], f"禁止输出字段: {banned}"


# ============ T7: 源码扫描 ============
def test_t7_source_scan():
    src = open(os.path.join(_REPO, "tools", "governance_diff.py"),
               encoding="utf-8").read()
    for token in ("llm", "openai", "recommendation", "suggestion", "risk",
                  "interpretation", "meaning"):
        assert token not in src.lower(), f"governance_diff.py 禁止出现: {token}"


# ============ T8: 最大 200 条截断 ============
def test_t8_max_results_cap():
    assert MAX_RESULTS == 200
    cands = [_cand(f"RC-{i}", "同一文本") for i in range(300)]
    rows = compare_facts(cands, [("RC:RC-001", "同一文本")])
    assert len(rows) == 200, "超出 200 必须截断"


# ============ 附加: 面板集成（只读 tab，无按钮，空状态） ============
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _fake_api(self, path, method="GET", body=None):
    if path.startswith("/candidates"):
        return {"count": 1, "candidates": [_cand("RC-300", "羽依每晚和清清道晚安")]}
    if path.startswith("/patterns"):
        return {"patterns": []}
    if path.startswith("/relationship-core"):
        return {"facts": [{"fact_id": "RC-001",
                           "agreements": ["羽依每晚和清清道晚安"],
                           "confirmed_at": "2026-08-27T05:00:00"}]}
    if path.startswith("/self-model-statements"):
        return {"statements": []}
    if "proposals" in path or "growth" in path:
        return {"data": {"pending": [], "approved": [], "applied": []}}
    if path.startswith("/audit-log"):
        return {"entries": []}
    if "personality" in path:
        return {"available": False, "data": {"current": {}}}
    return {}


def test_panel_diff_tab(monkeypatch, tmp_path, qapp):
    monkeypatch.setattr(GovernancePanel, "_api", _fake_api)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    html = panel.tab_diff.toHtml()
    assert "候选事实" in html
    assert "RC:RC-001" in html
    assert "文本关系" in html
    assert "equal" in html
    for banned in ("冲突", "错误", "重复", "异常", "风险", "建议"):
        assert banned not in html, f"差异视图禁止词: {banned}"


def test_panel_diff_empty(monkeypatch, tmp_path, qapp):
    def fake_empty(self, path, method="GET", body=None):
        if path.startswith("/candidates"):
            return {"count": 0, "candidates": []}
        if path.startswith("/patterns"):
            return {"patterns": []}
        if path.startswith("/relationship-core"):
            return {"facts": []}
        if path.startswith("/self-model-statements"):
            return {"statements": []}
        if "proposals" in path or "growth" in path:
            return {"data": {"pending": [], "approved": [], "applied": []}}
        if path.startswith("/audit-log"):
            return {"entries": []}
        if "personality" in path:
            return {"available": False, "data": {"current": {}}}
        return {}

    monkeypatch.setattr(GovernancePanel, "_api", fake_empty)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    assert "（暂无文本关系）" in panel.tab_diff.toHtml(), "空状态必须明确"
