# -*- coding: utf-8 -*-
"""Governance Console Phase 2B.5 Snapshot 只读导出测试（T1-T8，offscreen）。

覆盖（任务书）：
  T1 JSON 结构符合契约（顶层 created_at/trigger/data）
  T2 created_at 和 trigger 存在（trigger=manual_export 固定）
  T3 data 五块结构存在
  T4 Timeline 字段来自 Phase 2B.2（ts/action/object_id/status/reason）
  T5 Explain context 来自固定映射（affected_fields 与 ACTION_FIELD_MAP 一致）
  T6 源码扫描禁止 restore/rollback/write_back/apply_snapshot/sync
  T7 无 LLM/summary/interpretation/meaning/recommendation（源码 + JSON 内容）
  T8 缺字段情况下安全占位

安全：dummy 数据；凭据隔离临时路径。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402

from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_explain import ACTION_FIELD_MAP, OPERATIONS_BY_KIND  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402
from tools.governance_snapshot import collect, export, serialize  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _fake_api(self, path, method="GET", body=None):
    if path.startswith("/candidates"):
        return {"count": 1, "candidates": [
            {"candidate_id": "RC-009", "fact": "测试事实", "current_status": "candidate"}]}
    if path.startswith("/patterns/active"):
        return {"patterns": [{"pattern_id": "night_companionship", "title": "夜间陪伴",
                              "status": "active"}]}
    if path.startswith("/patterns"):
        return {"patterns": [
            {"pattern_id": "night_companionship#1", "status": "confirmed",
             "reviewed_at": "2026-08-27T05:00:00", "review_note": "审核"}]}
    if path.startswith("/relationship-core"):
        return {"facts": [{"fact_id": "RC-001", "agreements": ["称呼清清"],
                           "confirmed_at": "2026-08-27T05:00:00"}]}
    if path.startswith("/self-model-statements"):
        return {"statements": [{"statement_id": "SM-001", "fact": "羽依喜欢安静",
                                "status": "confirmed", "created_at": "2026-08-01T00:00:00"}]}
    if "proposals" in path:
        return {"proposals": [{"proposal_id": "prop_g1", "status": "approved",
                               "proposal_type": "personality",
                               "created_at": "2026-08-27T08:00:00", "reason": "r"}]}
    if "growth" in path:
        return {"data": {"pending": [], "approved": [1], "applied": []}}
    if path.startswith("/audit-log"):
        return {"entries": []}
    if "personality" in path:
        return {"available": False, "data": {"current": {}}}
    return {}


def _make_panel(monkeypatch, tmp_path, qapp):
    monkeypatch.setattr(GovernancePanel, "_api", _fake_api)
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: 0)
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    return panel


# ============ T1: JSON 结构符合契约 ============
def test_t1_json_structure():
    snap = collect({"status": "CONNECTED"}, {"a": 1}, [], [], {})
    text = serialize(snap)
    data = json.loads(text)
    assert set(data.keys()) == {"created_at", "trigger", "data"}


# ============ T2: created_at 与 trigger ============
def test_t2_created_at_and_trigger():
    snap = collect()
    assert snap["created_at"], "created_at 必须存在"
    assert snap["trigger"] == "manual_export", "trigger 固定字符串"


# ============ T3: data 五块结构 ============
def test_t3_five_blocks():
    snap = collect()
    blocks = snap["data"]
    assert set(blocks.keys()) == {"connection", "subjects", "timeline",
                                  "health", "explain_context"}


# ============ T4: Timeline 字段来自 Phase 2B.2 ============
def test_t4_timeline_fields():
    events = [{"ts": "2026-08-27T05:00:00", "action": "pattern:confirmed",
               "object_id": "night_companionship", "status": "confirmed",
               "reason": "审核", "kind": "pattern"}]
    snap = collect(timeline=events)
    ev = snap["data"]["timeline"][0]
    for key in ("ts", "action", "object_id", "status", "reason"):
        assert key in ev, f"Timeline 事件必须含字段: {key}"


# ============ T5: Explain context 来自固定映射 ============
def test_t5_explain_context_fixed_mapping():
    # 与固定映射表一致：candidate 可用操作的影响字段展开（confirm/reject/hold → status，modify → fact）
    expected = sorted({f for op in OPERATIONS_BY_KIND["candidate"]
                       for f in ACTION_FIELD_MAP.get(op, [])})
    ctx = {"object_id": "RC-009", "kind": "candidate",
           "current_status": "candidate",
           "affected_fields": list(expected)}
    snap = collect(explain_context=ctx)
    assert snap["data"]["explain_context"]["affected_fields"] == expected


# ============ T6: 源码扫描禁止恢复路径 ============
def test_t6_no_restore_paths():
    src = open(os.path.join(_REPO, "tools", "governance_snapshot.py"),
               encoding="utf-8").read()
    for token in ("restore", "rollback", "write_back", "apply_snapshot", "sync"):
        assert token not in src.lower(), f"governance_snapshot.py 禁止出现: {token}"
    # 模块只允许 collect/serialize/export 三个公开函数
    for name in ("restore", "recover", "apply", "sync"):
        assert f"def {name}" not in src, f"禁止定义函数: {name}"


# ============ T7: 无 LLM / summary / interpretation / meaning / recommendation ============
def test_t7_no_llm_and_no_summary_tokens():
    src = open(os.path.join(_REPO, "tools", "governance_snapshot.py"),
               encoding="utf-8").read()
    for token in ("llm", "openai", "summary", "interpretation", "meaning",
                  "recommendation", "suggestion", "prediction", "analysis"):
        assert token not in src.lower(), f"governance_snapshot.py 禁止出现: {token}"
    # JSON 内容同样无痕迹（键名来自真实数据契约）
    snap = collect({"status": "CONNECTED"},
                   {"self_model_confirmed": ["a"], "growth_counts": {"pending": 1}},
                   [{"ts": "t", "action": "a", "object_id": "o", "status": "s", "reason": "r"}],
                   ["growth counts: pending 1"], {"object_id": "o", "kind": "candidate"})
    text = serialize(snap).lower()
    for token in ("llm", "summary", "interpretation", "meaning",
                  "recommendation", "suggestion", "risk", "prediction", "analysis"):
        assert token not in text, f"Snapshot JSON 禁止出现: {token}"


# ============ T8: 缺字段情况下安全占位 ============
def test_t8_missing_fields_safe():
    snap = collect(None, None, None, None, None)
    blocks = snap["data"]
    assert blocks["connection"] == {}
    assert blocks["subjects"] == {}
    assert blocks["timeline"] == []
    assert blocks["health"] == []
    assert blocks["explain_context"] == {}


# ============ 附加: 面板导出流程（用户主动保存） ============
def test_panel_export_flow(monkeypatch, tmp_path, qapp):
    out = str(tmp_path / "snap.json")

    def fake_save(parent, title, default, filt):
        return out, "JSON (*.json)"

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(fake_save))
    panel = _make_panel(monkeypatch, tmp_path, qapp)
    panel._export_snapshot()
    assert os.path.exists(out), "导出文件必须写入用户指定路径"
    data = json.loads(open(out, encoding="utf-8").read())
    assert data["trigger"] == "manual_export"
    assert set(data["data"].keys()) == {"connection", "subjects", "timeline",
                                        "health", "explain_context"}
    # 面板导出流程中 explain_context 来自当前选中对象
    assert data["data"]["subjects"]["growth_counts"] == {"pending": 0, "approved": 1, "applied": 0}
