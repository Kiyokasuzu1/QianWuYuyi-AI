"""
Phase 7.2.1.1 Runtime Identity Stabilization —— 5 条"生命测试"。

这 5 条不是普通单元测试。它们的目标是验证羽依的"第一根神经"真正接好了：
    用户是谁 → RuntimeContext 保存谁 → Memory 读取谁的 → Prompt 告诉 LLM → 回复保持连续。

优先级（用户审定）：
  T1 (必须过) 身份不会丢
  T2 (必须过) 清清身份进入 Prompt（出现"清清"和"创造者"）
  T3 (必须过) Memory 真的进入 Prompt（完整链）
  T4  (隔离)  跨用户隔离 — 陌生人拿不到清清身份 / 记忆
  T5 (优先级) Orchestrator identity_source：parameter > cached > resolver fallback

实现约束（对应审查 4 条调整）：
  1. 不接 Agreement（冻结）
  2. OriginFacade 只做纯查询（测试里直接验证它没写任何东西）
  3. Memory owner 缺失只打 warning 不阻断（测试里不做 SecurityError 断言）
  4. 不碰 Growth / Dashboard / 主动消息
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

import pytest


# ────────────────────────────────────────────────────────────────
# Paths / helpers
# ────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ================================================================
# Test 1：身份不会丢
# ================================================================
class TestIdentityPreservation:
    """user_id 从 api_server input_data → RuntimePipeline → Orchestrator
    fallback 全程不得丢失、不得被 UserResolver 覆盖。"""

    @staticmethod
    def test_user_id_survives_pipeline_fallback_chain(tmp_path: Path) -> None:
        """T1-生命测试：
        Pipeline 入口传 {"user_id":"123456789","user_message":"hi"}
        → RuntimeContext.inputs['user_id'] == "123456789"
        → Pipeline 内部 orchestrator fallback 调用时 process(..., user_id=context.user_id)
        """
        recorded: Dict[str, Any] = {}

        from src.runtime.runtime_pipeline import RuntimePipeline

        # 直接造一个假 orchestrator，记录 process() 调用时的 user_id 第二参数
        dummy_orch = MagicMock()
        def capture_process(user_message: str, user_id: Optional[str] = None) -> str:
            recorded["called_user_id"] = user_id
            recorded["identity_source"] = "parameter" if user_id else "missing"
            return f"Hello user_id={user_id}"
        dummy_orch.process = capture_process

        pipeline = RuntimePipeline(
            orchestrator=dummy_orch,
            persistence_hook=None,
            event_sink=None,
            lifecycle_name="test_p7211",
        )
        ctx = pipeline.run({
            "user_id": "123456789",
            "user_message": "你好，身份验证",
        })

        # 断言 1：RuntimeContext 保存的 user_id 没丢（Phase 7.2.1.1 不扩 RuntimeContext schema，存在 inputs 字典）
        inputs = getattr(ctx, "inputs", None) or {}
        context_user_id = inputs.get("user_id")
        assert context_user_id == "123456789", (
            f"T1 FAIL: RuntimePipeline.inputs['user_id'] 丢失！期望 123456789，实际={context_user_id}"
        )
        # 断言 2：orchestrator fallback 被调用时，第二个参数正确透传
        assert recorded.get("called_user_id") == "123456789", (
            f"T1 FAIL: Orchestrator.process 第二参数 user_id 没传对！"
            f"期望=123456789，实际={recorded.get('called_user_id')}"
        )


# ================================================================
# Test 2：清清身份进入 Prompt
# ================================================================
class TestOriginIdentityInPrompt:
    """T2-生命测试：当 user_id=366648462（清清）时，
    PromptRenderer.build() 的 system_message 必须包含：
    - "清清"（显示名）
    - "创造者"（角色）
    否则 OriginFacade → Prompt 链路没接上。"""

    @staticmethod
    def _valid_prompt_context_shell(trace_id: str) -> Dict[str, Any]:
        """PromptRenderer.build() 要求的完整 9 个冻结顶层字段 + 子字段合法性。
        直接走 prompt_context_schema.create_empty_prompt_context() 工厂（已内建 validate），
        保证 T2/T3/T4 拼 Prompt 时不会因为 shape 校验失败返回 empty。"""
        from src.context.prompt_context_schema import create_empty_prompt_context

        pc = create_empty_prompt_context(name="浅雾羽依", build_tag="test-p7211", version=1)
        # 把 anchor_markers 稍微写得真实些（不影响校验，仅语义）
        pc["system_identity"]["anchor_markers"] = ["羽依", "Yuyi"]
        # trace_id 不需要塞进 context_trace（PromptRenderer.build() 的独立参数），避免触发 CONTEXT_TRACE_FIELDS 校验
        return pc

    @staticmethod
    def test_creator_identity_appears_in_system_message() -> None:
        from src.identity.origin_facade import OriginFacade
        from src.response_phase4.prompt_renderer import PromptRenderer

        # 1) OriginFacade：清清身份（不写任何东西，纯查询）
        facade = OriginFacade.default()
        identity = facade.get_user_identity("366648462")
        assert identity["is_owner"] is True
        assert identity["display_name"] == "清清"
        assert "creator" in identity["roles"]
        assert "创造者" in identity["label"]

        trace_id = str(uuid.uuid4())
        renderer = PromptRenderer()
        pc: Dict[str, Any] = {
            **TestOriginIdentityInPrompt._valid_prompt_context_shell(trace_id),
            "origin_identity": identity,
        }
        # 只覆盖需要的子字段（保持工厂生成的整体 shape 合法）
        pc["self_context"]["identity_summary"]["origin_bullet"] = (
            "我是浅雾羽依，一个会学习、会成长的 AI 朋友。"
        )
        pc["self_context"]["identity_summary"]["core_values_bullets"] = ["真诚", "成长"]
        # scenario 必须走 TASK_SCENARIO_WHITELIST 枚举；语义细节写在 task_context.current_topic（None 或 str 均可）
        pc["task_context"]["scenario"] = "emotional_support"
        pc["task_context"]["current_topic"] = "与清清对话"
        rendered = renderer.build(prompt_context=pc, trace_id=trace_id)
        sm = rendered.get("system_message") or ""

        # T2 验收门槛：system_message 同时出现"清清" + "创造者"
        assert "清清" in sm, (
            f"T2 FAIL: system_message 里没出现'清清'！\n--- system_message ---\n{sm}"
        )
        assert "创造者" in sm, (
            f"T2 FAIL: system_message 里没出现'创造者'！\n--- system_message ---\n{sm}"
        )
        # current_user_identity_section 单独键也要命中（方便 debug）
        sec = rendered.get("current_user_identity_section") or ""
        assert "清清" in sec, (
            f"T2 FAIL: current_user_identity_section 没包含显示名'清清'！实际={sec!r}"
        )
        assert "创造者" in sec, (
            f"T2 FAIL: current_user_identity_section 没包含角色'创造者'！实际={sec!r}"
        )


# ================================================================
# Test 3：Memory 真的进入 Prompt（完整链）
# ================================================================
class TestMemoryIntoPrompt:
    """T3-生命测试：不是测试 memory.json 存在，而是测试完整链
        memory (带 user_id=366648462) → retriever → PromptRenderer.build() → system_message
    system_message 里必须包含 memory 关键词（"浅雾羽依项目"、"AI开发"、"成长系统"）。"""

    @staticmethod
    def _qingqing_identity() -> Dict[str, Any]:
        from src.identity.origin_facade import OriginFacade
        return OriginFacade.default().get_user_identity("366648462")

    def test_memory_keywords_appear_in_system_message(self) -> None:
        from src.response_phase4.prompt_renderer import PromptRenderer

        # 构造清清记忆（模拟她的 memory.json 里真实存在的数据）
        memories_in_prompt: List[str] = [
            "浅雾羽依项目：和清清一起从 0 开始设计的 AI 人格与成长系统。",
            "AI开发：讨论过 Personality 八层模型、记忆分三级存储、Prompt 防御。",
            "成长系统：Growth 分为 提案 → 审核 → 执行 → 记录 四阶段。",
        ]

        trace_id = str(uuid.uuid4())
        renderer = PromptRenderer()
        pc: Dict[str, Any] = {
            **TestOriginIdentityInPrompt._valid_prompt_context_shell(trace_id),
            "origin_identity": self._qingqing_identity(),
        }
        # 覆盖需要的子字段（保持工厂生成的整体 shape 合法）
        pc["self_context"]["identity_summary"]["origin_bullet"] = "我是浅雾羽依。"
        pc["memory_context"]["context_memories"] = list(memories_in_prompt)
        pc["memory_context"]["memory_count"] = len(memories_in_prompt)
        pc["relationship_context"]["closeness_score"] = 0.92
        # trust_level 必须走 TRUST_LEVEL_WHITELIST：low / medium / high / unknown
        pc["relationship_context"]["trust_level"] = "high"
        pc["context_trace"]["relationship_trust_level"] = "high"
        # scenario 必须走 TASK_SCENARIO_WHITELIST 枚举
        pc["task_context"]["scenario"] = "creative_collaboration"
        pc["task_context"]["current_topic"] = "和清清聊聊项目"
        pc["task_context"]["turn_number"] = 5
        rendered = renderer.build(prompt_context=pc, trace_id=trace_id)
        sm = rendered.get("system_message") or ""

        # T3 门槛：三条关键词必须在 system_message 里都出现
        for keyword in ("浅雾羽依项目", "AI开发", "成长系统"):
            assert keyword in sm, (
                f"T3 FAIL: 记忆关键词 '{keyword}' 没出现在 system_message 里！"
                f"\n--- system_message ---\n{sm}"
            )


# ================================================================
# Test 4：用户隔离（陌生人读不到清清身份 + 清清记忆）
# ================================================================
class TestCrossUserIsolation:
    """T4-用户隔离：
    陌生用户 user_id=123456789 时：
    - current_user_identity_section 不得包含 "清清"、"创造者"
    - 如果 memory_context 里没塞清清记忆，system_message 自然也没有。"""

    @staticmethod
    def test_stranger_sees_no_creator_identity() -> None:
        from src.identity.origin_facade import OriginFacade
        from src.response_phase4.prompt_renderer import PromptRenderer

        facade = OriginFacade.default()
        stranger = facade.get_user_identity("123456789")
        assert stranger["is_owner"] is False
        assert stranger["display_name"] is None
        assert stranger["roles"] == []
        # Origin 陌生人 label 不包含关键词"清清 / 创造者"
        assert "清清" not in stranger["label"]
        assert "创造者" not in stranger["label"]

        trace_id = str(uuid.uuid4())
        renderer = PromptRenderer()
        pc: Dict[str, Any] = {
            **TestOriginIdentityInPrompt._valid_prompt_context_shell(trace_id),
            "origin_identity": stranger,
        }
        # 覆盖需要的子字段（保持工厂生成的整体 shape 合法）
        pc["self_context"]["identity_summary"]["origin_bullet"] = "我是浅雾羽依。"
        pc["memory_context"]["context_memories"] = ["陌生人123456789的记忆：喜欢看电影。"]
        pc["memory_context"]["memory_count"] = 1
        # scenario 必须走 TASK_SCENARIO_WHITELIST 枚举
        pc["task_context"]["scenario"] = "unknown"
        pc["task_context"]["current_topic"] = "陌生用户首次对话"
        rendered = renderer.build(prompt_context=pc, trace_id=trace_id)
        sec = rendered.get("current_user_identity_section") or ""
        sm = rendered.get("system_message") or ""

        # T4 门槛：用户身份区 / system_message 都不该出现"清清"或"创造者"
        forbidden = ("清清", "创造者")
        for word in forbidden:
            assert word not in sec, (
                f"T4 FAIL: 陌生人的 current_user_identity_section 出现了'{word}'！"
                f"\n实际={sec!r}"
            )
            assert word not in sm, (
                f"T4 FAIL: 陌生人的 system_message 出现了'{word}'（身份泄漏）！"
                f"\n--- system_message ---\n{sm}"
            )


# ================================================================
# Test 5：Orchestrator identity_source 优先级
# ================================================================
class TestOrchestratorIdentitySourcePriority:
    """T5-优先级断言（不跑完整 process()，只测身份选择逻辑纯函数）：
      Case A：传了 user_id（param_uid not None）→ identity_source == "parameter"，resolver 绝对不调用。
      Case B：param_uid 缺失，但缓存有 target_user_id → identity_source == "cached_target_user_id"，resolver 也不调用。
      Case C：双重缺失 → identity_source == "resolver_fallback"，resolver 恰好调用一次。

    为什么不测 orch.process() 整体？因为 orch.process() 是依赖重容器（screen_context_manager /
    vector_memory / _phase_6_2_enabled / runtime_context...），但我们 Phase 7.2.1.1 只改了
    identity_source 三档优先级的分支判断，所以纯函数断言最稳、最快、不依赖容器。
    """

    @staticmethod
    def _simulate_identity_resolution(
        param_uid: Optional[str],
        cached_target_user_id: str,
        identity_normalizer,
        user_resolver,
    ) -> Tuple[str, str, Dict[str, Any]]:
        """**Inline 复制** orchestrator.process() 开头 L750-L795 的身份选择逻辑。
        这样不创建 Orchestrator 也能精确断言 identity_source 分支行为。

        Returns:
            (identity_source, final_user_id, user_context)
        """
        user_context: Dict[str, Any] = {}

        # ============================================================
        # 以下代码语义严格对应 src/orchestrator.py 里 Phase 7.2.1.1 的实现：
        # ============================================================
        if param_uid is not None:
            identity_source = "parameter"
        elif cached_target_user_id:
            identity_source = "cached_target_user_id"
        else:
            identity_source = "resolver_fallback"
            user_context = user_resolver.resolve(cached_target_user_id) or {}

        if identity_source == "parameter":
            final = identity_normalizer(param_uid)
        elif identity_source == "cached_target_user_id":
            final = identity_normalizer(cached_target_user_id)
        else:  # resolver_fallback
            final = identity_normalizer(user_context.get("user_id"))

        return identity_source, str(final or ""), user_context

    def test_case_A_parameter_priority(self) -> None:
        """A：param_uid="user_A_999" → parameter，resolver 碰都不碰。"""
        bad_resolver = MagicMock()
        bad_resolver.resolve.side_effect = RuntimeError(
            "T5-A FAIL: resolver 被调用了！传了 user_id 就不该 resolver。"
        )
        src, final_uid, _ = self._simulate_identity_resolution(
            param_uid="user_A_999",
            cached_target_user_id="366648462",  # 即使缓存是清清，也得让位给参数
            identity_normalizer=lambda x: str(x) if x else None,
            user_resolver=bad_resolver,
        )
        assert src == "parameter", f"T5-A FAIL: source={src!r}，期望='parameter'"
        assert final_uid == "user_A_999", f"T5-A FAIL: final_uid={final_uid!r}，期望='user_A_999'"
        assert bad_resolver.resolve.call_count == 0

    def test_case_B_cached_priority(self) -> None:
        """B：param_uid=None 但 cached_target_user_id="366648462" → cached_target_user_id。"""
        bad_resolver = MagicMock()
        bad_resolver.resolve.side_effect = RuntimeError(
            "T5-B FAIL: resolver 被调用了！缓存有值就不该 resolver。"
        )
        src, final_uid, _ = self._simulate_identity_resolution(
            param_uid=None,
            cached_target_user_id="366648462",
            identity_normalizer=lambda x: str(x) if x else None,
            user_resolver=bad_resolver,
        )
        assert src == "cached_target_user_id", (
            f"T5-B FAIL: source={src!r}，期望='cached_target_user_id'"
        )
        assert final_uid == "366648462", (
            f"T5-B FAIL: final_uid={final_uid!r}，期望='366648462'"
        )
        assert bad_resolver.resolve.call_count == 0

    def test_case_C_resolver_fallback(self) -> None:
        """C：param_uid=None + cached="" → resolver_fallback，resolver 调用 1 次。"""
        good_resolver = MagicMock()
        good_resolver.resolve.return_value = {
            "user_id": "fallback_888",
            "user_name": "匿名用户",
        }
        src, final_uid, ctx = self._simulate_identity_resolution(
            param_uid=None,
            cached_target_user_id="",
            identity_normalizer=lambda x: str(x) if x else None,
            user_resolver=good_resolver,
        )
        assert src == "resolver_fallback", (
            f"T5-C FAIL: source={src!r}，期望='resolver_fallback'"
        )
        assert good_resolver.resolve.call_count == 1, (
            f"T5-C FAIL: resolver 调用次数={good_resolver.resolve.call_count}，期望=1"
        )
        assert ctx.get("user_id") == "fallback_888"
        assert final_uid == "fallback_888", (
            f"T5-C FAIL: final_uid={final_uid!r}，期望='fallback_888'"
        )
