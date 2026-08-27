# -*- coding: utf-8 -*-
"""Governance Console Phase 2B.3 Subject View + Health 测试（T1-T5+，offscreen）。

覆盖（任务书）：
  T1 Subject View 数据映射正确（原文事实展示）
  T2 空数据状态显示正确
  T3 不存在字段不能假设（health_facts 缺字段不抛）
  T4 Health 只输出事实字段（数量/状态/时间，零判断）
  T5 源码扫描禁止（llm/openai/summary/interpretation/meaning/recommendation/risk）
  + 附加：Subject View 渲染禁止评价词

安全：dummy 数据；凭据隔离临时路径（沿用 2B.1 纪律）。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from tools.governance_credential_store import CredentialStore  # noqa: E402
from tools.governance_health import health_facts  # noqa: E402
from tools.governance_panel import GovernancePanel  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _fake_api_with(overrides=None):
    """类级 _api mock：返回构造数据（Subject View / Health 用）。"""
    overrides = overrides or {}

    def fake_api(self, path, method="GET", body=None):
        if path.startswith("/candidates"):
            return overrides.get("candidates", {"count": 0, "candidates": []})
        if path.startswith("/patterns/active"):
            return overrides.get("patterns_active", {"patterns": []})
        if path.startswith("/patterns"):
            return overrides.get("patterns", {"patterns": []})
        if path.startswith("/relationship-core"):
            return overrides.get("rc", {"facts": []})
        if path.startswith("/self-model-statements"):
            return overrides.get("sm", {"statements": []})
        if "proposals" in path:
            return overrides.get("proposals", {"proposals": []})
        if "growth" in path:
            return overrides.get("growth", {"data": {"pending": [], "approved": [], "applied": []}})
        if path.startswith("/audit-log"):
            return {"entries": []}
        if "personality" in path:
            return overrides.get("personality",
                                 {"available": False, "data": {"current": {}}})
        return {}
    return fake_api


def _make_panel(monkeypatch, tmp_path, qapp, overrides=None):
    monkeypatch.setattr(GovernancePanel, "_api", _fake_api_with(overrides))
    panel = GovernancePanel()
    panel._cred = CredentialStore(tmp_path / "cred.bin")
    panel._stored_token = ""
    return panel


# ============ T1: Subject View 数据映射正确 ============
def test_t1_subject_view_maps_facts(monkeypatch, tmp_path, qapp):
    data = {
        "sm": {"statements": [
            {"statement_id": "SM-001", "fact": "羽依喜欢在安静的环境里思考",
             "status": "confirmed", "created_at": "2026-08-01T00:00:00"},
            {"statement_id": "SM-002", "fact": "候选陈述",
             "status": "candidate", "created_at": "2026-08-02T00:00:00"},
        ]},
        "rc": {"facts": [{"fact_id": "RC-001", "agreements": ["羽依对清夏铃的惯用称呼是「清清」"],
                          "status": "confirmed", "confirmed_at": "2026-08-27T05:00:00"}]},
        "patterns_active": {"patterns": [{"pattern_id": "night_companionship",
                                          "title": "夜间陪伴", "status": "active"}]},
        "proposals": {"proposals": [{"proposal_id": "prop_g1", "proposal_type": "personality",
                                     "status": "approved",
                                     "created_at": "2026-08-27T08:00:00", "reason": ""}]},
        "growth": {"data": {"pending": [1], "approved": [1, 2], "applied": [1]}},
        "personality": {"available": True,
                        "data": {"current": {"warmth": 0.6, "playfulness": 0.5}}},
    }
    panel = _make_panel(monkeypatch, tmp_path, qapp, data)
    html = panel.tab_subject.toHtml()
    # ① 她是谁：confirmed 原文展示，candidate 不展示
    assert "羽依喜欢在安静的环境里思考" in html
    assert "候选陈述" not in html
    assert "warmth=0.6" in html
    # ② 她和用户：agreements 原文 + 时间 + 共同生活
    assert "「清清」" in html
    assert "夜间陪伴" in html
    # ③ 最近成长：proposal 事实字段
    assert "personality · approved · 2026-08-27T08:00:00" in html
    # ④ 治理状态观察：数量（approved 列表 2 条 → 计数 2）
    assert "approved: 2 · applied: 1 · pending: 1" in html


# ============ T2: 空数据状态显示正确 ============
def test_t2_empty_states(monkeypatch, tmp_path, qapp):
    panel = _make_panel(monkeypatch, tmp_path, qapp)  # 全部空
    html = panel.tab_subject.toHtml()
    assert "（无 confirmed 自我陈述）" in html
    assert "（无关系核心事实）" in html
    assert "（无 growth 提案" in html
    assert "未探测" in html, "连接未探测 ≠ 健康"


# ============ T3: 不存在字段不能假设 ============
def test_t3_missing_fields_not_assumed():
    # 全部缺字段：不抛异常，默认值占位
    facts = health_facts(None, None, None, None, None)
    assert any("pending 0" in f for f in facts)
    assert any("connection: —" in f for f in facts)
    # growth_counts 缺键
    facts2 = health_facts({"pending": 3})
    assert any("approved 0 · applied 0" in f for f in facts2)
    # last_error 缺字段
    facts3 = health_facts({}, last_error={"kind": "network"})
    assert any("last error: [network] " in f for f in facts3)


# ============ T4: Health 只输出事实字段 ============
def test_t4_health_facts_only():
    facts = health_facts({"pending": 1, "approved": 2, "applied": 0},
                         "CONNECTED", "MANUAL", "12:00:00",
                         {"kind": "network", "detail": "timeout"})
    assert len(facts) == 5
    assert facts[0] == "growth counts: pending 1 · approved 2 · applied 0"
    assert facts[1] == "connection: CONNECTED"
    assert facts[2] == "token source: MANUAL"
    assert facts[3] == "last refresh: 12:00:00"
    assert facts[4] == "last error: [network] timeout"
    # 输出不得包含判断词
    for f in facts:
        for banned in ("正常", "异常", "健康", "风险", "建议", "应该", "问题"):
            assert banned not in f, f"Health 输出禁止判断词: {banned} in {f}"


# ============ T5: 源码扫描禁止 ============
def test_t5_source_scan_banned_tokens():
    banned = ["llm", "openai", "summary", "interpretation", "meaning",
              "recommendation", "risk", "health judgement"]
    # governance_health.py
    src = open(os.path.join(_REPO, "tools", "governance_health.py"),
               encoding="utf-8").read()
    for token in banned:
        assert not re.search(token, src, re.IGNORECASE), \
            f"governance_health.py 禁止出现: {token}"
    # 面板 2B.3 新增函数段（_render_subject_view / _render_health）
    panel_src = open(os.path.join(_REPO, "tools", "governance_panel.py"),
                     encoding="utf-8").read()
    seg = panel_src[panel_src.index("def _render_subject_view"):
                    panel_src.index("def _render_health") + len("def _render_health")]
    for token in banned:
        assert not re.search(token, seg, re.IGNORECASE), \
            f"面板 2B.3 函数段禁止出现: {token}"


# ============ 附加: Subject View 渲染无评价词 ============
def test_subject_view_no_judgement_words(monkeypatch, tmp_path, qapp):
    data = {
        "rc": {"facts": [{"fact_id": "RC-001", "agreements": ["事实 A"],
                          "status": "confirmed", "confirmed_at": "2026-08-27T05:00:00"}]},
        "proposals": {"proposals": [{"proposal_id": "prop_g1", "proposal_type": "personality",
                                     "status": "applied",
                                     "created_at": "2026-08-27T08:00:00", "reason": ""}]},
    }
    panel = _make_panel(monkeypatch, tmp_path, qapp, data)
    html = panel.tab_subject.toHtml()
    for banned in ("关系健康", "亲密度", "成长成功", "成长失败", "变得更稳定",
                   "系统认为", "状态良好"):
        assert banned not in html, f"Subject View 禁止评价词: {banned}"
