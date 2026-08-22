# -*- coding: utf-8 -*-
"""
v1.2 Self Understanding — Phase 3.2 SHADOW 测试。

验证 SHADOW 语义:
    narrative_assembly_enabled=true（后台生成叙事）
    narrative_context_injection=false（聊天上下文零变化）

Test 1: SHADOW 开启 → shadow 路径产生 narrative_history; 聊天 Provider(默认路径)无叙事。
Test 2: 相同数据重复 20 tick → snapshot 数量稳定(不重复 append)。
Test 3: 模拟新成长记录 → 生成新 Narrative(快照 +1)。
Test 4: 空数据 → 不生成虚构故事(「正在积累中」)。
Test 5: 完整 tick → personality/emotion/relationship/self_model 状态文件与
        state_mutations 的 hash 全部不变。
Test 6: RuntimeCore SHADOW 传参: injection=false → shadow 路径; =true → 默认路径。
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import pytest

from src.personality.self_narrative_history import SelfNarrativeHistory
from src.runtime.integration.runtime_integration_host import RuntimeIntegrationHost


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_host(**kw):
    return RuntimeIntegrationHost(
        name="v12_shadow",
        auto_create_manager=False,
        auto_register_tasks=False,
        enable_persistence=False,
        **kw,
    )


def _data(growth=True):
    return {
        "memories": [{"id": "m1", "content": "用户喜欢咖啡", "timestamp": "t"}],
        "experiences": [],
        "reflections": [],
        "growth_records": (
            [{
                "record_id": "pgr_s1",
                "timestamp": "2026-07-15T20:00:00",
                "trigger_events": ["e1"],
                "changes": {"initiative": {"before": 0.3, "after": 0.5, "reason": "r"}},
                "affected_dimensions": ["initiative"],
                "meaning": "r",
                "narrative": "",
                "confidence": 0.85,
                "validation_count": 1,
                "growth_level": "trait",
            }] if growth else []
        ),
        "audit_entries": (
            [{
                "id": "sm_s1",
                "component": "personality",
                "proposal_id": "prop_s1",
                "approval_id": "reviewer:2026-07-15T20:05:00",
                "before": {"initiative": 0.3},
                "after": {"initiative": 0.5},
                "actor": "runtime_drain",
                "timestamp": "2026-07-15T20:10:00",
            }] if growth else []
        ),
        "growth_narratives": [],
    }


# ------------------------------------------------------------
# Test 1: SHADOW → shadow 文件生成; 聊天上下文零注入
# ------------------------------------------------------------
def test_shadow_writes_shadow_file_but_context_untouched(tmp_path):
    shadow_path = str(tmp_path / "narrative_history_shadow.json")
    default_path = str(tmp_path / "narrative_history.json")
    host = _make_host(
        narrative_assembly_enabled=True,
        narrative_history_path=shadow_path,
        narrative_data_provider=_data,
    )
    host.start()
    host.tick()
    assert Path(shadow_path).exists(), "SHADOW 应产生叙事文件"
    assert not Path(default_path).exists(), "聊天侧默认路径不得出现叙事文件"
    h = SelfNarrativeHistory.load(shadow_path)
    assert len(h.snapshots) >= 1
    assert "Growth Narrative" not in h.get_latest().narrative_text or "[Growth]" in h.get_latest().narrative_text

    from src.personality.self_model_context_provider import SelfModelContextProvider

    class _Store:
        def get_active_self_model(self):
            return None

    ctx = SelfModelContextProvider(
        _Store(), narrative_history_path=default_path
    ).get_combined_context()
    assert "Growth Narrative" not in ctx, "聊天上下文必须保持 v1.1 形态（零注入）"


# ------------------------------------------------------------
# Test 2: 相同数据 20 tick → snapshot 稳定
# ------------------------------------------------------------
def test_shadow_repeat_ticks_stable(tmp_path):
    shadow_path = str(tmp_path / "narrative_history_shadow.json")
    host = _make_host(
        narrative_assembly_enabled=True,
        narrative_history_path=shadow_path,
        narrative_data_provider=_data,
    )
    host.start()
    for _ in range(20):
        host.tick()
    h = SelfNarrativeHistory.load(shadow_path)
    assert len(h.snapshots) == 1, "相同数据不得重复 append"


# ------------------------------------------------------------
# Test 3: 新成长记录 → 新 Narrative
# ------------------------------------------------------------
def test_shadow_new_growth_generates_new_narrative(tmp_path):
    shadow_path = str(tmp_path / "narrative_history_shadow.json")
    state = {"growth": False}

    def provider():
        d = _data(growth=state["growth"])
        return d

    host = _make_host(
        narrative_assembly_enabled=True,
        narrative_history_path=shadow_path,
        narrative_data_provider=provider,
    )
    host.start()
    host.tick()  # 空成长 → 快照1（正在积累中）
    state["growth"] = True
    host.tick()  # 新成长记录出现 → 快照2
    h = SelfNarrativeHistory.load(shadow_path)
    assert len(h.snapshots) >= 2
    assert "prop_s1" in h.get_latest().narrative_text, "新成长必须反映到新叙事"


# ------------------------------------------------------------
# Test 4: 空数据 → 不编造
# ------------------------------------------------------------
def test_shadow_empty_data_no_fabrication(tmp_path):
    shadow_path = str(tmp_path / "narrative_history_shadow.json")

    def empty():
        return {
            "memories": [], "experiences": [], "reflections": [],
            "growth_records": [], "audit_entries": [], "growth_narratives": [],
        }

    host = _make_host(
        narrative_assembly_enabled=True,
        narrative_history_path=shadow_path,
        narrative_data_provider=empty,
    )
    host.start()
    host.tick()
    h = SelfNarrativeHistory.load(shadow_path)
    assert h.get_latest().narrative_text == "正在积累中"
    assert h.get_latest().major_changes == []
    assert h.get_latest().changed_traits == {}


# ------------------------------------------------------------
# Test 5: 状态文件 hash 不变
# ------------------------------------------------------------
def test_shadow_state_files_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "state_mutations.jsonl")
    )
    shadow_path = str(tmp_path / "narrative_history_shadow.json")
    # 哨兵状态文件（模拟人格/情绪/关系/自我模型落盘状态）
    sentinels = {}
    for name in ("personality_state.json", "emotion_state.json",
                 "relationship_state.json", "self_model_state.json"):
        p = tmp_path / name
        p.write_text('{"state": "%s", "v": 1}' % name, encoding="utf-8")
        sentinels[name] = _sha256(p)
    audit_file = tmp_path / "state_mutations.jsonl"
    audit_file.write_text('{"id":"sm_pre","proposal_id":"pre"}\n', encoding="utf-8")
    audit_before = _sha256(audit_file)

    host = _make_host(
        narrative_assembly_enabled=True,
        narrative_history_path=shadow_path,
        narrative_data_provider=_data,
    )
    host.start()
    host.tick()

    for name, before in sentinels.items():
        assert _sha256(tmp_path / name) == before, f"{name} 不得被修改"
    assert _sha256(audit_file) == audit_before, "state_mutations 不得被修改"


# ------------------------------------------------------------
# Test 6: RuntimeCore SHADOW 传参
# ------------------------------------------------------------
def test_runtime_core_shadow_flag_passthrough(monkeypatch):
    rmod = importlib.import_module("src.runtime.runtime_core")
    rcls = getattr(rmod, "RuntimeCore")
    inst = rcls.__new__(rcls)
    captured = {}

    class _FakeHost:
        def __init__(self, **kw):
            captured.update(kw)

        def start(self):
            return True

        def tick(self):
            return []

    monkeypatch.setattr(
        "src.runtime.integration.runtime_integration_host.RuntimeIntegrationHost",
        _FakeHost,
    )

    inst.config = {
        "integration_host_enabled": True,
        "narrative_assembly_enabled": True,
        "narrative_context_injection": False,
    }
    inst._tick_background_host()
    assert captured.get("narrative_assembly_enabled") is True
    assert captured.get("narrative_history_path", "").endswith(
        "narrative_history_shadow.json"
    ), "injection=false 必须写 shadow 路径"

    inst._integration_host = None
    inst.config["narrative_context_injection"] = True
    inst._tick_background_host()
    assert captured.get("narrative_history_path", "").endswith(
        "narrative_history.json"
    ), "injection=true 才写默认路径"

    # 默认配置（无叙事 flag）→ assembly 关闭（v1.1 行为）
    inst._integration_host = None
    inst.config = {"integration_host_enabled": True}
    inst._tick_background_host()
    assert captured.get("narrative_assembly_enabled") is False
