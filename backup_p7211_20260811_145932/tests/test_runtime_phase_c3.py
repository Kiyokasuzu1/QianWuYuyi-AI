# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_c3.py

Phase C.3 Emotion Runtime Integration —— EmotionRuntimeAdapter 测试

覆盖 7 类:
  1. TestEmotionAdapterBasic   - 初始化 / Protocol 兼容 / schema
  2. TestEmotionReadOnly       - 不调用 process_event / update / 不修改 state
  3. TestEmotionProcessing     - 正常状态 / 空状态 / 缺字段
  4. TestRuntimeIntegration    - 注册到 RuntimeCycle / ctx.emotion_output 生成 / step 顺序
  5. TestExceptionIsolation    - manager 异常 / context 异常 / fallback
  6. TestHealthSnapshot        - health_check / snapshot
  7. TestRegression            - 不修改 C.1 / C.2 / B.4-B.13

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化 EmotionManager
  - 不依赖 RuntimeCore 单例
"""
from __future__ import annotations

import sys
import os
import time
import threading
import tempfile
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ============================================================
# 0. 路径 + 导入
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.runtime.adapters.impl.emotion_runtime_adapter import (
    EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION,
    PHASE_C3_NAME,
    PHASE_C3_VERSION,
    PHASE_C3_STAGE_EMOTION_READ,
    PHASE_C3_STAGE_EMOTION_DEGRADED,
    ALL_PHASE_C3_STAGES,
    SOURCE_NAME,
    EmotionRuntimeAdapter,
    create_emotion_runtime_adapter,
    safe_get_emotion_adapter_summary,
    _EmotionSnapshot,
)
from src.runtime.cycle_adapter import (
    is_cycle_adapter,
    STANDARD_ADAPTER_EMOTION,
)
from src.runtime.cycle_context import RuntimeCycleContext
from src.runtime.cycle_event import (
    CYCLE_EVENT_EMOTION_COMPLETED,
    CYCLE_EVENT_COMPLETED,
    PHASE_C1_STAGE_STEP_COMPLETED,
)
from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Helper:Mock EmotionManager
# ============================================================


class _FakeEmotionState:
    """模拟 EmotionState(只暴露 attribute,不暴露行为)。"""

    def __init__(
        self,
        valence: float = 0.0,
        arousal: float = 0.5,
        curiosity: float = 0.5,
        anxiety: float = 0.0,
        confidence: float = 0.5,
        energy: float = 0.5,
        dominant: str = "neutral",
        intensity: float = 0.5,
    ) -> None:
        self.valence = valence
        self.arousal = arousal
        self.curiosity = curiosity
        self.anxiety = anxiety
        self.confidence = confidence
        self.energy = energy
        self.dominant = dominant
        self.intensity = intensity
        self.updated_at = "2026-08-03T00:00:00"
        # 用于验证"不修改"语义
        self.modified_count = 0
        self.snapshot_before: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valence": self.valence,
            "arousal": self.arousal,
            "curiosity": self.curiosity,
            "anxiety": self.anxiety,
            "confidence": self.confidence,
            "energy": self.energy,
            "dominant": self.dominant,
            "intensity": self.intensity,
            "updated_at": self.updated_at,
        }

    def mark_modified(self) -> None:
        """测试用:任何写入都计 1 次。"""
        self.modified_count += 1


class _FakeEmotionContext:
    """模拟 EmotionContext。"""

    def __init__(
        self,
        mood: str = "neutral",
        summary: str = "ok",
        expression_tendencies: Optional[List[str]] = None,
    ) -> None:
        self.mood = mood
        self.summary = summary
        self.expression_tendencies = list(expression_tendencies or [])


class _FakeEmotionManager:
    """
    模拟 EmotionManager。

    **不实现**:
      - process_event  -> 禁止
      - update         -> 禁止
      - repository.save -> 禁止
      - trace_repository.append -> 禁止

    仅提供:
      - state (属性)
      - get_context(influence)
      - increment_analysis_counter (测试 monitor)
    """

    def __init__(
        self,
        state: Optional[_FakeEmotionState] = None,
        context: Optional[_FakeEmotionContext] = None,
        fail_get_context: bool = False,
        fail_state_attr: bool = False,
    ) -> None:
        self._state = state or _FakeEmotionState()
        self._context = context or _FakeEmotionContext()
        self._fail_get_context = fail_get_context
        self._fail_state_attr = fail_state_attr
        # monitor
        self.process_event_count = 0
        self.update_count = 0
        self.save_count = 0
        self.trace_append_count = 0
        self.get_context_count = 0

    @property
    def state(self) -> Any:
        if self._fail_state_attr:
            raise RuntimeError("state_attr_error")
        return self._state

    def get_context(self, influence: float = 0.3) -> Any:
        self.get_context_count += 1
        if self._fail_get_context:
            raise RuntimeError("get_context_error")
        return self._context

    # --- 以下方法绝不应该被调用(只读 adapter) ---
    def process_event(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.process_event_count += 1
        raise AssertionError(
            "EmotionRuntimeAdapter 不得调用 EmotionManager.process_event()"
        )

    def update(self) -> None:  # pragma: no cover
        self.update_count += 1
        raise AssertionError(
            "EmotionRuntimeAdapter 不得调用 EmotionManager.update()"
        )

    def save(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.save_count += 1
        raise AssertionError(
            "EmotionRuntimeAdapter 不得调用 repository.save()"
        )

    def trace_append(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.trace_append_count += 1
        raise AssertionError(
            "EmotionRuntimeAdapter 不得调用 trace_repository.append()"
        )


# ============================================================
# 2. 测试 1:Emotion Adapter 基础
# ============================================================


class TestEmotionAdapterBasic:
    def test_construction_no_dependencies(self) -> None:
        """无依赖构造:不抛。"""
        a = EmotionRuntimeAdapter()
        assert a.name == STANDARD_ADAPTER_EMOTION == "emotion"
        assert a.schema_version == EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"
        assert a.is_attached() is False

    def test_attach_with_existing_manager(self) -> None:
        """外部注入 manager 时,attach 不重建。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        assert a.is_attached() is True
        # manager 未被替换
        assert a._manager is mgr

    def test_attach_self_build_manager(self) -> None:
        """外部未注入时,attach 内部尝试建一个(可失败,不影响 attached=True)。"""
        a = EmotionRuntimeAdapter()
        a.attach()
        # 不论是否成功建出 manager,attached 必然 True
        assert a.is_attached() is True

    def test_detach(self) -> None:
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        assert a.is_attached() is True
        assert a.detach() is True
        assert a.is_attached() is False

    def test_implements_cycle_adapter_protocol(self) -> None:
        """必须满足 CycleAdapter duck-type。"""
        a = EmotionRuntimeAdapter()
        assert is_cycle_adapter(a) is True
        assert hasattr(a, "name")
        assert hasattr(a, "health_check")
        assert hasattr(a, "process_cycle")
        assert hasattr(a, "snapshot")
        assert hasattr(a, "attach")
        assert hasattr(a, "detach")

    def test_snapshot_basic(self) -> None:
        a = EmotionRuntimeAdapter(user_id="u1", influence=0.4)
        snap = a.snapshot()
        assert snap["name"] == "emotion"
        assert snap["schema_version"] == "1.0"
        assert snap["user_id"] == "u1"
        assert snap["influence"] == 0.4
        assert snap["process_count"] == 0
        assert snap["read_count"] == 0
        assert snap["degraded_count"] == 0
        assert snap["attached"] is False

    def test_schema_version_locked(self) -> None:
        """schema_version 冻结在 1.0。"""
        a = EmotionRuntimeAdapter()
        assert a.schema_version == "1.0"
        assert EMOTION_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"

    def test_default_user_id_and_influence(self) -> None:
        a = EmotionRuntimeAdapter()
        assert a._user_id == "yuyi"
        # influence 0.3 为默认(通过 snapshot 看)
        assert a.snapshot()["influence"] == 0.3

    def test_influence_clamped(self) -> None:
        a = EmotionRuntimeAdapter(influence=99.0)
        assert a.snapshot()["influence"] == 1.0
        a2 = EmotionRuntimeAdapter(influence=-0.5)
        assert a2.snapshot()["influence"] == 0.0


# ============================================================
# 3. 测试 2:只读特性(Read-Only)
# ============================================================


class TestEmotionReadOnly:
    def test_does_not_call_process_event(self) -> None:
        """绝不调 EmotionManager.process_event。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert mgr.process_event_count == 0

    def test_does_not_call_update(self) -> None:
        """绝不调 EmotionManager.update。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert mgr.update_count == 0

    def test_does_not_call_save(self) -> None:
        """绝不调 repository.save。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert mgr.save_count == 0

    def test_does_not_call_trace_append(self) -> None:
        """绝不调 trace_repository.append。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert mgr.trace_append_count == 0

    def test_does_not_modify_state(self) -> None:
        """绝不修改 state。"""
        state = _FakeEmotionState(
            valence=0.3, arousal=0.7, dominant="joyful", intensity=0.65,
        )
        before = state.to_dict()
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()

        # 跑 5 次 process_cycle
        for _ in range(5):
            ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
            a.process_cycle(ctx)
        # state 不被修改
        assert state.to_dict() == before
        assert state.modified_count == 0

    def test_get_context_readonly(self) -> None:
        """get_context 应被读(>=1 次)但不修改 state。"""
        state = _FakeEmotionState()
        ctx_obj = _FakeEmotionContext(mood="joyful")
        mgr = _FakeEmotionManager(state=state, context=ctx_obj)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        # get_context 至少被调 1 次
        assert mgr.get_context_count >= 1
        # ctx_obj 的字段未被修改(传引用,不应改)
        assert ctx_obj.mood == "joyful"

    def test_multiple_process_cycle_remains_readonly(self) -> None:
        """多次 process_cycle 仍只读。"""
        state = _FakeEmotionState()
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        for i in range(20):
            ctx = RuntimeCycleContext(input_event=f"msg_{i}", user_id="u")
            a.process_cycle(ctx)
        assert mgr.process_event_count == 0
        assert mgr.update_count == 0
        assert mgr.save_count == 0
        assert mgr.trace_append_count == 0
        assert state.modified_count == 0


# ============================================================
# 4. 测试 3:Emotion Processing(正常 / 空 / 缺字段)
# ============================================================


class TestEmotionProcessing:
    def test_normal_state(self) -> None:
        """正常状态:emotion_output 完整字段。"""
        state = _FakeEmotionState(
            valence=0.5, arousal=0.8, curiosity=0.7, anxiety=0.1,
            confidence=0.9, energy=0.6, dominant="joyful", intensity=0.7,
        )
        ctx_obj = _FakeEmotionContext(
            mood="joyful",
            summary="user 开心",
            expression_tendencies=["smile", "warm"],
        )
        mgr = _FakeEmotionManager(state=state, context=ctx_obj)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)

        out = ctx.emotion_output
        assert out is not None
        assert isinstance(out, dict)
        assert out["emotion_available"] is True
        assert out["degraded"] is False
        assert out["source"] == SOURCE_NAME == "emotion_runtime_adapter"
        # current_state
        cs = out["current_state"]
        assert cs["valence"] == 0.5
        assert cs["arousal"] == 0.8
        assert cs["curiosity"] == 0.7
        assert cs["anxiety"] == 0.1
        assert cs["confidence"] == 0.9
        assert cs["energy"] == 0.6
        # emotion label / mood
        assert out["emotion"] == "joyful"
        assert out["mood"] == "joyful"
        assert out["context_summary"] == "user 开心"
        assert out["intensity"] == 0.8  # = arousal
        # 顶层冗余字段
        assert out["valence"] == 0.5
        assert out["arousal"] == 0.8
        assert out["confidence"] == 0.9
        # 表达倾向
        assert isinstance(out["expression_tendencies"], list)
        assert "smile" in out["expression_tendencies"]
        assert "warm" in out["expression_tendencies"]

    def test_empty_state(self) -> None:
        """空 state:6 维都为默认,仍输出 OK(只要 state 存在)。"""
        state = _FakeEmotionState(
            valence=0.0, arousal=0.0, curiosity=0.0, anxiety=0.0,
            confidence=0.0, energy=0.0, dominant="", intensity=0.0,
        )
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        # 仍可读,emotion_available = True
        assert out["emotion_available"] is True
        assert out["degraded"] is False
        assert out["current_state"]["valence"] == 0.0
        # mood 兜底为 "neutral"
        assert out["mood"] == "neutral"

    def test_missing_state_attributes(self) -> None:
        """state 缺字段(比如 dominant/intensity 抛异常),仍输出 OK。"""
        class _PartialState:
            valence = 0.2
            arousal = 0.6
            curiosity = 0.5
            anxiety = 0.0
            confidence = 0.7
            energy = 0.5

            @property
            def dominant(self):  # type: ignore
                raise RuntimeError("dominant_error")

            @property
            def intensity(self):  # type: ignore
                raise RuntimeError("intensity_error")

        mgr = _FakeEmotionManager(state=_PartialState())  # type: ignore
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        assert out["emotion_available"] is True
        assert out["degraded"] is False
        # 6 维正常
        assert out["current_state"]["valence"] == 0.2
        # dominant 缺失时 mood 兜底 neutral
        assert out["mood"] == "neutral"

    def test_get_context_failure_falls_back(self) -> None:
        """get_context 异常时,降级到 state.dominant,mood 不为空。"""
        state = _FakeEmotionState(dominant="serene")
        mgr = _FakeEmotionManager(state=state, fail_get_context=True)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        assert out["emotion_available"] is True
        assert out["degraded"] is False
        # 降级到 state.dominant
        assert out["mood"] == "serene"
        # 表达倾向为空列表
        assert out["expression_tendencies"] == []

    def test_state_none_returns_degraded(self) -> None:
        """state 为 None → degraded。"""
        mgr = _FakeEmotionManager()
        mgr._state = None  # type: ignore
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        assert out["emotion_available"] is False
        assert out["degraded"] is True
        assert out["source"] == "emotion_runtime_adapter"
        assert "error" in out

    def test_manager_none_returns_degraded(self) -> None:
        """manager 为 None → degraded。"""
        a = EmotionRuntimeAdapter()  # 不传 manager
        # 不 attach,manager 仍为 None
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        assert out["emotion_available"] is False
        assert out["degraded"] is True
        assert out["source"] == "emotion_runtime_adapter"

    def test_state_clamping(self) -> None:
        """值超出范围会被 clamp 到合法区间。"""
        state = _FakeEmotionState(
            valence=5.0, arousal=-0.5, curiosity=2.0,
            anxiety=-0.1, confidence=2.0, energy=99.0,
        )
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        cs = out["current_state"]
        assert -1.0 <= cs["valence"] <= 1.0
        assert 0.0 <= cs["arousal"] <= 1.0
        assert 0.0 <= cs["curiosity"] <= 1.0
        assert 0.0 <= cs["anxiety"] <= 1.0
        assert 0.0 <= cs["confidence"] <= 1.0
        assert 0.0 <= cs["energy"] <= 1.0

    def test_read_emotion_public(self) -> None:
        """read_emotion 公开接口,返回统一 output。"""
        # 显式设置 context.mood="positive",使 mood 来自 context
        state = _FakeEmotionState(dominant="positive")
        ctx_obj = _FakeEmotionContext(mood="positive")
        mgr = _FakeEmotionManager(state=state, context=ctx_obj)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        out = a.read_emotion()
        assert out["emotion_available"] is True
        assert out["mood"] == "positive"
        # 计数器累加
        assert a.read_count == 1

    def test_read_emotion_empty_mood_falls_back_neutral(self) -> None:
        """context.mood 为空时,降级到 neutral(规格定义)。"""
        state = _FakeEmotionState(dominant="serene")
        ctx_obj = _FakeEmotionContext(mood="")  # 空 mood
        mgr = _FakeEmotionManager(state=state, context=ctx_obj)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        out = a.read_emotion()
        # context.mood 为空 → 降级到 "neutral"
        assert out["mood"] == "neutral"

    def test_unknown_event_type(self) -> None:
        """未知类型 event 不抛。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        for ev in [None, "", 0, {}, [], object()]:
            ctx = RuntimeCycleContext(input_event=ev)
            a.process_cycle(ctx)
            assert ctx.emotion_output is not None


# ============================================================
# 5. 测试 4:Runtime Integration
# ============================================================


class TestRuntimeIntegration:
    def test_register_with_orchestrator(self) -> None:
        """EmotionRuntimeAdapter 可注册到 RuntimeCycleOrchestrator。"""
        a = EmotionRuntimeAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        ok = orch.register_adapter(a, name="emotion")
        assert ok is True
        assert orch.has_adapter("emotion") is True
        assert orch.adapter_count == 1

    def test_full_cycle_integration(self) -> None:
        """完整 cycle:orchestrator 调度 emotion adapter,ctx.emotion_output 被填充。"""
        state = _FakeEmotionState(
            valence=0.4, arousal=0.6, curiosity=0.5, anxiety=0.1,
            confidence=0.7, energy=0.6, dominant="positive", intensity=0.5,
        )
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        ctx = orch.process_event("hi", user_id="u1", session_id="s1")

        # adapter 被调用
        assert a.process_count == 1
        # ctx.emotion_output 被填充
        assert ctx.emotion_output is not None
        assert isinstance(ctx.emotion_output, dict)
        assert ctx.emotion_output["emotion_available"] is True
        # adapter_results 应有 emotion
        assert ctx.adapter_results["emotion"]["ok"] is True

    def test_step_order_emotion_after_memory(self) -> None:
        """emotion 是 5 步标准 adapter 的第 2 步,memory 在 emotion 之前。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        ctx = orch.process_event("hi", user_id="u1")
        # emotion 应在 cycle 之内
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_EMOTION_COMPLETED in stages
        # emotion 在 cycle 完成前
        assert stages.index(CYCLE_EVENT_EMOTION_COMPLETED) < stages.index(CYCLE_EVENT_COMPLETED)

    def test_emotion_in_5_step_position(self) -> None:
        """注册 memory + emotion 时,emotion 在 memory 之后。"""
        from src.runtime.adapters.impl.memory_runtime_adapter import (
            MemoryRuntimeAdapter,
        )

        a_em = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a_em.attach()
        a_mem = MemoryRuntimeAdapter(
            memory_store=None, vector_memory=None, enable_vector=False, enable_store=False,
        )
        a_mem.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a_mem, name="memory")
        orch.register_adapter(a_em, name="emotion")
        ctx = orch.process_event("hi", user_id="u1")
        # memory_output 与 emotion_output 都填
        assert ctx.memory_output is not None
        assert ctx.emotion_output is not None
        # 顺序: memory -> emotion
        stages = ctx.get_stage_names()
        from src.runtime.cycle_event import CYCLE_EVENT_MEMORY_COMPLETED
        mem_idx = stages.index(CYCLE_EVENT_MEMORY_COMPLETED)
        em_idx = stages.index(CYCLE_EVENT_EMOTION_COMPLETED)
        assert mem_idx < em_idx

    def test_orchestrator_handles_emotion_failure(self) -> None:
        """emotion adapter 抛异常时,orchestrator 标记 degraded 但不中断。"""

        class _BoomAdapter(EmotionRuntimeAdapter):
            def process_cycle(self, ctx):
                raise RuntimeError("emotion_boom")

        a = _BoomAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.adapter_results["emotion"]["ok"] is False
        assert "emotion" in ctx.degraded_adapters
        # cycle 仍完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert orch.cycle_count == 1

    def test_emotion_uses_memory_output(self) -> None:
        """emotion 可读 ctx.memory_output(虽然不强制依赖)。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.memory_output = {"query": "hi", "matched": []}
        a.process_cycle(ctx)
        # emotion_output 已填,不影响
        assert ctx.emotion_output is not None
        assert ctx.emotion_output["emotion_available"] is True

    def test_emotion_uses_input_event(self) -> None:
        """emotion 可读 ctx.input_event。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        ctx = RuntimeCycleContext(
            input_event={"user_input": "hello yuyi", "type": "chat"},
            user_id="u1",
        )
        a.process_cycle(ctx)
        assert ctx.emotion_output is not None

    def test_emotion_uses_metadata(self) -> None:
        """emotion 可读 ctx.metadata(不影响主流程)。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.metadata["session_topic"] = "small_talk"
        a.process_cycle(ctx)
        assert ctx.emotion_output is not None


# ============================================================
# 6. 测试 5:异常隔离
# ============================================================


class TestExceptionIsolation:
    def test_manager_exception_during_get_context(self) -> None:
        """manager.get_context 抛异常时,fail-soft,降级到 state。"""
        state = _FakeEmotionState(dominant="serene")
        mgr = _FakeEmotionManager(state=state, fail_get_context=True)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        # 不抛,且 emotion_available 仍为 True(降级用 state)
        assert ctx.emotion_output is not None
        assert ctx.emotion_output["emotion_available"] is True
        assert ctx.emotion_output["mood"] == "serene"

    def test_state_attribute_exception(self) -> None:
        """manager.state 属性抛异常时,降级。"""
        mgr = _FakeEmotionManager(fail_state_attr=True)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.emotion_output
        assert out["emotion_available"] is False
        assert out["degraded"] is True
        assert "error" in out

    def test_process_cycle_with_invalid_ctx(self) -> None:
        """非 RuntimeCycleContext,直接返回原对象。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        fake = {"x": 1}
        out = a.process_cycle(fake)
        assert out is fake

    def test_process_cycle_with_none_ctx(self) -> None:
        """ctx 为 None,不抛,返回 None。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        out = a.process_cycle(None)  # type: ignore
        # 不抛,返回 None
        assert out is None

    def test_read_emotion_with_boom_manager(self) -> None:
        """read_emotion 在 manager 异常时降级。"""
        mgr = _FakeEmotionManager(fail_state_attr=True)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        out = a.read_emotion()
        assert out["emotion_available"] is False
        assert out["degraded"] is True

    def test_process_cycle_increments_degraded_count(self) -> None:
        """degraded 时,degraded_count 累加。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        # 第 1 次:正常
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        assert a.degraded_count == 0
        # 切到 fail 状态
        mgr._fail_state_attr = True
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        # degraded_count >= 3
        assert a.degraded_count >= 3

    def test_health_check_with_exception(self) -> None:
        """health_check 在 manager state 异常时不抛。"""
        mgr = _FakeEmotionManager(fail_state_attr=True)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        # health 标记 degraded
        assert hc["status"] == "degraded"
        assert hc["manager_available"] is True
        assert hc["state_available"] is False

    def test_concurrent_process_cycle(self) -> None:
        """并发 process_cycle 不抛。"""
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
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
    def test_health_basic_with_manager(self) -> None:
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert hc["adapter"] == "emotion"
        assert hc["status"] in ("healthy", "degraded")
        assert hc["manager_available"] is True
        assert hc["attached"] is True

    def test_health_without_manager(self) -> None:
        """未 attach 时(无 manager)→ status degraded。"""
        a = EmotionRuntimeAdapter()
        # 不调用 attach(),这样不会自建 manager
        hc = a.health_check()
        assert isinstance(hc, dict)
        # manager 不存在 → status degraded
        assert hc["status"] == "degraded"
        assert hc["manager_available"] is False

    def test_health_counts(self) -> None:
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        hc = a.health_check()
        assert hc["process_count"] == 3

    def test_health_records_schema_version(self) -> None:
        a = EmotionRuntimeAdapter()
        hc = a.health_check()
        assert hc["schema_version"] == "1.0"

    def test_snapshot_basic(self) -> None:
        mgr = _FakeEmotionManager()
        a = EmotionRuntimeAdapter(emotion_manager=mgr, user_id="alice", influence=0.5)
        a.attach()
        snap = a.snapshot()
        assert snap["name"] == "emotion"
        assert snap["schema_version"] == "1.0"
        assert snap["user_id"] == "alice"
        assert snap["influence"] == 0.5
        assert snap["process_count"] == 0
        assert snap["read_count"] == 0
        assert snap["degraded_count"] == 0
        assert snap["attached"] is True
        assert snap["manager_available"] is True

    def test_snapshot_includes_last_state(self) -> None:
        state = _FakeEmotionState(
            valence=0.4, arousal=0.6, dominant="positive", intensity=0.5,
        )
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        snap = a.snapshot()
        # last_state 应包含 6 维 + dominant + intensity
        ls = snap["last_state"]
        assert ls["valence"] == 0.4
        assert ls["arousal"] == 0.6
        assert ls["dominant"] == "positive"

    def test_snapshot_available_flag(self) -> None:
        a = EmotionRuntimeAdapter()
        # 未 attach / 未读
        snap = a.snapshot()
        # available 应为 False
        assert snap["available"] is False
        # 读一次后
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        snap2 = a.snapshot()
        assert snap2["available"] is True

    def test_safe_summary_with_none(self) -> None:
        s = safe_get_emotion_adapter_summary(None)
        assert s["name"] == "emotion"
        assert s["attached"] is False
        assert s["available"] is False

    def test_safe_summary_with_adapter(self) -> None:
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        s = safe_get_emotion_adapter_summary(a)
        assert s["name"] == "emotion"
        assert s["attached"] is True


# ============================================================
# 8. 测试 7:Regression
# ============================================================


class TestRegression:
    def test_does_not_modify_emotion_state(self) -> None:
        """state 不被修改。"""
        state = _FakeEmotionState(
            valence=0.2, arousal=0.6, dominant="positive", intensity=0.5,
        )
        before = state.to_dict()
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="x"))
        a.read_emotion()
        # state 没动
        assert state.to_dict() == before
        # manager 任何"写"调用计数均为 0
        assert mgr.process_event_count == 0
        assert mgr.update_count == 0
        assert mgr.save_count == 0
        assert mgr.trace_append_count == 0

    def test_does_not_modify_runtime_cycle_context(self) -> None:
        """除 emotion_output 外,ctx 其他字段不应被改。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        ctx = RuntimeCycleContext(
            input_event="hi", user_id="u1", session_id="s1",
        )
        ctx.metadata["k"] = "v"
        ctx.memory_output = {"existing": True}
        before = {
            "cycle_id": ctx.cycle_id,
            "session_id": ctx.session_id,
            "user_id": ctx.user_id,
            "input_event": ctx.input_event,
            "metadata": dict(ctx.metadata),
            "memory_output": ctx.memory_output,
            "personality_output": ctx.personality_output,
            "relationship_output": ctx.relationship_output,
            "growth_output": list(ctx.growth_output),
        }
        a.process_cycle(ctx)
        # emotion_output 被填
        assert ctx.emotion_output is not None
        # 其他字段不变
        assert ctx.cycle_id == before["cycle_id"]
        assert ctx.session_id == before["session_id"]
        assert ctx.user_id == before["user_id"]
        assert ctx.input_event == before["input_event"]
        assert ctx.metadata == before["metadata"]
        assert ctx.memory_output is before["memory_output"]
        assert ctx.personality_output is before["personality_output"]
        assert ctx.relationship_output is before["relationship_output"]
        assert ctx.growth_output == before["growth_output"]

    def test_does_not_modify_orchestrator(self) -> None:
        """RuntimeCycleOrchestrator 公共方法集合不变。"""
        a = EmotionRuntimeAdapter(emotion_manager=_FakeEmotionManager())
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        orch.process_event("x", user_id="u1")
        # orchestrator 仍可被外部使用
        ctx2 = orch.process_event("y", user_id="u1")
        assert ctx2.cycle_id != ""

    def test_does_not_call_forbidden_methods(self) -> None:
        """所有禁止方法均未被调。"""
        state = _FakeEmotionState()
        mgr = _FakeEmotionManager(state=state)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        # 验证所有禁止方法都没被调
        assert mgr.process_event_count == 0
        assert mgr.update_count == 0
        assert mgr.save_count == 0
        assert mgr.trace_append_count == 0

    def test_b4_bridge_untouched(self) -> None:
        """C.3 不与 B.4-B.13 交互,任何 B4 bridge 调用都应不存在。"""
        a = EmotionRuntimeAdapter()
        public_attrs = [m for m in dir(a) if not m.startswith("_")]
        forbidden = {"b4_bridge", "bridge", "b4", "decision", "growth", "memory"}
        for f in forbidden:
            assert f not in public_attrs

    def test_phase_c3_constants(self) -> None:
        """Phase C.3 常量完整。"""
        for name in [
            "PHASE_C3_STAGE_EMOTION_READ",
            "PHASE_C3_STAGE_EMOTION_DEGRADED",
        ]:
            assert name in dir(sys.modules["src.runtime.adapters.impl.emotion_runtime_adapter"])
        assert PHASE_C3_NAME == "phase_c3"
        assert PHASE_C3_VERSION == "1.0.0"
        assert len(ALL_PHASE_C3_STAGES) == 2
        assert SOURCE_NAME == "emotion_runtime_adapter"

    def test_uses_real_emotion_state_end_to_end(self) -> None:
        """E2E:用真实 EmotionState(只读),验证不破坏现有 state。"""
        from src.emotion.emotion_state import EmotionState

        real_state = EmotionState(
            valence=0.3, arousal=0.6, curiosity=0.5, anxiety=0.1,
            confidence=0.7, energy=0.5,
        )
        before = real_state.to_dict()
        mgr = _FakeEmotionManager(state=real_state)  # type: ignore
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        # 关键:不抛异常,emotion_output 是 dict
        assert ctx.emotion_output is not None
        assert isinstance(ctx.emotion_output, dict)
        assert ctx.emotion_output["emotion_available"] is True
        assert ctx.emotion_output["mood"] == real_state.dominant
        # state 内容未被破坏
        after = real_state.to_dict()
        assert before["valence"] == after["valence"]
        assert before["arousal"] == after["arousal"]
        assert before["dominant"] == after["dominant"]


# ============================================================
# 9. 工厂 + 工具函数
# ============================================================


class TestFactoryAndUtils:
    def test_create_default(self) -> None:
        a = create_emotion_runtime_adapter()
        assert isinstance(a, EmotionRuntimeAdapter)

    def test_create_with_manager(self) -> None:
        mgr = _FakeEmotionManager()
        a = create_emotion_runtime_adapter(emotion_manager=mgr)
        assert a._manager is mgr

    def test_create_with_user_id(self) -> None:
        a = create_emotion_runtime_adapter(user_id="alice")
        assert a._user_id == "alice"

    def test_emotion_snapshot_dataclass(self) -> None:
        """_EmotionSnapshot dataclass 字段完整。"""
        snap = _EmotionSnapshot(
            valence=0.5, arousal=0.7, curiosity=0.6, anxiety=0.1,
            confidence=0.8, energy=0.7, dominant="joyful", intensity=0.65,
        )
        d = snap.to_dict()
        assert d["valence"] == 0.5
        assert d["arousal"] == 0.7
        assert d["dominant"] == "joyful"

    def test_emotion_snapshot_from_state(self) -> None:
        """_EmotionSnapshot.from_state 安全提取。"""
        state = _FakeEmotionState(
            valence=0.3, arousal=0.6, curiosity=0.5, anxiety=0.1,
            confidence=0.7, energy=0.5, dominant="positive", intensity=0.45,
        )
        snap = _EmotionSnapshot.from_state(state)
        assert snap.valence == 0.3
        assert snap.arousal == 0.6
        assert snap.dominant == "positive"
        assert snap.intensity == 0.45

    def test_emotion_snapshot_from_state_with_exception(self) -> None:
        """from_state 在 state 异常时降级。"""
        class _BoomState:
            @property
            def valence(self):  # type: ignore
                raise RuntimeError("boom")

        snap = _EmotionSnapshot.from_state(_BoomState())  # type: ignore
        # 不抛,返回默认 snapshot
        assert isinstance(snap, _EmotionSnapshot)
        assert snap.valence == 0.0


# ============================================================
# 10. 端到端 smoke
# ============================================================


class TestE2ESmoke:
    def test_full_emotion_runtime_flow(self) -> None:
        """完整 E2E:orchestrator + emotion adapter + emotion_output。"""
        state = _FakeEmotionState(
            valence=0.4, arousal=0.6, curiosity=0.5, anxiety=0.1,
            confidence=0.7, energy=0.6, dominant="positive", intensity=0.5,
        )
        ctx_obj = _FakeEmotionContext(
            mood="positive",
            summary="user 心情不错",
            expression_tendencies=["smile", "nod"],
        )
        mgr = _FakeEmotionManager(state=state, context=ctx_obj)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        ctx = orch.process_event("hi", user_id="u1", session_id="sess1")

        # 1. emotion_output 填充
        assert ctx.emotion_output is not None
        out = ctx.emotion_output
        assert out["emotion_available"] is True
        assert out["mood"] == "positive"
        assert out["current_state"]["valence"] == 0.4
        assert "smile" in out["expression_tendencies"]

        # 2. adapter_results 标记
        assert ctx.adapter_results["emotion"]["ok"] is True

        # 3. stage_log
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_EMOTION_COMPLETED in stages
        assert CYCLE_EVENT_COMPLETED in stages

        # 4. cycle 整体成功
        assert ctx.error_count == 0
        assert orch.cycle_count == 1

        # 5. manager 任何"写"操作均未发生
        assert mgr.process_event_count == 0
        assert mgr.update_count == 0
        assert mgr.save_count == 0
        assert mgr.trace_append_count == 0

    def test_degraded_e2e(self) -> None:
        """降级 E2E:manager state 异常 → emotion degraded,cycle 仍完成。"""
        mgr = _FakeEmotionManager(fail_state_attr=True)
        a = EmotionRuntimeAdapter(emotion_manager=mgr)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        ctx = orch.process_event("hi", user_id="u1")

        out = ctx.emotion_output
        assert out["emotion_available"] is False
        assert out["degraded"] is True
        # cycle 完成(emotion adapter 内部 fail-soft,orchestrator 视为 OK)
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        # adapter_results.emotion 仍 ok=True(因为 process_cycle 没抛)
        assert ctx.adapter_results["emotion"]["ok"] is True
        # cycle 整体无 error
        assert ctx.error_count == 0

    def test_no_manager_e2e(self) -> None:
        """无 manager E2E:degraded,cycle 仍完成。"""
        # 构造一个不自动 build manager 的 adapter
        class _StubAdapter(EmotionRuntimeAdapter):
            def attach(self) -> bool:
                # 故意不建 manager
                with self._lock:
                    self._attached = True
                return True

        a = _StubAdapter()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="emotion")
        ctx = orch.process_event("hi", user_id="u1")

        out = ctx.emotion_output
        assert out["emotion_available"] is False
        assert out["degraded"] is True
        # cycle 完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        # adapter_results.emotion 仍 ok=True(因为 process_cycle 没抛)
        assert ctx.adapter_results["emotion"]["ok"] is True
