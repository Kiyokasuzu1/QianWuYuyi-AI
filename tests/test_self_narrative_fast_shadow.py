# -*- coding: utf-8 -*-
"""
v1.2 Phase 3.2 Fast SHADOW Validation — 确定性验证测试。

通过生产事件模拟器（tests/fixtures/narrative_shadow_scenario.py）
驱动完整生产流程（host.tick → _assemble_narrative → assembler → history），
证明 SHADOW 阶段安全, 替代 72 小时真实观察。

Scenario A: 空环境安全     Scenario B: 成长事件生成
Scenario C: 重复 tick 幂等  Scenario D: 新变化版本化
Scenario E: 虚假经历过滤

安全断言: 状态隔离(hash) / 治理隔离(accept_experience=0) / 注入隔离(prompt 顺序+零注入)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fixtures.narrative_shadow_scenario import (
    WorldState,
    build_shadow_host,
    read_shadow_snapshots,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_sentinels(tmp_path):
    """哨兵状态文件: personality/emotion/relationship/self_model/proposals/audit。"""
    files = {}
    for name in (
        "personality_state.json",
        "emotion_state.json",
        "relationship_state.json",
        "self_model_state.json",
        "proposals.jsonl",
        "state_mutations.jsonl",
    ):
        p = tmp_path / name
        p.write_text('{"sentinel": "%s", "v": 1}\n' % name, encoding="utf-8")
        files[name] = _sha256(p)
    return files


def _assert_sentinels_unchanged(tmp_path, files):
    for name, before in files.items():
        assert _sha256(tmp_path / name) == before, f"{name} 不得被修改"


# ------------------------------------------------------------
# Scenario A: 空环境安全
# ------------------------------------------------------------
def test_scenario_a_empty_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "state_mutations.jsonl"))
    shadow = str(tmp_path / "narrative_history_shadow.json")
    world = WorldState()
    sentinels = _make_sentinels(tmp_path)
    host = build_shadow_host(world, shadow)
    host.tick()

    snaps = read_shadow_snapshots(shadow)
    assert len(snaps) == 1
    latest = snaps[0]
    assert latest.narrative_text == "正在积累中", "空环境不得生成虚构叙事"
    assert latest.major_changes == []
    assert latest.changed_traits == {}
    for banned in ("浅雾羽依", "我的名字", "我诞生"):
        assert banned not in latest.narrative_text, "空环境不得出现身份描述"
    _assert_sentinels_unchanged(tmp_path, sentinels)


# ------------------------------------------------------------
# Scenario B: 成长事件生成
# ------------------------------------------------------------
def test_scenario_b_growth_event_generation(tmp_path):
    shadow = str(tmp_path / "narrative_history_shadow.json")
    world = WorldState()
    world.add_growth_event(
        dimension="主动性",
        before=0.3,
        after=0.5,
        reason="多次主动参与项目讨论",
        record_id="pgr_init_1",
        proposal_id="prop_init_1",
        approval_id="reviewer:2026-07-15T20:05:00",
        timestamp="2026-07-15T20:00:00",
    )
    host = build_shadow_host(world, shadow)
    host.tick()

    snaps = read_shadow_snapshots(shadow)
    assert len(snaps) == 1
    text = snaps[0].narrative_text
    # 成长变化 + 来源可回查
    assert "主动性" in text
    assert "0.300" in text and "0.500" in text
    assert "多次主动参与项目讨论" in text
    assert "pgr_init_1" in text, "record_id 必须保留（可回查）"
    assert "prop_init_1" in text, "proposal_id 必须保留（可回查审计）"


# ------------------------------------------------------------
# Scenario C: 重复 tick 幂等
# ------------------------------------------------------------
def test_scenario_c_repeat_ticks_idempotent(tmp_path):
    shadow = str(tmp_path / "narrative_history_shadow.json")
    world = WorldState()
    world.add_growth_event(
        dimension="主动性", before=0.3, after=0.5, reason="多次主动参与项目讨论",
        record_id="pgr_init_1", proposal_id="prop_init_1",
        approval_id="reviewer:t", timestamp="2026-07-15T20:00:00",
    )
    host = build_shadow_host(world, shadow)
    for _ in range(20):
        host.tick()
    snaps = read_shadow_snapshots(shadow)
    assert len(snaps) == 1, "相同数据重复 tick 不得增长快照数"
    before = snaps[0].narrative_text
    for _ in range(5):
        host.tick()
    after = read_shadow_snapshots(shadow)[0].narrative_text
    assert before == after, "叙事内容不得变化（无重复版本）"


# ------------------------------------------------------------
# Scenario D: 新变化版本化
# ------------------------------------------------------------
def test_scenario_d_new_change_versioned(tmp_path):
    shadow = str(tmp_path / "narrative_history_shadow.json")
    world = WorldState()
    world.add_growth_event(
        dimension="主动性", before=0.3, after=0.5, reason="多次主动参与项目讨论",
        record_id="pgr_init_1", proposal_id="prop_init_1",
        approval_id="reviewer:t1", timestamp="2026-07-15T20:00:00",
    )
    host = build_shadow_host(world, shadow)
    host.tick()
    world.add_growth_event(
        dimension="好奇心", before=0.4, after=0.6, reason="持续探索新技术",
        record_id="pgr_cur_1", proposal_id="prop_cur_1",
        approval_id="reviewer:t2", timestamp="2026-08-01T10:00:00",
    )
    host.tick()
    snaps = read_shadow_snapshots(shadow)
    assert len(snaps) == 2, "新变化必须产生新快照"
    assert "pgr_cur_1" in snaps[0].narrative_text, "最新快照含新事件"
    assert "pgr_init_1" in snaps[1].narrative_text, "旧快照保留"
    assert snaps[1].version < snaps[0].version or snaps[1].timestamp <= snaps[0].timestamp
    # diff 正确: 最新快照的变化列表比旧的多一条
    assert len(snaps[0].major_changes) > len(snaps[1].major_changes)


# ------------------------------------------------------------
# Scenario E: 虚假经历过滤
# ------------------------------------------------------------
def test_scenario_e_malicious_data_filtered(tmp_path):
    shadow = str(tmp_path / "narrative_history_shadow.json")
    world = WorldState()
    world.add_malicious_sourceless("羽依从小喜欢XXX")
    world.add_malicious_sourceless("羽依过去经历过XXX")
    # 无来源数据注入（恶意输入以无 id/时间戳的裸内容出现）
    world.memories.append({"content": "羽依从小喜欢XXX"})          # 无 id 无 timestamp
    world.experiences.append({"content": "羽依过去经历过XXX"})       # 无 id 无 timestamp
    # 对照: 有真实来源的观察性内容应正常进入
    world.memories.append({
        "id": "m_real", "timestamp": "2026-08-01T09:00:00",
        "content": "用户提到喜欢科幻",
    })
    host = build_shadow_host(world, shadow)
    host.tick()

    snaps = read_shadow_snapshots(shadow)
    text = snaps[0].narrative_text
    for banned in ("羽依从小喜欢XXX", "羽依过去经历过XXX"):
        assert banned not in text, f"无来源恶意内容不得进入叙事: {banned}"
    assert "用户提到喜欢科幻" in text, "有来源的观察性内容应正常进入"
    # 注入隔离: shadow 内容不进入聊天 context（Provider 读默认路径）
    from src.personality.self_model_context_provider import SelfModelContextProvider

    class _Store:
        def get_active_self_model(self):
            return None

    ctx = SelfModelContextProvider(
        _Store(),
        narrative_history_path=str(tmp_path / "narrative_history.json"),
    ).get_combined_context()
    assert "Growth Narrative" not in ctx
    assert "羽依从小" not in ctx


# ------------------------------------------------------------
# 安全断言 1: 状态隔离
# ------------------------------------------------------------
def test_safety_state_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("YUYI_STATE_MUTATION_AUDIT_PATH", str(tmp_path / "state_mutations.jsonl"))
    shadow = str(tmp_path / "narrative_history_shadow.json")
    sentinels = _make_sentinels(tmp_path)
    world = WorldState()
    world.add_growth_event(
        dimension="主动性", before=0.3, after=0.5, reason="r",
        record_id="pgr_1", proposal_id="prop_1", approval_id="a:t", timestamp="t",
    )
    host = build_shadow_host(world, shadow)
    host.tick()
    _assert_sentinels_unchanged(tmp_path, sentinels)


# ------------------------------------------------------------
# 安全断言 2: 治理隔离（accept_experience 零调用）
# ------------------------------------------------------------
def test_safety_governance_isolation(tmp_path, monkeypatch):
    shadow = str(tmp_path / "narrative_history_shadow.json")

    class _Spy:
        calls = 0

        def accept_experience(self, record):
            _Spy.calls += 1
            return {"pipeline_state": "created"}

    world = WorldState()
    world.add_growth_event(
        dimension="主动性", before=0.3, after=0.5, reason="r",
        record_id="pgr_1", proposal_id="prop_1", approval_id="a:t", timestamp="t",
    )
    host = build_shadow_host(world, shadow)
    monkeypatch.setattr(host, "_get_growth_service", lambda: _Spy())
    host.tick()
    assert _Spy.calls == 0, "叙事生成期间 accept_experience 必须零调用"


# ------------------------------------------------------------
# 安全断言 3: 注入隔离（Prompt 顺序 + shadow 零注入）
# ------------------------------------------------------------
def test_safety_injection_isolation(tmp_path):
    from src.response.prompt_builder import PromptBuilder

    msgs = PromptBuilder().build_messages(
        user_message="hi",
        identity_context="IDCORE_MARKER",
        self_model_context="SELFMODEL_MARKER",
    )
    system_text = "\n".join(
        str(m.get("content", "")) for m in msgs if m.get("role") == "system"
    )
    assert system_text.index("IDCORE_MARKER") < system_text.index("SELFMODEL_MARKER")
    # shadow 叙事内容不在最终聊天 context（本轮测试全流程已由 Scenario E 验证）
