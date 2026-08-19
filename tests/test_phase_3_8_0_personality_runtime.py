# -*- coding: utf-8 -*-
"""
tests/test_phase_3_8_0_personality_runtime.py

Phase 3.8.0: Personality Runtime Enforcement 测试

覆盖:
1. PersonalityRuntimeContext 可以创建 / 序列化 / 抽取字段
2. RuntimeCore.process() 会生成 personality snapshot + personality context
3. 人格数据不会修改原人格系统（PersonalityResolver 仍是唯一权威）
4. PersonalityGuard 可发现明显违规（语气 / 行为 / 风格）
5. PerceptionGuard 与 PersonalityGuard 顺序正确（链路可独立也可串联）
6. Runtime 不直接 import personality 核心实现
"""
import sys
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _build_registry():
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
    return reg


def _build_event(type_="user_input", text="hi", priority=0):
    from src.runtime.events import Event
    return Event(
        type=type_,
        source="user",
        payload={"text": text, "content": text},
        priority=priority,
    )


def _build_runtime(reg=None):
    from src.runtime.runtime import RuntimeCore
    if reg is None:
        reg = _build_registry()
    core = RuntimeCore(adapter_registry=reg)
    core.start()
    return core


# ============================================================
# T1: PersonalityRuntimeContext
# ============================================================
class TestPersonalityRuntimeContext:
    def test_can_create(self):
        from src.runtime.personality_context import (
            PersonalityRuntimeContext,
            PERSONALITY_RUNTIME_CONTEXT_SCHEMA_VERSION,
        )
        prc = PersonalityRuntimeContext()
        assert prc.schema_version == PERSONALITY_RUNTIME_CONTEXT_SCHEMA_VERSION
        assert prc.personality_snapshot is None
        assert prc.emotion_state is None
        assert prc.communication_style == {}
        assert prc.behavior_constraints == []

    def test_from_sources_extracts_style(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        snapshot = {
            "tone": "warm",
            "warmth": 0.8,
            "formality": 0.3,
            "verbosity": "medium",
            "behavior_constraints": ["不许辱骂", "不许冷漠"],
            "relationship": {"trust": 0.9, "familiarity": 0.7},
        }
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot=snapshot,
            emotion_state={"valence": 0.6},
        )
        assert prc.communication_style["tone"] == "warm"
        assert prc.communication_style["warmth"] == 0.8
        assert prc.communication_style["formality"] == 0.3
        assert prc.communication_style["verbosity"] == "medium"
        assert "不许辱骂" in prc.behavior_constraints
        assert prc.relationship_state == {"trust": 0.9, "familiarity": 0.7}
        assert prc.has_personality() is True
        assert prc.has_emotion() is True
        assert prc.has_relationship() is True

    def test_from_sources_handles_missing(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        prc = PersonalityRuntimeContext.from_sources()
        assert prc.personality_snapshot is None
        assert prc.communication_style == {}
        assert prc.behavior_constraints == []
        assert prc.has_personality() is False

    def test_to_from_dict(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        prc = PersonalityRuntimeContext(
            personality_snapshot={"tone": "warm"},
            emotion_state={"valence": 0.5},
            relationship_state={"trust": 0.7},
            communication_style={"tone": "warm", "warmth": 0.9},
            behavior_constraints=["x"],
        )
        d = prc.to_dict()
        prc2 = PersonalityRuntimeContext.from_dict(d)
        assert prc2.personality_snapshot == {"tone": "warm"}
        assert prc2.communication_style == {"tone": "warm", "warmth": 0.9}
        assert prc2.behavior_constraints == ["x"]
        assert prc2.schema_version == "1.0"


# ============================================================
# T2: Runtime 集成 - personality_context_build 阶段
# ============================================================
class TestRuntimeIntegration:
    def test_runtime_generates_personality_snapshot(self):
        reg = _build_registry()
        core = _build_runtime(reg)
        ctx = core.process(_build_event(text="hello yuyi"))
        # personality_snapshot 必须存在(可能 None,但字段存在)
        assert hasattr(ctx, "personality_snapshot")
        # personality_context_build 阶段应已运行(stage_errors 不含该 key)
        errs = core.get_stage_errors()
        # 真实 adapter 通常不会失败,所以 errors 不应包含 personality_context_build
        assert "personality_context_build" not in errs

    def test_runtime_generates_personality_runtime_context(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        reg = _build_registry()
        core = _build_runtime(reg)
        ctx = core.process(_build_event(text="今天心情不错"))
        prc = core.get_personality_context(ctx)
        assert isinstance(prc, PersonalityRuntimeContext)

    def test_personality_context_contains_emotion(self):
        reg = _build_registry()
        core = _build_runtime(reg)
        ctx = core.process(_build_event(type_="user_praise", text="好棒"))
        prc = core.get_personality_context(ctx)
        assert prc is not None
        # emotion_state 必填(dict,包含 updated)
        assert prc.emotion_state is not None
        assert isinstance(prc.emotion_state, dict)

    def test_personality_context_build_failure_isolated(self):
        """personality_context_build 失败不应中断 Runtime。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.personality_context import PersonalityRuntimeContext
        from unittest.mock import patch

        reg = _build_registry()
        core = _build_runtime(reg)

        with patch.object(
            PersonalityRuntimeContext, "from_sources",
            side_effect=RuntimeError("ctx build broken"),
        ):
            ctx = core.process(_build_event(text="hi"))
            # 错误被记录
            errs = core.get_stage_errors()
            assert "personality_context_build" in errs
            # 但 ctx 仍然有效
            assert ctx.schema_version == "1.0"

    def test_new_stage_in_lifecycle(self):
        from src.runtime.runtime import (
            RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
        )
        # PERSONALITY_CONTEXT_BUILD 必须在 PERSONALITY_UPDATE 之后
        idx_pu = RUNTIME_LIFECYCLE_ORDER.index(
            RuntimeStage.PERSONALITY_UPDATE
        )
        idx_pcb = RUNTIME_LIFECYCLE_ORDER.index(
            RuntimeStage.PERSONALITY_CONTEXT_BUILD
        )
        assert idx_pcb == idx_pu + 1
        # 必须早于 RESPONSE
        idx_resp = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.RESPONSE)
        assert idx_pcb < idx_resp


# ============================================================
# T3: 人格数据不会修改原人格系统
# ============================================================
class TestPersonalityDataIntact:
    def test_personality_resolver_unchanged(self):
        """PersonalityResolver 仍是人格唯一权威,Runtime 不修改它。"""
        from src.personality.personality_resolver import PersonalityResolver
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapters.impl.personality_adapter_impl import (
            PersonalityAdapterImpl,
        )

        # 1) PersonalityResolver.resolve() 仍可独立调用
        r = PersonalityResolver()
        v1 = r.resolve()
        # 2) 经过 Runtime 后,vector 应等价(没有外部副作用修改人格)
        adapter = PersonalityAdapterImpl(personality_resolver=r)
        adapter.attach()
        adapter.snapshot()
        v2 = r.resolve()
        # 两者的 get_all 应等价(没人偷偷改)
        d1 = v1.get_all() if v1 is not None else None
        d2 = v2.get_all() if v2 is not None else None
        assert d1 == d2

    def test_no_direct_personality_import_in_runtime(self):
        """Runtime 顶层模块不应直接 import src.personality.* 具体实现。"""
        runtime_path = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
        text = runtime_path.read_text(encoding="utf-8")
        # 仅检查 import 语句,跳过 docstring 中的提示字符串
        import_lines = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        forbidden = [
            "from src.personality.personality_resolver",
            "from src.personality.personality_adapter",
            "from src.personality.identity_core",
            "from src.personality.personality_vector",
            "from src.personality.personality_profile",
            "from src.personality.behavior_resolver",
            "from src.personality.personality_evolution",
        ]
        for pat in forbidden:
            assert pat not in joined, (
                f"runtime.py 直接 import {pat} 违反 Runtime→Adapter 边界"
            )

    def test_adapter_only_resolves_via_protocol(self):
        """RuntimeCore 通过 *_port / AdapterRegistry 解析,而不是 import 类。"""
        from src.runtime import runtime as runtime_mod
        src = Path(runtime_mod.__file__).read_text(encoding="utf-8")
        # 仅检查 import 语句
        import_lines = [
            line for line in src.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        assert "PersonalityResolver" not in joined
        assert "PersonalityAdapterImpl" not in joined


# ============================================================
# T4: PersonalityGuard 行为测试
# ============================================================
class TestPersonalityGuard:
    def test_warm_persona_rejects_cold_reply(self):
        """人格温暖,回复'闭嘴,别烦我' 应被拒。"""
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_guard import PersonalityGuard
        guard = PersonalityGuard()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm", "warmth": 0.9},
        )
        report = guard.check("闭嘴,别烦我", prc)
        assert report.valid is False
        assert any("cold" in v or "hostile" in v or "tone" in v for v in report.violations)

    def test_neutral_persona_allows_neutral_reply(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_guard import PersonalityGuard
        guard = PersonalityGuard()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "neutral"},
        )
        report = guard.check("我在这里回答你的问题。", prc)
        assert report.valid is True

    def test_prohibited_behavior_detected(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_guard import PersonalityGuard
        guard = PersonalityGuard()
        prc = PersonalityRuntimeContext(
            behavior_constraints=["不许辱骂", "不许欺骗用户"],
        )
        report = guard.check("我不许欺骗用户,我要这么做", prc)
        assert report.valid is False
        assert any("prohibited_behavior" in v for v in report.violations)

    def test_high_formality_rejects_casual_open(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_guard import PersonalityGuard
        guard = PersonalityGuard()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"formality": 0.9},
        )
        report = guard.check("嘿,你看这个", prc)
        assert report.valid is False
        assert any("style_conflict" in v for v in report.violations)

    def test_empty_reply_rejected(self):
        from src.runtime.personality_guard import PersonalityGuard
        guard = PersonalityGuard()
        report = guard.check("", None)
        assert report.valid is False
        assert "empty_reply" in report.violations

    def test_assert_valid_raises(self):
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_guard import PersonalityGuard
        guard = PersonalityGuard()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm"},
        )
        with pytest.raises(ValueError):
            guard.assert_valid("闭嘴,走开", prc)


# ============================================================
# T5: 双 Guard 链路顺序
# ============================================================
class TestGuardChain:
    def test_chain_perception_then_personality(self):
        """链路顺序:PerceptionGuard 在前,PersonalityGuard 在后。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, USER_INPUT, INFERENCE
        from src.runtime.personality_context import PersonalityRuntimeContext

        chain = ResponseGuardChain()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm", "warmth": 0.9},
        )
        # 含 INFERENCE -> Perception 注入 hedge -> 不会再被 Personality 拒
        facts = [Fact(content="推测", source=INFERENCE, confidence=0.3)]
        result = chain.run("这是答案", facts, prc)
        assert "推测" in result.final_reply or "推理" in result.final_reply
        assert result.perception_report is not None
        assert result.personality_report is not None

    def test_chain_personality_blocks_after_perception_passes(self):
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, USER_INPUT
        from src.runtime.personality_context import PersonalityRuntimeContext

        chain = ResponseGuardChain()
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm", "warmth": 0.9},
        )
        facts = [Fact(content="用户输入", source=USER_INPUT)]
        # perception 通过,personality 拒绝
        result = chain.run("闭嘴,别烦我", facts, prc)
        assert result.blocked_by == "personality"
        assert result.personality_report.valid is False

    def test_chain_perception_refusal_blocks_first(self):
        """严格 refusal (min_grounded_ratio=1.0 + 全 INFERENCE) 时 Perception 先 block。"""
        from src.runtime.response_guard_chain import ResponseGuardChain
        from src.runtime.perception import Fact, INFERENCE, PerceptionGuard
        from src.runtime.personality_context import PersonalityRuntimeContext

        chain = ResponseGuardChain(
            perception_guard=PerceptionGuard(min_grounded_ratio=1.0),
        )
        prc = PersonalityRuntimeContext.from_sources(
            personality_snapshot={"tone": "warm"},
        )
        facts = [Fact(content="推测", source=INFERENCE, confidence=0.3)]
        result = chain.run("答案", facts, prc)
        assert result.blocked_by == "perception"

    def test_two_guards_are_independent(self):
        from src.runtime.perception import PerceptionGuard
        from src.runtime.personality_guard import PersonalityGuard
        pg = PerceptionGuard()
        psg = PersonalityGuard()
        # 两 Guard 独立:personality 不感知 fact
        assert not isinstance(psg, type(pg))


# ============================================================
# T6: Runtime 不直接 import personality 核心实现
# ============================================================
class TestRuntimeImportBoundary:
    def test_runtime_no_direct_personality_concrete_import(self):
        runtime_path = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
        text = runtime_path.read_text(encoding="utf-8")
        # 仅检查 import 语句,跳过 docstring
        import_lines = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(import_lines)
        for cls in [
            "PersonalityResolver",
            "PersonalityAdapterImpl",
            "PersonalityProfile",
            "PersonalityVector",
            "BehaviorResolver",
            "PersonalityEvolutionEngine",
        ]:
            assert cls not in joined, (
                f"runtime.py import 语句中出现 personality 核心类 {cls},"
                "违反 Runtime→Adapter 边界"
            )

    def test_runtime_uses_protocol_only(self):
        from src.runtime import runtime as runtime_mod
        src = Path(runtime_mod.__file__).read_text(encoding="utf-8")
        # 应通过 _PersonalityPortLike Protocol 调用
        assert "_PersonalityPortLike" in src


# ============================================================
# T7: 阶段总结
# ============================================================
def test_phase_3_8_0_summary():
    from src.runtime.personality_context import PersonalityRuntimeContext
    from src.runtime.personality_guard import (
        PersonalityGuard, PersonalityGuardReport,
    )
    from src.runtime.response_guard_chain import (
        ResponseGuardChain, ResponseGuardResult,
    )
    from src.runtime.runtime import (
        RuntimeStage, RUNTIME_LIFECYCLE_ORDER,
    )

    # 1) 关键类可用
    assert PersonalityRuntimeContext is not None
    assert PersonalityGuard is not None
    assert PersonalityGuardReport is not None
    assert ResponseGuardChain is not None
    assert ResponseGuardResult is not None

    # 2) 新阶段已纳入生命周期
    assert RuntimeStage.PERSONALITY_CONTEXT_BUILD in RUNTIME_LIFECYCLE_ORDER

    # 3) Runtime 端到端跑一次
    reg = _build_registry()
    core = _build_runtime(reg)
    ctx = core.process(_build_event(text="phase 3.8.0"))
    prc = core.get_personality_context(ctx)
    assert prc is not None
    assert prc.schema_version == "1.0"

    # 4) 双 Guard 串联一次
    chain = ResponseGuardChain()
    from src.runtime.perception import Fact, USER_INPUT
    facts = [Fact(content="u", source=USER_INPUT)]
    result = chain.run("我在这里,继续陪伴你。", facts, prc)
    assert result.final_reply == "我在这里,继续陪伴你。"

    core.shutdown()
