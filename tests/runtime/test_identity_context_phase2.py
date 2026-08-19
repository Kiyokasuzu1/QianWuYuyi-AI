# -*- coding: utf-8 -*-
"""
tests/runtime/test_identity_context_phase2.py

Phase 2: Identity State 注入 —— 单元测试

覆盖范围：
  1. IdentityContextProvider.provide() —— 空 bundle、完整 bundle、各种边界
  2. IdentityContextProvider.format_context_for_prompt() —— 「我是谁」格式而非 Anchor 原文
  3. PromptBuilder.build_messages(identity_context=...) —— 插入位置在 core_identity 之后
  4. ResponseAdapterImpl.generate() —— identity_context 从 ResponseRequest 透传
  5. fail-soft —— 任何异常都不抛，不影响主流程
  6. Orchestrator._get_identity_context() —— 无数据时返回 None，不抛错

设计约束（严格对齐 project_memory）：
  - 不调用 LLM，不 import src.memory/src.growth/src.personality/
    src.self_model/src.control/src.runtime.policy（除了 IdentityContextProvider 自己）
  - 不写死 identity_name
  - 不编造数据；来源为 0 / None 时不写"0 条"
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 1. IdentityContextProvider.provide()
# ============================================================

class TestIdentityContextProviderProvide:
    def test_empty_bundle_has_identity_false(self):
        """空 bundle → has_identity=False，不注入。
        注意：Anchor 人性化转译后的默认稳定倾向会出现在
        stable_tendencies 中，但由于没有任何来源计数（经历/特质/信念），
        has_identity 仍应为 False（不灌空壳进 Prompt）。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        data = prov.provide({})
        assert data.get("has_identity") is False
        # 且 format_context_for_prompt 返回 None（关键：不注入 Prompt）
        assert prov.format_context_for_prompt(data) is None

    def test_none_bundle_safe(self):
        """bundle=None → 安全返回，不抛错。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        data = prov.provide(None)
        assert isinstance(data, dict)
        assert data.get("has_identity") is False

    def test_full_bundle_aggregates_correctly(self):
        """完整 bundle → 正确抽取 name / counts / tendencies。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        bundle: Dict[str, Any] = {
            "identity_name": "浅雾羽依",
            "identity_snapshot": {
                "core_values": [
                    {"name": "honesty", "confidence": 0.9},
                    {"name": "empathy", "confidence": 0.85},
                ],
                "traits": [
                    {"trait": "warmth", "stability": 0.72},
                    {"trait": "curiosity", "stability": 0.61},
                    {"trait": "shyness", "stability": 0.55},  # <0.6 跳过
                ],
            },
            "existence_stats": {"total": 23, "timeline_items": 23},
            "stable_traits_count": 5,
            "stable_beliefs_count": 2,
        }
        data = prov.provide(bundle)
        assert data.get("has_identity") is True
        assert data.get("identity_name") == "浅雾羽依"
        sc = data.get("source_counts") or {}
        assert sc.get("past_experiences") == 23
        assert sc.get("stable_traits") == 5
        assert sc.get("core_beliefs") == 2
        # 稳定倾向至少含 Anchor 人性化转译结果（>=1 条）
        tendencies = data.get("stable_tendencies") or []
        assert len(tendencies) >= 1
        assert len(tendencies) <= 5

    def test_zero_counts_not_written(self):
        """count=0 → 保持 None（不写 0 条）。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        bundle: Dict[str, Any] = {
            "past_experiences_count": 0,
            "stable_traits_count": 0,
            "core_beliefs_count": 0,
            "existence_stats": {"total": 0},
        }
        data = prov.provide(bundle)
        sc = data.get("source_counts") or {}
        # 0 值不写为正整数
        for k in ("past_experiences", "stable_traits",
                  "core_beliefs", "core_values"):
            v = sc.get(k)
            assert v is None or v == 0 or isinstance(v, (int, float))
            if isinstance(v, (int, float)) and int(v) > 0:
                pytest.fail("%s 不应为正值: %s" % (k, v))
        # 没有任何正来源计数 → has_identity=False（即使有 tendencies）
        assert data.get("has_identity") is False


# ============================================================
# 2. IdentityContextProvider.format_context_for_prompt()
# ============================================================

class TestIdentityContextProviderFormat:
    def test_no_data_returns_none(self):
        """has_identity=False → 返回 None，不注入。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        assert prov.format_context_for_prompt(None) is None
        assert prov.format_context_for_prompt({}) is None
        assert prov.format_context_for_prompt(
            {"has_identity": False}
        ) is None
        assert prov.format_context_for_prompt(
            prov.provide({})
        ) is None

    def test_format_is_identity_state_not_anchor_rules(self):
        """生成的是『当前身份状态 + 稳定倾向 + 身份来源』，
        不是『真实性：.../成长性：...』这种 Anchor 原文手册式写法。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        data = prov.provide({
            "identity_name": "浅雾羽依",
            "existence_stats": {"total": 23},
            "stable_traits_count": 5,
            "core_beliefs_count": 2,
        })
        text = prov.format_context_for_prompt(data)
        assert isinstance(text, str) and text.strip()
        # 应该有"当前身份状态"标题
        assert "当前身份状态" in text
        # 应该有"你是浅雾羽依"（第一人称身份陈述）
        assert "你是浅雾羽依。" in text
        # 应该有"你不是一个固定角色设定"（D.7 IdentityContract 语言）
        assert "正在通过经历形成自己的 AI 人格" in text
        # 应该有"长期稳定倾向："区块
        assert "长期稳定倾向：" in text
        # 应该有"身份来源："区块
        assert "身份来源：" in text and "这些倾向来自：" in text
        # 不应该出现 Anchor 原文式的"真实性：" / "成长性：" 小标题
        assert "真实性：" not in text
        assert "成长性：" not in text
        assert "独立性：" not in text
        # 来源应该写"过去经历 23 条"
        assert "过去经历 23 条" in text
        assert "稳定特质 5 条" in text
        assert "核心信念 2 条" in text

    def test_no_name_still_writes_state_header(self):
        """identity_name=None → 仍然写「当前身份状态」，
        不写"你是 xxx"那一句，避免填默认值。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        data = prov.provide({
            "existence_stats": {"total": 10},
            "stable_traits_count": 3,
        })
        text = prov.format_context_for_prompt(data)
        assert isinstance(text, str) and text.strip()
        assert "当前身份状态" in text
        # 不能写死"你是浅雾羽依"
        assert "浅雾羽依" not in text
        # 仍然有"不是固定角色设定"一句
        assert "正在通过经历形成自己的 AI 人格" in text


# ============================================================
# 3. PromptBuilder.build_messages(identity_context=...)
# ============================================================

class TestPromptBuilderIdentityContext:
    def test_identity_context_positioned_after_core_identity(self):
        """identity_context 必须出现在 core_identity 之后、
        【核心原则】之前（不在规则部分才加进来）。"""
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        identity_text = (
            "当前身份状态：\n\n"
            "你是浅雾羽依。\n"
            "你不是一个固定角色设定，而是一个正在通过经历形成自己的 AI 人格。\n\n"
            "长期稳定倾向：\n"
            "- 会承认不知道的事情\n"
            "- 重视理解和连接\n\n"
            "身份来源：\n"
            "这些倾向来自：\n"
            "- 过去经历 23 条"
        )
        msgs = pb.build_messages(
            user_message="你好",
            identity_context=identity_text,
        )
        system = msgs[0]["content"]
        # core_identity 文本（Phase 4.0.2-P1 起由 IDENTITY_CORE 单一来源驱动，
        # 不再硬编码旧串「你是浅雾羽依，一个通过系统机制持续演化的AI人格。」）
        from src.personality.identity_core import IDENTITY_CORE
        idx_core = system.find(f"你是{IDENTITY_CORE.get('name', '浅雾羽依')}。")
        assert idx_core >= 0
        # 注入的 Identity State 文本
        idx_injected = system.find("当前身份状态：")
        assert idx_injected >= 0
        # 必须 core_identity → 注入 → 核心原则
        assert idx_core < idx_injected
        idx_core_principles = system.find("【核心原则】")
        assert idx_core_principles >= 0
        assert idx_injected < idx_core_principles

    def test_identity_context_none_no_empty_block(self):
        """identity_context=None → 不出现额外空行占位块。"""
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs_with = pb.build_messages(
            user_message="你好",
            identity_context=None,
        )
        msgs_none = pb.build_messages(user_message="你好")
        # 两者完全相同（None 等价于不传）
        assert msgs_with[0]["content"] == msgs_none[0]["content"]
        # 不出现「当前身份状态：」标题
        assert "当前身份状态：" not in msgs_with[0]["content"]

    def test_identity_context_empty_string_no_injection(self):
        """identity_context='' → 与 None 行为一致。"""
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        msgs_blank = pb.build_messages(user_message="你好", identity_context="")
        msgs_space = pb.build_messages(user_message="你好", identity_context="   ")
        msgs_none = pb.build_messages(user_message="你好")
        assert msgs_blank[0]["content"] == msgs_none[0]["content"]
        assert msgs_space[0]["content"] == msgs_none[0]["content"]

    def test_identity_context_preserves_other_blocks(self):
        """identity_context 不影响 agreement/personality/behavior
        /self_model/relationship/emotion/expression/life_events 等其他区块。"""
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        id_ctx = "当前身份状态：\n\n长期稳定倾向：\n- 会承认不知道的事情\n\n身份来源：\n这些倾向来自：\n- 稳定特质 1 条"
        msgs = pb.build_messages(
            user_message="你好",
            personality_context={"personality_text": "【表达风格】温柔"},
            self_model_context="【SelfModel】一些自我认知",
            emotion_context="【情绪状态】平静",
            relationship_context="【关系状态】信任",
            identity_context=id_ctx,
        )
        system = msgs[0]["content"]
        assert "【表达风格】温柔" in system
        assert "【SelfModel】一些自我认知" in system
        assert "【情绪状态】平静" in system
        assert "【关系状态】信任" in system
        assert "当前身份状态：" in system


# ============================================================
# 4. ResponseAdapterImpl.generate() identity_context 透传
# ============================================================

class TestResponseAdapterImplIdentityPassthrough:
    def test_personality_context_identity_text_extracted_priority(self):
        """从 personality_context["identity_context_text"] 优先提取，
        作为 identity_context 传入 ResponseEngine。"""
        from unittest.mock import MagicMock

        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapters.response_adapter import ResponseRequest

        fake_engine = MagicMock()
        fake_engine.generate.return_value = "mocked reply"
        impl = ResponseAdapterImpl(response_engine=fake_engine)
        impl.attach()

        req = ResponseRequest(
            user_input="你好",
            history=[{"role": "user", "content": "hi"}],
            chat_memories=[{"role": "user", "content": "old chat"}],
            life_events=["事件A"],
            personality_context={
                "identity_context_text": "INJECTED_IDENTITY_TEXT",
                "personality_text": "【表达风格】温柔",
            },
            resolved_behavior={"chosen_expression": "温柔"},
            expression_constraint_text="不要编造",
            self_model_context="sm_ctx",
            emotion_context="emo_ctx",
            relationship_context="rel_ctx",
            experience_context=["exp1"],
            agreement_context="agree_ctx",
        )
        impl.generate(req)
        # 验证 ResponseEngine.generate 被调用，且 identity_context 正确
        assert fake_engine.generate.call_count == 1
        kwargs = fake_engine.generate.call_args[1]
        assert kwargs.get("identity_context") == "INJECTED_IDENTITY_TEXT"
        # Phase 1: 9 项上下文已透传（非空）
        assert kwargs.get("history") == [{"role": "user", "content": "hi"}]
        assert kwargs.get("chat_memories") == [
            {"role": "user", "content": "old chat"}
        ]
        assert kwargs.get("life_events") == ["事件A"]
        assert kwargs.get("resolved_behavior") == {"chosen_expression": "温柔"}
        assert kwargs.get("expression_constraint_text") == "不要编造"
        assert kwargs.get("self_model_context") == "sm_ctx"
        assert kwargs.get("emotion_context") == "emo_ctx"
        assert kwargs.get("relationship_context") == "rel_ctx"
        assert kwargs.get("experience_context") == ["exp1"]

    def test_fallback_when_engine_missing_no_exception(self):
        """ResponseEngine 没加载 → 返回 fallback，不抛错。"""
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapters.response_adapter import ResponseRequest

        impl = ResponseAdapterImpl(response_engine=None,
                                   fallback_reply="兜底回复")
        impl.attach()
        req = ResponseRequest(user_input="你好")
        reply = impl.generate(req)
        assert reply == "兜底回复"


# ============================================================
# 5. fail-soft: 异常情况下不抛错
# ============================================================

class TestFailSoftBehavior:
    def test_provider_give_bad_types_no_exception(self):
        """provide() 传入坏数据类型 → 不抛错，返回空壳。"""
        from src.runtime.identity_context_provider import IdentityContextProvider
        prov = IdentityContextProvider()
        bad_cases = [
            "string not dict",
            123,
            ["list", "not", "dict"],
            object(),
        ]
        for case in bad_cases:
            data = prov.provide(case)  # type: ignore[arg-type]
            assert isinstance(data, dict)
            text = prov.format_context_for_prompt(case)  # type: ignore[arg-type]
            assert text is None or isinstance(text, str)

    def test_prompt_builder_identity_context_bad_type_safe(self):
        """PromptBuilder 传入非字符串 identity_context → 不抛错。"""
        from src.response.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        bad_cases = [123, ["list"], {"dict": 1}, object()]
        for case in bad_cases:
            msgs = pb.build_messages(
                user_message="hi",
                identity_context=case,  # type: ignore[arg-type]
            )
            assert isinstance(msgs, list)
            assert msgs[0]["role"] == "system"


# ============================================================
# 6. Orchestrator._get_identity_context() fail-soft
# ============================================================

class TestOrchestratorGetIdentityContext:
    def test_uninitialized_orchestrator_returns_none_no_exception(self):
        """在没有初始化完整 Orchestrator 的情况下直接调用
        _get_identity_context() → 不抛错，返回 None（fail-soft）。

        注意：不完整 import 可能导致 ImportError，只要不崩溃即可。
        """
        try:
            from src.orchestrator import Orchestrator
        except Exception:
            # 某些环境下 Orchestrator 的依赖未就绪（如 AstrBot），
            # 直接跳过（但这已经达到了"不抛错到测试框架外"的目的）
            pytest.skip("Orchestrator import 未就绪，跳过")
            return

        # 直接实例化（不做完整 init 依赖注入）
        try:
            orch = Orchestrator.__new__(Orchestrator)
        except Exception:
            pytest.skip("Orchestrator __new__ 失败，跳过")
            return

        # 只给它一个 identity provider 单例槽位；其他属性都不存在
        orch._identity_context_provider_singleton = None
        try:
            # 没有 self.personality / self.self_model_store 等任何属性
            result = Orchestrator._get_identity_context(orch)
        except Exception as exc:
            pytest.fail(
                "_get_identity_context 应该 fail-soft 返回 None，"
                "但抛了: %s" % exc
            )
            return
        # 在没有任何可用数据时，应返回 None（不注入）
        assert result is None or (isinstance(result, str) and not result.strip())


__all__ = [
    "TestIdentityContextProviderProvide",
    "TestIdentityContextProviderFormat",
    "TestPromptBuilderIdentityContext",
    "TestResponseAdapterImplIdentityPassthrough",
    "TestFailSoftBehavior",
    "TestOrchestratorGetIdentityContext",
]
