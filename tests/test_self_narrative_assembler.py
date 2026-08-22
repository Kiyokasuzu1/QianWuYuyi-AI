# -*- coding: utf-8 -*-
"""
v1.2 Self Understanding — Phase 1 验收测试: SelfNarrativeAssembler。

覆盖（任务书 Test 1-7）:
1. GrowthHistory → Growth Narrative（含 record/trait/from→to/proposal_id/source_reference）。
2. 禁止编造: 空输入不产生虚构经历/默认人格故事。
3. 无 LLM 依赖: assembler 源码不得出现 LLMClient / PromptBuilder。
4. 幂等: 相同 source_reference 两次组装 → narrative_id 一致。
5. 版本保存: append 快照保留旧版本, 最新在前。
6. 身份隔离: 叙事不修改/不生成 IdentityCore 断言（只允许观察性表述）。
7. 来源断裂: 有成长记录、无审计证据 → 标记「来源不足」, 不得声称「已审批」。

隔离: 全部使用纯 dict 工厂数据, 不触碰任何真实状态文件/单例。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.personality.self_narrative_assembler import (
    NARRATIVE_TYPE_GROWTH,
    NARRATIVE_TYPE_IDENTITY,
    NARRATIVE_TYPE_RECENT,
    ORIGIN_ASSEMBLED,
    NarrativeItem,
    SelfNarrativeAssembler,
)
from src.personality.self_narrative_history import (
    NarrativeSnapshot,
    SelfNarrativeHistory,
)


def _growth_record(
    record_id="pgr_test1",
    trait="openness",
    before=0.5,
    after=0.58,
    reason="长期互动中表现出更强的探索意愿",
    confidence=0.8,
    timestamp="2026-08-01T10:00:00",
):
    return {
        "record_id": record_id,
        "timestamp": timestamp,
        "trigger_events": ["evt_1"],
        "changes": {trait: {"before": before, "after": after, "delta": after - before, "reason": reason}},
        "affected_dimensions": [trait],
        "meaning": reason,
        "narrative": "",
        "confidence": confidence,
        "validation_count": 1,
        "growth_level": "trait",
    }


def _audit_entry(audit_id="sm_test1", proposal_id="prop_test1", timestamp="2026-08-01T10:05:00"):
    return {
        "id": audit_id,
        "component": "personality",
        "target": "personality",
        "before": {"openness": 0.5},
        "after": {"openness": 0.58},
        "proposal_id": proposal_id,
        "approval_id": "reviewer:2026-08-01T10:04:00",
        "actor": "runtime_drain",
        "timestamp": timestamp,
    }


# ------------------------------------------------------------
# Test 1: GrowthHistory → Growth Narrative
# ------------------------------------------------------------
def test_growth_history_to_growth_narrative():
    a = SelfNarrativeAssembler()
    rec = _growth_record()
    audit = _audit_entry()
    item = a.assemble_growth_narrative([rec], [audit])
    assert item is not None
    assert item.narrative_type == NARRATIVE_TYPE_GROWTH
    assert item.origin == ORIGIN_ASSEMBLED
    assert "pgr_test1" in item.content, "必须保留来源 record_id"
    assert "openness" in item.content, "必须保留 trait"
    assert "0.500" in item.content and "0.580" in item.content, "必须保留 from/to"
    assert "prop_test1" in item.content, "必须保留 proposal_id（来自审计证据）"
    assert "growth:pgr_test1" in item.source_reference
    assert "audit:sm_test1" in item.source_reference
    assert 0.0 <= item.confidence <= 1.0
    assert item.timestamp


# ------------------------------------------------------------
# Test 2: 禁止编造（空输入）
# ------------------------------------------------------------
def test_no_fabrication_on_empty_input():
    a = SelfNarrativeAssembler()
    assert a.assemble_recent_narrative([], [], []) is None
    assert a.assemble_growth_narrative([], []) is None
    assert a.assemble_identity_narrative([], None, None) is None
    snap = a.assemble_snapshot()
    assert snap.narrative_text == "正在积累中", "空输入只能给出'正在积累中', 不得编故事"
    assert snap.major_changes == []
    assert snap.changed_traits == {}


# ------------------------------------------------------------
# Test 3: 无 LLM 依赖
# ------------------------------------------------------------
def test_no_llm_dependency():
    import ast

    src = Path(__file__).parent.parent / "src" / "personality" / "self_narrative_assembler.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for banned in ("src.response.llm", "src.response.prompt_builder"):
        assert banned not in imported, f"assembler 不得 import {banned}"
    # 依赖只允许: 标准库 + 叙事历史模块
    for mod in sorted(imported):
        assert mod == "src.personality.self_narrative_history" or "." not in mod, (
            f"assembler 引入未授权依赖: {mod}"
        )


# ------------------------------------------------------------
# Test 4: 幂等（相同来源 → 相同 narrative_id）
# ------------------------------------------------------------
def test_idempotent_narrative_id():
    a = SelfNarrativeAssembler()
    rec = _growth_record()
    audit = _audit_entry()
    first = a.assemble_growth_narrative([rec], [audit])
    second = a.assemble_growth_narrative([rec], [audit])
    assert first.narrative_id == second.narrative_id
    # 相同来源组合即使顺序不同也同 id（source_reference 内部排序）
    rec2 = _growth_record(record_id="pgr_test2", trait="curiosity", before=0.4, after=0.45)
    audit2 = _audit_entry(audit_id="sm_test2", proposal_id="prop_test2")
    third = a.assemble_growth_narrative([rec2, rec], [audit, audit2])
    assert first.narrative_id != third.narrative_id, "不同来源组合应产生不同 id"


# ------------------------------------------------------------
# Test 5: 版本保存（append-only, 旧版本保留, 最新在前）
# ------------------------------------------------------------
def test_snapshot_versions_preserved(tmp_path):
    h = SelfNarrativeHistory(max_snapshots=10)
    s1 = NarrativeSnapshot(major_changes=["v1 change"], changed_traits={"openness": 0.58})
    s1.version = 1
    h.add_snapshot(s1)

    s2 = NarrativeSnapshot(major_changes=["v1 change", "v2 change"], changed_traits={"openness": 0.62})
    s2.version = 2
    h.append_snapshot(s2)

    assert len(h.snapshots) == 2, "有 diff 时应新增版本"
    assert h.get_recent(5)[0].version == 2, "get_recent 最新在前"
    assert h.get_recent(5)[1].version == 1, "旧版本必须保留"

    # 相同内容 append → 不新增版本（幂等）
    s3 = NarrativeSnapshot(major_changes=["v1 change", "v2 change"], changed_traits={"openness": 0.62})
    h.append_snapshot(s3)
    assert len(h.snapshots) == 2, "无显著差异不得重复追加"

    # 持久化往返
    p = str(tmp_path / "narrative_history.json")
    h.save(p)
    h2 = SelfNarrativeHistory.load(p)
    assert len(h2.get_recent(5)) == 2
    # 损坏文件 fail-soft
    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    h3 = SelfNarrativeHistory.load(str(bad))
    assert h3.snapshots == [], "损坏文件必须降级为空历史而非抛异常"


# ------------------------------------------------------------
# Test 6: 身份隔离（叙事不得生成身份断言）
# ------------------------------------------------------------
def test_identity_isolation_no_identity_claims():
    a = SelfNarrativeAssembler()
    rec = _growth_record(trait="extraversion", before=0.3, after=0.4, reason="互动增多")
    audit = _audit_entry(proposal_id="prop_iden1")
    growth = a.assemble_growth_narrative([rec], [audit])
    recent = a.assemble_recent_narrative(
        memories=[{"id": "m1", "content": "用户主动发起更多聊天", "timestamp": "2026-08-02T09:00:00"}],
        experiences=[], reflections=[],
    )
    iden = a.assemble_identity_narrative([], recent, growth)
    assert iden is not None
    # 禁止身份结论句；只允许"记录显示"式观察表述
    assert "是外向的" not in iden.content
    assert "是内向的" not in iden.content
    assert "记录显示" in iden.content
    # 组装器没有任何写入口（无 save/write/apply 方法）
    for banned in ("save", "write", "apply", "update"):
        assert not hasattr(a, banned), f"assembler 不得拥有 {banned} 写入口"


# ------------------------------------------------------------
# Test 7: 来源断裂（有成长记录、无审计 → 标记来源不足）
# ------------------------------------------------------------
def test_missing_audit_marks_insufficient_source():
    a = SelfNarrativeAssembler()
    rec = _growth_record()
    item = a.assemble_growth_narrative([rec], None)
    assert item is not None
    assert "来源不足" in item.content, "审计缺失必须标记来源不足"
    assert "已审批" not in item.content and "经审批" not in item.content, "不得声称已审批"
    assert "audit:missing" in item.source_reference
    # 置信度应打折（低于有审计时的置信度）
    with_audit = a.assemble_growth_narrative([rec], [_audit_entry()])
    assert item.confidence < with_audit.confidence


# ------------------------------------------------------------
# 补充: recent 叙事的来源引用与类型
# ------------------------------------------------------------
def test_recent_narrative_sources():
    a = SelfNarrativeAssembler()
    item = a.assemble_recent_narrative(
        memories=[{"id": "m1", "content": "用户喜欢科幻", "timestamp": "2026-08-01T10:00:00"}],
        experiences=[{"id": "e1", "content": "一起讨论了新书", "timestamp": "2026-08-02T10:00:00"}],
        reflections=[],
    )
    assert item is not None
    assert item.narrative_type == NARRATIVE_TYPE_RECENT
    assert "memory:m1" in item.source_reference
    assert "experience:e1" in item.source_reference
