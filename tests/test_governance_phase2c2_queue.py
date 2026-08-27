# -*- coding: utf-8 -*-
"""Governance Console Phase 2C.2.1 Queue UX 测试（T1-T5，offscreen）。

覆盖（任务书）：
  T1 状态过滤正确
  T2 排序规则正确（机械键：confidence/created_at 降序）
  T3 空数据安全
  T4 缺字段安全
  T5 不产生额外判断字段

安全：dummy 数据；凭据隔离临时路径。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_queue import (  # noqa: E402
    SORT_KEYS, filter_candidates, sort_candidates, status_counts,
)
from tools.governance_panel import GovernancePanel  # noqa: E402


def _c(cid, status="candidate", conf=0.5, created="2026-08-01T00:00:00"):
    return {"candidate_id": cid, "fact": f"事实{cid}", "current_status": status,
            "confidence": conf, "created_at": created}


# ============ T1: 状态过滤 ============
def test_t1_status_filter():
    cands = [_c("A", "candidate"), _c("B", "confirmed"), _c("C", "rejected"),
             _c("D", "held"), _c("E", "candidate")]
    assert [c["candidate_id"] for c in filter_candidates(cands, "candidate")] == ["A", "E"]
    assert [c["candidate_id"] for c in filter_candidates(cands, "confirmed")] == ["B"]
    assert [c["candidate_id"] for c in filter_candidates(cands, "held")] == ["D"]
    assert len(filter_candidates(cands, None)) == 5, "不过滤"
    # 缺失状态字段视为 candidate（投影契约）
    assert len(filter_candidates([{"candidate_id": "X", "fact": "f"}], "candidate")) == 1


# ============ T2: 排序规则 ============
def test_t2_sort_rules():
    cands = [_c("A", conf=0.3), _c("B", conf=0.9), _c("C", conf=0.6)]
    assert [c["candidate_id"] for c in sort_candidates(cands, "confidence")] == ["B", "C", "A"]
    t = [_c("A", created="2026-07-01T00:00:00"), _c("B", created="2026-08-01T00:00:00")]
    assert [c["candidate_id"] for c in sort_candidates(t, "created_at")] == ["B", "A"]
    # 非机械键 → 原样返回（禁止新排序维度）
    assert [c["candidate_id"] for c in sort_candidates(cands, "importance")] == ["A", "B", "C"]
    assert SORT_KEYS == ("confidence", "created_at"), "排序键固定"


# ============ T3: 空数据安全 ============
def test_t3_empty_safe():
    assert filter_candidates([], "candidate") == []
    assert sort_candidates([], "confidence") == []
    assert status_counts([]) == {"candidate": 0, "confirmed": 0,
                                 "rejected": 0, "held": 0}
    assert filter_candidates(None, None) == []


# ============ T4: 缺字段安全 ============
def test_t4_missing_fields():
    cands = [{"candidate_id": "X"}, {"candidate_id": "Y", "confidence": None}]
    rows = sort_candidates(cands, "confidence")  # 缺 confidence 按 0
    assert len(rows) == 2
    assert status_counts([{"candidate_id": "X"}])["candidate"] == 1, "缺状态按 candidate"


# ============ T5: 不产生额外判断字段 ============
def test_t5_no_extra_fields():
    cands = [_c("A")]
    for fn, arg in ((filter_candidates, "candidate"), (sort_candidates, "confidence")):
        rows = fn(cands, arg) if fn is filter_candidates else fn(cands, arg)
        for r in rows:
            for banned in ("score", "priority", "importance", "recommendation", "risk"):
                assert banned not in r, f"禁止新增判断字段: {banned}"
    counts = status_counts(cands)
    assert set(counts.keys()) == {"candidate", "confirmed", "rejected", "held"}


# ============ 附加: 面板集成（过滤控件渲染） ============
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_panel_queue_integration(monkeypatch, tmp_path, qapp):
    def fake_api(self, path, method="GET", body=None):
        if path.startswith("/candidates"):
            return {"count": 3, "candidates": [
                _c("A", "candidate", conf=0.9),
                _c("B", "confirmed", conf=0.5),
                _c("C", "candidate", conf=0.2)]}
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

    monkeypatch.setattr(GovernancePanel, "_api", fake_api)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    # 计数事实展示
    assert "candidate: 2" in panel.lbl_queue_counts.text()
    assert "confirmed: 1" in panel.lbl_queue_counts.text()
    # 过滤：candidate → 仅 A、C（列表 2 项）
    idx = panel.cmb_status.findData("candidate")
    panel.cmb_status.setCurrentIndex(idx)
    assert panel.list.count() == 2
    # 排序：confidence 降序 → A（0.9）在前
    sidx = panel.cmb_sort.findData("confidence")
    panel.cmb_sort.setCurrentIndex(sidx)
    item0 = panel.list.item(0)
    assert "A" in str(item0.data(0)) or item0.data(0x0100)["candidate_id"] == "A"
