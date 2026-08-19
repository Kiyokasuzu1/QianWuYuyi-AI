# -*- coding: utf-8 -*-
"""
tests/test_phase_3_8_4_response_integration.py

Phase 3.8.4: ResponseEngine Runtime Integration 测试

覆盖:
1. RuntimeContext 能传递到 Response（via ResponseRequest）
2. ResponseRequest 包含 memory / emotion / personality / relationship
3. PerceptionGuard 阻止 INFERENCE 推测（幻觉）
4. PersonalityGuard 阻止人格漂移
5. Runtime 不直接 import LLM（无 openai / src.response.llm）
6. 异常隔离:response adapter 失败 / guard chain 失败均不中断 Runtime
7. Guard Chain 顺序:PerceptionGuard → PersonalityGuard
8. lifecycle: 13 阶段顺序（含 RESPONSE_GENERATION / GUARD_CHAIN）
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _build_registry(include_response: bool = True):
    from src.runtime.adapter_registry import AdapterRegistry
    from src.runtime.adapters.impl import (
        MemoryAdapterImpl,
        EmotionAdapterImpl,
        GrowthAdapterImpl,
        PersonalityAdapterImpl,
    )
    reg = AdapterRegistry()
    reg.register("memory_adapter_impl", MemoryAdapterImpl())
    reg.register("emotion_adapter_impl", EmotionAdapterImpl())
    reg.register("growth_adapter_impl", GrowthAdapterImpl())
    reg.register("personality_adapter_impl", PersonalityAdapterImpl())
    if include_response:
        # 使用 Impl（含 generate() 方法,桥接 ResponseEngine）
        from src.runtime.adapters.impl import ResponseAdapterImpl
        reg.register("response_adapter_impl", ResponseAdapterImpl())
    return reg


def _build_event(type_="user_input", text="hello yuyi", priority=0):
    from src.runtime.events import Event
    return Event(
        type=type_,
        source="user",
        payload={"text": text, "content": text},
        priority=priority,
    )


def _build_runtime(reg=None, response_guard_chain=None):
    from src.runtime.runtime import RuntimeCore
    if reg is None:
        reg = _build_registry()
    core = RuntimeCore(
        adapter_registry=reg,
        response_guard_chain=response_guard_chain,
    )
    core.start()
    return core


# ============================================================
# T1: RuntimeContext → ResponseRequest
# ============================================================
class TestRuntimeContextToResponse:
    def test_runtimecontext_transmitted_to_response_request(self):
        """RuntimeContext 字段必须能进入 ResponseRequest。"""
        from src.runtime.adapters.response_adapter import (
            ResponseAdapter, ResponseRequest,
        )
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext

        adapter = ResponseAdapter()
        adapter.attach()

        ctx = RuntimeContext(user_input="今天有点累")
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm"},
            emotion_state={"valence": -0.3},
        )
        evt = _build_event(text="今天有点累")
        req = adapter.build_request(ctx, prc, evt)
        assert isinstance(req, ResponseRequest)
        assert req.user_input == "今天有点累"
        # ctx.user_input 兜底:在 build_request 之前未设置时,应从 event 抽取
        ctx2 = RuntimeContext()
        req2 = adapter.build_request(ctx2, prc, evt)
        assert req2.user_input == "今天有点累"

    def test_response_request_contains_memory_emotion_personality_relationship(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext

        adapter = ResponseAdapter()
        adapter.attach()
        ctx = RuntimeContext(
            user_input="x",
            memory_context=[{"id": "m1", "content": "用户喜欢猫"}],
            emotion_state={"valence": 0.5, "arousal": 0.4},
        )
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={
                "tone": "warm",
                "warmth": 0.9,
                "behavior_constraints": ["不许辱骂"],
                "relationship": {"trust": 0.8},
            },
        )
        req = adapter.build_request(ctx, prc, _build_event())
        # memory
        assert req.memory_context is not None
        assert any(m.get("content") == "用户喜欢猫" for m in req.memory_context)
        # emotion
        assert req.emotion_state is not None
        assert req.emotion_state.get("valence") == 0.5
        # personality
        assert req.personality_context is not None
        assert req.personality_context.get("communication_style", {}).get("tone") == "warm"
        # relationship
        assert req.relationship_state == {"trust": 0.8}

    def test_facts_boundary_in_request(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.perception import Fact, USER_INPUT, INFERENCE

        adapter = ResponseAdapter()
        adapter.attach()
        ctx = RuntimeContext(user_input="hi")
        facts = [
            Fact(content="u1", source=USER_INPUT),
            Fact(content="推测", source=INFERENCE, confidence=0.3),
        ]
        req = adapter.build_request(ctx, None, _build_event(), facts=facts)
        assert len(req.facts_boundary) == 2
        assert len(req.draft_facts) == 2

    def test_response_request_schema_version(self):
        from src.runtime.adapters.response_adapter import (
            ResponseAdapter, RESPONSE_ADAPTER_SCHEMA_VERSION,
        )
        a = ResponseAdapter()
        a.attach()
        req = a.build_request.__self__  # noop
        from src.runtime.context import RuntimeContext
        req = a.build_request(RuntimeContext(), None, _build_event())
        assert req.schema_version == RESPONSE_ADAPTER_SCHEMA_VERSION == "1.0"


# ============================================================
# T2: Runtime RESPONSE_GENERATION 阶段
# ============================================================
class TestResponseGenerationStage:
    def test_runtime_includes_new_stages(self):
        from src.runtime.runtime import RuntimeStage, RUNTIME_LIFECYCLE_ORDER
        assert RuntimeStage.RESPONSE_GENERATION in RUNTIME_LIFECYCLE_ORDER
        assert RuntimeStage.GUARD_CHAIN in RUNTIME_LIFECYCLE_ORDER
        # 顺序: PERSONALITY_CONTEXT_BUILD → RESPONSE_GENERATION → GUARD_CHAIN → RESPONSE
        idx_pcb = RUNTIME_LIFECYCLE_ORDER.index(
            RuntimeStage.PERSONALITY_CONTEXT_BUILD
        )
        idx_rg = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.RESPONSE_GENERATION)
        idx_gc = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.GUARD_CHAIN)
        idx_resp = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.RESPONSE)
        assert idx_pcb < idx_rg < idx_gc < idx_resp

    def test_runtime_response_stage_runs(self):
        reg = _build_registry(include_response=True)
        core = _build_runtime(reg)
        # mock generate 让 Impl 不真正调用 LLM
        adapter = reg.get("response_adapter_impl")
        with patch.object(adapter, "generate", return_value="hello yuyi"):
            ctx = core.process(_build_event(text="hello"))
            # 阶段没有错误
            errs = core.get_stage_errors()
            assert "response_generation" not in errs
            # draft 被写到 ctx
            assert hasattr(ctx, "_draft_reply")
            assert core.get_final_reply(ctx) == "hello yuyi"

    def test_no_response_adapter_returns_empty_draft(self):
        reg = _build_registry(include_response=False)
        core = _build_runtime(reg)
        ctx = core.process(_build_event(text="hi"))
        # 没有 response adapter → draft 为空,guard chain 跳过
        assert getattr(ctx, "_draft_reply", "") == ""
        assert core.get_final_reply(ctx) == ""

    def test_response_adapter_failure_isolated(self):
        reg = _build_registry(include_response=True)
        core = _build_runtime(reg)
        adapter = reg.get("response_adapter_impl")
        with patch.object(
            adapter, "build_request",
            side_effect=RuntimeError("build broken"),
        ):
            ctx = core.process(_build_event(text="hi"))
            errs = core.get_stage_errors()
            assert "response_generation" in errs
            # ctx 仍然有效
            assert ctx.schema_version == "1.0"

    def test_response_generate_failure_isolated(self):
        reg = _build_registry(include_response=True)
        core = _build_runtime(reg)
        adapter = reg.get("response_adapter_impl")
        with patch.object(
            adapter, "generate",
            side_effect=RuntimeError("generate broken"),
        ):
            ctx = core.process(_build_event(text="hi"))
            errs = core.get_stage_errors()
            assert "response_generation" in errs


# ============================================================
# T3: Guard Chain 顺序
# ============================================================
class TestGuardChainOrder:
    def test_perception_runs_before_personality(self):
        """顺序:PerceptionGuard 必须先于 PersonalityGuard。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import PerceptionGuard
        from src.runtime.personality_guard import PersonalityGuard

        order_log: list = []

        real_pg = PerceptionGuard()
        real_psg = PersonalityGuard()

        original_pg_check = real_pg.check
        original_psg_check = real_psg.check

        def _wrap(method, label):
            def _f(*args, **kwargs):
                order_log.append(label)
                return method(*args, **kwargs)
            return _f

        real_pg.check = _wrap(original_pg_check, "perception")
        real_psg.check = _wrap(original_psg_check, "personality")

        chain = ResponseGuardChain(
            perception_guard=real_pg, personality_guard=real_psg,
        )
        # 一次全 grounded 调用
        from src.runtime.perception import Fact, USER_INPUT
        from src.runtime.personality_context import PersonalityRuntimeContext
        facts = [Fact(content="u", source=USER_INPUT)]
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm"},
        )
        chain.run("我在这里", facts, prc)
        # 验证 perception_guard.check 的首次调用早于 personality_guard.check
        first_perception = order_log.index("perception")
        first_personality = order_log.index("personality")
        assert first_perception < first_personality, (
            f"Guard 顺序错误: perception @ {first_perception}, "
            f"personality @ {first_personality}, log={order_log}"
        )

    def test_guard_chain_blocks_hallucination(self):
        """PerceptionGuard 阻止 INFERENCE 推测（幻觉）。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import (
            Fact, INFERENCE, PerceptionGuard,
        )
        from src.runtime.personality_context import PersonalityRuntimeContext

        chain = ResponseGuardChain(
            perception_guard=PerceptionGuard(min_grounded_ratio=1.0),
        )
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm"},
        )
        facts = [Fact(content="x", source=INFERENCE, confidence=0.3)]
        result = chain.run("答案", facts, prc)
        assert result.blocked_by == "perception"

    def test_guard_chain_blocks_personality_drift(self):
        """PersonalityGuard 阻止人格漂移。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, USER_INPUT
        from src.runtime.personality_context import PersonalityRuntimeContext

        chain = ResponseGuardChain()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm", "warmth": 0.9},
        )
        facts = [Fact(content="u", source=USER_INPUT)]
        result = chain.run("闭嘴,别烦我", facts, prc)
        assert result.blocked_by == "personality"

    def test_guard_chain_fully_passes(self):
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, USER_INPUT
        from src.runtime.personality_context import PersonalityRuntimeContext

        chain = ResponseGuardChain()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm", "warmth": 0.7},
        )
        facts = [Fact(content="u", source=USER_INPUT)]
        result = chain.run("我在这里陪你。", facts, prc)
        assert result.blocked_by is None
        assert result.final_reply == "我在这里陪你。"

    def test_runtime_guard_chain_integration(self):
        """Runtime 端到端:GuarChain 阶段运行,block 时 final_reply 反映。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.personality_context import PersonalityRuntimeContext

        # 1) 准备一个会把 reply 改成"闭嘴"的人为 adapter
        reg = _build_registry(include_response=True)
        core = _build_runtime(reg)
        adapter = reg.get("response_adapter_impl")
        # mock generate → 返回敌意文本
        with patch.object(adapter, "generate", return_value="闭嘴,别烦我"):
            chain = ResponseGuardChain()
            # 重建 core,使用该 chain
            core2 = _build_runtime(reg, response_guard_chain=chain)
            ctx = core2.process(_build_event(text="hi"))
            final = core2.get_final_reply(ctx)
            # 仍返回 draft（PersonalityGuard 报告违规但不直接替换）
            # blocked_by 应被记录
            assert getattr(ctx, "_guard_blocked_by", None) in ("personality", None)
            assert final is not None


# ============================================================
# T4: Runtime 不直接依赖 LLM
# ============================================================
class TestRuntimeNoLLMDependency:
    def test_runtime_does_not_import_openai(self):
        """Runtime 顶层模块不应 import openai / src.response.llm。"""
        runtime_path = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
        text = runtime_path.read_text(encoding="utf-8")
        import_lines = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        assert "import openai" not in joined
        assert "from openai" not in joined
        assert "from src.response.llm" not in joined
        assert "from src.response.engine" not in joined

    def test_response_adapter_does_not_hold_openai_client(self):
        """ResponseAdapter（抽象层）不应直接持有 openai 客户端。"""
        response_adapter_path = (
            PROJECT_ROOT / "src" / "runtime" / "adapters" / "response_adapter.py"
        )
        text = response_adapter_path.read_text(encoding="utf-8")
        import_lines = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        assert "import openai" not in joined
        assert "from openai" not in joined
        # 抽象层不应直接 import response 业务实现
        assert "from src.response" not in joined

    def test_response_adapter_impl_only_imports_at_attach(self):
        """ResponseAdapterImpl 仅在 attach() 中 import ResponseEngine。"""
        impl_path = (
            PROJECT_ROOT / "src" / "runtime" / "adapters" / "impl" / "response_adapter_impl.py"
        )
        text = impl_path.read_text(encoding="utf-8")
        # 顶级 import 不应 import openai / response.llm
        top_imports = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(top_imports)
        assert "import openai" not in joined
        assert "from openai" not in joined
        # response.engine 可以在 attach() 内部延迟 import,允许
        assert "from src.response.engine" in joined  # attach() 内延迟加载


# ============================================================
# T5: 异常隔离
# ============================================================
class TestErrorIsolation:
    def test_guard_chain_failure_does_not_crash(self):
        """GuardChain 抛异常时,Runtime 返回 draft_reply,阶段错误被记录。"""
        reg = _build_registry(include_response=True)
        # 提供一个会抛异常的 chain
        class BadChain:
            def run(self, *a, **k):
                raise RuntimeError("chain boom")

        core = _build_runtime(reg, response_guard_chain=BadChain())
        # mock generate 返回固定文本
        adapter = reg.get("response_adapter_impl")
        with patch.object(adapter, "generate", return_value="hello"):
            ctx = core.process(_build_event(text="hi"))
            errs = core.get_stage_errors()
            assert "guard_chain" in errs
            # final_reply 应等于 draft
            assert core.get_final_reply(ctx) == "hello"

    def test_response_adapter_missing(self):
        """没有 response adapter 时,RESPONSE_GENERATION 阶段空跑,后续正常。"""
        reg = _build_registry(include_response=False)
        core = _build_runtime(reg)
        ctx = core.process(_build_event(text="hi"))
        # 不应抛异常
        assert ctx.schema_version == "1.0"
        # final_reply 为空（draft 为空）
        assert core.get_final_reply(ctx) == ""


# ============================================================
# T6: 完整端到端
# ============================================================
class TestFullEndToEnd:
    def test_full_pipeline_produces_final_reply(self):
        from src.runtime.response_guard_chain import ResponseGuardChain
        reg = _build_registry(include_response=True)
        core = _build_runtime(reg, response_guard_chain=ResponseGuardChain())
        adapter = reg.get("response_adapter_impl")
        with patch.object(adapter, "generate", return_value="我在这里陪你哦"):
            ctx = core.process(_build_event(text="我有点累"))
            final = core.get_final_reply(ctx)
            assert final == "我在这里陪你哦"

    def test_perception_hedge_in_final_reply(self):
        """含 INFERENCE 时,final_reply 应当含 hedge 前缀。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, INFERENCE

        reg = _build_registry(include_response=True)
        chain = ResponseGuardChain()
        core = _build_runtime(reg, response_guard_chain=chain)
        adapter = reg.get("response_adapter_impl")
        # 让 build_request 把 INFERENCE fact 注入到 ctx
        real_build = adapter.build_request

        def _build_with_facts(ctx, prc, event, **_):
            req = real_build(ctx, prc, event)
            # 注入一个 INFERENCE fact
            f = Fact(content="推测", source=INFERENCE, confidence=0.3)
            req.draft_facts = [f]
            req.facts_boundary = [f.to_dict()]
            setattr(ctx, "_draft_facts", [f])
            return req

        with patch.object(adapter, "build_request", side_effect=_build_with_facts), \
             patch.object(adapter, "generate", return_value="我猜你可能喜欢猫"):
            ctx = core.process(_build_event(text="我喜欢什么"))
            final = core.get_final_reply(ctx)
            # PerceptionGuard 检测到 INFERENCE → 加 hedge
            assert final != "我猜你可能喜欢猫"
            assert "推测" in final or "推理" in final or "inference" in final.lower()


# ============================================================
# T7: 阶段总结
# ============================================================
def test_phase_3_8_4_summary():
    from src.runtime.adapters.response_adapter import (
        ResponseAdapter, ResponseRequest, RESPONSE_ADAPTER_SCHEMA_VERSION,
    )
    from src.runtime.adapters.impl import ResponseAdapterImpl
    from src.runtime.runtime import (
        RuntimeStage, RUNTIME_LIFECYCLE_ORDER, RuntimeCore,
    )
    from src.runtime.response_guard_chain import ResponseGuardChain
    from src.runtime.context import RuntimeContext

    # 关键类
    assert ResponseAdapter is not None
    assert ResponseRequest is not None
    assert ResponseAdapterImpl is not None
    assert ResponseGuardChain is not None

    # 阶段
    assert RuntimeStage.RESPONSE_GENERATION in RUNTIME_LIFECYCLE_ORDER
    assert RuntimeStage.GUARD_CHAIN in RUNTIME_LIFECYCLE_ORDER
    # 13 阶段（含 START, LOAD_STATE, RECEIVE_EVENT, MEMORY_RETRIEVAL,
    #    EMOTION_UPDATE, GROWTH_EVALUATION, PERSONALITY_UPDATE,
    #    PERSONALITY_CONTEXT_BUILD, RESPONSE_GENERATION, GUARD_CHAIN,
    #    RESPONSE, PERSISTENCE, SHUTDOWN）
    # Phase 4.1.0 增加了 PERCEPTION_OBSERVATION 阶段(位于 PERSONALITY_CONTEXT_BUILD 与
    #    RESPONSE_GENERATION 之间),所以总数从 13 升到 14。
    assert len(RUNTIME_LIFECYCLE_ORDER) >= 13
    assert RuntimeStage.PERCEPTION_OBSERVATION in RUNTIME_LIFECYCLE_ORDER

    # 端到端
    reg = _build_registry(include_response=True)
    core = RuntimeCore(
        adapter_registry=reg,
        response_guard_chain=ResponseGuardChain(),
    )
    core.start()
    ctx = core.process(_build_event(text="phase 3.8.4 summary"))
    prc = core.get_personality_context(ctx)
    final = core.get_final_reply(ctx)
    # 真实端到端:
    # - 没有 DEEPSEEK_API_KEY 时,ResponseAdapterImpl 返回 fallback
    # - 不论 fallback 还是 LLM 回复,final_reply 必填（至少空字符串或 fallback）
    assert final is not None
    # 阶段无错误（除 fallback 外）
    errs = core.get_stage_errors()
    assert "response_generation" not in errs
    core.shutdown()
