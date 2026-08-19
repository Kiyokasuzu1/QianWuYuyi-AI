# -*- coding: utf-8 -*-
"""
tests/test_phase_3_7_0_runtime_design.py

Phase 3.7.0: Runtime Integration Design 测试

目标：验证 Runtime 集成设计骨架（不验证业务逻辑）
- RuntimeContext 可以创建
- Event 可以序列化
- RuntimeCore 生命周期接口存在
- Runtime 不直接依赖 Memory 实现
- Runtime 不直接依赖 Personality 实现
- Runtime 不直接依赖 GrowthEngine
- Runtime 不直接依赖 Emotion 实现

约束：
- 不修改 Memory / Emotion / Personality / GrowthEngine / Canonical Schema
- 不实现 Runtime 业务逻辑
"""
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 关键文件路径
# ============================================================
RUNTIME_PATH = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
CONTEXT_PATH = PROJECT_ROOT / "src" / "runtime" / "context.py"
EVENTS_PATH = PROJECT_ROOT / "src" / "runtime" / "events.py"
RUNTIME_DOC_PATH = PROJECT_ROOT / "docs" / "runtime.md"

# 禁止 Runtime 直接 import 的业务实现模块
FORBIDDEN_BUSINESS_IMPORTS = {
    "src.memory.memory_service",
    "src.memory.memory_store",
    "src.memory.memory_relevance_evaluator",
    "src.emotion.emotion_manager",
    "src.emotion.emotion_engine",
    "src.emotion.emotion_state",
    "src.personality.personality_resolver",
    "src.personality.personality_controller",
    "src.personality.personality_evolution",
    "src.personality.personality_profile",
    "src.growth.growth_engine",
    "src.growth.proposal.proposal",
    "src.contracts.proposal_normalizer",
    "src.contracts.growth_schema",
}

# Runtime 自身内部文件
RUNTIME_FILES = [
    RUNTIME_PATH,
    CONTEXT_PATH,
    EVENTS_PATH,
]


# ============================================================
# T1: RuntimeContext
# ============================================================

class TestRuntimeContext:
    """RuntimeContext 可以创建并支持序列化。"""

    def test_runtime_context_can_be_created(self):
        """T1.1: RuntimeContext 可实例化"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        assert ctx is not None
        assert isinstance(ctx.session_id, str)
        assert ctx.session_id.startswith("session_")

    def test_runtime_context_default_fields(self):
        """T1.2: RuntimeContext 默认字段"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        # 核心字段
        assert ctx.user_input == ""
        assert isinstance(ctx.timestamp, str)
        assert ctx.memory_context is None
        assert ctx.emotion_state is None
        assert ctx.personality_snapshot is None
        assert ctx.growth_proposals == []

    def test_runtime_context_has_schema_version(self):
        """T1.3: RuntimeContext 含 schema_version 字段（v1.0）"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        assert hasattr(ctx, "schema_version")
        assert ctx.schema_version == "1.0"

    def test_runtime_context_to_dict(self):
        """T1.4: RuntimeContext 可序列化"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext(user_input="hello")
        d = ctx.to_dict()
        assert isinstance(d, dict)
        for k in (
            "session_id", "user_input", "timestamp",
            "memory_context", "emotion_state", "personality_snapshot",
            "growth_proposals", "schema_version",
        ):
            assert k in d, f"to_dict 缺字段 {k}"
        assert d["user_input"] == "hello"
        assert d["schema_version"] == "1.0"

    def test_runtime_context_from_dict(self):
        """T1.5: RuntimeContext 可反序列化"""
        from src.runtime.context import RuntimeContext
        d = {
            "session_id": "session_test_001",
            "user_input": "test input",
            "timestamp": "2026-01-01T00:00:00Z",
            "memory_context": {"k": "v"},
            "emotion_state": {"mood": "neutral"},
            "personality_snapshot": {"trait": "warm"},
            "growth_proposals": [{"id": "p1"}],
            "schema_version": "1.0",
        }
        ctx = RuntimeContext.from_dict(d)
        assert ctx.session_id == "session_test_001"
        assert ctx.user_input == "test input"
        assert ctx.memory_context == {"k": "v"}
        assert ctx.emotion_state == {"mood": "neutral"}
        assert ctx.personality_snapshot == {"trait": "warm"}
        assert len(ctx.growth_proposals) == 1

    def test_runtime_context_from_dict_backward_compat(self):
        """T1.6: RuntimeContext.from_dict 缺省 schema_version 回退到 v1.0"""
        from src.runtime.context import RuntimeContext
        d = {
            "session_id": "session_v0_001",
            "user_input": "v0 data",
        }
        ctx = RuntimeContext.from_dict(d)
        assert ctx.schema_version == "1.0"

    def test_runtime_context_helper_methods(self):
        """T1.7: RuntimeContext 提供便捷判断方法"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        # 初始全空
        assert not ctx.has_memory()
        assert not ctx.has_emotion()
        assert not ctx.has_personality()
        assert not ctx.has_growth()
        # 填充
        ctx.memory_context = {"k": "v"}
        ctx.emotion_state = {"mood": "happy"}
        ctx.personality_snapshot = {"trait": "curious"}
        ctx.growth_proposals = [{"id": "p1"}]
        assert ctx.has_memory()
        assert ctx.has_emotion()
        assert ctx.has_personality()
        assert ctx.has_growth()


# ============================================================
# T2: Event
# ============================================================

class TestEvent:
    """Event 基类可创建、可序列化。"""

    def test_event_can_be_created(self):
        """T2.1: Event 可实例化"""
        from src.runtime.events import Event
        e = Event()
        assert e is not None
        assert isinstance(e.id, str)
        assert e.id.startswith("evt_")

    def test_event_default_fields(self):
        """T2.2: Event 默认字段"""
        from src.runtime.events import Event
        e = Event()
        assert e.type == "user_input"
        assert e.source is None
        assert isinstance(e.timestamp, str)
        assert e.payload == {}
        assert e.related_ids == []
        assert e.priority == 0
        assert e.metadata == {}

    def test_event_type_constants_exist(self):
        """T2.3: 事件类型常量存在"""
        from src.runtime.events import (
            EVENT_TYPE_USER_INPUT,
            EVENT_TYPE_SYSTEM,
            EVENT_TYPE_TICK,
            EVENT_TYPE_GROWTH_PROPOSAL,
        )
        assert EVENT_TYPE_USER_INPUT == "user_input"
        assert EVENT_TYPE_SYSTEM == "system"
        assert EVENT_TYPE_TICK == "tick"
        assert EVENT_TYPE_GROWTH_PROPOSAL == "growth_proposal"

    def test_event_priority_constants(self):
        """T2.4: 事件优先级常量存在"""
        from src.runtime.events import (
            EVENT_PRIORITY_NORMAL,
            EVENT_PRIORITY_HIGH,
            EVENT_PRIORITY_CRITICAL,
        )
        assert EVENT_PRIORITY_NORMAL == 0
        assert EVENT_PRIORITY_HIGH == 1
        assert EVENT_PRIORITY_CRITICAL == 2

    def test_event_to_dict(self):
        """T2.5: Event 可序列化"""
        from src.runtime.events import Event
        e = Event(
            type="system",
            source="admin",
            payload={"key": "value"},
            priority=1,
        )
        d = e.to_dict()
        assert isinstance(d, dict)
        for k in (
            "id", "type", "source", "timestamp", "payload",
            "related_ids", "priority", "metadata",
        ):
            assert k in d, f"to_dict 缺字段 {k}"
        assert d["type"] == "system"
        assert d["source"] == "admin"
        assert d["payload"] == {"key": "value"}
        assert d["priority"] == 1

    def test_event_from_dict(self):
        """T2.6: Event 可反序列化"""
        from src.runtime.events import Event
        d = {
            "id": "evt_test_001",
            "type": "tick",
            "source": "scheduler",
            "timestamp": "2026-01-01T00:00:00Z",
            "payload": {"tick": 1},
            "related_ids": ["p1", "p2"],
            "priority": 2,
            "metadata": {"k": "v"},
        }
        e = Event.from_dict(d)
        assert e.id == "evt_test_001"
        assert e.type == "tick"
        assert e.source == "scheduler"
        assert e.priority == 2
        assert e.related_ids == ["p1", "p2"]

    def test_event_is_high_priority(self):
        """T2.7: Event.is_high_priority() 正确判断"""
        from src.runtime.events import Event, EVENT_PRIORITY_HIGH
        e_low = Event(priority=0)
        e_high = Event(priority=EVENT_PRIORITY_HIGH)
        assert not e_low.is_high_priority()
        assert e_high.is_high_priority()


# ============================================================
# T3: RuntimeCore 接口
# ============================================================

class TestRuntimeCoreInterface:
    """RuntimeCore 生命周期接口存在。"""

    def test_runtime_core_class_exists(self):
        """T3.1: RuntimeCore 类存在"""
        from src.runtime.runtime import RuntimeCore
        assert RuntimeCore is not None

    def test_runtime_core_lifecycle_methods_exist(self):
        """T3.2: RuntimeCore 关键生命周期方法存在"""
        from src.runtime.runtime import RuntimeCore
        for method in ("start", "process", "persist", "shutdown"):
            assert hasattr(RuntimeCore, method), (
                f"RuntimeCore 缺方法 {method}"
            )
            assert callable(getattr(RuntimeCore, method))

    def test_runtime_core_can_be_constructed(self):
        """T3.3: RuntimeCore 可无参构造（设计阶段允许 port 为空）"""
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core is not None
        assert core.is_started is False
        # Phase 3.7.3 装配升级后,版本号由 3.7.0 推进到 3.7.3
        # Phase 4.1.0 进一步推进到 4.1.0 (Perception 阶段真实激活)
        # Phase 4.2.0 进一步推进到 4.2.0 (Vision 阶段真实激活)
        # Phase 4.2.1 进一步推进到 4.2.1 (Self Model Foundation 阶段真实激活)
        # Phase 4.2.2 进一步推进到 4.2.2 (Self Model SourceAdapter 集成)
        # Phase 4.2.3 进一步推进到 4.2.3 (SelfModel Runtime Consumption)
        # Phase 4.2.4 进一步推进到 4.2.4 (SelfModel History & Audit)
        assert core.RUNTIME_VERSION in (
            "3.7.0", "3.7.1", "3.7.2", "3.7.3",
            "4.0.0", "4.1.0", "4.2.0", "4.2.1", "4.2.2",
            "4.2.3", "4.2.4",
        )

    def test_runtime_core_start_returns_context(self):
        """T3.4: start() 返回 RuntimeContext"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        core = RuntimeCore()
        ctx = core.start()
        assert isinstance(ctx, RuntimeContext)
        assert core.is_started is True

    def test_runtime_core_process_returns_context(self):
        """T3.5: process() 返回 RuntimeContext"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event
        core = RuntimeCore()
        core.start()
        event = Event(type="user_input", payload={"text": "hi"})
        ctx = core.process(event)
        assert isinstance(ctx, RuntimeContext)

    def test_runtime_core_shutdown_clears_started(self):
        """T3.6: shutdown() 清理 started 状态"""
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        assert core.is_started is True
        core.shutdown()
        assert core.is_started is False

    def test_runtime_stage_enum_complete(self):
        """T3.7: RuntimeStage 枚举完整（Phase 3.8.0 新增 PERSONALITY_CONTEXT_BUILD, Phase 3.8.4 新增 RESPONSE_GENERATION + GUARD_CHAIN, Phase 4.0.0 新增 PERCEPTION_OBSERVATION, Phase 4.2.0 新增 PERCEPTION_ANALYSIS, Phase 4.2.1 新增 SELF_MODEL_BUILD）"""
        from src.runtime.runtime import RuntimeStage, RUNTIME_LIFECYCLE_ORDER
        expected_base = [
            "start", "load_state", "receive_event",
            "memory_retrieval", "emotion_update", "growth_evaluation",
            "personality_update",
            "personality_context_build",  # Phase 3.8.0
            "perception_observation",     # Phase 4.0.0
            "response_generation",         # Phase 3.8.4
            "guard_chain",                 # Phase 3.8.4
            "response", "persistence", "shutdown",
        ]
        # Phase 4.2.0: 新增 perception_analysis 阶段
        expected_with_42 = expected_base[:9] + ["perception_analysis"] + expected_base[9:]
        # Phase 4.2.1: 新增 self_model_build 阶段(在 perception_analysis 之后)
        expected_with_421 = (
            expected_base[:9]
            + ["perception_analysis", "self_model_build"]
            + expected_base[9:]
        )
        actual = [s.value for s in RUNTIME_LIFECYCLE_ORDER]
        # 兼容: 14 阶段(<=4.1.0) / 15 阶段(==4.2.0) / 16 阶段(==4.2.1)
        assert (
            actual == expected_base
            or actual == expected_with_42
            or actual == expected_with_421
        ), (
            f"lifecycle 阶段序列不一致: {actual}"
        )


# ============================================================
# T4: 不直接依赖 Memory 实现
# ============================================================

class TestRuntimeDoesNotDependOnMemory:
    """Runtime 不直接 import Memory 业务实现。"""

    @pytest.mark.parametrize("file_path", RUNTIME_FILES)
    def test_no_memory_implementation_import(self, file_path):
        """T4.1: Runtime 三个新文件均不 import Memory 业务实现"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        for forbidden in (
            "from src.memory.memory_service",
            "from src.memory.memory_store",
            "from src.memory.memory_relevance_evaluator",
            "import src.memory.memory_service",
        ):
            assert forbidden not in content, (
                f"{file_path.name} 不应直接 import Memory 业务实现: {forbidden}"
            )


# ============================================================
# T5: 不直接依赖 Personality 实现
# ============================================================

class TestRuntimeDoesNotDependOnPersonality:
    """Runtime 不直接 import Personality 业务实现。"""

    @pytest.mark.parametrize("file_path", RUNTIME_FILES)
    def test_no_personality_implementation_import(self, file_path):
        """T5.1: Runtime 三个新文件均不 import Personality 业务实现"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        for forbidden in (
            "from src.personality.personality_resolver",
            "from src.personality.personality_controller",
            "from src.personality.personality_evolution",
            "from src.personality.personality_profile",
            "import src.personality.personality_resolver",
        ):
            assert forbidden not in content, (
                f"{file_path.name} 不应直接 import Personality 业务实现: {forbidden}"
            )


# ============================================================
# T6: 不直接依赖 GrowthEngine
# ============================================================

class TestRuntimeDoesNotDependOnGrowth:
    """Runtime 不直接 import GrowthEngine 业务实现。"""

    @pytest.mark.parametrize("file_path", RUNTIME_FILES)
    def test_no_growth_implementation_import(self, file_path):
        """T6.1: Runtime 三个新文件均不 import GrowthEngine 业务实现"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        for forbidden in (
            "from src.growth.growth_engine",
            "from src.growth.proposal.proposal",
            "import src.growth.growth_engine",
        ):
            assert forbidden not in content, (
                f"{file_path.name} 不应直接 import GrowthEngine 业务实现: {forbidden}"
            )

    def test_runtime_does_not_import_growth_schema_directly(self):
        """T6.2: Runtime 不 import canonical GrowthProposal（避免反向依赖）"""
        for file_path in RUNTIME_FILES:
            if not file_path.exists():
                continue
            content = file_path.read_text(encoding="utf-8")
            # 允许通过 contract 间接引用，但不应直接 import
            assert "from src.contracts.growth_schema" not in content, (
                f"{file_path.name} 不应直接 import canonical schema"
            )

    def test_runtime_does_not_import_proposal_normalizer(self):
        """T6.3: Runtime 不 import Proposal Normalizer（属于业务层）"""
        for file_path in RUNTIME_FILES:
            if not file_path.exists():
                continue
            content = file_path.read_text(encoding="utf-8")
            assert "proposal_normalizer" not in content, (
                f"{file_path.name} 不应 import proposal_normalizer（属于业务层）"
            )


# ============================================================
# T7: 不直接依赖 Emotion 实现
# ============================================================

class TestRuntimeDoesNotDependOnEmotion:
    """Runtime 不直接 import Emotion 业务实现。"""

    @pytest.mark.parametrize("file_path", RUNTIME_FILES)
    def test_no_emotion_implementation_import(self, file_path):
        """T7.1: Runtime 三个新文件均不 import Emotion 业务实现"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        for forbidden in (
            "from src.emotion.emotion_manager",
            "from src.emotion.emotion_engine",
            "from src.emotion.emotion_state",
            "import src.emotion.emotion_manager",
        ):
            assert forbidden not in content, (
                f"{file_path.name} 不应直接 import Emotion 业务实现: {forbidden}"
            )


# ============================================================
# T8: 反依赖基线（综合）
# ============================================================

class TestNoForbiddenBusinessDependencies:
    """Runtime 三个新文件均不依赖禁止的业务实现模块。"""

    @pytest.mark.parametrize("file_path", RUNTIME_FILES)
    def test_no_forbidden_business_imports(self, file_path):
        """T8.1: 不 import 任何禁止的业务实现模块"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_BUSINESS_IMPORTS:
            assert f"from {forbidden}" not in content, (
                f"{file_path.name} 不应 import {forbidden}"
            )
            assert f"import {forbidden}" not in content, (
                f"{file_path.name} 不应 import {forbidden}"
            )


# ============================================================
# T9: 文档
# ============================================================

class TestRuntimeDocExists:
    """docs/runtime.md 设计文档存在并含关键章节。"""

    def test_doc_exists(self):
        """T9.1: docs/runtime.md 存在"""
        assert RUNTIME_DOC_PATH.exists(), (
            f"runtime 文档不存在: {RUNTIME_DOC_PATH}"
        )

    def test_doc_contains_lifecycle_sections(self):
        """T9.2: runtime 文档含生命周期各阶段"""
        content = RUNTIME_DOC_PATH.read_text(encoding="utf-8")
        for stage in (
            "START",
            "Load State",
            "Receive Event",
            "Memory Retrieval",
            "Emotion Update",
            "Growth Evaluation",
            "Personality Update",
            "Response",
            "Persistence",
        ):
            assert stage in content, f"文档缺阶段: {stage}"

    def test_doc_contains_runtime_context_section(self):
        """T9.3: runtime 文档含 RuntimeContext 字段说明"""
        content = RUNTIME_DOC_PATH.read_text(encoding="utf-8")
        for field in (
            "session_id",
            "user_input",
            "timestamp",
            "memory_context",
            "emotion_state",
            "personality_snapshot",
            "growth_proposals",
        ):
            assert field in content, f"文档缺字段: {field}"


# ============================================================
# T10: 错误隔离（设计契约）
# ============================================================

class TestErrorIsolation:
    """Runtime 在某个 port 抛出异常时不影响其他阶段。"""

    def test_port_exception_does_not_break_pipeline(self):
        """T10.1: Memory port 抛异常时，Emotion 阶段仍执行"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event

        class ExplodingMemory:
            def retrieve(self, ctx):
                raise RuntimeError("memory 故障")

        class WorkingEmotion:
            def __init__(self):
                self.called = False
            def update(self, ctx):
                self.called = True
                ctx.emotion_state = {"mood": "calm"}
                return ctx.emotion_state

        emo = WorkingEmotion()
        core = RuntimeCore(
            memory_port=ExplodingMemory(),
            emotion_port=emo,
        )
        core.start()
        ctx = core.process(Event(type="user_input"))
        # Emotion 阶段仍执行
        assert emo.called is True
        assert ctx.emotion_state == {"mood": "calm"}
        # 阶段错误被记录
        errors = core.get_stage_errors()
        assert "memory_retrieval" in errors

    def test_no_port_does_not_break_pipeline(self):
        """T10.2: 没有 port 时仍可运行（设计阶段允许）"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        core = RuntimeCore()
        core.start()
        ctx = core.process(Event(type="user_input"))
        # 不报错，字段保持空
        assert ctx.memory_context is None
        assert ctx.emotion_state is None
        assert ctx.growth_proposals == []


# ============================================================
# 主入口（汇总）
# ============================================================

def test_phase_3_7_0_design_summary():
    """汇总 Phase 3.7.0 设计状态"""
    # 1) RuntimeContext
    from src.runtime.context import RuntimeContext
    ctx = RuntimeContext()
    assert ctx.schema_version == "1.0"
    # 2) Event
    from src.runtime.events import Event
    e = Event()
    assert e.priority == 0
    # 3) RuntimeCore
    from src.runtime.runtime import RuntimeCore, RUNTIME_LIFECYCLE_ORDER
    core = RuntimeCore()
    # Phase 3.8.0 + 3.8.4 + 4.0.0 + 4.1.0: PERSONALITY_CONTEXT_BUILD + RESPONSE_GENERATION + GUARD_CHAIN + PERCEPTION_OBSERVATION (14 阶段)
    # Phase 4.2.0: 新增 PERCEPTION_ANALYSIS (15 阶段)
    # Phase 4.2.1: 新增 SELF_MODEL_BUILD (16 阶段)
    assert len(RUNTIME_LIFECYCLE_ORDER) in (14, 15, 16)
    # 4) Runtime 三个文件存在
    for p in RUNTIME_FILES:
        assert p.exists(), f"{p} 不存在"
    # 5) 设计文档存在
    assert RUNTIME_DOC_PATH.exists()
