# -*- coding: utf-8 -*-
"""
tests/test_phase_3_7_3_runtime_assembly.py

Phase 3.7.3: Runtime Assembly 测试

目标：
1. RuntimeCore 可以注入 AdapterRegistry
2. Registry 可注册 4 个 AdapterImpl
3. Runtime 启动时所有 Adapter attach 被调用
4. health_check_all 返回完整结果
5. 事件流顺序正确：Memory → Emotion → Growth → Personality
6. Runtime 不直接 import 业务模块
7. 单 Adapter 异常不影响其它 Adapter

约束：
- 不修改 Memory / Emotion / Personality / GrowthEngine / Normalizer
- 不修改 canonical schema
"""
import ast
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 关键文件路径
# ============================================================
RUNTIME_FILE = PROJECT_ROOT / "src" / "runtime" / "runtime.py"
REGISTRY_FILE = PROJECT_ROOT / "src" / "runtime" / "adapter_registry.py"

# Runtime 核心文件（用于反依赖检查）
RUNTIME_CORE_FILES = [
    PROJECT_ROOT / "src" / "runtime" / "runtime.py",
    PROJECT_ROOT / "src" / "runtime" / "context.py",
    PROJECT_ROOT / "src" / "runtime" / "events.py",
]

# 禁止 Runtime / Registry / Adapter 抽象 / Adapter Impl 直接 import 的业务实现模块
FORBIDDEN_BUSINESS_IMPORTS = {
    "src.memory.memory_service",
    "src.memory.memory_store",
    "src.emotion.emotion_manager",
    "src.emotion.emotion_engine",
    "src.personality.personality_resolver",
    "src.personality.personality_controller",
    "src.growth.growth_engine",
    "src.contracts.proposal_normalizer",
}


# ============================================================
# 工具函数
# ============================================================
def _extract_imports(file_path: Path) -> List[str]:
    """从 .py 文件中提取所有 import / from ... import 字符串。"""
    if not file_path.exists():
        return []
    src = file_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    imports: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and node.level > 0:
                module = "." * node.level + module
            imports.append(module)
    return imports


def _build_default_registry() -> Any:
    """构造一个默认 Registry,内含 4 个 AdapterImpl。"""
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


# ============================================================
# T1: RuntimeCore 接受 AdapterRegistry
# ============================================================

class TestRuntimeAcceptsRegistry:
    """RuntimeCore 支持注入 AdapterRegistry。"""

    def test_runtime_accepts_adapter_registry(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry

        reg = AdapterRegistry()
        core = RuntimeCore(adapter_registry=reg)
        assert core.adapter_registry is reg

    def test_runtime_version_is_3_7_3(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.1.0 进一步推进到 4.1.0 (Perception 阶段真实激活)
        # Phase 4.2.0 进一步推进到 4.2.0 (Vision 阶段真实激活)
        # Phase 4.2.1 进一步推进到 4.2.1 (Self Model Foundation 阶段真实激活)
        # Phase 4.2.2 进一步推进到 4.2.2 (Self Model SourceAdapter 集成)
        # Phase 4.5 进一步推进到 4.5 (Self Model Evolution)
        assert RuntimeCore.RUNTIME_VERSION in (
            "3.7.3", "4.0.0", "4.1.0", "4.2.0", "4.2.1", "4.2.2",
            "4.2.3", "4.2.4", "4.3", "4.4", "4.5", "4.6",
        )

    def test_runtime_default_adapter_names_exposed(self):
        from src.runtime.runtime import (
            DEFAULT_MEMORY_ADAPTER_NAME,
            DEFAULT_EMOTION_ADAPTER_NAME,
            DEFAULT_GROWTH_ADAPTER_NAME,
            DEFAULT_PERSONALITY_ADAPTER_NAME,
        )
        assert DEFAULT_MEMORY_ADAPTER_NAME == "memory_adapter_impl"
        assert DEFAULT_EMOTION_ADAPTER_NAME == "emotion_adapter_impl"
        assert DEFAULT_GROWTH_ADAPTER_NAME == "growth_adapter_impl"
        assert DEFAULT_PERSONALITY_ADAPTER_NAME == "personality_adapter_impl"

    def test_runtime_configure_injects_registry(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry

        core = RuntimeCore()
        assert core.adapter_registry is None
        reg = AdapterRegistry()
        core.configure(reg)
        assert core.adapter_registry is reg

    def test_runtime_backward_compat_with_ports(self):
        """Phase 3.7.0 旧 API *_port 仍可工作。"""
        from src.runtime.runtime import RuntimeCore

        mem_port = MagicMock()
        emo_port = MagicMock()
        gro_port = MagicMock()
        per_port = MagicMock()
        core = RuntimeCore(
            memory_port=mem_port,
            emotion_port=emo_port,
            growth_port=gro_port,
            personality_port=per_port,
        )
        assert core.adapter_registry is None


# ============================================================
# T2: Registry 可注册 4 个 AdapterImpl
# ============================================================

class TestRegistryRegistration:
    """AdapterRegistry 可注册 4 个 AdapterImpl。"""

    def test_registry_registers_four_adapters(self):
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

        assert len(reg) == 4
        assert reg.has("memory_adapter_impl")
        assert reg.has("emotion_adapter_impl")
        assert reg.has("growth_adapter_impl")
        assert reg.has("personality_adapter_impl")

    def test_get_returns_correct_instances(self):
        reg = _build_default_registry()
        m = reg.get("memory_adapter_impl")
        e = reg.get("emotion_adapter_impl")
        g = reg.get("growth_adapter_impl")
        p = reg.get("personality_adapter_impl")
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )
        assert isinstance(m, MemoryAdapterImpl)
        assert isinstance(e, EmotionAdapterImpl)
        assert isinstance(g, GrowthAdapterImpl)
        assert isinstance(p, PersonalityAdapterImpl)


# ============================================================
# T3: Runtime 启动时所有 Adapter attach 被调用
# ============================================================

class TestRuntimeStartCallsAttach:
    """Runtime 启动时所有 Adapter attach 被调用。"""

    def test_start_invokes_attach_on_all_adapters(self):
        """使用真实 Impl + patch.object 验证 attach 被调用。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )

        m = MemoryAdapterImpl()
        e = EmotionAdapterImpl()
        g = GrowthAdapterImpl()
        p = PersonalityAdapterImpl()

        with patch.object(m, "attach") as m_attach, \
             patch.object(e, "attach") as e_attach, \
             patch.object(g, "attach") as g_attach, \
             patch.object(p, "attach") as p_attach:
            reg = AdapterRegistry()
            reg.register("memory_adapter_impl", m)
            reg.register("emotion_adapter_impl", e)
            reg.register("growth_adapter_impl", g)
            reg.register("personality_adapter_impl", p)

            core = RuntimeCore(adapter_registry=reg)
            core.start()

            m_attach.assert_called_once()
            e_attach.assert_called_once()
            g_attach.assert_called_once()
            p_attach.assert_called_once()

    def test_attach_results_recorded(self):
        from src.runtime.runtime import RuntimeCore

        reg = _build_default_registry()
        core = RuntimeCore(adapter_registry=reg)
        core.start()
        results = core.attach_results
        assert results.get("memory_adapter_impl") is True
        assert results.get("emotion_adapter_impl") is True
        assert results.get("growth_adapter_impl") is True
        assert results.get("personality_adapter_impl") is True

    def test_is_started_after_start(self):
        from src.runtime.runtime import RuntimeCore

        reg = _build_default_registry()
        core = RuntimeCore(adapter_registry=reg)
        assert not core.is_started
        core.start()
        assert core.is_started


# ============================================================
# T4: health_check_all 返回完整结果
# ============================================================

class TestHealthCheckAll:
    """health_check_all 返回完整结果。"""

    def test_health_check_format(self):
        """格式：{adapter_name: {healthy, schema_version: "1.0"}}。"""
        reg = _build_default_registry()
        reg.attach_all()
        result = reg.health_check_all()
        assert "adapters" in result
        assert result["count"] == 4
        for name in (
            "memory_adapter_impl",
            "emotion_adapter_impl",
            "growth_adapter_impl",
            "personality_adapter_impl",
        ):
            assert name in result["adapters"]
            entry = result["adapters"][name]
            assert entry["healthy"] is True
            assert entry["schema_version"] == "1.0"

    def test_runtime_last_health_after_start(self):
        from src.runtime.runtime import RuntimeCore

        reg = _build_default_registry()
        core = RuntimeCore(adapter_registry=reg)
        core.start()
        h = core.last_health
        assert h is not None
        assert h["count"] == 4
        assert "adapters" in h
        assert h["adapters"]["memory_adapter_impl"]["schema_version"] == "1.0"


# ============================================================
# T5: 事件流顺序正确
# ============================================================

class TestEventFlowOrder:
    """事件流:Memory → Emotion → Growth → Personality。"""

    def test_event_flow_order_memory_emotion_growth_personality(self):
        """使用真实 Impl + patch.object 验证顺序。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.events import Event
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )

        call_order: List[str] = []

        m = MemoryAdapterImpl()
        e = EmotionAdapterImpl()
        g = GrowthAdapterImpl()
        p = PersonalityAdapterImpl()

        def make_recorder(name: str):
            def record(*a, **kw):
                call_order.append(name)
            return record

        with patch.object(m, "retrieve", side_effect=make_recorder("memory_adapter_impl.retrieve")), \
             patch.object(m, "store", side_effect=make_recorder("memory_adapter_impl.store")), \
             patch.object(e, "analyze", side_effect=make_recorder("emotion_adapter_impl.analyze")), \
             patch.object(e, "update", side_effect=make_recorder("emotion_adapter_impl.update")), \
             patch.object(g, "evaluate", side_effect=make_recorder("growth_adapter_impl.evaluate")), \
             patch.object(g, "submit", side_effect=make_recorder("growth_adapter_impl.submit")), \
             patch.object(p, "snapshot", side_effect=make_recorder("personality_adapter_impl.snapshot")):

            reg = AdapterRegistry()
            reg.register("memory_adapter_impl", m)
            reg.register("emotion_adapter_impl", e)
            reg.register("growth_adapter_impl", g)
            reg.register("personality_adapter_impl", p)

            core = RuntimeCore(adapter_registry=reg)
            core.start()
            core.process(Event(type="user_input", payload={"text": "hello"}))

        # 验证调用顺序
        assert call_order[0] == "memory_adapter_impl.retrieve"
        assert "emotion_adapter_impl.analyze" in call_order
        assert "emotion_adapter_impl.update" in call_order
        assert "growth_adapter_impl.evaluate" in call_order
        assert "personality_adapter_impl.snapshot" in call_order
        # 顺序约束
        assert call_order.index("memory_adapter_impl.retrieve") < call_order.index(
            "emotion_adapter_impl.analyze"
        )
        assert call_order.index("emotion_adapter_impl.update") < call_order.index(
            "growth_adapter_impl.evaluate"
        )
        assert call_order.index("growth_adapter_impl.evaluate") < call_order.index(
            "personality_adapter_impl.snapshot"
        )

    def test_process_returns_runtime_context(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext
        from src.runtime.events import Event

        reg = _build_default_registry()
        core = RuntimeCore(adapter_registry=reg)
        core.start()
        ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
        assert isinstance(ctx, RuntimeContext)

    def test_process_populates_ctx(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event

        reg = _build_default_registry()
        core = RuntimeCore(adapter_registry=reg)
        core.start()
        ctx = core.process(Event(type="user_praise", payload={"text": "good job"}))
        # 至少某些字段被填充
        assert ctx.emotion_state is not None
        # memory_context 可能为 []（空 query 情况）;允许 None
        # personality_snapshot 在 real impl 下可能为 None
        # 仅验证不为完全空白
        assert ctx.schema_version == "1.0"


# ============================================================
# T6: Runtime 不 import 业务模块
# ============================================================

class TestRuntimeNoBusinessImport:
    """Runtime 核心文件不直接 import 业务模块。"""

    @pytest.mark.parametrize("file_path", RUNTIME_CORE_FILES)
    def test_runtime_file_no_business_import(self, file_path: Path):
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        imports = _extract_imports(file_path)
        forbidden_prefixes = (
            "src.memory",
            "src.emotion",
            "src.growth",
            "src.personality",
        )
        bad = [
            imp for imp in imports
            if any(imp == p or imp.startswith(p + ".") for p in forbidden_prefixes)
        ]
        assert not bad, (
            f"{file_path.name} 不应 import 业务模块,但发现: {bad}"
        )

    def test_registry_file_no_business_import(self):
        imports = _extract_imports(REGISTRY_FILE)
        forbidden_prefixes = (
            "src.memory",
            "src.emotion",
            "src.growth",
            "src.personality",
        )
        bad = [
            imp for imp in imports
            if any(imp == p or imp.startswith(p + ".") for p in forbidden_prefixes)
        ]
        assert not bad, (
            f"adapter_registry.py 不应 import 业务模块,但发现: {bad}"
        )


# ============================================================
# T7: 异常隔离
# ============================================================

class TestAdapterErrorIsolation:
    """单 Adapter 异常不影响其它 Adapter。"""

    def test_one_adapter_exception_does_not_break_others(self):
        """memory.retrieve 抛异常,emotion/growth/personality 仍被调用。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.events import Event
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )

        m = MemoryAdapterImpl()
        e = EmotionAdapterImpl()
        g = GrowthAdapterImpl()
        p = PersonalityAdapterImpl()

        with patch.object(m, "retrieve", side_effect=RuntimeError("memory broken")), \
             patch.object(e, "analyze") as e_analyze, \
             patch.object(e, "update") as e_update, \
             patch.object(g, "evaluate") as g_evaluate, \
             patch.object(p, "snapshot") as p_snapshot:

            reg = AdapterRegistry()
            reg.register("memory_adapter_impl", m)
            reg.register("emotion_adapter_impl", e)
            reg.register("growth_adapter_impl", g)
            reg.register("personality_adapter_impl", p)

            core = RuntimeCore(adapter_registry=reg)
            core.start()
            ctx = core.process(Event(type="user_input", payload={"text": "x"}))
            assert ctx is not None

            e_analyze.assert_called()
            e_update.assert_called()
            g_evaluate.assert_called()
            p_snapshot.assert_called()

            errs = core.get_stage_errors()
            assert "memory_retrieval" in errs

    def test_emotion_failure_does_not_break_growth(self):
        """emotion.update 抛异常,growth / personality 仍被调用。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.events import Event
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )

        m = MemoryAdapterImpl()
        e = EmotionAdapterImpl()
        g = GrowthAdapterImpl()
        p = PersonalityAdapterImpl()

        with patch.object(e, "update", side_effect=RuntimeError("emotion broken")), \
             patch.object(g, "evaluate") as g_evaluate, \
             patch.object(p, "snapshot") as p_snapshot:

            reg = AdapterRegistry()
            reg.register("memory_adapter_impl", m)
            reg.register("emotion_adapter_impl", e)
            reg.register("growth_adapter_impl", g)
            reg.register("personality_adapter_impl", p)

            core = RuntimeCore(adapter_registry=reg)
            core.start()
            core.process(Event(type="user_input"))
            g_evaluate.assert_called()
            p_snapshot.assert_called()
            errs = core.get_stage_errors()
            assert "emotion_update" in errs

    def test_attach_failure_does_not_break_start(self):
        """某个 Adapter attach 失败,Runtime 仍能 start。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )

        m = MemoryAdapterImpl()
        e = EmotionAdapterImpl()
        g = GrowthAdapterImpl()
        p = PersonalityAdapterImpl()

        with patch.object(m, "attach", side_effect=RuntimeError("attach broken")):
            reg = AdapterRegistry()
            reg.register("memory_adapter_impl", m)
            reg.register("emotion_adapter_impl", e)
            reg.register("growth_adapter_impl", g)
            reg.register("personality_adapter_impl", p)

            core = RuntimeCore(adapter_registry=reg)
            core.start()
            assert core.is_started

    def test_runtime_no_registry_still_works(self):
        """无 registry 时,start 不报错（向后兼容 Phase 3.7.0）。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event

        core = RuntimeCore()
        core.start()
        ctx = core.process(Event(type="user_input"))
        assert ctx is not None


# ============================================================
# T8: 集成测试（用真实 Impl）
# ============================================================

class TestIntegrationWithRealImpl:
    """使用真实 AdapterImpl 端到端验证。"""

    def test_end_to_end_with_real_impls(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event

        reg = _build_default_registry()
        core = RuntimeCore(adapter_registry=reg)
        ctx = core.start()
        assert ctx is not None

        # 验证 4 个 Adapter 已 attach
        for a in reg.all():
            assert a.is_attached

        evt = Event(
            type="user_praise",
            payload={"text": "you did well"},
            priority=1,
        )
        ctx = core.process(evt)
        # 验证 ctx 已填充
        assert ctx.schema_version == "1.0"
        # emotion_state 必有（EmotionManager.process_event 返回 dict）
        assert ctx.emotion_state is not None

        core.shutdown()
        # shutdown 后所有 Adapter 已 detach
        for a in reg.all():
            assert not a.is_attached


# ============================================================
# T9: 业务模块未在 Assembly 阶段被修改
# ============================================================

class TestBusinessModulesIntact:
    """业务模块 schema_version 仍为 1.0,Phase 3.6.5 冻结保持。"""

    def test_canonical_schema_version(self):
        from src.contracts.growth_schema import CANONICAL_SCHEMA_VERSION
        assert CANONICAL_SCHEMA_VERSION == "1.0"

    def test_normalizer_constants_unchanged(self):
        from src.contracts.proposal_normalizer import (
            CANONICAL,
            GOVERNANCE,
            UNKNOWN,
        )
        assert CANONICAL == "canonical"
        assert GOVERNANCE == "governance"
        assert UNKNOWN == "unknown"


# ============================================================
# 阶段总结
# ============================================================

def test_phase_3_7_3_summary():
    """Phase 3.7.3 阶段总结。"""
    from src.runtime.runtime import RuntimeCore, RUNTIME_LIFECYCLE_ORDER
    from src.runtime.adapter_registry import AdapterRegistry
    from src.runtime.adapters.impl import (
        MemoryAdapterImpl,
        EmotionAdapterImpl,
        GrowthAdapterImpl,
        PersonalityAdapterImpl,
    )

    # 验证关键交付物全部存在且可加载
    # Phase 4.1.0 进一步推进到 4.1.0 (Perception 阶段真实激活)
    # Phase 4.2.0 进一步推进到 4.2.0 (Vision 阶段真实激活,新增 PERCEPTION_ANALYSIS)
    # Phase 4.2.1 进一步推进到 4.2.1 (Self Model Foundation 阶段真实激活,新增 SELF_MODEL_BUILD)
    # Phase 4.2.2 进一步推进到 4.2.2 (SourceAdapter 集成)
    # Phase 4.5 进一步推进到 4.5 (新增 SELF_MODEL_EVOLUTION)
    assert RuntimeCore.RUNTIME_VERSION in (
        "3.7.3", "4.0.0", "4.1.0", "4.2.0", "4.2.1", "4.2.2",
        "4.2.3", "4.2.4", "4.3", "4.4", "4.5",
    )
    # Phase 3.8.0 + 3.8.4 + 4.0.0 + 4.1.0: 14 阶段
    # Phase 4.2.0: 15 阶段 (新增 PERCEPTION_ANALYSIS)
    # Phase 4.2.1: 16 阶段 (新增 SELF_MODEL_BUILD)
    # Phase 4.5:  17 阶段 (新增 SELF_MODEL_EVOLUTION)
    assert len(RUNTIME_LIFECYCLE_ORDER) in (14, 15, 16, 17)
    for cls in (MemoryAdapterImpl, EmotionAdapterImpl, GrowthAdapterImpl, PersonalityAdapterImpl):
        assert cls is not None
    assert AdapterRegistry is not None
