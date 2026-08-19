"""
Phase 6.2: SelfModel Runtime Context 测试

验证：
- SelfModelRuntimeContext 正确格式化
- 高 confidence belief 入选；低 confidence 被过滤
- recent growth / self_reflections 正确限制
- SelfModelContextProvider 集成 read 路径
- Orchestrator get_self_model_context 输出
"""
from __future__ import annotations

import os
import sys
import pytest

# Path setup
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_belief import SelfBelief, SelfBeliefStore
from src.personality.self_history import SelfHistory, SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote, SelfReflectionStore
from src.personality.self_model_runtime_context import (
    SelfModelRuntimeContext,
    DEFAULT_MAX_BELIEFS,
    DEFAULT_MAX_HISTORY,
    DEFAULT_MAX_REFLECTIONS,
)
from src.personality.self_model_context_provider import SelfModelContextProvider
from src.personality.self_model_store import SelfModelStore


# ============================================================
# 工具
# ============================================================

def _make_belief(content: str, confidence: float = 0.5, domain: str = "value") -> SelfBelief:
    return SelfBelief(domain=domain, content=content, confidence=confidence, sources=["test"])


def _make_history_event(summary: str = "test") -> SelfHistoryEvent:
    return SelfHistoryEvent(
        event_type=SelfHistoryEventType.PCR_APPLIED,
        source_type="test",
        source_id="t1",
        summary=summary,
    )


def _make_reflection(content: str, confidence: float = 0.6) -> SelfReflectionNote:
    return SelfReflectionNote(
        trigger_source="manual",
        reflection_type="identity",
        content=content,
        confidence=confidence,
        sources=["test"],
    )


def _make_ctx_with(
    beliefs=(),
    history=(),
    reflections=(),
    min_belief_confidence: float = 0.3,
) -> SelfModelRuntimeContext:
    bstore = SelfBeliefStore()
    for b in beliefs:
        bstore.add(b)
    hist = SelfHistory()
    for h in history:
        hist.append(h)
    rstore = SelfReflectionStore()
    for r in reflections:
        rstore.append(r)
    return SelfModelRuntimeContext(
        beliefs=bstore,
        history=hist,
        reflections=rstore,
        min_belief_confidence=min_belief_confidence,
        min_reflection_confidence=0.3,
    )


# ============================================================
# 1. SelfModelRuntimeContext 直接测试
# ============================================================

class TestSelfModelRuntimeContextBasic:
    def test_01_empty_inputs_returns_empty_sections(self):
        ctx = _make_ctx_with()
        out = ctx.build_context()
        assert "identity" in out
        assert out["beliefs"] == []
        assert out["recent_growth"] == []
        assert out["self_reflections"] == []

    def test_02_belief_filter_by_confidence(self):
        ctx = _make_ctx_with(
            beliefs=[
                _make_belief("a_high", confidence=0.8),
                _make_belief("b_low", confidence=0.2),
                _make_belief("c_mid", confidence=0.55),
            ],
            min_belief_confidence=0.5,
        )
        out = ctx.build_context()
        contents = [b["content"] for b in out["beliefs"]]
        assert "a_high" in contents
        assert "c_mid" in contents
        assert "b_low" not in contents

    def test_03_max_beliefs_limit(self):
        ctx = _make_ctx_with(
            beliefs=[_make_belief(f"b_{i}", confidence=0.7) for i in range(20)],
        )
        out = ctx.build_context(max_beliefs=5)
        assert len(out["beliefs"]) == 5

    def test_04_max_history_limit(self):
        ctx = _make_ctx_with(
            history=[_make_history_event(f"e_{i}") for i in range(10)],
        )
        out = ctx.build_context(max_history=3)
        assert len(out["recent_growth"]) == 3

    def test_05_max_reflections_limit(self):
        ctx = _make_ctx_with(
            reflections=[_make_reflection(f"r_{i}") for i in range(10)],
        )
        out = ctx.build_context(max_reflections=3)
        assert len(out["self_reflections"]) == 3

    def test_06_min_belief_confidence_default(self):
        ctx = SelfModelRuntimeContext()
        assert ctx.min_belief_confidence == 0.4


# ============================================================
# 2. build_prompt_text
# ============================================================

class TestBuildPromptText:
    def test_01_prompt_contains_belief_content(self):
        ctx = _make_ctx_with(
            beliefs=[_make_belief("羽依喜欢安静环境", confidence=0.7)],
        )
        text = ctx.build_prompt_text()
        assert "羽依喜欢安静环境" in text
        assert isinstance(text, str)

    def test_02_prompt_excludes_low_confidence(self):
        ctx = _make_ctx_with(
            beliefs=[
                _make_belief("高信念", confidence=0.8),
                _make_belief("低信念", confidence=0.2),
            ],
            min_belief_confidence=0.5,
        )
        text = ctx.build_prompt_text()
        assert "高信念" in text
        assert "低信念" not in text

    def test_03_prompt_contains_reflection(self):
        ctx = _make_ctx_with(
            reflections=[_make_reflection("我开始对世界好奇", confidence=0.6)],
        )
        text = ctx.build_prompt_text()
        assert "我开始对世界好奇" in text

    def test_04_prompt_empty_when_no_data(self):
        ctx = _make_ctx_with()
        text = ctx.build_prompt_text()
        assert isinstance(text, str)


# ============================================================
# 3. set_stores / bind_identity_provider
# ============================================================

class TestAttachChain:
    def test_01_set_stores_replaces(self):
        ctx = SelfModelRuntimeContext()
        bstore = SelfBeliefStore()
        bstore.add(_make_belief("after", 0.7))
        ctx.set_stores(beliefs=bstore)
        out = ctx.build_context()
        assert len(out["beliefs"]) == 1
        assert out["beliefs"][0]["content"] == "after"

    def test_02_bind_identity_provider(self):
        ctx = SelfModelRuntimeContext()
        ctx.bind_identity_provider(lambda: {
            "identity_id": "yuyi",
            "identity_name": "Yuyi",
            "core_values": [{"name": "warmth"}],
        })
        out = ctx.build_context()
        assert out["identity"].get("identity_name") == "Yuyi"
        assert "warmth" in out["identity"].get("core_values", [])

    def test_03_continuity_notes_default_present(self):
        ctx = _make_ctx_with()
        out = ctx.build_context()
        assert "continuity_notes" in out
        assert isinstance(out["continuity_notes"], list)


# ============================================================
# 4. SelfModelContextProvider 集成
# ============================================================

class TestContextProviderIntegration:
    def test_01_get_runtime_self_context_none_when_not_attached(self):
        provider = SelfModelContextProvider(SelfModelStore())
        out = provider.get_runtime_self_context()
        # 未注入时返回 None
        assert out is None

    def test_02_attach_phase_6_2_and_get_context(self):
        provider = SelfModelContextProvider(SelfModelStore())
        provider.attach_phase_6_2(
            beliefs=SelfBeliefStore(),
            history=SelfHistory(),
            reflections=SelfReflectionStore(),
        )
        out = provider.get_runtime_self_context()
        # 注入后空 store 返回带空 sections 的 dict
        assert isinstance(out, dict)
        assert "beliefs" in out

    def test_03_get_runtime_self_prompt_returns_str(self):
        provider = SelfModelContextProvider(SelfModelStore())
        bstore = SelfBeliefStore()
        bstore.add(_make_belief("p_belief", 0.7))
        provider.attach_phase_6_2(
            beliefs=bstore,
            history=SelfHistory(),
            reflections=SelfReflectionStore(),
        )
        text = provider.get_runtime_self_prompt()
        assert isinstance(text, str)
        assert "p_belief" in text


# ============================================================
# 5. belief → prompt 端到端
# ============================================================

class TestBeliefToPromptEndToEnd:
    def test_01_high_confidence_belief_appears_in_prompt(self):
        """测试1：高 confidence belief → prompt 存在"""
        ctx = _make_ctx_with(
            beliefs=[_make_belief("羽依喜欢安静环境", confidence=0.7)],
        )
        text = ctx.build_prompt_text()
        assert "羽依喜欢安静环境" in text

    def test_02_low_confidence_belief_excluded(self):
        """测试2：低 confidence belief → 不进入 prompt"""
        ctx = _make_ctx_with(
            beliefs=[_make_belief("噪音中的思考", confidence=0.1)],
            min_belief_confidence=0.4,
        )
        text = ctx.build_prompt_text()
        assert "噪音中的思考" not in text

    def test_03_reflection_appears_in_next_round(self):
        """测试3：reflection 创建 → 下一轮可被读取"""
        ctx = _make_ctx_with(
            reflections=[_make_reflection("我开始意识到自己的变化", confidence=0.7)],
        )
        text = ctx.build_prompt_text()
        assert "我开始意识到自己的变化" in text

    def test_04_belief_dedup_in_store(self):
        """BeliefStore 自身会去重 (content+domain 命中→reinforce)"""
        bstore = SelfBeliefStore()
        bstore.add(_make_belief("x", 0.6))
        bstore.add(_make_belief("x", 0.6))
        ctx = SelfModelRuntimeContext(beliefs=bstore)
        out = ctx.build_context()
        # 去重后应只剩 1 条
        assert len(out["beliefs"]) == 1
