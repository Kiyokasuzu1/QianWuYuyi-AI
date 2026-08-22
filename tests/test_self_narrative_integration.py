# -*- coding: utf-8 -*-
"""
v1.2 Self Understanding — Phase 2 集成测试。

覆盖（任务书 Test 1-7）:
1. Context 注入: self_model_context 含 Growth Narrative; Identity 在前。
2. 无 Narrative 文件: 聊天 context 正常降级。
3. ContextProvider 不调用 assembler（只读历史）。
4. Runtime trigger: reflection completed → 生成 snapshot。
5. Drain trigger: 审计材料变化 → 生成 snapshot（重复 tick 不重复 append）。
6. 状态隔离: 组装全程不写 personality/emotion/relationship/self_model/audit。
7. Regression: 开关默认关闭时 v1.1 行为不变（不产生叙事文件）。

隔离: 假 store / 假 service / tmp 路径; 遵守 conftest 陷阱 token 规则。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.personality.self_narrative_history import SelfNarrativeHistory
from src.runtime.integration.integration_event import (
    INTEGRATION_REFLECTION_COMPLETED,
    make_integration_event,
)
from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost


class _FakeStore:
    """Provider 只需要 get_active_self_model() → None（legacy 段为空）。"""

    def get_active_self_model(self):
        return None


class _FakeService:
    def __init__(self):
        self.records = []

    def accept_experience(self, record):
        self.records.append(record)
        return {"pipeline_state": "created"}


def _make_host(tmp_path, **kw):
    return RuntimeIntegrationHost(
        name="v12_it",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        **kw,
    )


def _write_narrative(tmp_path, text):
    from src.personality.self_narrative_history import NarrativeSnapshot

    h = SelfNarrativeHistory()
    h.add_snapshot(NarrativeSnapshot(narrative_text=text))
    h.save(str(tmp_path / "narrative_history.json"))
    return str(tmp_path / "narrative_history.json")


# ------------------------------------------------------------
# Test 1: Context 注入 + 顺序
# ------------------------------------------------------------
def test_context_injection_and_ordering(tmp_path):
    from src.personality.self_model_context_provider import SelfModelContextProvider

    path = _write_narrative(
        tmp_path,
        "[Recent]\n近期记忆：用户喜欢科幻\n\n[Growth]\n成长评估：openness 由 0.500 调整为 0.580\n\n[Identity]\n记录显示，近期关注点：科幻话题",
    )
    provider = SelfModelContextProvider(_FakeStore(), narrative_history_path=path)
    ctx = provider.get_combined_context()
    assert "Growth Narrative" in ctx
    assert "Recent Experience" in ctx
    assert "Growth History" in ctx
    assert "Self Understanding" in ctx
    # 自我模型（legacy/runtime 为空时也不得让叙事排在身份之前 —— 顺序由
    # PromptBuilder 保证, 见下）
    from src.response.prompt_builder import PromptBuilder

    msgs = PromptBuilder().build_messages(
        user_message="hi",
        identity_context="IDCORE_MARKER",
        self_model_context="SELFMODEL_MARKER",
    )
    system_text = "\n".join(
        str(m.get("content", "")) for m in msgs if m.get("role") == "system"
    )
    assert system_text.index("IDCORE_MARKER") < system_text.index("SELFMODEL_MARKER"), (
        "Identity Core 必须位于 Self Model 之前"
    )


# ------------------------------------------------------------
# Test 2: 无 Narrative 文件 → 正常降级
# ------------------------------------------------------------
def test_context_without_narrative_file(tmp_path):
    from src.personality.self_model_context_provider import SelfModelContextProvider

    provider = SelfModelContextProvider(
        _FakeStore(),
        narrative_history_path=str(tmp_path / "missing.json"),
    )
    ctx = provider.get_combined_context()
    assert ctx == "", "无叙事无自我模型时应返回空串而非异常"
    assert "Growth Narrative" not in ctx


# ------------------------------------------------------------
# Test 3: Provider 不调用 assembler
# ------------------------------------------------------------
def test_provider_does_not_import_assembler():
    src = (
        Path(__file__).parent.parent
        / "src" / "personality" / "self_model_context_provider.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "src.personality.self_narrative_assembler", (
                "ContextProvider 只读历史, 不得调用 assembler"
            )


# ------------------------------------------------------------
# Test 4: Reflection trigger → 生成 snapshot
# ------------------------------------------------------------
def test_reflection_trigger_generates_snapshot(tmp_path, monkeypatch):
    narrative_path = str(tmp_path / "narrative_history.json")

    def provider():
        return {
            "memories": [],
            "experiences": [],
            "reflections": [
                {"reflection_id": "ref_x", "insights": ["用户对科幻话题兴趣增强"], "confidence": 0.8}
            ],
            "growth_records": [],
            "audit_entries": [],
            "growth_narratives": [],
        }

    service = _FakeService()
    host = _make_host(
        tmp_path,
        reflection_cycle_enabled=True,
        narrative_assembly_enabled=True,
        narrative_history_path=narrative_path,
        narrative_data_provider=provider,
    )
    host.start()
    monkeypatch.setattr(host, "_get_growth_service", lambda: service)
    host.publish_integration_event(make_integration_event(
        event_type=INTEGRATION_REFLECTION_COMPLETED,
        source="reflection",
        payload={"reflection_id": "ref_x", "insights": ["用户对科幻话题兴趣增强"], "confidence": 0.8},
        related_ids=["ref_x"],
    ))
    host.tick()
    assert service.records, "反思应进入治理链"
    history = SelfNarrativeHistory.load(narrative_path)
    assert len(history.snapshots) >= 1
    assert "近期反思" in history.get_latest().narrative_text


# ------------------------------------------------------------
# Test 5: Drain trigger（审计变化 → 快照; 幂等不重复）
# ------------------------------------------------------------
def test_drain_trigger_and_idempotent_append(tmp_path, monkeypatch):
    narrative_path = str(tmp_path / "narrative_history.json")
    state = {"audit": 0}

    def provider():
        audits = []
        if state["audit"] >= 1:
            audits = [{
                "id": "sm_1",
                "component": "personality",
                "proposal_id": "prop_1",
                "approval_id": "reviewer:2026-08-01T10:00:00",
                "before": {"openness": 0.5},
                "after": {"openness": 0.58},
                "actor": "runtime_drain",
                "timestamp": "2026-08-01T10:05:00",
            }]
        return {
            "memories": [],
            "experiences": [],
            "reflections": [],
            "growth_records": [{
                "record_id": "pgr_1",
                "timestamp": "2026-08-01T10:00:00",
                "trigger_events": ["evt_1"],
                "changes": {"openness": {"before": 0.5, "after": 0.58, "reason": "探索意愿增强"}},
                "affected_dimensions": ["openness"],
                "meaning": "探索意愿增强",
                "narrative": "",
                "confidence": 0.8,
                "validation_count": 1,
                "growth_level": "trait",
            }],
            "audit_entries": audits,
            "growth_narratives": [],
        }

    host = _make_host(
        tmp_path,
        narrative_assembly_enabled=True,
        narrative_history_path=narrative_path,
        narrative_data_provider=provider,
    )
    host.start()
    host.tick()  # 无审计 → 快照1（来源不足标记）
    state["audit"] = 1
    host.tick()  # 审计出现（模拟 drain apply 后）→ 快照2
    history = SelfNarrativeHistory.load(narrative_path)
    assert len(history.snapshots) >= 2, "材料变化后应新增快照版本"
    latest = history.get_latest().narrative_text
    assert "治理应用" in latest and "prop_1" in latest
    # 相同材料再 tick → 不重复 append
    host.tick()
    history2 = SelfNarrativeHistory.load(narrative_path)
    assert len(history2.snapshots) == len(history.snapshots), "无变化不得重复生成快照"


# ------------------------------------------------------------
# Test 6: 状态隔离
# ------------------------------------------------------------
def test_state_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "state_mutations.jsonl")
    )
    narrative_path = str(tmp_path / "narrative_history.json")
    records = [{
        "record_id": "pgr_1",
        "timestamp": "2026-08-01T10:00:00",
        "trigger_events": ["evt_1"],
        "changes": {"openness": {"before": 0.5, "after": 0.58, "reason": "r"}},
        "affected_dimensions": ["openness"],
        "meaning": "r",
        "narrative": "",
        "confidence": 0.8,
        "validation_count": 1,
        "growth_level": "trait",
    }]
    records_before = len(records)

    def provider():
        return {
            "memories": [],
            "experiences": [],
            "reflections": [],
            "growth_records": records,
            "audit_entries": [],
            "growth_narratives": [],
        }

    host = _make_host(
        tmp_path,
        narrative_assembly_enabled=True,
        narrative_history_path=narrative_path,
        narrative_data_provider=provider,
    )
    host.start()
    host.tick()
    # 组装不写 audit（审计只读）
    assert not (tmp_path / "state_mutations.jsonl").exists(), "叙事组装不得写审计账本"
    # 输入材料不被修改
    assert len(records) == records_before
    # tmp 目录中只有叙事文件（无任何人格/情绪/关系/自我模型状态文件）
    created = [p.name for p in tmp_path.iterdir() if p.is_file()]
    assert created == ["narrative_history.json"], f"意外写入: {created}"


# ------------------------------------------------------------
# Test 7: Regression（默认关闭 → v1.1 行为不变）
# ------------------------------------------------------------
def test_default_off_no_narrative_file(tmp_path):
    narrative_path = str(tmp_path / "narrative_history.json")
    host = _make_host(tmp_path)  # narrative_assembly_enabled 默认 False
    host.start()
    host.tick()
    assert not Path(narrative_path).exists(), "默认关闭时不得产生叙事文件"
