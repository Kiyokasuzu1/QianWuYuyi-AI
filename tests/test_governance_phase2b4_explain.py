# -*- coding: utf-8 -*-
"""Governance Console Phase 2B.4 Explain Mode + Impact Preview 测试（T1-T6）。

覆盖（任务书）：
  T1 Explain 数据映射正确
  T2 Impact Preview 字段映射正确（固定表）
  T3 不存在自动推理字段（输出无判断词 + 人工标注区）
  T4 禁止词扫描（中文禁止列表）
  T5 无 LLM / summary / interpretation / meaning 痕迹
  T6 缺字段情况下显示占位，不假设

安全：dummy 数据；凭据隔离临时路径。
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.governance_explain import (  # noqa: E402
    ACTION_FIELD_MAP, DRAIN_RULES, OPERATIONS_BY_KIND, STATUS_AFTER_MAP,
    build_explain,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============ T1: Explain 数据映射正确 ============
def test_t1_explain_mapping():
    rec = {"candidate_id": "RC-009", "fact": "羽依和清清每晚互道晚安",
           "status": "candidate"}
    lines = build_explain(rec, "candidate", "12:30:00")
    assert any("对象: RC-009" in l for l in lines)
    assert any("当前状态: candidate" in l for l in lines)
    assert any("数据截至: 12:30:00" in l for l in lines)
    assert any("羽依和清清每晚互道晚安" in l for l in lines)


def test_t1b_pattern_and_growth_mapping():
    pat = {"pattern_id": "night_companionship#3", "status": "confirmed",
           "title": "夜间陪伴"}
    lines = build_explain(pat, "pattern", "12:31:00")
    assert any("对象: night_companionship#3" in l for l in lines)
    assert any("当前状态: confirmed" in l for l in lines)
    g = {"proposal_id": "prop_g1", "proposal_type": "personality",
         "status": "pending", "created_at": "2026-08-27T08:00:00"}
    lines2 = build_explain(g, "growth", "12:32:00")
    assert any("对象: prop_g1" in l for l in lines2)
    assert any("growth_approve" in l for l in lines2)
    assert any("drain: YES" in l for l in lines2)


# ============ T2: Impact Preview 字段映射正确 ============
def test_t2_impact_preview_mapping():
    assert ACTION_FIELD_MAP["confirm"] == ["fact_candidate.status"]
    assert STATUS_AFTER_MAP["confirm"] == "confirmed"
    assert DRAIN_RULES["confirm"] == "NO"
    assert ACTION_FIELD_MAP["reject"] == ["fact_candidate.status"]
    assert STATUS_AFTER_MAP["reject"] == "rejected"
    assert ACTION_FIELD_MAP["modify"] == ["fact_candidate.fact"]
    assert ACTION_FIELD_MAP["pattern_supersede"] == ["shared_life_pattern.status"]
    assert STATUS_AFTER_MAP["pattern_supersede"] == "superseded"
    assert STATUS_AFTER_MAP["pattern_archive"] == "archived"
    assert ACTION_FIELD_MAP["growth_approve"] == ["proposal.status"]
    assert STATUS_AFTER_MAP["growth_approve"] == "approved"
    assert DRAIN_RULES["growth_approve"] == "YES", "仅 growth_approve 需要 drain"
    assert DRAIN_RULES["growth_reject"] == "NO"
    # 可用操作按类型固定
    assert OPERATIONS_BY_KIND["candidate"] == ["confirm", "reject", "hold", "modify"]
    assert "growth_approve" in OPERATIONS_BY_KIND["growth"]


# ============ T3: 不存在自动推理字段 ============
def test_t3_no_inference():
    rec = {"candidate_id": "RC-001", "fact": "f", "status": "candidate"}
    lines = build_explain(rec, "candidate", "12:00:00")
    joined = "\n".join(lines)
    for w in ("建议", "系统认为", "应该", "风险", "未来会", "预计",
              "这个决定会改善", "有利于成长"):
        assert w not in joined, f"Explain 输出禁止推理词: {w}"
    assert "本页面仅展示已存在字段" in joined, "人工标注区必须存在"
    assert "无法证明未展示的数据状态" in joined


# ============ T4: 禁止词扫描（源码） ============
def test_t4_banned_words_scan():
    src = open(os.path.join(_REPO, "tools", "governance_explain.py"),
               encoding="utf-8").read()
    for w in ("系统认为", "应该", "建议", "风险", "问题", "异常",
              "优化", "未来会", "预计", "成长更好", "关系更稳定", "人格变化"):
        assert w not in src, f"governance_explain.py 禁止出现: {w}"


# ============ T5: 无 LLM / summary / interpretation / meaning ============
def test_t5_no_llm_trace():
    src = open(os.path.join(_REPO, "tools", "governance_explain.py"),
               encoding="utf-8").read()
    for t in ("llm", "openai", "interpretation", "meaning"):
        assert not re.search(t, src, re.IGNORECASE), \
            f"governance_explain.py 禁止出现: {t}"
    # "summary" 仅允许作为 pattern 已实证字段名引用（.get("summary")），
    # 其余任何出现（AI 总结含义）均禁止
    assert not re.search(r'summary(?!")', src, re.IGNORECASE), \
        '"summary" 仅允许字段名引用 .get("summary")'
    # 输出内容同样无痕迹
    lines = build_explain({"candidate_id": "RC-001", "fact": "f", "status": "candidate"},
                          "candidate", "12:00:00")
    for t in ("llm", "summary", "interpretation", "meaning"):
        assert t not in "\n".join(lines).lower()


# ============ T6: 缺字段情况下显示占位，不假设 ============
def test_t6_missing_fields_placeholder():
    lines = build_explain({}, "candidate", "12:00:00")
    assert lines, "缺字段不应抛异常"
    assert any("对象: —" in l for l in lines)
    assert any("当前状态: —" in l for l in lines)
    assert any("数据截至: 12:00:00" in l for l in lines)
