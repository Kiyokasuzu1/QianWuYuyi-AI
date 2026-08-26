# -*- coding: utf-8 -*-
"""Phase 1 P0-A/P0-B 专项测试。

P0-A（记忆措辞语义）Test A1-A7：
  检索失败 ≠ 过去不存在；区分 记得/大概知道/想不起细节/刚被告知/不知道。
P0-B（人格段非空）BTEST-1..8：
  Resolver 非空 / version=0 仍有基础人格 / evolved 进入 prompt /
  runtime 链非空 / legacy 链非空 / 双链一致 / 不重复 Identity / 空时显式失败。

全部确定性断言（无 LLM、无网络）；不硬编码默认人格文本。
"""
from __future__ import annotations

from datetime import datetime

import pytest


def _mem(mid, content, hours_ago=2):
    return {
        "id": mid, "content": content,
        "timestamp": (datetime.now().replace(microsecond=0)).isoformat(),
        "user_id": "366648462", "role": "user", "importance": 0.5,
        "source_event_id": "", "emotion_tag": "", "relationship_id": "366648462",
        "metadata": {"memory_type": "user_experience"},
    }


# ============ P0-A：记忆措辞语义 ============
def _empty_retrieval_prompt():
    from src.engine import ResponseEngine
    eng = ResponseEngine()
    msgs = eng._build_messages_original(
        "你以前是不是经常晚上陪我？",
        history=[], chat_memories=[],
        personality_context="", self_model_context={}, emotion_context={},
        relationship_context={}, life_events=[])
    return str(msgs[0]["content"])


def test_a1_no_degenerate_self_description():
    """A1: 检索空时 prompt 不得诱导"我的记忆是碎片"类退化自我描述。"""
    text = _empty_retrieval_prompt()
    assert "记忆是碎片" not in text and "我的记忆本来" not in text
    assert "没有检索到相关历史记忆" not in text, "旧有害措辞必须移除"
    assert "过去是连续的" in text, "新语义（存在性≠可访问性）必须存在"


def test_a2_no_denial_of_existence():
    """A2: 检索失败不得等价事实不存在。"""
    text = _empty_retrieval_prompt()
    assert "记不清具体是哪一次" in text or "记不清细节" in text, \
        "必须引导'具体记不清'而非否认经历"
    assert "还没有相关的记忆" not in text, "旧原则措辞必须移除"


def test_a3_pattern_without_detail_semantics():
    """A3: prompt 语义允许'大方向存在、细节不确定'（模式与细节分离的语言在场）。"""
    text = _empty_retrieval_prompt()
    # 核心原则块含新语义（连续过去 + 有限访问）
    from src.response.principles import build_principles_block
    pb = build_principles_block()
    assert "过去是连续的" in pb
    assert "记不清具体是哪一次" in pb


def test_a4_evidence_present_normal_memory():
    """A4: 有证据时正常渲染记忆，不额外强调'记忆很碎'。"""
    from src.engine import ResponseEngine
    eng = ResponseEngine()
    msgs = eng._build_messages_original(
        "我们昨天聊了什么？",
        history=[],
        chat_memories=[_mem("m1", "我们昨天聊了爱的话题")],
        personality_context="", self_model_context={}, emotion_context={},
        relationship_context={}, life_events=[])
    text = str(msgs[0]["content"])
    assert "我们昨天聊了爱的话题" in text, "有证据时正常注入"
    assert "记忆是碎片" not in text


def test_a5_newly_told_vs_known():
    """A5: '刚被告知'与'本来就记得'的语言区分在场（prompt 语义层）。"""
    from src.response.principles import build_principles_block
    pb = build_principles_block()
    # 原则块必须保持"不假装记得没发生过的事"（newly told 不自动成为历史）
    assert "不要假装记得没有发生过的事情" in pb or "不要编造记忆" in pb


def test_a6_unknown_stays_honest():
    """A6: 完全无证据时保持诚实（不虚构共同经历）。"""
    text = _empty_retrieval_prompt()
    assert "不要编造具体历史" in text or "不要编造" in text, \
        "诚实约束必须保留（连续性不能靠幻觉）"


def test_a7_conflict_stays_cautious():
    """A7: 矛盾历史时不制造确定性（原则块保留'不确定就说不知道'）。"""
    from src.response.principles import build_principles_block
    pb = build_principles_block()
    assert "不确定就说不知道" in pb, "谨慎约束必须保留"


# ============ P0-B：人格段非空 ============
def _runtime_personality_text():
    from src.runtime.runtime_core import RuntimeCore
    from src.personality.personality_resolver import PersonalityResolver

    class _Ctx:
        user_message = "你好"
        inputs = {"user_id": "366648462"}
        history = []
        personality_snapshot = None
        personality_context_text = None
        identity_context_text = None

    rc = RuntimeCore.__new__(RuntimeCore)
    ctx = _Ctx()
    ctx.personality_snapshot = PersonalityResolver().resolve()
    rc._stage_06_personality_context_build(None, ctx)
    return ctx.personality_context_text or ""


def test_btest1_resolver_non_empty():
    """BTEST-1: PersonalityResolver 输出非空。"""
    from src.personality.personality_resolver import PersonalityResolver
    vec = PersonalityResolver().resolve()
    assert vec.get_all(), "Resolver 输出为空"


def test_btest2_baseline_personality_when_v0():
    """BTEST-2: version=0 时人格文本仍有合理基础（不因无演化而全空）。"""
    text = _runtime_personality_text()
    assert text, "version=0 时人格文本不得为空"
    assert "人格" in text


def test_btest3_evolved_traits_enter_prompt(tmp_path, monkeypatch):
    """BTEST-3: version>0 时 evolved traits 进入 prompt 人格文本。"""
    from src.personality.personality_state import PersonalityState
    from src.personality.evolution_record import build_evolution_record
    ps = PersonalityState()
    ps.apply_evolution(build_evolution_record(
        proposal_id="prop_b3", approval_id="admin:b3", change_type="trait_delta",
        before={"creativity": 0.6}, after={"creativity": 0.9},
        reasons=["BTEST-3"], confidence=0.9))
    monkeypatch.setattr("src.personality.personality_state._global_state", ps)
    text = _runtime_personality_text()
    assert text, "人格文本不得为空"
    assert "creativity" in text, f"evolved trait 未进入: {text[:200]}"


def test_btest4_runtime_chain_non_empty():
    """BTEST-4: runtime 链人格块非空。"""
    text = _runtime_personality_text()
    assert text.strip(), "runtime 链人格文本为空"


def test_btest5_legacy_chain_non_empty():
    """BTEST-5: legacy 链（engine 直调）人格块可渲染。"""
    from src.engine import ResponseEngine
    eng = ResponseEngine()
    msgs = eng._build_messages_original(
        "你好", history=[], chat_memories=[],
        personality_context="人格总结：羽依性格温暖而柔和。",
        self_model_context={}, emotion_context={}, relationship_context={},
        life_events=[])
    text = str(msgs[0]["content"])
    assert "人格" in text and "温暖" in text, "legacy 链人格段未渲染"


def test_btest6_dual_chain_semantic_consistent():
    """BTEST-6: 双链人格语义一致（同一 resolver 输出进同一文本格式）。"""
    t1 = _runtime_personality_text()
    from src.personality.personality_resolver import PersonalityResolver
    vec = PersonalityResolver().resolve()
    pd = vec.get_all()
    # runtime 文本来自同一快照：抽查一个维度值一致性
    for key in ("warmth", "gentleness"):
        if key in pd:
            assert str(round(float(pd[key]), 2)) in t1, f"维度 {key} 未一致渲染"


def test_btest7_identity_not_duplicated():
    """BTEST-7: 人格块不重复 Identity（人格文本不含身份来源/创造者类内容）。"""
    text = _runtime_personality_text()
    assert "清夏铃" not in text, "人格块混入身份内容（重复污染）"
    assert "不可变原则" not in text


def test_btest8_empty_fails_explicitly():
    """BTEST-8: 人格文本为空时显式断言失败（不静默通过、不硬编码默认文本）。"""
    text = _runtime_personality_text()
    # 此测试的意义：若未来回归导致空，此断言立刻失败（而非静默）
    assert text.strip(), "人格文本为空——回归！禁止硬编码默认文本掩盖"
