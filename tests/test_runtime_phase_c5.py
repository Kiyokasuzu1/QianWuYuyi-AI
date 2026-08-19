# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_c5.py

Phase C.5 Relationship Runtime Integration —— RelationshipRuntimeAdapter 测试

覆盖 7 类:
  1. TestRelationshipAdapterBasic   - 初始化 / Protocol 兼容 / schema
  2. TestRelationshipReadOnly       - 不调用 process_interaction / save_* / append / 字段赋值
  3. TestRelationshipProcessing     - 正常状态 / 空状态 / 缺字段 / 极端值
  4. TestRuntimeIntegration         - 注册到 RuntimeCycle / ctx.relationship_output 生成 / step 顺序
  5. TestExceptionIsolation         - state 异常 / repo 异常 / 字段异常 / fallback
  6. TestHealthSnapshot             - health_check / snapshot
  7. TestRegression                 - 不修改 C.1 / C.2 / C.3 / C.4 / B.4-B.13

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化 RelationshipState / RelationshipModel / RelationshipRepository
  - 不依赖 RuntimeCore 单例
"""
from __future__ import annotations

import sys
import os
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


from src.runtime.adapters.impl.relationship_runtime_adapter import (
    RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION,
    PHASE_C5_NAME,
    PHASE_C5_VERSION,
    PHASE_C5_STAGE_RELATIONSHIP_READ,
    PHASE_C5_STAGE_RELATIONSHIP_DEGRADED,
    ALL_PHASE_C5_STAGES,
    SOURCE_NAME,
    STAGE_LABEL_MAP,
    RelationshipRuntimeAdapter,
    create_relationship_runtime_adapter,
    safe_get_relationship_adapter_summary,
    _RelationshipSnapshot,
)
from src.runtime.cycle_adapter import (
    is_cycle_adapter,
    STANDARD_ADAPTER_RELATIONSHIP,
)
from src.runtime.cycle_context import RuntimeCycleContext
from src.runtime.cycle_event import (
    CYCLE_EVENT_RELATIONSHIP_COMPLETED,
    CYCLE_EVENT_COMPLETED,
    CYCLE_EVENT_MEMORY_COMPLETED,
    CYCLE_EVENT_EMOTION_COMPLETED,
    CYCLE_EVENT_PERSONALITY_COMPLETED,
)
from src.runtime.phase_c1_integration import RuntimeCycleOrchestrator


# ============================================================
# 1. Helper:Mock Relationship State / Model / Repository
# ============================================================


class _FakeRelationshipState:
    """模拟 RelationshipState(只暴露 attribute,不暴露行为)。

    **直接对 familiarity/trust/collaboration 赋值会触发 AssertionError**。
    """

    def __init__(
        self,
        familiarity: float = 0.0,
        trust: float = 0.0,
        collaboration: float = 0.0,
        interaction_frequency: float = 0.0,
        relationship_stage: str = "initial",
        communication_style: Optional[List[str]] = None,
        last_interaction_at: str = "2026-08-03T00:00:00",
        fail_attrs: bool = False,
    ) -> None:
        # 写入通过内部方法,不暴露给外部
        object.__setattr__(self, "_familiarity", float(familiarity))
        object.__setattr__(self, "_trust", float(trust))
        object.__setattr__(self, "_collaboration", float(collaboration))
        object.__setattr__(self, "_interaction_frequency", float(interaction_frequency))
        object.__setattr__(self, "_relationship_stage", str(relationship_stage))
        object.__setattr__(self, "_communication_style", list(communication_style or []))
        object.__setattr__(self, "_last_interaction_at", str(last_interaction_at))
        object.__setattr__(self, "_fail_attrs", bool(fail_attrs))
        # monitor
        object.__setattr__(self, "modified_count", 0)

    @property
    def familiarity(self) -> float:
        if self._fail_attrs:  # type: ignore
            raise RuntimeError("familiarity_error")
        return self._familiarity  # type: ignore

    @familiarity.setter
    def familiarity(self, value: float) -> None:  # type: ignore
        # ❌ Runtime adapter 绝不应修改这些字段
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.familiarity"
        )

    @property
    def trust(self) -> float:
        if self._fail_attrs:  # type: ignore
            raise RuntimeError("trust_error")
        return self._trust  # type: ignore

    @trust.setter
    def trust(self, value: float) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.trust"
        )

    @property
    def collaboration(self) -> float:
        if self._fail_attrs:  # type: ignore
            raise RuntimeError("collaboration_error")
        return self._collaboration  # type: ignore

    @collaboration.setter
    def collaboration(self, value: float) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.collaboration"
        )

    @property
    def interaction_frequency(self) -> float:
        if self._fail_attrs:  # type: ignore
            raise RuntimeError("interaction_frequency_error")
        return self._interaction_frequency  # type: ignore

    @interaction_frequency.setter
    def interaction_frequency(self, value: float) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.interaction_frequency"
        )

    @property
    def relationship_stage(self) -> str:
        if self._fail_attrs:  # type: ignore
            raise RuntimeError("relationship_stage_error")
        return self._relationship_stage  # type: ignore

    @relationship_stage.setter
    def relationship_stage(self, value: str) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.relationship_stage"
        )

    @property
    def communication_style(self) -> List[str]:
        if self._fail_attrs:  # type: ignore
            raise RuntimeError("communication_style_error")
        return self._communication_style  # type: ignore

    @communication_style.setter
    def communication_style(self, value: List[str]) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.communication_style"
        )

    @property
    def last_interaction_at(self) -> str:
        return self._last_interaction_at  # type: ignore

    @last_interaction_at.setter
    def last_interaction_at(self, value: str) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得修改 state.last_interaction_at"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "familiarity": self.familiarity,
            "trust": self.trust,
            "collaboration": self.collaboration,
            "interaction_frequency": self.interaction_frequency,
            "relationship_stage": self.relationship_stage,
            "communication_style": self.communication_style,
            "last_interaction_at": self.last_interaction_at,
        }


class _FakeRelationshipModel:
    """模拟 RelationshipModel,只暴露 get_snapshot() + 只读 list。

    **append/extend 任何 list 会触发 AssertionError**。
    """

    def __init__(
        self,
        total_interactions: int = 0,
        total_trust_changes: int = 0,
        total_milestones: int = 0,
        total_shared_experiences: int = 0,
        trust_changes: Optional[List[Dict[str, Any]]] = None,
        interaction_history: Optional[List[Dict[str, Any]]] = None,
        fail_snapshot: bool = False,
    ) -> None:
        object.__setattr__(self, "_ti", int(total_interactions))
        object.__setattr__(self, "_ttc", int(total_trust_changes))
        object.__setattr__(self, "_tm", int(total_milestones))
        object.__setattr__(self, "_tse", int(total_shared_experiences))
        object.__setattr__(self, "_trust_changes", list(trust_changes or []))
        object.__setattr__(self, "_interaction_history", list(interaction_history or []))
        object.__setattr__(self, "_fail_snapshot", bool(fail_snapshot))
        # monitor
        object.__setattr__(self, "modified_count", 0)

    def get_snapshot(self) -> Dict[str, Any]:
        if self._fail_snapshot:  # type: ignore
            raise RuntimeError("snapshot_error")
        return {
            "total_interactions": int(self._ti),  # type: ignore
            "total_trust_changes": int(self._ttc),  # type: ignore
            "total_milestones": int(self._tm),  # type: ignore
            "total_shared_experiences": int(self._tse),  # type: ignore
        }

    @property
    def trust_changes(self) -> List[Dict[str, Any]]:
        return self._trust_changes  # type: ignore

    @trust_changes.setter
    def trust_changes(self, value: List[Dict[str, Any]]) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得替换 model.trust_changes"
        )

    @property
    def interaction_history(self) -> List[Dict[str, Any]]:
        return self._interaction_history  # type: ignore

    @interaction_history.setter
    def interaction_history(self, value: List[Dict[str, Any]]) -> None:  # type: ignore
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得替换 model.interaction_history"
        )

    # ⚠️ append 拦截
    def _record_append(self, list_name: str) -> None:
        self.modified_count += 1  # type: ignore
        raise AssertionError(
            f"RelationshipRuntimeAdapter 不得 append 到 model.{list_name}"
        )


class _FakeRelationshipRepository:
    """模拟 RelationshipRepository,只暴露 load_* 方法,绝不能调 save_*。"""

    def __init__(
        self,
        state: Optional[_FakeRelationshipState] = None,
        model: Optional[_FakeRelationshipModel] = None,
        fail_load_state: bool = False,
        fail_load_model: bool = False,
    ) -> None:
        self._state = state
        self._model = model
        self._fail_load_state = fail_load_state
        self._fail_load_model = fail_load_model
        # monitor
        self.load_state_count = 0
        self.load_model_count = 0
        self.save_state_count = 0
        self.save_model_count = 0

    def load_state(self) -> Optional[_FakeRelationshipState]:
        self.load_state_count += 1
        if self._fail_load_state:
            raise RuntimeError("load_state_error")
        return self._state

    def load_relationship_model(self) -> Optional[_FakeRelationshipModel]:
        self.load_model_count += 1
        if self._fail_load_model:
            raise RuntimeError("load_model_error")
        return self._model

    # --- 以下方法绝不应该被调用(只读 adapter) ---
    def save_state(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.save_state_count += 1
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得调用 repository.save_state()"
        )

    def save_relationship_model(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        self.save_model_count += 1
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得调用 repository.save_relationship_model()"
        )


class _FakeRelationshipIntelligenceEngine:
    """模拟 RelationshipIntelligenceEngine,任何 process_interaction() 都报错。"""

    def __init__(self) -> None:
        self.process_interaction_count = 0

    def process_interaction(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        self.process_interaction_count += 1
        raise AssertionError(
            "RelationshipRuntimeAdapter 不得调用 "
            "RelationshipIntelligenceEngine.process_interaction()"
        )


# ============================================================
# 2. 测试 1:Relationship Adapter 基础
# ============================================================


class TestRelationshipAdapterBasic:
    def test_construction_no_dependencies(self) -> None:
        """无依赖构造:不抛。"""
        a = RelationshipRuntimeAdapter()
        assert a.name == STANDARD_ADAPTER_RELATIONSHIP == "relationship"
        assert a.schema_version == RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"
        assert a.is_attached() is False

    def test_attach_with_existing_state(self) -> None:
        """外部注入 state 时,attach 不重建。"""
        state = _FakeRelationshipState(familiarity=0.5, trust=0.6)
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        assert a.is_attached() is True
        # state 未被替换
        assert a._state is state

    def test_attach_self_build_via_repository(self) -> None:
        """外部注入 repository 时,attach 自建 state/model。"""
        state = _FakeRelationshipState(familiarity=0.3)
        model = _FakeRelationshipModel(total_interactions=5)
        repo = _FakeRelationshipRepository(state=state, model=model)
        a = RelationshipRuntimeAdapter(relationship_repository=repo)
        a.attach()
        # state/model 被自动加载
        assert a._state is state
        assert a._model is model
        assert a.is_attached() is True

    def test_attach_self_build_no_dependencies(self) -> None:
        """无依赖时,attach 不抛,可能失败降级(attached=True)。"""
        a = RelationshipRuntimeAdapter()
        a.attach()
        # 不论是否成功,attached 必然 True
        assert a.is_attached() is True

    def test_detach(self) -> None:
        state = _FakeRelationshipState()
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        assert a.is_attached() is True
        assert a.detach() is True
        assert a.is_attached() is False

    def test_implements_cycle_adapter_protocol(self) -> None:
        """必须满足 CycleAdapter duck-type。"""
        a = RelationshipRuntimeAdapter()
        assert is_cycle_adapter(a) is True
        assert hasattr(a, "name")
        assert hasattr(a, "health_check")
        assert hasattr(a, "process_cycle")
        assert hasattr(a, "snapshot")
        assert hasattr(a, "attach")
        assert hasattr(a, "detach")

    def test_snapshot_basic(self) -> None:
        a = RelationshipRuntimeAdapter(user_id="alice")
        snap = a.snapshot()
        assert snap["name"] == "relationship"
        assert snap["schema_version"] == "1.0"
        assert snap["user_id"] == "alice"
        assert snap["process_count"] == 0
        assert snap["read_count"] == 0
        assert snap["degraded_count"] == 0
        assert snap["attached"] is False

    def test_schema_version_locked(self) -> None:
        """schema_version 冻结在 1.0。"""
        a = RelationshipRuntimeAdapter()
        assert a.schema_version == "1.0"
        assert RELATIONSHIP_RUNTIME_ADAPTER_SCHEMA_VERSION == "1.0"

    def test_default_user_id(self) -> None:
        a = RelationshipRuntimeAdapter()
        assert a._user_id == "yuyi"

    def test_explicit_user_id(self) -> None:
        a = RelationshipRuntimeAdapter(user_id="bob")
        assert a._user_id == "bob"
        assert a.snapshot()["user_id"] == "bob"

    def test_factory_create(self) -> None:
        state = _FakeRelationshipState()
        a = create_relationship_runtime_adapter(
            relationship_state=state, user_id="u1",
        )
        assert isinstance(a, RelationshipRuntimeAdapter)
        assert a._state is state
        assert a._user_id == "u1"

    def test_factory_default(self) -> None:
        a = create_relationship_runtime_adapter()
        assert isinstance(a, RelationshipRuntimeAdapter)
        assert a._user_id == "yuyi"


# ============================================================
# 3. 测试 2:只读特性(Read-Only)
# ============================================================


class TestRelationshipReadOnly:
    def test_does_not_call_process_interaction(self) -> None:
        """绝不调 RelationshipIntelligenceEngine.process_interaction() (核心约束)。"""
        state = _FakeRelationshipState(familiarity=0.5)
        engine = _FakeRelationshipIntelligenceEngine()
        # 将 engine 注入到 model.__dict__(虽然不应被访问,只是 mock)
        model = _FakeRelationshipModel()
        model._intelligence_engine = engine  # type: ignore
        a = RelationshipRuntimeAdapter(relationship_state=state, relationship_model=model)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert engine.process_interaction_count == 0

    def test_does_not_call_save_state(self) -> None:
        """绝不调 repository.save_state()。"""
        state = _FakeRelationshipState()
        repo = _FakeRelationshipRepository(state=state)
        a = RelationshipRuntimeAdapter(
            relationship_state=state, relationship_repository=repo,
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert repo.save_state_count == 0

    def test_does_not_call_save_relationship_model(self) -> None:
        """绝不调 repository.save_relationship_model()。"""
        state = _FakeRelationshipState()
        model = _FakeRelationshipModel()
        repo = _FakeRelationshipRepository(state=state, model=model)
        a = RelationshipRuntimeAdapter(
            relationship_state=state,
            relationship_model=model,
            relationship_repository=repo,
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        assert repo.save_model_count == 0

    def test_does_not_modify_state_fields(self) -> None:
        """绝不修改 state 字段(familiarity/trust/collaboration 等)。"""
        state = _FakeRelationshipState(
            familiarity=0.5, trust=0.6, collaboration=0.4,
            relationship_stage="developing",
        )
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
        # state 任何字段都未被修改
        assert state.modified_count == 0
        assert state.familiarity == 0.5
        assert state.trust == 0.6
        assert state.collaboration == 0.4
        assert state.relationship_stage == "developing"

    def test_does_not_replace_model_lists(self) -> None:
        """绝不替换 model.trust_changes / model.interaction_history。"""
        state = _FakeRelationshipState()
        model = _FakeRelationshipModel(
            total_interactions=3,
            total_trust_changes=2,
            trust_changes=[{"delta": 0.05, "dimension": "trust"}],
            interaction_history=[{"summary": "msg1"}],
        )
        a = RelationshipRuntimeAdapter(relationship_state=state, relationship_model=model)
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
        # model 任何字段都未被修改
        assert model.modified_count == 0

    def test_does_not_modify_state(self) -> None:
        """绝不修改 state 任何属性。"""
        state = _FakeRelationshipState(
            familiarity=0.5, trust=0.6, collaboration=0.4,
            interaction_frequency=0.3,
            relationship_stage="developing",
            communication_style=["warm"],
        )
        before = state.to_dict()
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
            a.read_relationship()
        after = state.to_dict()
        assert before == after

    def test_multiple_process_cycle_remains_readonly(self) -> None:
        """多次 process_cycle 仍只读。"""
        state = _FakeRelationshipState(
            familiarity=0.4, trust=0.5, collaboration=0.3,
        )
        repo = _FakeRelationshipRepository(state=state)
        a = RelationshipRuntimeAdapter(
            relationship_state=state, relationship_repository=repo,
        )
        a.attach()
        before = state.to_dict()
        for i in range(20):
            ctx = RuntimeCycleContext(input_event=f"msg_{i}", user_id="u")
            a.process_cycle(ctx)
        # 所有写操作计数器均为 0
        assert state.modified_count == 0
        assert repo.save_state_count == 0
        assert repo.save_model_count == 0
        # state 未被修改
        assert state.to_dict() == before

    def test_output_does_not_reference_original_objects(self) -> None:
        """output 不得返回 state / model 原对象引用(必须 copy)。"""
        state = _FakeRelationshipState(familiarity=0.5, trust=0.6)
        model = _FakeRelationshipModel(
            trust_changes=[{"delta": 0.05}],
            interaction_history=[{"summary": "msg1"}],
        )
        a = RelationshipRuntimeAdapter(relationship_state=state, relationship_model=model)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out is not None
        # current_metrics 必须是新 dict
        assert out["current_metrics"] is not state
        # interaction_history.recent 必须是新 list
        assert out["interaction_history"]["recent"] is not model.interaction_history
        # trust_changes.recent 必须是新 list
        assert out["trust_changes"]["recent"] is not model.trust_changes


# ============================================================
# 4. 测试 3:Relationship Processing(正常 / 空 / 缺字段 / 极端值)
# ============================================================


class TestRelationshipProcessing:
    def _adapter_with(
        self,
        familiarity: float = 0.5,
        trust: float = 0.5,
        collaboration: float = 0.4,
        interaction_frequency: float = 0.3,
        stage: str = "developing",
        communication_style: Optional[List[str]] = None,
        total_interactions: int = 0,
        total_trust_changes: int = 0,
        total_milestones: int = 0,
        total_shared_experiences: int = 0,
        trust_changes: Optional[List[Dict[str, Any]]] = None,
        interaction_history: Optional[List[Dict[str, Any]]] = None,
    ) -> RelationshipRuntimeAdapter:
        state = _FakeRelationshipState(
            familiarity=familiarity,
            trust=trust,
            collaboration=collaboration,
            interaction_frequency=interaction_frequency,
            relationship_stage=stage,
            communication_style=communication_style,
        )
        model = _FakeRelationshipModel(
            total_interactions=total_interactions,
            total_trust_changes=total_trust_changes,
            total_milestones=total_milestones,
            total_shared_experiences=total_shared_experiences,
            trust_changes=trust_changes,
            interaction_history=interaction_history,
        )
        return RelationshipRuntimeAdapter(
            relationship_state=state, relationship_model=model,
        )

    def test_normal_state(self) -> None:
        """正常状态:relationship_output 完整字段。"""
        a = self._adapter_with(
            familiarity=0.5, trust=0.6, collaboration=0.4,
            interaction_frequency=0.3,
            stage="developing",
            communication_style=["warm", "gentle"],
            total_interactions=10,
            total_trust_changes=3,
            total_milestones=2,
            total_shared_experiences=1,
            trust_changes=[{"delta": 0.05, "dimension": "trust"}],
            interaction_history=[{"summary": "msg1"}, {"summary": "msg2"}],
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        out = ctx.relationship_output

        assert out is not None
        assert isinstance(out, dict)
        assert out["relationship_available"] is True
        assert out["degraded"] is False
        assert out["source"] == SOURCE_NAME == "relationship_runtime_adapter"

        # current_metrics
        cm = out["current_metrics"]
        assert cm["familiarity"] == 0.5
        assert cm["trust"] == 0.6
        assert cm["collaboration"] == 0.4
        assert cm["interaction_frequency"] == 0.3

        # stage
        assert out["stage"] == "developing"
        assert out["stage_label"] == "正在发展"

        # communication_style
        assert "warm" in out["communication_style"]
        assert "gentle" in out["communication_style"]

        # labels
        labels = out["labels"]
        assert "familiarity_level" in labels
        assert "trust_level" in labels
        assert "collaboration_level" in labels
        assert "interaction_frequency_level" in labels
        assert "stage_label" in labels

        # interaction_history
        ih = out["interaction_history"]
        assert ih["total_count"] == 10
        assert isinstance(ih["recent"], list)
        assert len(ih["recent"]) == 2

        # trust_changes
        tc = out["trust_changes"]
        assert tc["total_count"] == 3
        assert isinstance(tc["recent"], list)
        assert len(tc["recent"]) == 1

        # counts
        assert out["milestones_count"] == 2
        assert out["shared_experiences_count"] == 1

    def test_empty_state(self) -> None:
        """空 state:全部为 0 / initial,仍输出 OK。"""
        a = self._adapter_with()
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        # 仍可读
        assert out["relationship_available"] is True
        assert out["degraded"] is False
        assert out["stage"] == "developing"  # 默认
        assert out["current_metrics"]["familiarity"] == 0.5
        # 熟悉度 0.5 → 标签
        assert out["labels"]["familiarity_level"] == "熟悉中"

    def test_state_zero_values(self) -> None:
        """state 全 0:仍能 output,标签为最底。"""
        a = self._adapter_with(
            familiarity=0.0, trust=0.0, collaboration=0.0,
            interaction_frequency=0.0, stage="initial",
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out["current_metrics"]["familiarity"] == 0.0
        assert out["stage"] == "initial"
        assert out["stage_label"] == "初步接触"
        # 全 0 标签
        assert out["labels"]["familiarity_level"] == "陌生"
        assert out["labels"]["trust_level"] == "陌生"
        assert out["labels"]["collaboration_level"] == "尚未协作"
        assert out["labels"]["interaction_frequency_level"] == "很少互动"

    def test_state_high_values(self) -> None:
        """state 高值:标签为最高。"""
        a = self._adapter_with(
            familiarity=0.95, trust=0.9, collaboration=0.85,
            interaction_frequency=0.92, stage="deep_collaboration",
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out["labels"]["familiarity_level"] == "非常熟悉"
        assert out["labels"]["trust_level"] == "深度信任"
        assert out["labels"]["collaboration_level"] == "深度协作"
        assert out["labels"]["interaction_frequency_level"] == "持续互动"
        assert out["stage_label"] == "深度协作"

    def test_extreme_values_clamped(self) -> None:
        """值超出范围会被 clamp 到合法区间。"""
        a = self._adapter_with(
            familiarity=-5.0, trust=99.0, collaboration=2.0,
            interaction_frequency=-0.5,
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        cm = out["current_metrics"]
        assert 0.0 <= cm["familiarity"] <= 1.0
        assert 0.0 <= cm["trust"] <= 1.0
        assert 0.0 <= cm["collaboration"] <= 1.0
        assert 0.0 <= cm["interaction_frequency"] <= 1.0
        assert cm["familiarity"] == 0.0  # -5 clamp
        assert cm["trust"] == 1.0  # 99 clamp
        assert cm["collaboration"] == 1.0  # 2 clamp

    def test_state_missing_attributes(self) -> None:
        """state 缺字段(比如 stage 抛异常)→ degraded。"""

        class _PartialState:
            familiarity = 0.5
            trust = 0.6
            # 缺 collaboration/interaction_frequency/stage

        a = RelationshipRuntimeAdapter(relationship_state=_PartialState())  # type: ignore
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        # 缺字段时通过 getattr 拿默认值 → 不 degraded
        assert out["relationship_available"] is True
        # 缺字段用 0
        assert out["current_metrics"]["collaboration"] == 0.0
        assert out["current_metrics"]["interaction_frequency"] == 0.0
        assert out["stage"] == "initial"  # 默认

    def test_unknown_stage(self) -> None:
        """未知 stage:仍 output,stage_label 退到原值。"""
        a = self._adapter_with(stage="unknown_stage_xyz")
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out["stage"] == "unknown_stage_xyz"
        # 未知 stage 用原值
        assert out["stage_label"] == "unknown_stage_xyz"

    def test_empty_input_event(self) -> None:
        """空 input_event 不影响 output。"""
        a = self._adapter_with(familiarity=0.5, trust=0.6)
        a.attach()
        for ev in [None, "", 0, {}, [], object()]:
            ctx = RuntimeCycleContext(input_event=ev)
            a.process_cycle(ctx)
            assert ctx.relationship_output is not None
            assert ctx.relationship_output["relationship_available"] is True

    def test_uses_personality_output_from_ctx(self) -> None:
        """可读 ctx.personality_output(但不强制依赖,空也不影响)。"""
        a = self._adapter_with(familiarity=0.5, trust=0.6)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.personality_output = {"personality_available": True, "warmth": 0.7}
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out["relationship_available"] is True

    def test_uses_metadata(self) -> None:
        """可读 ctx.metadata(空也不影响)。"""
        a = self._adapter_with()
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.metadata["session_topic"] = "small_talk"
        a.process_cycle(ctx)
        assert ctx.relationship_output is not None

    def test_read_relationship_public(self) -> None:
        """read_relationship 公开接口,返回统一 output。"""
        a = self._adapter_with(familiarity=0.5, trust=0.6)
        a.attach()
        out = a.read_relationship()
        assert out["relationship_available"] is True
        # 计数器累加
        assert a.read_count == 1
        # 二次 read
        a.read_relationship()
        assert a.read_count == 2

    def test_trust_changes_truncation(self) -> None:
        """trust_changes recent 只取最近 5 条。"""
        changes = [{"delta": float(i), "dimension": "trust"} for i in range(20)]
        a = self._adapter_with(total_trust_changes=20, trust_changes=changes)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        # recent 应只有 5 条
        assert len(out["trust_changes"]["recent"]) == 5
        # total 仍为 20
        assert out["trust_changes"]["total_count"] == 20

    def test_interaction_history_truncation(self) -> None:
        """interaction_history recent 只取最近 5 条。"""
        history = [{"summary": f"msg_{i}"} for i in range(20)]
        a = self._adapter_with(total_interactions=20, interaction_history=history)
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        # recent 应只有 5 条
        assert len(out["interaction_history"]["recent"]) == 5
        # total 仍为 20
        assert out["interaction_history"]["total_count"] == 20

    def test_relationship_summary_present(self) -> None:
        """relationship_summary 必须存在(可能空,取决于 boundary)。"""
        a = self._adapter_with(
            familiarity=0.5, trust=0.6, collaboration=0.4,
            total_interactions=10,
        )
        a.attach()
        ctx = RuntimeCycleContext(input_event="x")
        a.process_cycle(ctx)
        # relationship_summary 字段存在
        assert "relationship_summary" in ctx.relationship_output
        # 类型为 str
        assert isinstance(ctx.relationship_output["relationship_summary"], str)


# ============================================================
# 5. 测试 4:Runtime Integration
# ============================================================


class TestRuntimeIntegration:
    def test_register_with_orchestrator(self) -> None:
        """RelationshipRuntimeAdapter 可注册到 RuntimeCycleOrchestrator。"""
        a = RelationshipRuntimeAdapter(
            relationship_state=_FakeRelationshipState(),
        )
        a.attach()
        orch = RuntimeCycleOrchestrator()
        ok = orch.register_adapter(a, name="relationship")
        assert ok is True
        assert orch.has_adapter("relationship") is True
        assert orch.adapter_count == 1

    def test_full_cycle_integration(self) -> None:
        """完整 cycle:orchestrator 调度 relationship adapter,ctx.relationship_output 被填充。"""
        state = _FakeRelationshipState(familiarity=0.5, trust=0.6)
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="relationship")
        ctx = orch.process_event("hi", user_id="u1", session_id="s1")

        # adapter 被调用
        assert a.process_count == 1
        # ctx.relationship_output 被填充
        assert ctx.relationship_output is not None
        assert isinstance(ctx.relationship_output, dict)
        assert ctx.relationship_output["relationship_available"] is True
        # adapter_results 应有 relationship
        assert ctx.adapter_results["relationship"]["ok"] is True

    def test_step_order_relationship_after_personality(self) -> None:
        """relationship 在 5 步中是 step 4,personality 在 relationship 之前。"""
        from src.runtime.adapters.impl.memory_runtime_adapter import (
            MemoryRuntimeAdapter,
        )
        from src.runtime.adapters.impl.emotion_runtime_adapter import (
            EmotionRuntimeAdapter,
        )
        from src.runtime.adapters.impl.personality_runtime_adapter import (
            PersonalityRuntimeAdapter,
        )

        a_r = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a_r.attach()
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
        orch.register_adapter(a_r, name="relationship")
        ctx = orch.process_event("hi", user_id="u1")

        stages = ctx.get_stage_names()
        # 4 个都执行
        assert CYCLE_EVENT_MEMORY_COMPLETED in stages
        assert CYCLE_EVENT_EMOTION_COMPLETED in stages
        assert CYCLE_EVENT_PERSONALITY_COMPLETED in stages
        assert CYCLE_EVENT_RELATIONSHIP_COMPLETED in stages
        # 顺序: memory -> emotion -> personality -> relationship
        mem_idx = stages.index(CYCLE_EVENT_MEMORY_COMPLETED)
        em_idx = stages.index(CYCLE_EVENT_EMOTION_COMPLETED)
        per_idx = stages.index(CYCLE_EVENT_PERSONALITY_COMPLETED)
        rel_idx = stages.index(CYCLE_EVENT_RELATIONSHIP_COMPLETED)
        assert mem_idx < em_idx < per_idx < rel_idx
        # relationship 仍在 cycle 结束前
        assert rel_idx < stages.index(CYCLE_EVENT_COMPLETED)

    def test_orchestrator_handles_relationship_failure(self) -> None:
        """relationship adapter 抛异常时,orchestrator 标记 degraded 但不中断。"""

        class _BoomAdapter(RelationshipRuntimeAdapter):
            def process_cycle(self, ctx):
                raise RuntimeError("relationship_boom")

        a = _BoomAdapter()
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="relationship")
        ctx = orch.process_event("x", user_id="u1")
        assert ctx.adapter_results["relationship"]["ok"] is False
        assert "relationship" in ctx.degraded_adapters
        # cycle 仍完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        assert orch.cycle_count == 1

    def test_relationship_uses_input_event(self) -> None:
        """relationship 可读 ctx.input_event。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        ctx = RuntimeCycleContext(
            input_event={"user_input": "hello yuyi", "type": "chat"},
            user_id="u1",
        )
        a.process_cycle(ctx)
        assert ctx.relationship_output is not None

    def test_relationship_uses_personality_output(self) -> None:
        """relationship 可读 ctx.personality_output(虽然不强制依赖)。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.personality_output = {"personality_available": True, "warmth": 0.7}
        a.process_cycle(ctx)
        # relationship_output 已填,不影响
        assert ctx.relationship_output is not None
        assert ctx.relationship_output["relationship_available"] is True

    def test_relationship_uses_metadata(self) -> None:
        """relationship 可读 ctx.metadata(不影响主流程)。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        ctx.metadata["session_topic"] = "small_talk"
        a.process_cycle(ctx)
        assert ctx.relationship_output is not None


# ============================================================
# 6. 测试 5:异常隔离
# ============================================================


class TestExceptionIsolation:
    def test_state_none_returns_degraded(self) -> None:
        """state 为 None → degraded。"""
        a = RelationshipRuntimeAdapter()  # 不传 state
        # 不 attach,state 仍为 None
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out is not None
        assert out["relationship_available"] is False
        assert out["degraded"] is True
        assert out["source"] == "relationship_runtime_adapter"
        assert "error" in out

    def test_state_attribute_exception(self) -> None:
        """state 属性访问抛异常时 → degraded。"""
        state = _FakeRelationshipState(fail_attrs=True)
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        assert out["relationship_available"] is False
        assert out["degraded"] is True
        assert "error" in out

    def test_model_snapshot_exception(self) -> None:
        """model.get_snapshot() 抛异常时 → core 仍能读,model 字段降级。"""
        state = _FakeRelationshipState(familiarity=0.5)
        model = _FakeRelationshipModel(fail_snapshot=True)
        a = RelationshipRuntimeAdapter(relationship_state=state, relationship_model=model)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi")
        a.process_cycle(ctx)
        out = ctx.relationship_output
        # core 仍 OK
        assert out["relationship_available"] is True
        # model 字段为 0
        assert out["interaction_history"]["total_count"] == 0
        assert out["trust_changes"]["total_count"] == 0

    def test_process_cycle_with_invalid_ctx(self) -> None:
        """非 RuntimeCycleContext,直接返回原对象。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        fake = {"x": 1}
        out = a.process_cycle(fake)
        assert out is fake

    def test_process_cycle_with_none_ctx(self) -> None:
        """ctx 为 None,不抛,返回 None。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        out = a.process_cycle(None)  # type: ignore
        # 不抛,返回 None
        assert out is None

    def test_process_cycle_increments_degraded_count(self) -> None:
        """degraded 时,degraded_count 累加。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        # 第 1 次:正常
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        assert a.degraded_count == 0
        # 切到 fail 状态
        a._state._fail_attrs = True  # type: ignore
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        # degraded_count >= 3
        assert a.degraded_count >= 3

    def test_health_check_with_exception(self) -> None:
        """health_check 在 state 异常时不抛。"""
        state = _FakeRelationshipState(fail_attrs=True)
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        # health 标记 degraded
        assert hc["status"] == "degraded"
        assert hc["state_available"] is True
        assert hc["state_readable"] is False

    def test_health_check_with_no_state(self) -> None:
        """无 state → health_check status degraded。"""
        a = RelationshipRuntimeAdapter()
        # 不 attach,state 仍为 None
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert hc["status"] == "degraded"
        assert hc["state_available"] is False
        assert hc["attached"] is False

    def test_concurrent_process_cycle(self) -> None:
        """并发 process_cycle 不抛。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
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
    def test_health_basic_with_state(self) -> None:
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert hc["adapter"] == "relationship"
        assert hc["status"] in ("healthy", "degraded")
        assert hc["state_available"] is True
        assert hc["attached"] is True

    def test_health_without_state(self) -> None:
        """未 attach 时(无 state)→ status degraded。"""
        a = RelationshipRuntimeAdapter()
        # 不调用 attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        # state 不存在 → status degraded
        assert hc["status"] == "degraded"
        assert hc["state_available"] is False

    def test_health_counts(self) -> None:
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        for _ in range(3):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        hc = a.health_check()
        assert hc["process_count"] == 3
        assert hc["read_count"] == 0

    def test_health_records_schema_version(self) -> None:
        a = RelationshipRuntimeAdapter()
        hc = a.health_check()
        assert hc["schema_version"] == "1.0"

    def test_health_records_user_id(self) -> None:
        a = RelationshipRuntimeAdapter(user_id="alice")
        hc = a.health_check()
        assert hc["user_id"] == "alice"

    def test_snapshot_basic(self) -> None:
        a = RelationshipRuntimeAdapter(
            relationship_state=_FakeRelationshipState(),
            user_id="alice",
        )
        a.attach()
        snap = a.snapshot()
        assert snap["name"] == "relationship"
        assert snap["schema_version"] == "1.0"
        assert snap["user_id"] == "alice"
        assert snap["process_count"] == 0
        assert snap["read_count"] == 0
        assert snap["degraded_count"] == 0
        assert snap["attached"] is True
        assert snap["state_available"] is True

    def test_snapshot_includes_last_state(self) -> None:
        state = _FakeRelationshipState(familiarity=0.5, trust=0.6)
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        snap = a.snapshot()
        # last_state 应包含 state
        ls = snap["last_state"]
        assert "state" in ls
        assert ls["state"]["familiarity"] == 0.5
        assert ls["state"]["trust"] == 0.6

    def test_snapshot_available_flag(self) -> None:
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        # 未 attach / 未读
        snap = a.snapshot()
        # available 应为 False
        assert snap["available"] is False
        # 跑一次后
        a.attach()
        a.process_cycle(RuntimeCycleContext(input_event="hi"))
        snap2 = a.snapshot()
        assert snap2["available"] is True

    def test_safe_summary_with_none(self) -> None:
        s = safe_get_relationship_adapter_summary(None)
        assert s["name"] == "relationship"
        assert s["attached"] is False
        assert s["available"] is False

    def test_safe_summary_with_adapter(self) -> None:
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        s = safe_get_relationship_adapter_summary(a)
        assert s["name"] == "relationship"
        assert s["attached"] is True

    def test_phase_c5_constants(self) -> None:
        """Phase C.5 常量完整。"""
        for name in [
            "PHASE_C5_STAGE_RELATIONSHIP_READ",
            "PHASE_C5_STAGE_RELATIONSHIP_DEGRADED",
        ]:
            assert name in dir(
                sys.modules["src.runtime.adapters.impl.relationship_runtime_adapter"]
            )
        assert PHASE_C5_NAME == "phase_c5"
        assert PHASE_C5_VERSION == "1.0.0"
        assert len(ALL_PHASE_C5_STAGES) == 2
        assert SOURCE_NAME == "relationship_runtime_adapter"


# ============================================================
# 8. 测试 7:Regression
# ============================================================


class TestRegression:
    def test_does_not_modify_state(self) -> None:
        """state 任何字段都未被修改。"""
        state = _FakeRelationshipState(
            familiarity=0.4, trust=0.5, collaboration=0.3,
            relationship_stage="developing",
        )
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        before = state.to_dict()
        # 多次读
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
            a.read_relationship()
        # state 没动
        assert state.to_dict() == before
        # 任何"写"调用计数均为 0
        assert state.modified_count == 0

    def test_does_not_modify_model(self) -> None:
        """model 任何字段都未被修改。"""
        state = _FakeRelationshipState()
        model = _FakeRelationshipModel(
            total_interactions=5,
            total_trust_changes=3,
            trust_changes=[{"delta": 0.05, "dimension": "trust"}],
            interaction_history=[{"summary": "msg1"}],
        )
        a = RelationshipRuntimeAdapter(relationship_state=state, relationship_model=model)
        a.attach()
        before_snapshot = model.get_snapshot()
        before_tc = list(model.trust_changes)
        before_ih = list(model.interaction_history)
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="x"))
            a.read_relationship()
        # model 没动
        assert model.get_snapshot() == before_snapshot
        assert model.trust_changes == before_tc
        assert model.interaction_history == before_ih
        assert model.modified_count == 0

    def test_does_not_modify_runtime_cycle_context_except_relationship_output(self) -> None:
        """除 relationship_output 外,ctx 其他字段不应被改。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        ctx = RuntimeCycleContext(
            input_event="hi", user_id="u1", session_id="s1",
        )
        ctx.metadata["k"] = "v"
        ctx.memory_output = {"existing": True}
        ctx.emotion_output = {"existing": True}
        ctx.personality_output = {"existing": True}
        before = {
            "cycle_id": ctx.cycle_id,
            "session_id": ctx.session_id,
            "user_id": ctx.user_id,
            "input_event": ctx.input_event,
            "metadata": dict(ctx.metadata),
            "memory_output": ctx.memory_output,
            "emotion_output": ctx.emotion_output,
            "personality_output": ctx.personality_output,
            "growth_output": list(ctx.growth_output),
        }
        a.process_cycle(ctx)
        # relationship_output 被填
        assert ctx.relationship_output is not None
        # 其他字段不变
        assert ctx.cycle_id == before["cycle_id"]
        assert ctx.session_id == before["session_id"]
        assert ctx.user_id == before["user_id"]
        assert ctx.input_event == before["input_event"]
        assert ctx.metadata == before["metadata"]
        assert ctx.memory_output is before["memory_output"]
        assert ctx.emotion_output is before["emotion_output"]
        assert ctx.personality_output is before["personality_output"]
        assert ctx.growth_output == before["growth_output"]

    def test_does_not_modify_orchestrator(self) -> None:
        """RuntimeCycleOrchestrator 公共方法集合不变。"""
        a = RelationshipRuntimeAdapter(relationship_state=_FakeRelationshipState())
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="relationship")
        orch.process_event("x", user_id="u1")
        # orchestrator 仍可被外部使用
        ctx2 = orch.process_event("y", user_id="u1")
        assert ctx2.cycle_id != ""

    def test_does_not_call_forbidden_methods(self) -> None:
        """所有禁止方法均未被调。"""
        state = _FakeRelationshipState()
        model = _FakeRelationshipModel(
            trust_changes=[{"delta": 0.05}],
            interaction_history=[{"summary": "msg1"}],
        )
        repo = _FakeRelationshipRepository(state=state, model=model)
        a = RelationshipRuntimeAdapter(
            relationship_state=state,
            relationship_model=model,
            relationship_repository=repo,
        )
        a.attach()
        for _ in range(5):
            a.process_cycle(RuntimeCycleContext(input_event="hi"))
        # 验证所有禁止方法都没被调
        assert state.modified_count == 0
        assert model.modified_count == 0
        assert repo.save_state_count == 0
        assert repo.save_model_count == 0

    def test_b4_bridge_untouched(self) -> None:
        """C.5 不与 B.4-B.13 交互,任何 B4 bridge 调用都应不存在。"""
        a = RelationshipRuntimeAdapter()
        public_attrs = [m for m in dir(a) if not m.startswith("_")]
        forbidden = {"b4_bridge", "bridge", "b4", "decision", "growth"}
        for f in forbidden:
            assert f not in public_attrs

    def test_uses_real_relationship_state_end_to_end(self) -> None:
        """E2E:用真实 RelationshipState(只读),验证不破坏现有 state。"""
        from src.relationship.relationship_state import RelationshipState

        real_state = RelationshipState(
            familiarity=0.4, trust=0.5, collaboration=0.3,
            interaction_frequency=0.2,
            relationship_stage="developing",
        )
        before = real_state.to_dict()
        a = RelationshipRuntimeAdapter(relationship_state=real_state)
        a.attach()
        ctx = RuntimeCycleContext(input_event="hi", user_id="u1")
        a.process_cycle(ctx)
        # 关键:不抛异常,relationship_output 是 dict
        assert ctx.relationship_output is not None
        assert isinstance(ctx.relationship_output, dict)
        assert ctx.relationship_output["relationship_available"] is True
        assert ctx.relationship_output["current_metrics"]["familiarity"] == 0.4
        # state 内容未被破坏
        after = real_state.to_dict()
        assert before == after


# ============================================================
# 9. 工厂 + 工具函数
# ============================================================


class TestFactoryAndUtils:
    def test_create_default(self) -> None:
        a = create_relationship_runtime_adapter()
        assert isinstance(a, RelationshipRuntimeAdapter)

    def test_create_with_state(self) -> None:
        state = _FakeRelationshipState()
        a = create_relationship_runtime_adapter(relationship_state=state)
        assert a._state is state

    def test_create_with_user_id(self) -> None:
        a = create_relationship_runtime_adapter(user_id="alice")
        assert a._user_id == "alice"

    def test_relationship_snapshot_dataclass(self) -> None:
        """_RelationshipSnapshot dataclass 字段完整。"""
        snap = _RelationshipSnapshot(
            state={"familiarity": 0.5},
            model_snapshot={"total_interactions": 10},
            stage_label="developing",
        )
        d = snap.to_dict()
        assert d["state"]["familiarity"] == 0.5
        assert d["model_snapshot"]["total_interactions"] == 10
        assert d["stage_label"] == "developing"
        assert d["schema_version"] == "1.0"

    def test_stage_label_map_complete(self) -> None:
        """4 个标准 stage 全部有标签。"""
        for stage in ["initial", "developing", "stable", "deep_collaboration"]:
            assert stage in STAGE_LABEL_MAP
            assert STAGE_LABEL_MAP[stage] != ""


# ============================================================
# 10. 端到端 smoke
# ============================================================


class TestE2ESmoke:
    def test_full_relationship_runtime_flow(self) -> None:
        """完整 E2E:orchestrator + relationship adapter + relationship_output。"""
        state = _FakeRelationshipState(
            familiarity=0.5, trust=0.6, collaboration=0.4,
            interaction_frequency=0.3,
            relationship_stage="developing",
            communication_style=["warm", "gentle"],
        )
        model = _FakeRelationshipModel(
            total_interactions=10,
            total_trust_changes=3,
            total_milestones=2,
            total_shared_experiences=1,
            trust_changes=[{"delta": 0.05, "dimension": "trust"}],
            interaction_history=[{"summary": "msg1"}, {"summary": "msg2"}],
        )
        a = RelationshipRuntimeAdapter(relationship_state=state, relationship_model=model)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="relationship")
        ctx = orch.process_event("hi", user_id="u1", session_id="sess1")

        # 1. relationship_output 填充
        assert ctx.relationship_output is not None
        out = ctx.relationship_output
        assert out["relationship_available"] is True
        assert out["current_metrics"]["familiarity"] == 0.5
        assert out["current_metrics"]["trust"] == 0.6
        assert out["stage"] == "developing"

        # 2. adapter_results 标记
        assert ctx.adapter_results["relationship"]["ok"] is True

        # 3. stage_log
        stages = ctx.get_stage_names()
        assert CYCLE_EVENT_RELATIONSHIP_COMPLETED in stages
        assert CYCLE_EVENT_COMPLETED in stages

        # 4. cycle 整体成功
        assert ctx.error_count == 0
        assert orch.cycle_count == 1

        # 5. state 任何"写"操作均未发生
        assert state.modified_count == 0
        assert model.modified_count == 0

    def test_degraded_e2e(self) -> None:
        """降级 E2E:state 异常 → relationship degraded,cycle 仍完成。"""
        state = _FakeRelationshipState(fail_attrs=True)
        a = RelationshipRuntimeAdapter(relationship_state=state)
        a.attach()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="relationship")
        ctx = orch.process_event("hi", user_id="u1")

        out = ctx.relationship_output
        assert out["relationship_available"] is False
        assert out["degraded"] is True
        # cycle 完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        # adapter_results.relationship 仍 ok=True(因为 process_cycle 没抛)
        assert ctx.adapter_results["relationship"]["ok"] is True
        # cycle 整体无 error
        assert ctx.error_count == 0

    def test_no_state_e2e(self) -> None:
        """无 state E2E:degraded,cycle 仍完成。"""

        class _StubAdapter(RelationshipRuntimeAdapter):
            def attach(self) -> bool:
                with self._lock:
                    self._attached = True
                return True

        a = _StubAdapter()
        orch = RuntimeCycleOrchestrator()
        orch.register_adapter(a, name="relationship")
        ctx = orch.process_event("hi", user_id="u1")

        out = ctx.relationship_output
        assert out["relationship_available"] is False
        assert out["degraded"] is True
        # cycle 完成
        assert CYCLE_EVENT_COMPLETED in ctx.get_stage_names()
        # adapter_results.relationship 仍 ok=True(因为 process_cycle 没抛)
        assert ctx.adapter_results["relationship"]["ok"] is True


# ============================================================
# 11. _FakePersonalityResolver (供 TestRuntimeIntegration 使用)
# ============================================================


# 仿 C.4 的简单 resolver mock(只用于 step 顺序测试)
class _FakePersonalityResolver:
    """最小可用 PersonalityResolver mock(给 personality adapter 用)。"""

    def __init__(self) -> None:
        self.state = type("_FakeGrowthState", (), {
            "get": lambda self_: {"metrics": {"trust": 0.5, "closeness": 0.4, "attachment": 0.3}},
        })()
        self.relationship_state = type("_FakeRelState", (), {
            "get_bond_strength": lambda self_: 0.5,
            "get_trust": lambda self_: 0.5,
            "get_familiarity": lambda self_: 0.4,
        })()
        self.self_model_store = type("_FakeSMStore", (), {
            "get": lambda self_: {"identity_summary": ""},
        })()
        self._trait_states = {
            dim: {"current_value": 0.7, "base_value": 0.7}
            for dim in ["warmth", "gentleness", "shyness", "sensitivity",
                        "emotional_expression", "caring"]
        }
