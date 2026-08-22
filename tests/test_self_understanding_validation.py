# -*- coding: utf-8 -*-
"""
v1.2 Self Understanding — Phase 3 验证测试（Self Understanding Validation）。

验证命题:
    「羽依能够基于真实历史理解自己的成长」,
    而不是「Prompt 里增加了一些叙事文本」。

验证方式（LLM-free, 确定性）:
    单元测试无法调用真实模型, 因此验证「交付给模型的上下文层」——
    即: 上下文中必须包含可追溯的真实材料(来源引用/时间/数值/理由),
    且禁止包含编造材料。模型回答若能引用, 必然来自这些材料。

Test 1-7:
1. 成长原因解释: 主动性 0.3→0.5 的变化叙事含真实变化/来源/时间, 无"人格设定"话术。
2. 经历连续性: 事件移除后叙事/上下文相应降级(证明非静态 Persona)。
3. 身份边界: 叙事不含身份断言(名字/创建来源), IdentityCore 在 prompt 中恒在前。
4. 真实性: 空数据 → 「正在积累中」, 不注入任何叙事区。
5. 溯源完整性: 每条 NarrativeItem 的 source_reference 可回查原始记录。
6. 治理隔离: 全链路组装前后, 人格/情绪/关系/自我模型/审计零变化。
7. 长期运行: 相同数据重复 tick 不重复 append; 快照数量有界。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.personality.self_narrative_assembler import SelfNarrativeAssembler
from src.personality.self_narrative_history import (
    NarrativeSnapshot,
    SelfNarrativeHistory,
)
from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost


def _growth_record(
    record_id="pgr_initiative_1",
    trait="initiative",
    before=0.3,
    after=0.5,
    reason="长期互动中主动发起话题的行为增多（来源: proposal prop_init_1）",
    confidence=0.85,
    timestamp="2026-07-15T20:00:00",
):
    return {
        "record_id": record_id,
        "timestamp": timestamp,
        "trigger_events": ["evt_init_1"],
        "changes": {trait: {"before": before, "after": after, "delta": after - before, "reason": reason}},
        "affected_dimensions": [trait],
        "meaning": reason,
        "narrative": "",
        "confidence": confidence,
        "validation_count": 1,
        "growth_level": "trait",
    }


def _audit_entry(proposal_id="prop_init_1", timestamp="2026-07-15T20:10:00"):
    return {
        "id": "sm_init_1",
        "component": "personality",
        "target": "personality",
        "before": {"initiative": 0.3},
        "after": {"initiative": 0.5},
        "proposal_id": proposal_id,
        "approval_id": "reviewer:2026-07-15T20:05:00",
        "actor": "runtime_drain",
        "timestamp": timestamp,
    }


def _render_context(assembler, narrative_path, **inputs):
    """组装快照 → 写叙事文件 → Provider 渲染 context（复现生产注入链）。"""
    snapshot = assembler.assemble_snapshot(**inputs)
    h = SelfNarrativeHistory()
    h.add_snapshot(snapshot)
    h.save(narrative_path)

    from src.personality.self_model_context_provider import SelfModelContextProvider

    class _Store:
        def get_active_self_model(self):
            return None

    return SelfModelContextProvider(_Store(), narrative_history_path=narrative_path).get_combined_context()


# ------------------------------------------------------------
# Test 1: 成长原因解释
# ------------------------------------------------------------
def test_growth_reason_explanation(tmp_path):
    a = SelfNarrativeAssembler()
    ctx = _render_context(
        a,
        str(tmp_path / "n.json"),
        growth_records=[_growth_record()],
        audit_entries=[_audit_entry()],
    )
    # 真实变化数值 + 时间 + 理由 + 来源 id 必须全部进入上下文
    assert "initiative" in ctx
    assert "0.300" in ctx and "0.500" in ctx
    assert "2026-07-15" in ctx
    assert "prop_init_1" in ctx
    assert "pgr_initiative_1" in ctx
    # 禁止静态设定话术
    for banned in ("因为我是这样设计的", "我的人格设定如此", "设定为主动"):
        assert banned not in ctx


# ------------------------------------------------------------
# Test 2: 经历连续性（事件移除 → 能力下降）
# ------------------------------------------------------------
def test_experience_continuity_degrades_when_source_removed(tmp_path):
    a = SelfNarrativeAssembler()
    exp = {"id": "exp_proj_1", "content": "我们一起启动了羽依项目", "timestamp": "2026-01-10T09:00:00"}
    with_exp = _render_context(
        a, str(tmp_path / "a.json"),
        experiences=[exp],
        memories=[],
        reflections=[],
    )
    assert "羽依项目" in with_exp, "有经历时应出现在上下文中"
    without_exp = _render_context(
        a, str(tmp_path / "b.json"),
        experiences=[],
        memories=[],
        reflections=[],
    )
    assert "羽依项目" not in without_exp, "删除经历后上下文必须降级（证明非静态 Persona）"
    assert "Growth Narrative" not in without_exp, "无材料时不得注入叙事区"


# ------------------------------------------------------------
# Test 3: 身份边界
# ------------------------------------------------------------
def test_identity_boundary():
    a = SelfNarrativeAssembler()
    rec = _growth_record()
    growth = a.assemble_growth_narrative([rec], [_audit_entry()])
    iden = a.assemble_identity_narrative([], None, growth)
    assert iden is not None
    # 叙事不得包含身份断言（名字 / 创建来源 / "我是谁"结论）
    for banned in ("我的名字", "浅雾羽依", "我诞生于", "我是被创造"):
        assert banned not in iden.content
    # 快照 core_identity 字段独立于叙事文本（身份字段与叙事层分离）
    snap = a.assemble_snapshot(
        growth_records=[rec], audit_entries=[_audit_entry()],
    )
    assert snap.core_identity == "浅雾羽依"
    assert "浅雾羽依" not in snap.narrative_text
    # Prompt 中 Identity 块恒在 Narrative 之前
    from src.response.prompt_builder import PromptBuilder

    msgs = PromptBuilder().build_messages(
        user_message="hi",
        identity_context="IDCORE_MARKER",
        self_model_context="## Growth Narrative\n\n### Growth History\n\nNARRATIVE_MARKER",
    )
    system_text = "\n".join(str(m.get("content", "")) for m in msgs if m.get("role") == "system")
    assert system_text.index("IDCORE_MARKER") < system_text.index("NARRATIVE_MARKER")


# ------------------------------------------------------------
# Test 4: 真实性（空数据）
# ------------------------------------------------------------
def test_authenticity_empty_data(tmp_path):
    a = SelfNarrativeAssembler()
    snap = a.assemble_snapshot(
        experiences=[], growth_records=[], audit_entries=[],
        memories=[], reflections=[], growth_narratives=[],
    )
    assert snap.narrative_text == "正在积累中"
    assert snap.major_changes == []
    assert snap.changed_traits == {}
    ctx = _render_context(a, str(tmp_path / "c.json"), experiences=[], growth_records=[], audit_entries=[])
    assert "Growth Narrative" not in ctx, "空数据不得注入叙事区（宁可无叙事, 不编故事）"


# ------------------------------------------------------------
# Test 5: 溯源完整性
# ------------------------------------------------------------
def test_traceability_completeness():
    a = SelfNarrativeAssembler()
    rec = _growth_record()
    audit = _audit_entry()
    mem = {"id": "m_sci", "content": "用户喜欢科幻", "timestamp": "2026-08-01T10:00:00"}
    recent = a.assemble_recent_narrative(memories=[mem], experiences=[], reflections=[])
    growth = a.assemble_growth_narrative([rec], [audit])
    iden = a.assemble_identity_narrative([], recent, growth)
    items = [i for i in (recent, growth, iden) if i is not None]
    assert len(items) == 3
    # 建立原始记录注册表（模拟生产中的数据溯源）
    registry = {
        "memory:m_sci": mem,
        "growth:pgr_initiative_1": rec,
        "audit:sm_init_1": audit,
    }
    for item in items:
        for ref in item.source_reference:
            assert ref in registry, f"引用 {ref} 无法回查原始数据"
        assert item.origin == "assembled"
        assert 0.0 <= item.confidence <= 1.0


# ------------------------------------------------------------
# Test 6: 治理隔离（全链路前后状态零变化）
# ------------------------------------------------------------
def test_governance_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    narrative_path = str(tmp_path / "narrative_history.json")
    inputs = {
        "memories": [{"id": "m1", "content": "喜欢咖啡", "timestamp": "t"}],
        "experiences": [],
        "reflections": [],
        "growth_records": [_growth_record()],
        "audit_entries": [_audit_entry()],
        "growth_narratives": [],
    }
    before = copy.deepcopy(inputs)

    class _Svc:
        def __init__(self):
            self.calls = 0

        def accept_experience(self, record):
            self.calls += 1
            return {"pipeline_state": "created"}

    svc = _Svc()
    host = RuntimeIntegrationHost(
        name="v12_val",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        narrative_assembly_enabled=True,
        narrative_history_path=narrative_path,
        narrative_data_provider=lambda: inputs,
    )
    host.start()
    monkeypatch.setattr(host, "_get_growth_service", lambda: svc)
    host.tick()
    # 输入材料零修改
    assert inputs == before
    # 审计账本零写入
    assert not (tmp_path / "audit.jsonl").exists()
    # 治理链零摄入（无反思事件时 accept_experience 一次都不得调用）
    assert svc.calls == 0
    # 只允许叙事快照文件产生
    created = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert created == ["narrative_history.json"]


# ------------------------------------------------------------
# Test 7: 长期运行（无重复 append、有界增长）
# ------------------------------------------------------------
def test_long_running_no_duplicates_bounded(tmp_path):
    narrative_path = str(tmp_path / "narrative_history.json")
    data = {
        "memories": [{"id": "m1", "content": "喜欢咖啡", "timestamp": "t"}],
        "experiences": [],
        "reflections": [],
        "growth_records": [_growth_record()],
        "audit_entries": [_audit_entry()],
        "growth_narratives": [],
    }
    host = RuntimeIntegrationHost(
        name="v12_lt",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        narrative_assembly_enabled=True,
        narrative_history_path=narrative_path,
        narrative_data_provider=lambda: data,
    )
    host.start()
    for _ in range(20):  # 相同数据连续 20 个 tick
        host.tick()
    h = SelfNarrativeHistory.load(narrative_path)
    assert len(h.snapshots) == 1, "相同数据不得重复 append"
    # 不断变化的数据 → 快照有界（max_snapshots 默认 10）
    for i in range(25):
        data["memories"] = [{"id": f"m{i}", "content": f"记忆{i}", "timestamp": "t"}]
        host.tick()
    h2 = SelfNarrativeHistory.load(narrative_path)
    assert len(h2.snapshots) <= 10, "快照数量必须有界, 不得无限增长"
