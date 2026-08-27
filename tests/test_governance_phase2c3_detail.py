# -*- coding: utf-8 -*-
"""Governance Console Phase 2C.3 Detail Explorer 测试（T1-T5+，offscreen）。

覆盖（任务书）：
  T1 数据映射正确（id/名称/状态/原始字段/相关事件/相关审计/影响字段）
  T2 空对象安全
  T3 缺字段安全
  T4 输出字段限制（无判断/推荐字段）
  T5 无禁止词扫描（llm/summary(字段名豁免)/interpretation/meaning/recommendation/suggestion/risk）
  + 面板集成（详情按钮 → tab 渲染）

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
from tools.governance_detail import build_detail  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _obj(cid="RC-100", fact="羽依每晚和清清道晚安", status="candidate"):
    return {"candidate_id": cid, "fact": fact, "status": status}


def _events():
    return [{"ts": "2026-08-27T05:00:00", "action": "review:confirm",
             "object_id": "RC-100", "status": "confirmed", "reason": "审核",
             "kind": "relationship_core"}]


def _audit():
    return [{"ts": "2026-08-27T05:00:00", "action": "review:confirm",
             "object_type": "fact_candidate", "object_id": "RC-100",
             "reviewer": "admin", "reason": "审核备注"}]


# ============ T1: 数据映射正确 ============
def test_t1_mapping():
    lines = build_detail(_obj(), "candidate", _events(), _audit(),
                         ["fact_candidate.status"])
    joined = "\n".join(lines)
    assert "对象: RC-100" in joined
    assert "名称: 羽依每晚和清清道晚安" in joined
    assert "当前状态: candidate" in joined
    assert "fact: 羽依每晚和清清道晚安" in joined
    assert "相关 Timeline 事件: 1 条" in joined
    assert "review:confirm" in joined
    assert "相关 Audit 记录: 1 条" in joined
    assert "admin" in joined
    assert "fact_candidate.status" in joined, "影响字段来自固定映射"


# ============ T2: 空对象安全 ============
def test_t2_empty_object():
    lines = build_detail(None, "candidate", [], [], [])
    assert any("（无选中对象）" in l for l in lines)


# ============ T3: 缺字段安全 ============
def test_t3_missing_fields():
    lines = build_detail({"candidate_id": "X"}, "candidate", None, None, None)
    joined = "\n".join(lines)
    assert "对象: X" in joined
    assert "名称: —" in joined
    assert "（无原始事实字段）" in joined
    assert "相关 Timeline 事件: 0 条" in joined
    assert "Explain 上下文: （无）" in joined


# ============ T4: 输出字段限制 ============
def test_t4_output_shape():
    lines = build_detail(_obj(), "candidate", _events(), _audit(),
                         ["fact_candidate.status"])
    joined = "\n".join(lines)
    for banned in ("score", "confidence:", "similarity", "recommendation",
                   "priority", "系统认为", "应该", "建议"):
        assert banned not in joined, f"详情输出禁止: {banned}"


# ============ T5: 无禁止词扫描 ============
def test_t5_source_scan():
    src = open(os.path.join(_REPO, "tools", "governance_detail.py"),
               encoding="utf-8").read()
    for token in ("llm", "openai", "interpretation", "meaning",
                  "recommendation", "suggestion", "risk"):
        assert token not in src.lower(), f"governance_detail.py 禁止出现: {token}"
    # "summary" 仅允许作为 pattern 字段名引用
    assert not re.search(r'summary(?!")', src, re.IGNORECASE)


# ============ 附加: 面板集成 ============
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_panel_detail_flow(monkeypatch, tmp_path, qapp):
    def fake_api(self, path, method="GET", body=None):
        if path.startswith("/candidates"):
            return {"count": 1, "candidates": [_obj()]}
        if path.startswith("/patterns"):
            return {"patterns": []}
        if path.startswith("/relationship-core"):
            return {"facts": []}
        if path.startswith("/self-model-statements"):
            return {"statements": []}
        if "proposals" in path or "growth" in path:
            return {"data": {"pending": [], "approved": [], "applied": []}}
        if path.startswith("/audit-log"):
            return {"entries": _audit()}
        if "personality" in path:
            return {"available": False, "data": {"current": {}}}
        return {}

    monkeypatch.setattr(GovernancePanel, "_api", fake_api)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    panel.list.setCurrentRow(0)  # 选中候选
    panel.show_detail("candidate")
    html = panel.tab_detail.toHtml()
    assert "对象: RC-100" in html
    assert "相关 Timeline 事件" in html
    assert "相关 Audit 记录" in html
    assert panel.tabs.currentWidget() is panel.tab_detail, "应切换到详情 tab"
