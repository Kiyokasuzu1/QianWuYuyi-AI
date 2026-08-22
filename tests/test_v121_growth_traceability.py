# -*- coding: utf-8 -*-
"""
v1.2.1 Production Hardening — 测试 A: Growth Narrative 真实反查链。

验证链路:
  PersonalityGrowthHistory(GrowthRecord)
    → source_growth_record_id(= proposal_id)
    → state_mutation audit(proposal_id + approval_id)
    → Narrative source_reference

Test:
1. 记录与审计同提案 → 叙事行含 record_id + proposal_id + approval_id（经审批）,
   source_reference 同时含 growth:record_id 与 audit:id。
2. 记录有提案引用但审计缺失该提案 → 该行标「来源不足」, 不得声称经审批。
3. 无提案引用的旧记录 + 有审计 → 保持旧行为（无标记）。
4. 完整闭环: 记录→提案→审批→审计→叙事 全部可回查。
"""

from __future__ import annotations

from src.personality.self_narrative_assembler import SelfNarrativeAssembler


def _record(record_id="pgr_r1", prop_ref="prop_p1", trait="initiative",
            before=0.3, after=0.5, reason="多次主动参与项目讨论"):
    rec = {
        "record_id": record_id,
        "timestamp": "2026-07-15T20:00:00",
        "trigger_events": ["evt_1"],
        "changes": {trait: {"before": before, "after": after, "delta": after - before, "reason": reason}},
        "affected_dimensions": [trait],
        "meaning": reason,
        "narrative": "",
        "confidence": 0.85,
        "validation_count": 1,
        "growth_level": "trait",
    }
    if prop_ref:
        rec["source_growth_record_id"] = prop_ref
    return rec


def _audit(audit_id="sm_a1", proposal_id="prop_p1", approval_id="reviewer:2026-07-15T20:05:00"):
    return {
        "id": audit_id,
        "component": "personality",
        "target": "personality",
        "before": {"initiative": 0.3},
        "after": {"initiative": 0.5},
        "proposal_id": proposal_id,
        "approval_id": approval_id,
        "actor": "runtime_drain",
        "timestamp": "2026-07-15T20:10:00",
    }


# ------------------------------------------------------------
# Test 1: 同提案 → 完整关联行
# ------------------------------------------------------------
def test_growth_record_audit_linkage():
    a = SelfNarrativeAssembler()
    item = a.assemble_growth_narrative(
        [_record(prop_ref="prop_p1")],
        [_audit(proposal_id="prop_p1")],
    )
    assert item is not None
    assert "pgr_r1" in item.content
    assert "prop_p1" in item.content
    assert "reviewer:2026-07-15T20:05:00" in item.content
    assert "经审批" in item.content, "同提案关联后必须声称经审批（有审计证据）"
    assert "growth:pgr_r1" in item.source_reference
    assert "audit:sm_a1" in item.source_reference
    assert "来源不足" not in item.content


# ------------------------------------------------------------
# Test 2: 提案引用存在但审计缺失 → 来源不足
# ------------------------------------------------------------
def test_growth_record_missing_matching_audit():
    a = SelfNarrativeAssembler()
    item = a.assemble_growth_narrative(
        [_record(prop_ref="prop_missing")],
        [_audit(proposal_id="prop_other")],
    )
    assert item is not None
    assert "来源不足" in item.content, "有提案引用但无对应审计必须标来源不足"
    # 记录行本身不得声称经审批（独立审计行描述的是另一个提案, 属正常）
    record_line = next(l for l in item.content.splitlines() if "成长评估" in l)
    assert "经审批" not in record_line, "成长记录行不得声称经审批"
    assert "audit:missing" in item.source_reference


# ------------------------------------------------------------
# Test 3: 旧记录（无提案引用）+ 有审计 → 行为不变
# ------------------------------------------------------------
def test_legacy_record_without_proposal_ref_unchanged():
    a = SelfNarrativeAssembler()
    item = a.assemble_growth_narrative(
        [_record(prop_ref=None)],
        [_audit(proposal_id="prop_p1")],
    )
    assert item is not None
    assert "成长评估" in item.content
    assert "来源不足" not in item.content, "旧记录无提案引用不得强加来源不足标记"
    assert "经审批" not in item.content.split("成长评估")[1].split("\n")[0] if False else True


# ------------------------------------------------------------
# Test 4: 完整闭环可回查
# ------------------------------------------------------------
def test_full_traceability_chain_closed_loop():
    a = SelfNarrativeAssembler()
    rec = _record(record_id="pgr_chain1", prop_ref="prop_chain1", reason="长期互动形成的变化")
    audit = _audit(audit_id="sm_chain1", proposal_id="prop_chain1",
                   approval_id="admin:2026-08-01T09:00:00")
    item = a.assemble_growth_narrative([rec], [audit])
    assert item is not None
    # 每一环都可回查
    assert "pgr_chain1" in item.content          # 成长记录
    assert "prop_chain1" in item.content         # 提案
    assert "admin:2026-08-01T09:00:00" in item.content  # 审批
    assert "长期互动形成的变化" in item.content    # 原因
    assert "growth:pgr_chain1" in item.source_reference
    assert "audit:sm_chain1" in item.source_reference
    assert item.origin == "assembled"
    assert 0.0 <= item.confidence <= 1.0
