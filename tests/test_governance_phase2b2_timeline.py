# -*- coding: utf-8 -*-
"""Governance Console Phase 2B.2 Timeline + Entity Projection 测试（T1-T9，offscreen）。

覆盖（任务书）：
  T1 Pattern lineage 投影（不读取显式 history 字段）
  T2 Self Model history 投影
  T3 Relationship audit-log 投影
  T4 Growth proposal 投影
  T5 时间排序（最近优先）
  T6 空状态
  T7 缺失数据 → "部分历史"
  T8 reason 原样展示
  T9 禁止自动解释：代码审查断言（无 LLM/summary/interpretation）

安全：全部 dummy 数据；凭据路径注入临时目录（沿用 2B.1 隔离纪律）。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_entity import (  # noqa: E402
    collect_timeline_events, family_root, to_event,
)
from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_panel import GovernancePanel, project_pattern_families, project_sm_families  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def qbox(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)


# ============ T1: Pattern lineage（无显式 history 字段） ============
def test_t1_pattern_lineage_projection():
    # 原始 pattern 行：无 history 字段（append-only 家族记录）
    rows = [
        {"pattern_id": "night_companionship", "status": "candidate",
         "created_at": "2026-07-16T21:00:00"},
        {"pattern_id": "night_companionship#1", "status": "confirmed",
         "reviewed_at": "2026-08-27T05:00:00", "reviewed_by": "admin",
         "review_note": "人工审核通过"},
    ]
    fams = project_pattern_families(rows)
    assert len(fams) == 1
    evs = to_event("pattern", fams[0])
    assert len(evs) == 2, "家族历史每条记录 → 一个事件"
    assert evs[0]["action"] == "pattern:candidate"
    assert evs[1]["action"] == "pattern:confirmed"
    assert evs[1]["object_id"] == "night_companionship", "展示 id = family root"
    assert evs[1]["status"] == "confirmed"
    assert evs[1]["ts"] == "2026-08-27T05:00:00", "ts 原字段"


# ============ T2: Self Model history 投影 ============
def test_t2_sm_history_projection():
    rows = [
        {"statement_id": "SM-001", "status": "candidate", "fact": "f1",
         "created_at": "2026-08-01T10:00:00"},
        {"statement_id": "SM-001#1", "status": "confirmed", "fact": "f1",
         "reviewed_at": "2026-08-27T06:00:00", "review_note": "确认"},
    ]
    fams = project_sm_families(rows)
    assert len(fams) == 1
    evs = to_event("self_model", fams[0])
    assert len(evs) == 2
    assert evs[0]["action"] == "sm:candidate"
    assert evs[1]["action"] == "sm:confirmed"
    assert evs[1]["object_id"] == "SM-001"
    assert evs[1]["reason"] == "确认"


# ============ T3: Relationship Core audit-log 投影 ============
def test_t3_rc_audit_projection():
    entry = {
        "ts": "2026-08-27T05:12:00",
        "reviewer": "admin",
        "action": "review:confirm",
        "object_type": "fact_candidate",
        "object_id": "RC-001",
        "before": {"status": "candidate"},
        "after": {"status": "confirmed", "target": "relationship_core"},
        "reason": "用户明确确认的称呼习惯",
    }
    evs = to_event("relationship_core", entry)
    assert len(evs) == 1
    assert evs[0]["ts"] == "2026-08-27T05:12:00"
    assert evs[0]["action"] == "review:confirm", "audit action 原样"
    assert evs[0]["object_id"] == "RC-001", "展示 id = family root"
    assert evs[0]["status"] == "confirmed", "status 来自 after 原字段"
    assert evs[0]["reason"] == "用户明确确认的称呼习惯", "reason 原样"


# ============ T4: Growth proposal 投影 ============
def test_t4_growth_proposal_projection():
    proposal = {
        "proposal_id": "prop_g1",
        "status": "approved",
        "proposal_type": "personality",
        "created_at": "2026-08-27T08:00:00",
        "reason": "长期经历表明更愿意主动陪伴",
    }
    evs = to_event("growth", proposal)
    assert len(evs) == 1
    assert evs[0]["action"] == "growth:approved"
    assert evs[0]["object_id"] == "prop_g1"
    assert evs[0]["ts"] == "2026-08-27T08:00:00"
    assert evs[0]["reason"] == "长期经历表明更愿意主动陪伴", "proposal reason 原样"
    # 不允许评价成长：事件中不得出现"成长成功/更稳定"等解释词
    assert "更稳定" not in str(evs) and "成功" not in evs[0]["action"]


# ============ T5: 时间排序（最近优先） ============
def test_t5_time_sorting():
    pat_rows = [
        {"pattern_id": "p1", "status": "candidate", "created_at": "2026-07-01T00:00:00"},
        {"pattern_id": "p1#1", "status": "confirmed", "reviewed_at": "2026-08-01T00:00:00"},
    ]
    audit = [{"ts": "2026-08-27T09:00:00", "action": "review:confirm",
              "object_type": "fact_candidate", "object_id": "RC-009",
              "after": {"status": "confirmed"}, "reason": ""}]
    props = [{"proposal_id": "g1", "status": "applied", "created_at": "2026-08-27T10:00:00",
              "reason": ""}]
    events = collect_timeline_events(project_pattern_families(pat_rows), [], audit, props)
    ts_list = [e["ts"] for e in events]
    assert ts_list == sorted(ts_list, reverse=True), "时间降序（最近优先）"
    assert events[0]["action"] == "growth:applied", "最新事件在前"
    assert events[-1]["action"] == "pattern:candidate"


# ============ T6: 空状态 ============
def test_t6_empty_state(monkeypatch, tmp_path, qapp, qbox):
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
            return {"proposals": [], "pending": 0, "approved": 0, "applied": 0}
        if path.startswith("/audit-log"):
            return {"entries": []}
        if "personality" in path:
            return {"available": False, "data": {"current": {}}}
        return {}

    monkeypatch.setattr(GovernancePanel, "_api", fake_api)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    assert "（暂无治理事件）" in panel.tab_timeline_view.toHtml(), "空状态必须明确"
    # 纯函数层
    assert collect_timeline_events([], [], [], []) == []


# ============ T7: 缺失数据 → "部分历史" ============
def test_t7_partial_history_label(monkeypatch, tmp_path, qapp, qbox):
    def fake_api(self, path, method="GET", body=None):
        if path.startswith("/candidates"):
            return {"count": 0, "candidates": []}
        if path.startswith("/patterns"):
            return {"patterns": [
                {"pattern_id": "night_companionship", "status": "confirmed",
                 "reviewed_at": "2026-08-27T05:00:00", "review_note": "审核通过"}]}
        if path.startswith("/relationship-core"):
            return {"facts": []}
        if path.startswith("/self-model-statements"):
            return {"statements": []}
        if "proposals" in path or "growth" in path:
            return {"proposals": [], "pending": 0, "approved": 0, "applied": 0}
        if path.startswith("/audit-log"):
            return {"entries": []}  # 该 pattern 在 audit-log 中无对应记录（跨 sink 缺失）
        if "personality" in path:
            return {"available": False, "data": {"current": {}}}
        return {}

    monkeypatch.setattr(GovernancePanel, "_api", fake_api)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    html = panel.tab_timeline_view.toHtml()
    assert "night_companionship" in html, "事件应展示"
    assert "部分历史" in html, "跨 sink 缺失必须标注部分历史，禁止补全"


# ============ T8: reason 原样展示 ============
def test_t8_reason_preserved():
    entry = {"ts": "2026-08-27T05:00:00", "action": "review:confirm",
             "object_type": "fact_candidate", "object_id": "RC-001",
             "after": {"status": "confirmed"},
             "reason": "用户亲口确认「清清」称呼，且 30 次命中" * 3}
    evs = to_event("relationship_core", entry)
    assert evs[0]["reason"] == entry["reason"], "reason 必须原样，不做任何改写"


# ============ T9: 代码审查断言 —— 无 LLM / 无总结 / 无解释 ============
def test_t9_no_llm_no_interpretation():
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "tools", "governance_entity.py"), encoding="utf-8").read()
    banned = ["llm", "openai", "summary", "interpretation", "meaning",
              "系统认为", "因为用户", "代表羽依成长", "更加稳定"]
    for token in banned:
        assert not re.search(token, src, re.IGNORECASE), f"governance_entity.py 禁止出现: {token}"
    # 事件结构只允许五字段（不允许携带解释性附加字段）
    for kind, record in [("growth", {"proposal_id": "x", "status": "pending",
                                     "created_at": "2026-08-27T00:00:00", "reason": ""})]:
        ev = to_event(kind, record)[0]
        assert set(ev.keys()) == {"ts", "action", "object_id", "status", "reason"}


# ============ 补充: family_root 纯字符串 ============
def test_family_root_util():
    assert family_root("RC-001#3") == "RC-001"
    assert family_root("RC-001") == "RC-001"
    assert family_root("") == ""
