# -*- coding: utf-8 -*-
"""Governance Console Phase 2C.2.2 Audit 聚合统计测试（T1-T5，offscreen）。

覆盖（任务书）：
  T1 action count 正确
  T2 object_type count 正确
  T3 空 audit 安全
  T4 输出字段限制（纯计数，无解释/总结/判断）
  T5 源码扫描无 LLM / recommendation / risk / suggestion

安全：dummy 数据；凭据隔离临时路径。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_audit_stats import action_counts, object_type_counts  # noqa: E402
from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _entry(action, obj_type, obj_id="RC-001"):
    return {"ts": "2026-08-27T05:00:00", "action": action,
            "object_type": obj_type, "object_id": obj_id, "reason": ""}


# ============ T1: action count ============
def test_t1_action_counts():
    entries = [_entry("review:confirm", "fact_candidate"),
               _entry("review:confirm", "fact_candidate"),
               _entry("review:reject", "fact_candidate"),
               _entry("review_pattern:confirm", "shared_life_pattern")]
    out = action_counts(entries)
    assert out["review:confirm"] == 2
    assert out["review:reject"] == 1
    assert out["review_pattern:confirm"] == 1


# ============ T2: object_type count ============
def test_t2_object_type_counts():
    entries = [_entry("a", "fact_candidate"), _entry("b", "fact_candidate"),
               _entry("c", "shared_life_pattern")]
    out = object_type_counts(entries)
    assert out["fact_candidate"] == 2
    assert out["shared_life_pattern"] == 1


# ============ T3: 空 audit 安全 ============
def test_t3_empty_safe():
    assert action_counts([]) == {}
    assert object_type_counts([]) == {}
    assert action_counts(None) == {}
    assert object_type_counts(None) == {}


# ============ T4: 输出字段限制 ============
def test_t4_output_shape():
    entries = [_entry("review:confirm", "fact_candidate")]
    for out in (action_counts(entries), object_type_counts(entries)):
        for k, v in out.items():
            assert isinstance(k, str)
            assert isinstance(v, int), "计数必须为整数"
    text = str(action_counts(entries))
    for banned in ("异常", "风险", "重要", "建议", "效果", "趋势"):
        assert banned not in text, f"统计输出禁止: {banned}"


# ============ T5: 源码扫描 ============
def test_t5_source_scan():
    src = open(os.path.join(_REPO, "tools", "governance_audit_stats.py"),
               encoding="utf-8").read()
    for token in ("llm", "openai", "recommendation", "suggestion", "risk",
                  "interpretation", "meaning", "summary"):
        assert token not in src.lower(), f"governance_audit_stats.py 禁止出现: {token}"


# ============ 附加: 面板集成（统计行渲染） ============
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_panel_audit_stats(monkeypatch, tmp_path, qapp):
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
            return {"data": {"pending": [], "approved": [], "applied": []}}
        if path.startswith("/audit-log"):
            return {"entries": [_entry("review:confirm", "fact_candidate"),
                                _entry("review:confirm", "fact_candidate"),
                                _entry("review_pattern:confirm", "shared_life_pattern")]}
        if "personality" in path:
            return {"available": False, "data": {"current": {}}}
        return {}

    monkeypatch.setattr(GovernancePanel, "_api", fake_api)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    text = panel.lbl_audit_stats.text()
    assert "review:confirm: 2" in text
    assert "review_pattern:confirm: 1" in text
    assert "fact_candidate: 2" in text
    assert "shared_life_pattern: 1" in text
