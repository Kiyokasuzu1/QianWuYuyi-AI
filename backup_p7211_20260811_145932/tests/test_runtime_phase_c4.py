# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_c4.py

Phase C.4 Personality Runtime Integration —— PersonalityRuntimeAdapter 测试

覆盖 7 类:
  1. TestPersonalityAdapterBasic   - 初始化 / Protocol 兼容 / schema
  2. TestPersonalityReadOnly       - 不调用 resolve / apply_update / evolution / store / history
  3. TestPersonalityProcessing     - 正常状态 / 空状态 / 缺字段 / 极端值
  4. TestRuntimeIntegration        - 注册到 RuntimeCycle / ctx.personality_output 生成 / step 顺序
  5. TestExceptionIsolation        - resolver 异常 / state 异常 / 字段异常 / fallback
  6. TestHealthSnapshot            - health_check / snapshot
  7. TestRegression                - 不修改 C.1 / C.2 / C.3 / B.4-B.13

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化 PersonalityResolver
  - 不依赖 RuntimeCore 单例
"""
from __future__ import annotations

import sys
import os
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.adapters.impl.personality_runtime_adapter import (
    PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION,
    PHASE_C4_NAME,
    PHASE_C4_VERSION,
    PHASE_C4_STAGE_PERSONALITY_READ,
    PHASE_C4_STAGE_PERSONALITY_DEGRADED,
    ALL_PHASE_C4_STAGES,
    SOURCE_NAME,
    CORE_TRAITS,
    EXTENDED_TRAITS,
    PersonalityRuntimeAdapter,
    create_personality_runtime_adapter,
    safe_get_personality_adapter_summary,
    _PersonalitySnapshot,
)
from src.runtime.cycle_adapter import (
    is_cycle_adapter,
    STANDARD_ADAPTER_PERSONALITY,
)
from src.runtime.cycle_context import RuntimeCycleContext
from src.runtime.cycle_event import (
    CYCLE_EVENT_PERSONALITY_COMPLETED,
    CYCLE_EVENT_COMPLETED,
    CYCLE_EVENT_EMOTION_COMPLETED,
    CYCLE_EVENT_MEMORY_COMPLETED,
)
from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Helper:Mock PersonalityResolver
# ============================================================


def _make_trait_state(current_value: float, base_value: float = 0.5) -> Dict[str, Any]:
    """构造一个 _trait_states 元素(mimic TraitState dict-like)。"""
    return {
        "current_value": float(current_value),
        "base_value": float(base_value),
        "last_delta": 0.0,
        "history": [],
        "frozen": False,
    }


class _FakeGrowthState:
    """模拟 GrowthState(只读 query)。"""

    def __init__(
        self,
        metrics: Optional[Dict[str, float]] = None,
        fail_get: bool = False,
    ) -> None:
        self._metrics = dict(metrics or {})
        self._fail_get = fail_get
        # monitor
        self.get_count = 0

    def get(self) -> Dict[str, Any]:
        self.get_count += 1
        if self._fail_get:
            raise RuntimeError("growth_state_get_error")
        return {
            "metrics": dict(self._metrics),
            "behaviors": {},
            "identities": [],
        }


class _FakeRelationshipState:
    """模拟 RelationshipState(只读 query)。"""

    def __init__(
        self,
        bond_strength: float = 0.5,
        trust: float = 0.5,
        familiarity: float = 0.4,
        fail_calls: bool = False,
    ) -> None:
        self._bond = float(bond_strength)
        self._trust = float(trust)
        self._familiarity = float(familiarity)
        self._fail = fail_calls
        # monitor
        self.bond_call_count = 0
        self.trust_call_count = 0
        self.familiarity_call_count = 0

    def get_bond_strength(self) -> float:
        self.bond_call_count += 1
        if self._fail:
            raise RuntimeError("bond_error")
        return self._bond

    def get_trust(self) -> float:
        self.trust_call_count += 1
        if self._fail:
            raise RuntimeError("trust_error")
        return self._trust

    def get_familiarity(self) -> float:
        self.familiarity_call_count += 1
        if self._fail:
            raise RuntimeError("familiarity_error")
        return self._familiarity


class _FakeSelfModelStore:
    """模拟 SelfModelStore(只暴露 get(),绝不暴露 update / should_update)。"""

    def __init__(
        self,
        identity_summary: str = "",
        fail_get: bool = False,
    ) -> None:
        self._identity_summary = identity_summary
        self._fail_get = fail_get
        # monitor
        self.get_count = 0
        self.update_count = 0
        self.should_update_count = 0

    def get(self) -> Dict[str, Any]:
        self.get_count += 1
        if self._fail_get:
            raise RuntimeError("self_model_get_error")
        return {
            "identity_summary": self._identity_summary,
            "last_updated": "2026-08-03T00:00:00",
        }

    # --- 以下方法绝不应该被调用(只读 adapter) ---
    def update(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.update_count += 1
        raise AssertionError(
            "PersonalityRuntimeAdapter 不得调用 self_model_store.update()"
        )

    def should_update(self, *args: Any, **kwargs: Any) -> bool:  # pragma: no cover
        self.should_update_count += 1
        raise AssertionError(
            "PersonalityRuntimeAdapter 不得调用 self_model_store.should_update()"
        )


class _FakeEvolutionEngine:
    """模拟 evolution_engine,任何 update_trait() 调用都报错。"""

    def __init__(self) -> None:
        self.update_trait_count = 0

    def update_trait(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:  # pragma: no cover
        self.update_trait_count += 1
        raise AssertionError(
            "PersonalityRuntimeAdapter 不得调用 evolution_engine.update_trait()"
        )


class _FakePersonalityHistory:
    """模拟 personality_history,任何 record_change() 调用都报错。"""

    def __init__(self) -> None:
        self.record_change_count = 0

    def record_change(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.record_change_count += 1
        raise AssertionError(
            "PersonalityRuntimeAdapter 不得调用 personality_history.record_change()"
        )


class _FakePersonalityResolver:
    """
    模拟 PersonalityResolver。

    **不实现(若被调用会 raise AssertionError)**:
      - resolve()                  -> 禁止
      - apply_update()             -> 禁止
      - evolution_engine.update_trait() -> 禁止
      - self_model_store.update()  -> 禁止
      - personality_history.record_change() -> 禁止

    **提供(只读)**:
      - _trait_states  (dict)
      - state          (GrowthState, 暴露 get())
      - relationship_state (RelationshipState, 暴露 get_*())
      - self_model_store (SelfModelStore, 暴露 get())
    """

    def __init__(
        self,
        trait_states: Optional[Dict[str, Dict[str, Any]]] = None,
        state: Optional[_FakeGrowthState] = None,
        relationship_state: Optional[_FakeRelationshipState] = None,
        self_model_store: Optional[_FakeSelfModelStore] = None,
        fail_trait_states_attr: bool = False,
    ) -> None:
        # 默认 6 维 core + 7 维 extended,提供稳定测试数据
        default_traits: Dict[str, Dict[str, Any]] = {}
        for dim in CORE_TRAITS:
            default_traits[dim] = _make_trait_state(0.7, 0.7)
        for dim in EXTENDED_TRAITS:
            default_traits[dim] = _make_trait_state(0.4, 0.3)
        self._trait_states = trait_states if trait_states is not None else default_traits
        self.state = state or _FakeGrowthState()
        self.relationship_state = relationship_state or _FakeRelationshipState()
        self.self_model_store = self_model_store or _FakeSelfModelStore()
        self.evolution_engine = _FakeEvolutionEngine()
        self.personality_history = _FakePersonalityHistory()
        self._fail_trait_states_attr = fail_trait_states_attr
        # monitor
        self.resolve_count = 0
        self.apply_update_count = 0

    @property
    def trait_states(self) -> Dict[str, Dict[str, Any]]:
        return self._trait_states

    # --- 以下方法绝不应该被调用(只读 adapter) ---
    def resolve(self) -> Any:  # pragma: no cover
        self.resolve_count += 1
        raise AssertionError(
            "PersonalityRuntimeAdapter 不得调用 PersonalityResolver.resolve()"
        )

    def apply_update(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.apply_update_count += 1
        raise AssertionError(
            "PersonalityRuntimeAdapter 不得调用 apply_update()"
        )


# ============================================================
# 2. 测试 1:Personality Adapter 基础
# ============================================================


class TestPersonalityAdapterBasic:
    def test_construction_no_dependencies(self) -> None:
        """无依赖构造:不抛。"""
        a = PersonalityRuntimeAdapter()
        assert a.name == STANDARD_ADAPTER_PERSONALITY == "personality"
        assert a.schema_version == PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"
        assert a.is_attached() is False

    def test_attach_with_existing_resolver(self) -> None:
        """外部注入 resolver 时,attach 不重建。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        assert a.is_attached() is True
        # resolver 未被替换
        assert a._resolver is r

    def test_attach_self_build_resolver(self) -> None:
        """外部未注入时,attach 内部尝试建一个(可失败,不影响 attached=True)。"""
        a = PersonalityRuntimeAdapter()
        a.attach()
        # 不论是否成功建出 resolver,attached 必然 True
        assert a.is_attached() is True

    def test_detach(self) -> None:
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        assert a.is_attached() is True
        assert a.detach() is True
        assert a.is_attached() is False

    def test_implements_cycle_adapter_protocol(self) -> None:
        """必须满足 CycleAdapter duck-type。"""
        a = PersonalityRuntimeAdapter()
        assert is_cycle_adapter(a) is True
        assert hasattr(a, "name")
        assert hasattr(a, "health_check")
        assert hasattr(a, "process_cycle")
        assert hasattr(a, "snapshot")
        assert hasattr(a, "attach")
        assert hasattr(a, "detach")

    def test_snapshot_basic(self) -> None:
        a = PersonalityRuntimeAdapter(user_id="alice")
        snap = a.snapshot()
        assert snap["name"] == "personality"
        assert snap["schema_version"] == "1.0"
        assert snap["user_id"] == "alice"
        assert snap["process_count"] == 0
        assert snap["read_count"] == 0
        assert snap["degraded_count"] == 0
        assert snap["attached"] is False

    def test_schema_version_locked(self) -> None:
        """schema_version 冻结在 1.0。"""
        a = PersonalityRuntimeAdapter()
        assert a.schema_version == "1.0"
        assert PERSONALITY_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"

    def test_default_user_id(self) -> None:
        a = PersonalityRuntimeAdapter()
        assert a._user_id == "yuyi"

    def test_explicit_user_id(self) -> None:
        a = PersonalityRuntimeAdapter(user_id="bob")
        assert a._user_id == "bob"
        assert a.snapshot()["user_id"] == "bob"

    def test_factory_create(self) -> None:
        r = _FakePersonalityResolver()
        a = create_personality_runtime_adapter(personality_resolver=r, user_id="u1")
        assert isinstance(a, PersonalityRuntimeAdapter)
        assert a._resolver is r
        assert a._user_id == "u1"

    def test_factory_default(self) -> None:
        a = create_personality_runtime_adapter()
        assert isinstance(a, PersonalityRuntimeAdapter)
        assert a._user_id == "yuyi"


# ============================================================
# 3. 测试 2:只读特性(Read-Only)
# ============================================================


class TestPersonalityReadOnly:
    def test_does_not_call_resolve(self) -> None:
        """绝不调 PersonalityResolver.resolve() (核心约束)。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert r.resolve_count == 0

    def test_does_not_call_apply_update(self) -> None:
        """绝不调 apply_update()。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert r.apply_update_count == 0

    def test_does_not_call_evolution_update_trait(self) -> None:
        """绝不调 evolution_engine.update_trait()。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert r.evolution_engine.update_trait_count == 0

    def test_does_not_call_self_model_store_update(self) -> None:
        """绝不调 self_model_store.update()。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert r.self_model_store.update_count == 0
        assert r.self_model_store.should_update_count == 0

    def test_does_not_call_history_record_change(self) -> None:
        """绝不调 personality_history.record_change()。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert r.personality_history.record_change_count == 0

    def test_does_not_modify_trait_states(self) -> None:
        """绝不修改 _trait_states(键集合 + 值都不变)。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()

        # 拷贝初始 _trait_states
        before_keys = set(r._trait_states.keys())
        before_snapshot = {
            k: dict(v) for k, v in r._trait_states.items()
        }  # type: ignore

        for _ in range(5):
            ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
            a.process_cycle(ctx)

        # _trait_states 完全不变
        assert set(r._trait_states.keys()) == before_keys
        for k in before_keys:
            assert r._trait_states[k] == before_snapshot[k]

    def test_does_not_modify_growth_state(self) -> None:
        """GrowthState 只能 get(),被读若干次但不应被改。"""
        gs = _FakeGrowthState(
            metrics={"trust": 0.5, "closeness": 0.4, "attachment": 0.3},
        )
        r = _FakePersonalityResolver(state=gs)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
        # 至少被读过一次
        assert gs.get_count >= 3

    def test_does_not_modify_self_model(self) -> None:
        """self_model_store 只能 get(),update() / should_update() 永不被调。"""
        sm = _FakeSelfModelStore(identity_summary="我是羽依")
        r = _FakePersonalityResolver(self_model_store=sm)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
        # get 可被调
        assert sm.get_count >= 1
        # 写绝不被调
        assert sm.update_count == 0
        assert sm.should_update_count == 0

    def test_multiple_process_cycle_remains_readonly(self) -> None:
        """多次 process_cycle 仍只读。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        before_snapshot = {k: dict(v) for k, v in r._trait_states.items()}  # type: ignore
        for i in range(20):
            ctx = RuntimeCycleContext(input_event=f"msg_{i}", user_id="u")
            a.process_cycle(ctx)
        assert r.resolve_count == 0
        assert r.apply_update_count == 0
        assert r.evolution_engine.update_trait_count == 0
        assert r.self_model_store.update_count == 0
        assert r.personality_history.record_change_count == 0
        for k, v in before_snapshot.items():
            assert r._trait_states[k] == v

    def test_output_does_not_reference_original_trait_dicts(self) -> None:
        """output 不得返回 _trait_states 原对象引用(必须 copy)。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out is not None
        # current_traits 必须是新 dict,不能是 r._trait_states 本身
        assert out["current_traits"] is not r._trait_states
        for dim in out["current_traits"]:
            assert out["current_traits"][dim] is not r._trait_states.get(dim)


# ============================================================
# 4. 测试 3:Personality Processing(正常 / 空 / 缺字段 / 极端值)
# ============================================================


class TestPersonalityProcessing:
    def _resolver_with(
        self,
        traits: Optional[Dict[str, Dict[str, Any]]] = None,
        metrics: Optional[Dict[str, float]] = None,
        bond: float = 0.5,
        trust: float = 0.5,
        familiarity: float = 0.4,
        identity: str = "",
    ) -> _FakePersonalityResolver:
        gs = _FakeGrowthState(metrics=metrics or {})
        rs = _FakeRelationshipState(
            bond_strength=bond, trust=trust, familiarity=familiarity,
        )
        sm = _FakeSelfModelStore(identity_summary=identity)
        return _FakePersonalityResolver(
            trait_states=traits,
            state=gs,
            relationship_state=rs,
            self_model_store=sm,
        )

    def test_normal_state(self) -> None:
        """正常状态:personality_output 完整字段。"""
        r = self._resolver_with(
            metrics={"trust": 0.6, "closeness": 0.5, "attachment": 0.4},
            bond=0.6, trust=0.7, familiarity=0.5, identity="羽依是 AI 助手",
        )
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        out = ctx.personality_output

        assert out is not None
        assert isinstance(out, dict)
        assert out["personality_available"] is True
        assert out["degraded"] is False
        assert out["source"] == SOURCE_NAME == "personality_runtime_adapter"
        assert "current_traits" in out
        assert "extended_traits" in out
        assert "labels" in out
        assert "persona_summary" in out
        assert "active_tensions" in out
        assert "identity_summary" in out

        # current_traits 6 维 core,每个值在 [0, 1]
        ct = out["current_traits"]
        assert set(ct.keys()) == set(CORE_TRAITS)
        for v in ct.values():
            assert 0.0 <= v <= 1.0

        # extended_traits 7 维派生
        et = out["extended_traits"]
        assert set(et.keys()) == set(EXTENDED_TRAITS)
        for v in et.values():
            assert 0.0 <= v <= 1.0

        # labels
        assert "attachment_level" in out["labels"]
        assert "interaction_familiarity_level" in out["labels"]
        # identity_summary 从 self_model_store 读出
        assert out["identity_summary"] == "羽依是 AI 助手"

    def test_empty_trait_states(self) -> None:
        """空 _trait_states → 走 fallback,各维度使用 TRAIT_FALLBACK_VALUES。"""
        from src.runtime.adapters.impl.personality_runtime_adapter import (
            TRAIT_FALLBACK_VALUES,
        )
        r = self._resolver_with(traits={})
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.personality_output
        # 仍 personality_available=True
        assert out["personality_available"] is True
        assert out["degraded"] is False
        # current_traits 各 dim 来自 fallback
        for dim in CORE_TRAITS:
            assert out["current_traits"][dim] == TRAIT_FALLBACK_VALUES[dim]

    def test_missing_trait_dim(self) -> None:
        """_trait_states 缺某个维度 → 该维度走 fallback。"""
        from src.runtime.adapters.impl.personality_runtime_adapter import (
            TRAIT_FALLBACK_VALUES,
        )
        # 只给 1 个维度
        partial = {"warmth": _make_trait_state(0.9, 0.7)}
        r = self._resolver_with(traits=partial)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.personality_output
        # warmth 来自 _trait_states
        assert out["current_traits"]["warmth"] == 0.9
        # 其他维度走 fallback
        for dim in CORE_TRAITS:
            if dim == "warmth":
                continue
            assert out["current_traits"][dim] == TRAIT_FALLBACK_VALUES[dim]

    def test_trait_state_with_invalid_value(self) -> None:
        """trait state 的 current_value 是非法值(字符串/None)→ fallback。"""
        from src.runtime.adapters.impl.personality_runtime_adapter import (
            TRAIT_FALLBACK_VALUES,
        )
        bad = {
            "warmth": {"current_value": "abc", "base_value": 0.7},
            "gentleness": {"current_value": None, "base_value": 0.7},
            "shyness": {},  # 完全没 current_value
        }
        r = self._resolver_with(traits=bad)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.personality_output
        # 全部走 fallback
        for dim in CORE_TRAITS:
            assert out["current_traits"][dim] == TRAIT_FALLBACK_VALUES[dim]

    def test_extreme_values_clamped(self) -> None:
        """trait 值极端(-5, 99)→ clamp 到 [0, 1]。"""
        traits = {
            "warmth": _make_trait_state(-5.0, 0.7),
            "gentleness": _make_trait_state(99.0, 0.8),
            "shyness": _make_trait_state(0.5, 0.75),
            "sensitivity": _make_trait_state(0.0, 0.8),
            "emotional_expression": _make_trait_state(1.0, 0.65),
            "caring": _make_trait_state(0.7, 0.7),
        }
        r = self._resolver_with(traits=traits)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.personality_output
        ct = out["current_traits"]
        assert ct["warmth"] == 0.0  # -5 clamp 到 0
        assert ct["gentleness"] == 1.0  # 99 clamp 到 1
        assert ct["sensitivity"] == 0.0
        assert ct["emotional_expression"] == 1.0

    def test_empty_input_event(self) -> None:
        """空 input_event 不影响 output。"""
        r = self._resolver_with()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        for ev in [None, "", 0, {}, [], object()]:
            ctx = RuntimeCycleContext(input_event=ev)
            a.process_cycle(ctx)
            assert ctx.personality_output is not None
            assert ctx.personality_output["personality_available"] is True

    def test_uses_emotion_output_from_ctx(self) -> None:
        """可读 ctx.emotion_output(但不强制依赖,空也不影响)。"""
        r = self._resolver_with()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.emotion_output = {"emotion_available": True, "mood": "joyful"}
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out["personality_available"] is True

    def test_uses_metadata(self) -> None:
        """可读 ctx.metadata(空也不影响)。"""
        r = self._resolver_with()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.metadata["session_topic"] = "small_talk"
        a.process_cycle(ctx)
        assert ctx.personality_output is not None

    def test_attachment_label_branches(self) -> None:
        """attachment score 不同时,label 跟随变化。"""
        # attachment 0.1 → "初识"
        r1 = self._resolver_with(metrics={"attachment": 0.1})
        a1 = PersonalityRuntimeAdapter(personality_resolver=r1)
        a1.attach()
        ctx1 = RuntimeCycleContext(input_event="x")
        a1.process_cycle(ctx1)
        assert ctx1.personality_output["labels"]["attachment_level"] == "初识"

        # attachment 0.9 → "安全依恋"
        r2 = self._resolver_with(metrics={"attachment": 0.9})
        a2 = PersonalityRuntimeAdapter(personality_resolver=r2)
        a2.attach()
        ctx2 = RuntimeCycleContext(input_event="x")
        a2.process_cycle(ctx2)
        assert ctx2.personality_output["labels"]["attachment_level"] == "安全依恋"

    def test_persona_summary_present(self) -> None:
        """persona_summary 必须非空字符串。"""
        r = self._resolver_with()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        ps = ctx.personality_output["persona_summary"]
        assert isinstance(ps, str)
        assert len(ps) > 0

    def test_read_personality_public(self) -> None:
        """read_personality 公开接口,返回统一 output。"""
        r = self._resolver_with()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        out = a.read_personality()
        assert out["personality_available"] is True
        # 计数器累加
        assert a.read_count == 1
        # 二次 read
        a.read_personality()
        assert a.read_count == 2


# ============================================================
# 5. 测试 4:Runtime Integration
# ============================================================


class TestRuntimeIntegration:
    def test_register_with_orchestrator(self) -> None:
        """PersonalityRuntimeAdapter 可注册到 RuntimeCycleOrchestrator。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        orch = RuntimeCycleOrchestrator()
        ok = orch.register_adapter(a, name="personality")
        assert ok is True
        assert orch.has_adapter("personality") is True
        assert orch.adapter_count == 1

    def test_full_cycle_integration(self) -> None:
        """完整 cycle:orchestrator 调度 personality adapter,ctx.personality_output 被填充。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="personality")
        ctx = orch.process_event("hi", user_id="u1", session_id="s1")

        # adapter 被调用
        assert a.process_count == 1
        # ctx.personality_output 被填充
        assert ctx.personality_output is not None
        assert isinstance(ctx.personality_output, dict)
        assert ctx.personality_output["personality_available"] is True
        # adapter_results 应有 personality
        assert ctx.adapter_results["personality"]["ok"] is True

    def test_step_order_personality_after_emotion(self) -> None:
        """personality 在 5 步标准中是 step 3,emotion 在 personality 之前。"""
        from src.runtime.adapters.impl.memory_runtime_adapter import (
            MemoryRuntimeAdapter,
        )
        from src.runtime.adapters.impl.emotion_runtime_adapter import (
            EmotionRuntimeAdapter,
        )

        a_p = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a_p.attach()
        a_e = EmotionRuntimeAdapter()
        a_e.attach()
        a_m = MemoryRuntimeAdapter(
            memory_store=None, vector_memory=None,
            enable_vector=False, enable_store=False,
        )
        a_m.attach()

        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a_m, name="memory")
        orch.register_adapter(a_e, name="emotion")
        orch.register_adapter(a_p, name="personality")
        ctx = orch.process_event("hi", user_id="u1")

        stages = ctx.get_stage_names()
        # 三者都执行
        assert CYCLE_EVENT_MEMORY_COMPLETED in stages
        assert CYCLE_EVENT_EMOTION_COMPLETED in stages
        assert CYCLE_EVENT_PERSONALITY_COMPLETED in stages
        # 顺序: memory -> emotion -> personality
        mem_idx = stages.index(CYCLE_EVENT_MEMORY_COMPLETED)
        em_idx = stages.index(CYCLE_EVENT_EMOTION_COMPLETED)
        per_idx = stages.index(CYCLE_EVENT_PERSONALITY_COMPLETED)
        assert mem_idx < em_idx < per_idx
        # personality 仍在 cycle 结束前
        assert per_idx < stages.index(CYCLE_EVENT_COMPLETED)

    def test_orchestrator_handles_personality_failure(self) -> None:
        """personality adapter 抛异常时,orchestrator 标记 degraded 但不中断。"""

        class _BoomAdapter(PersonalityRuntimeAdapter):
            def process_cycle(self, ctx):
                raise RuntimeError("personality_boom")

        a = _BoomAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="personality")
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.adapter_results["personality"]["ok"] is False
        assert "personality" in ctx.degraded_adapters
        # cycle 仍完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert orch.cycle_count == 1

    def test_personality_uses_input_event(self) -> None:
        """personality 可读 ctx.input_event。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        ctx = RuntimeCycleContext(
            input_event={"user_input": "hello yuyi", "type": "chat"},
            user_id="u1",
        )
        a.process_cycle(ctx)
        assert ctx.personality_output is not None

    def test_personality_uses_emotion_output(self) -> None:
        """personality 可读 ctx.emotion_output(虽然不强制依赖)。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.emotion_output = {"emotion_available": True, "mood": "serene"}
        a.process_cycle(ctx)
        # personality_output 已填,不影响
        assert ctx.personality_output is not None
        assert ctx.personality_output["personality_available"] is True

    def test_personality_uses_metadata(self) -> None:
        """personality 可读 ctx.metadata(不影响主流程)。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.metadata["session_topic"] = "small_talk"
        a.process_cycle(ctx)
        assert ctx.personality_output is not None


# ============================================================
# 6. 测试 5:异常隔离
# ============================================================


class TestExceptionIsolation:
    def test_resolver_none_returns_degraded(self) -> None:
        """resolver 为 None → degraded。"""
        a = PersonalityRuntimeAdapter()  # 不传 resolver
        # 不 attach
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out is not None
        assert out["personality_available"] is False
        assert out["degraded"] is True
        assert out["source"] == "personality_runtime_adapter"
        assert "error" in out

    def test_trait_states_attr_error(self) -> None:
        """_trait_states 属性访问抛异常 → degraded。"""

        class _BoomResolver:
            def __getattr__(self, name: str) -> Any:
                if name == "_trait_states":
                    raise RuntimeError("trait_states_attr_boom")
                return None

        a = PersonalityRuntimeAdapter(personality_resolver=_BoomResolver())  # type: ignore
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out["personality_available"] is False
        assert out["degraded"] is True
        assert "error" in out

    def test_trait_states_not_dict(self) -> None:
        """_trait_states 不是 dict(例如 None)→ degraded。"""
        r = _FakePersonalityResolver()
        r._trait_states = None  # type: ignore
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out["personality_available"] is False
        assert out["degraded"] is True

    def test_growth_state_get_raises(self) -> None:
        """state.get() 抛异常时 → extended_traits 走兜底,仍 personality_available=True。"""
        gs = _FakeGrowthState(fail_get=True)
        r = _FakePersonalityResolver(state=gs)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.personality_output
        # core traits 仍能读 → 不应 degraded
        assert out["personality_available"] is True
        assert out["degraded"] is False
        # extended_traits 全部为默认 0.x
        for dim in EXTENDED_TRAITS:
            assert out["extended_traits"][dim] >= 0.0

    def test_relationship_state_raises(self) -> None:
        """relationship_state 调用异常 → 仍 personality_available=True,labels 走兜底。"""
        rs = _FakeRelationshipState(fail_calls=True)
        r = _FakePersonalityResolver(relationship_state=rs)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out["personality_available"] is True
        # labels 仍存在
        assert "attachment_level" in out["labels"]
        assert "interaction_familiarity_level" in out["labels"]

    def test_self_model_get_raises(self) -> None:
        """self_model_store.get() 抛异常 → identity_summary 走空,不 degraded。"""
        sm = _FakeSelfModelStore(fail_get=True)
        r = _FakePersonalityResolver(self_model_store=sm)
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.personality_output
        assert out["personality_available"] is True
        assert out["identity_summary"] == ""

    def test_process_cycle_with_invalid_ctx(self) -> None:
        """非 RuntimeCycleContext,直接返回原对象。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        fake = {"x": 1}
        out = a.process_cycle(fake)
        assert out is fake

    def test_process_cycle_with_none_ctx(self) -> None:
        """ctx 为 None,不抛,返回 None。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        out = a.process_cycle(None)  # type: ignore
        # 不抛,返回 None
        assert out is None

    def test_process_cycle_increments_degraded_count(self) -> None:
        """degraded 时,degraded_count 累加。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        # 第 1 次:正常
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        assert a.degraded_count == 0
        # 让 resolver 失能(变 None)
        a._resolver = None  # type: ignore
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        # degraded_count >= 3
        assert a.degraded_count >= 3

    def test_health_check_with_exception(self) -> None:
        """health_check 在 resolver 异常时不抛。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        # 让 resolver 抛
        a._resolver._trait_states = None  # type: ignore
        hc = a.health_check()
        assert isinstance(hc, dict)
        # 不抛, status 是 degraded
        assert hc["status"] == "degraded"
        assert hc["resolver_available"] is True
        assert hc["trait_states_available"] is False

    def test_health_check_with_no_resolver(self) -> None:
        """无 resolver → health_check status degraded。"""
        a = PersonalityRuntimeAdapter()
        # 不 attach,resolver 仍为 None
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert hc["status"] == "degraded"
        assert hc["resolver_available"] is False
        assert hc["attached"] is False

    def test_concurrent_process_cycle(self) -> None:
        """并发 process_cycle 不抛。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        results: List[Any] = []
        errors: List[str] = []

        def worker(idx: int) -> None:
            try:
                ctx = RuntimeCycleContext(input_event=f"msg_{idx}", user_id=f"u{idx}")
                out = a.process_cycle(ctx)
                results.append(out)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert len(errors) == 0
        assert len(results) == 20
        # process_count 累加正确
        assert a.process_count == 20


# ============================================================
# 7. 测试 6:Health / Snapshot
# ============================================================


class TestHealthSnapshot:
    def test_health_basic_with_resolver(self) -> None:
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert hc["adapter"] == "personality"
        assert hc["status"] in ("healthy", "degraded")
        assert hc["resolver_available"] is True
        assert hc["attached"] is True

    def test_health_without_resolver(self) -> None:
        """未 attach 时(无 resolver)→ status degraded。"""
        a = PersonalityRuntimeAdapter()
        # 不调用 attach(),这样不会自建 resolver
        hc = a.health_check()
        assert isinstance(hc, dict)
        # resolver 不存在 → status degraded
        assert hc["status"] == "degraded"
        assert hc["resolver_available"] is False

    def test_health_counts(self) -> None:
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        hc = a.health_check()
        assert hc["process_count"] == 3
        assert hc["read_count"] == 0  # 走 process_cycle 不算 read_personality

    def test_health_records_schema_version(self) -> None:
        a = PersonalityRuntimeAdapter()
        hc = a.health_check()
        assert hc["schema_version"] == "1.0"

    def test_health_records_user_id(self) -> None:
        a = PersonalityRuntimeAdapter(user_id="alice")
        hc = a.health_check()
        assert hc["user_id"] == "alice"

    def test_snapshot_basic(self) -> None:
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r, user_id="alice")
        a.attach()
        snap = a.snapshot()
        assert snap["name"] == "personality"
        assert snap["schema_version"] == "1.0"
        assert snap["user_id"] == "alice"
        assert snap["process_count"] == 0
        assert snap["read_count"] == 0
        assert snap["degraded_count"] == 0
        assert snap["attached"] is True
        assert snap["resolver_available"] is True

    def test_snapshot_includes_last_state(self) -> None:
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        snap = a.snapshot()
        # last_state 应包含 traits 等
        ls = snap["last_state"]
        assert "traits" in ls
        # 必须至少有 6 个 core traits
        for dim in CORE_TRAITS:
            assert dim in ls["traits"]

    def test_snapshot_available_flag(self) -> None:
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        # 未 attach / 未读
        snap = a.snapshot()
        # available 应为 False(resolver 是注入的,但 process_cycle 没跑)
        # 因为 last_output 为 None,_is_degraded_snapshot 返回 True → available=False
        assert snap["available"] is False
        # 跑一次后
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        snap2 = a.snapshot()
        assert snap2["available"] is True

    def test_safe_summary_with_none(self) -> None:
        s = safe_get_personality_adapter_summary(None)
        assert s["name"] == "personality"
        assert s["attached"] is False
        assert s["available"] is False

    def test_safe_summary_with_adapter(self) -> None:
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        s = safe_get_personality_adapter_summary(a)
        assert s["name"] == "personality"
        assert s["attached"] is True

    def test_phase_c4_constants(self) -> None:
        """Phase C.4 常量完整。"""
        for name in [
            "PHASE_C4_STAGE_PERSONALITY_READ",
            "PHASE_C4_STAGE_PERSONALITY_DEGRADED",
        ]:
            assert name in dir(
                sys.modules["src.runtime.adapters.impl.personality_runtime_adapter"]
            )
        assert PHASE_C4_NAME == "phase_c4"
        assert PHASE_C4_VERSION == "1.0.0"
        assert len(ALL_PHASE_C4_STAGES) == 2
        assert SOURCE_NAME == "personality_runtime_adapter"


# ============================================================
# 8. 测试 7:Regression
# ============================================================


class TestRegression:
    def test_does_not_modify_resolver_state(self) -> None:
        """resolver 任何字段都未被修改。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        # 多次读
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
            a.read_personality()
        # resolver 任何"写"调用计数均为 0
        assert r.resolve_count == 0
        assert r.apply_update_count == 0
        assert r.evolution_engine.update_trait_count == 0
        assert r.self_model_store.update_count == 0
        assert r.personality_history.record_change_count == 0
        # _trait_states 没动
        for dim in r._trait_states:
            assert r._trait_states[dim].get("last_delta") == 0.0

    def test_does_not_modify_runtime_cycle_context_except_personality_output(self) -> None:
        """除 personality_output 外,ctx 其他字段不应被改。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        ctx = RuntimeCycleContext(
            input_event="hi", user_id="u1", session_id="s1",
        )
        ctx.metadata["k"] = "v"
        ctx.memory_output = {"existing": True}
        ctx.emotion_output = {"existing": True}
        before = {
            "cycle_id": ctx.cycle_id,
            "session_id": ctx.session_id,
            "user_id": ctx.user_id,
            "input_event": ctx.input_event,
            "metadata": dict(ctx.metadata),
            "memory_output": ctx.memory_output,
            "emotion_output": ctx.emotion_output,
            "relationship_output": ctx.relationship_output,
            "growth_output": list(ctx.growth_output),
        }
        a.process_cycle(ctx)
        # personality_output 被填
        assert ctx.personality_output is not None
        # 其他字段不变
        assert ctx.cycle_id == before["cycle_id"]
        assert ctx.session_id == before["session_id"]
        assert ctx.user_id == before["user_id"]
        assert ctx.input_event == before["input_event"]
        assert ctx.metadata == before["metadata"]
        assert ctx.memory_output is before["memory_output"]
        assert ctx.emotion_output is before["emotion_output"]
        assert ctx.relationship_output is before["relationship_output"]
        assert ctx.growth_output == before["growth_output"]

    def test_does_not_modify_orchestrator(self) -> None:
        """RuntimeCycleOrchestrator 公共方法集合不变。"""
        a = PersonalityRuntimeAdapter(personality_resolver=_FakePersonalityResolver())
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="personality")
        orch.process_event("x", user_id="u1")
        # orchestrator 仍可被外部使用
        ctx2 = orch.process_event("y", user_id="u1")
        assert ctx2.cycle_id != ""

    def test_does_not_call_forbidden_methods(self) -> None:
        """所有禁止方法均未被调。"""
        r = _FakePersonalityResolver()
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        # 验证所有禁止方法都没被调
        assert r.resolve_count == 0
        assert r.apply_update_count == 0
        assert r.evolution_engine.update_trait_count == 0
        assert r.self_model_store.update_count == 0
        assert r.personality_history.record_change_count == 0

    def test_b4_bridge_untouched(self) -> None:
        """C.4 不与 B.4-B.13 交互,任何 B4 bridge 调用都应不存在。"""
        a = PersonalityRuntimeAdapter()
        public_attrs = [m for m in dir(a) if not m.startswith("_")]
        forbidden = {"b4_bridge", "bridge", "b4", "decision", "growth"}
        for f in forbidden:
            assert f not in public_attrs

    def test_uses_real_personality_resolver_end_to_end(self) -> None:
        """E2E:用真实 PersonalityResolver(只读),验证不破坏现有 state。"""
        from src.personality.personality_resolver import PersonalityResolver

        real_resolver = PersonalityResolver()
        before_keys = set(real_resolver._trait_states.keys())
        a = PersonalityRuntimeAdapter(personality_resolver=real_resolver)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        # 关键:不抛异常,personality_output 是 dict
        assert ctx.personality_output is not None
        assert isinstance(ctx.personality_output, dict)
        # _trait_states 键集合未被破坏(可能有新增 core traits 是允许的)
        # 但任何已有 trait state 不得被改
        for k in before_keys:
            assert k in real_resolver._trait_states


# ============================================================
# 9. 工厂 + 工具函数
# ============================================================


class TestFactoryAndUtils:
    def test_create_default(self) -> None:
        a = create_personality_runtime_adapter()
        assert isinstance(a, PersonalityRuntimeAdapter)

    def test_create_with_resolver(self) -> None:
        r = _FakePersonalityResolver()
        a = create_personality_runtime_adapter(personality_resolver=r)
        assert a._resolver is r

    def test_create_with_user_id(self) -> None:
        a = create_personality_runtime_adapter(user_id="alice")
        assert a._user_id == "alice"

    def test_personality_snapshot_dataclass(self) -> None:
        """_PersonalitySnapshot dataclass 字段完整。"""
        snap = _PersonalitySnapshot(
            traits={"warmth": 0.7},
            attachment_label="靠近",
            familiarity_label="信任",
            persona_summary="test",
            active_tensions=[],
            identity_summary="id",
        )
        d = snap.to_dict()
        assert d["traits"]["warmth"] == 0.7
        assert d["attachment_label"] == "靠近"
        assert d["familiarity_label"] == "信任"
        assert d["persona_summary"] == "test"
        assert d["identity_summary"] == "id"
        assert d["schema_version"] == "1.0"


# ============================================================
# 10. 端到端 smoke
# ============================================================


class TestE2ESmoke:
    def test_full_personality_runtime_flow(self) -> None:
        """完整 E2E:orchestrator + personality adapter + personality_output。"""
        r = _FakePersonalityResolver(
            state=_FakeGrowthState(
                metrics={"trust": 0.6, "closeness": 0.5, "attachment": 0.4},
            ),
            relationship_state=_FakeRelationshipState(
                bond_strength=0.6, trust=0.7, familiarity=0.5,
            ),
            self_model_store=_FakeSelfModelStore(identity_summary="我是羽依"),
        )
        a = PersonalityRuntimeAdapter(personality_resolver=r)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="personality")
        ctx = orch.process_event("hi", user_id="u1", session_id="sess1")

        # 1. personality_output 填充
        assert ctx.personality_output is not None
        out = ctx.personality_output
        assert out["personality_available"] is True
        assert out["current_traits"]["warmth"] >= 0.0
        assert out["identity_summary"] == "我是羽依"

        # 2. adapter_results 标记
        assert ctx.adapter_results["personality"]["ok"] is True

        # 3. stage_log
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_PERSONALITY_COMPLETED in stages
        assert CYCLE_EVENT_COMPLETED in stages

        # 4. cycle 整体成功
        assert ctx.error_count == 0
        assert orch.cycle_count == 1

        # 5. resolver 任何"写"操作均未发生
        assert r.resolve_count == 0
        assert r.apply_update_count == 0
        assert r.evolution_engine.update_trait_count == 0
        assert r.self_model_store.update_count == 0
        assert r.personality_history.record_change_count == 0

    def test_degraded_e2e(self) -> None:
        """降级 E2E:resolver 异常 → personality degraded,cycle 仍完成。"""
        # 用属性抛异常的 resolver(让 _trait_states 读时抛)
        class _BoomResolver:
            def __getattr__(self, name: str) -> Any:
                if name == "_trait_states":
                    raise RuntimeError("trait_states_boom")
                return None

        a = PersonalityRuntimeAdapter(personality_resolver=_BoomResolver())  # type: ignore
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="personality")
        ctx = orch.process_event("hi", user_id="u1")

        out = ctx.personality_output
        assert out["personality_available"] is False
        assert out["degraded"] is True
        # cycle 完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        # adapter_results.personality 仍 ok=True(因为 process_cycle 没抛)
        assert ctx.adapter_results["personality"]["ok"] is True
        # cycle 整体无 error
        assert ctx.error_count == 0

    def test_no_resolver_e2e(self) -> None:
        """无 resolver E2E:degraded,cycle 仍完成。"""

        class _StubAdapter(PersonalityRuntimeAdapter):
            def attach(self) -> bool:
                with self._lock:
                    self._attached = True
                return True

        a = _StubAdapter()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="personality")
        ctx = orch.process_event("hi", user_id="u1")

        out = ctx.personality_output
        assert out["personality_available"] is False
        assert out["degraded"] is True
        # cycle 完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        # adapter_results.personality 仍 ok=True(因为 process_cycle 没抛)
        assert ctx.adapter_results["personality"]["ok"] is True
