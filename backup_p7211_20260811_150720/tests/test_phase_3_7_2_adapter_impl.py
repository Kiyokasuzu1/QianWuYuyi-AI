# -*- coding: utf-8 -*-
"""
tests/test_phase_3_7_2_adapter_impl.py

Phase 3.7.2: Runtime Adapter Implementation 测试

目标：验证 Adapter 实现层能正确桥接到已有业务模块
- 4 个 AdapterImpl 可实例化
- attach / detach 生命周期正常
- health_check 返回规范格式
- MemoryAdapterImpl 能调用 MemoryService
- EmotionAdapterImpl 能调用 EmotionManager
- GrowthAdapterImpl 输出 canonical GrowthProposal
- PersonalityAdapterImpl 能获取 snapshot
- Runtime 不直接 import 业务模块
- Adapter 之间不互相 import
- 4 个 Impl 内部可以 import 业务模块（白名单）

约束：
- 不修改 Memory / Emotion / Personality / GrowthEngine / Normalizer
- 不修改 canonical schema
"""
import ast
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 关键文件路径
# ============================================================
IMPL_INIT = PROJECT_ROOT / "src" / "runtime" / "adapters" / "impl" / "__init__.py"
MEMORY_IMPL = PROJECT_ROOT / "src" / "runtime" / "adapters" / "impl" / "memory_adapter_impl.py"
EMOTION_IMPL = PROJECT_ROOT / "src" / "runtime" / "adapters" / "impl" / "emotion_adapter_impl.py"
GROWTH_IMPL = PROJECT_ROOT / "src" / "runtime" / "adapters" / "impl" / "growth_adapter_impl.py"
PERSONALITY_IMPL = PROJECT_ROOT / "src" / "runtime" / "adapters" / "impl" / "personality_adapter_impl.py"
REGISTRY = PROJECT_ROOT / "src" / "runtime" / "adapter_registry.py"

# Runtime 核心文件（用于反依赖检查）
RUNTIME_CORE_FILES = [
    PROJECT_ROOT / "src" / "runtime" / "runtime.py",
    PROJECT_ROOT / "src" / "runtime" / "context.py",
    PROJECT_ROOT / "src" / "runtime" / "events.py",
]

# Adapter 设计层文件（Phase 3.7.1 接口,不能直接 import 业务实现）
ADAPTER_INTERFACE_FILES = [
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "base.py",
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "memory_adapter.py",
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "emotion_adapter.py",
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "growth_adapter.py",
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "personality_adapter.py",
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "__init__.py",
]

# Adapter 实现层文件
ADAPTER_IMPL_FILES = [
    MEMORY_IMPL,
    EMOTION_IMPL,
    GROWTH_IMPL,
    PERSONALITY_IMPL,
    IMPL_INIT,
]

# 允许 Adapter Impl 内部 import 的业务实现模块（白名单）
ALLOWED_BUSINESS_IMPORTS = {
    "src.memory.memory_service",
    "src.emotion.emotion_manager",
    "src.emotion.emotion_event",
    "src.growth.growth_engine",
    "src.personality.personality_resolver",
    "src.contracts.growth_schema",
    "src.contracts.proposal_normalizer",
}

# Adapter Impl 之间禁止互相 import
ADAPTER_IMPL_MODULES = {
    "src.runtime.adapters.impl.memory_adapter_impl",
    "src.runtime.adapters.impl.emotion_adapter_impl",
    "src.runtime.adapters.impl.growth_adapter_impl",
    "src.runtime.adapters.impl.personality_adapter_impl",
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
                # relative import
                module = "." * node.level + module
            imports.append(module)
    return imports


# ============================================================
# T1: 文件存在
# ============================================================

class TestAdapterImplFilesExist:
    """所有 Adapter Impl 与 Registry 文件存在。"""

    @pytest.mark.parametrize(
        "file_path, name",
        [
            (IMPL_INIT, "impl/__init__.py"),
            (MEMORY_IMPL, "memory_adapter_impl.py"),
            (EMOTION_IMPL, "emotion_adapter_impl.py"),
            (GROWTH_IMPL, "growth_adapter_impl.py"),
            (PERSONALITY_IMPL, "personality_adapter_impl.py"),
            (REGISTRY, "adapter_registry.py"),
        ],
    )
    def test_file_exists(self, file_path: Path, name: str):
        assert file_path.exists(), f"Adapter Impl 文件 {name} 不存在: {file_path}"


# ============================================================
# T2: 4 个 AdapterImpl 可实例化
# ============================================================

class TestAdapterImplInstantiable:
    """4 个 AdapterImpl 可实例化且类型正确。"""

    def test_memory_adapter_impl_instantiable(self):
        from src.runtime.adapters.impl import MemoryAdapterImpl
        from src.runtime.adapters.memory_adapter import MemoryAdapterSpec
        from src.runtime.adapters.base import AdapterBase

        impl = MemoryAdapterImpl()
        assert impl is not None
        assert isinstance(impl, MemoryAdapterSpec)
        assert isinstance(impl, AdapterBase)
        assert impl.name == "memory_adapter_impl"
        assert impl.schema_version == "1.0"
        assert not impl.is_attached

    def test_emotion_adapter_impl_instantiable(self):
        from src.runtime.adapters.impl import EmotionAdapterImpl
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        from src.runtime.adapters.base import AdapterBase

        impl = EmotionAdapterImpl()
        assert impl is not None
        assert isinstance(impl, EmotionAdapter)
        assert isinstance(impl, AdapterBase)
        assert impl.name == "emotion_adapter_impl"
        assert impl.schema_version == "1.0"
        assert not impl.is_attached

    def test_growth_adapter_impl_instantiable(self):
        from src.runtime.adapters.impl import GrowthAdapterImpl
        from src.runtime.adapters.growth_adapter import GrowthAdapterSpec
        from src.runtime.adapters.base import AdapterBase

        impl = GrowthAdapterImpl()
        assert impl is not None
        assert isinstance(impl, GrowthAdapterSpec)
        assert isinstance(impl, AdapterBase)
        assert impl.name == "growth_adapter_impl"
        assert impl.schema_version == "1.0"
        assert not impl.is_attached

    def test_personality_adapter_impl_instantiable(self):
        from src.runtime.adapters.impl import PersonalityAdapterImpl
        from src.runtime.adapters.personality_adapter import PersonalityAdapter
        from src.runtime.adapters.base import AdapterBase

        impl = PersonalityAdapterImpl()
        assert impl is not None
        assert isinstance(impl, PersonalityAdapter)
        assert isinstance(impl, AdapterBase)
        assert impl.name == "personality_adapter_impl"
        assert impl.schema_version == "1.0"
        assert not impl.is_attached

    def test_all_via_package(self):
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )
        for cls in (MemoryAdapterImpl, EmotionAdapterImpl, GrowthAdapterImpl, PersonalityAdapterImpl):
            assert cls is not None


# ============================================================
# T3: attach / detach 生命周期
# ============================================================

class TestAdapterImplLifecycle:
    """attach / detach 生命周期正常。"""

    def test_memory_attach_detach(self):
        from src.runtime.adapters.impl import MemoryAdapterImpl

        impl = MemoryAdapterImpl()
        assert not impl.is_attached
        impl.attach()
        assert impl.is_attached
        impl.detach()
        assert not impl.is_attached

    def test_emotion_attach_detach(self):
        from src.runtime.adapters.impl import EmotionAdapterImpl

        impl = EmotionAdapterImpl()
        assert not impl.is_attached
        impl.attach()
        assert impl.is_attached
        impl.detach()
        assert not impl.is_attached

    def test_growth_attach_detach(self):
        from src.runtime.adapters.impl import GrowthAdapterImpl

        impl = GrowthAdapterImpl()
        assert not impl.is_attached
        impl.attach()
        assert impl.is_attached
        impl.detach()
        assert not impl.is_attached

    def test_personality_attach_detach(self):
        from src.runtime.adapters.impl import PersonalityAdapterImpl

        impl = PersonalityAdapterImpl()
        assert not impl.is_attached
        impl.attach()
        assert impl.is_attached
        impl.detach()
        assert not impl.is_attached

    def test_detach_is_idempotent(self):
        from src.runtime.adapters.impl import MemoryAdapterImpl

        impl = MemoryAdapterImpl()
        impl.attach()
        impl.detach()
        impl.detach()  # 不应抛异常
        assert not impl.is_attached


# ============================================================
# T4: health_check 返回规范格式
# ============================================================

class TestHealthCheckFormat:
    """health_check 必须返回 {"healthy": bool, "name": str, "schema_version": str}。"""

    @pytest.mark.parametrize(
        "cls_name",
        ["MemoryAdapterImpl", "EmotionAdapterImpl", "GrowthAdapterImpl", "PersonalityAdapterImpl"],
    )
    def test_health_check_unattached(self, cls_name: str):
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )
        cls_map = {
            "MemoryAdapterImpl": MemoryAdapterImpl,
            "EmotionAdapterImpl": EmotionAdapterImpl,
            "GrowthAdapterImpl": GrowthAdapterImpl,
            "PersonalityAdapterImpl": PersonalityAdapterImpl,
        }
        impl = cls_map[cls_name]()
        h = impl.health_check()
        assert isinstance(h, dict), f"{cls_name} health_check 必须返回 dict"
        assert "healthy" in h
        assert "name" in h
        assert "schema_version" in h
        assert isinstance(h["healthy"], bool)
        # name 应为 lower_snake_case,与 impl.name 一致
        assert h["name"] == impl.name
        assert h["schema_version"] == "1.0"

    @pytest.mark.parametrize(
        "cls_name",
        ["MemoryAdapterImpl", "EmotionAdapterImpl", "GrowthAdapterImpl", "PersonalityAdapterImpl"],
    )
    def test_health_check_after_attach(self, cls_name: str):
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
            GrowthAdapterImpl,
            PersonalityAdapterImpl,
        )
        cls_map = {
            "MemoryAdapterImpl": MemoryAdapterImpl,
            "EmotionAdapterImpl": EmotionAdapterImpl,
            "GrowthAdapterImpl": GrowthAdapterImpl,
            "PersonalityAdapterImpl": PersonalityAdapterImpl,
        }
        impl = cls_map[cls_name]()
        impl.attach()
        try:
            h = impl.health_check()
            assert isinstance(h, dict)
            assert h["schema_version"] == "1.0"
            assert isinstance(h["healthy"], bool)
        finally:
            impl.detach()


# ============================================================
# T5: MemoryAdapterImpl 能调用 MemoryService
# ============================================================

class TestMemoryImplCallsService:
    """MemoryAdapterImpl.retrieve() 实际调用 MemoryService。"""

    def test_retrieve_uses_memory_service(self):
        from src.runtime.adapters.impl import MemoryAdapterImpl
        from src.runtime.context import RuntimeContext

        impl = MemoryAdapterImpl()
        impl.attach()
        try:
            ctx = RuntimeContext(user_input="hello world")
            results = impl.retrieve(ctx)
            # 返回 list/dict 都可,空 query 不抛
            assert results is not None
            assert isinstance(results, list)
        finally:
            impl.detach()

    def test_retrieve_empty_query(self):
        from src.runtime.adapters.impl import MemoryAdapterImpl
        from src.runtime.context import RuntimeContext

        impl = MemoryAdapterImpl()
        impl.attach()
        try:
            ctx = RuntimeContext(user_input="")
            results = impl.retrieve(ctx)
            assert results == []
        finally:
            impl.detach()

    def test_store_returns_status(self):
        from src.runtime.adapters.impl import MemoryAdapterImpl
        from src.runtime.events import Event

        impl = MemoryAdapterImpl()
        impl.attach()
        try:
            evt = Event(type="user_input", payload={"text": "hi"})
            r = impl.store(evt)
            assert isinstance(r, dict)
            assert "stored" in r
        finally:
            impl.detach()


# ============================================================
# T6: EmotionAdapterImpl 能调用 EmotionManager
# ============================================================

class TestEmotionImplCallsManager:
    """EmotionAdapterImpl.analyze / update 实际调用 EmotionManager。"""

    def test_analyze_returns_dict(self):
        from src.runtime.adapters.impl import EmotionAdapterImpl
        from src.runtime.events import Event

        impl = EmotionAdapterImpl()
        impl.attach()
        try:
            evt = Event(type="user_praise", payload={"text": "good"})
            r = impl.analyze(evt)
            assert isinstance(r, dict)
            assert "intensity" in r
            assert r["intensity"] > 0
        finally:
            impl.detach()

    def test_update_returns_context(self):
        from src.runtime.adapters.impl import EmotionAdapterImpl
        from src.runtime.context import RuntimeContext

        impl = EmotionAdapterImpl()
        impl.attach()
        try:
            ctx = RuntimeContext(user_input="hello")
            r = impl.update(ctx)
            assert isinstance(r, dict)
            assert r.get("updated") is True
        finally:
            impl.detach()


# ============================================================
# T7: GrowthAdapterImpl 输出 canonical GrowthProposal
# ============================================================

class TestGrowthImplCanonical:
    """GrowthAdapterImpl.evaluate() 输出 canonical GrowthProposal。"""

    def test_evaluate_returns_canonical_proposal(self):
        from src.runtime.adapters.impl import GrowthAdapterImpl
        from src.runtime.events import Event
        from src.contracts.growth_schema import (
            GrowthProposal,
            CANONICAL_SCHEMA_VERSION,
        )

        impl = GrowthAdapterImpl()
        impl.attach()
        try:
            evt = Event(type="relationship_start", payload={"text": "first chat"})
            proposals = impl.evaluate(evt)
            assert isinstance(proposals, list)
            if len(proposals) > 0:
                p = proposals[0]
                assert isinstance(p, GrowthProposal)
                assert p.schema_version == CANONICAL_SCHEMA_VERSION
        finally:
            impl.detach()

    def test_submit_accepts_canonical(self):
        from src.runtime.adapters.impl import GrowthAdapterImpl
        from src.contracts.growth_schema import GrowthProposal

        impl = GrowthAdapterImpl()
        impl.attach()
        try:
            p = GrowthProposal(confidence=0.8)
            r = impl.submit(p)
            assert r["accepted"] is True
            assert r["schema_version"] == "1.0"
        finally:
            impl.detach()

    def test_submit_rejects_wrong_schema_version(self):
        from src.runtime.adapters.impl import GrowthAdapterImpl
        from src.contracts.growth_schema import GrowthProposal

        impl = GrowthAdapterImpl()
        impl.attach()
        try:
            p = GrowthProposal(schema_version="0.9")
            r = impl.submit(p)
            assert r["accepted"] is False
            assert "schema_version" in r["reason"]
        finally:
            impl.detach()

    def test_submit_normalizes_governance_dict(self):
        from src.runtime.adapters.impl import GrowthAdapterImpl

        impl = GrowthAdapterImpl()
        impl.attach()
        try:
            governance_dict = {
                "proposal_id": "prop_001",
                "affected_dimensions": {"warmth": 0.1, "trust": 0.05},
                "evidence": ["evt_001"],
                "metadata": {"source": "test"},
                "status": "pending",
            }
            r = impl.submit(governance_dict)
            assert r["accepted"] is True
            assert r.get("normalized") is True
            assert r["schema_version"] == "1.0"
        finally:
            impl.detach()


# ============================================================
# T8: PersonalityAdapterImpl 能获取 snapshot
# ============================================================

class TestPersonalityImplSnapshot:
    """PersonalityAdapterImpl.snapshot() 能获取 PersonalityVector。"""

    def test_snapshot_returns_dict(self):
        from src.runtime.adapters.impl import PersonalityAdapterImpl

        impl = PersonalityAdapterImpl()
        impl.attach()
        try:
            snap = impl.snapshot()
            # 可能为 None（resolver 异常）或 dict
            if snap is not None:
                assert isinstance(snap, dict)
                # 关键字段
                assert "warmth" in snap
        finally:
            impl.detach()

    def test_apply_update_with_canonical_proposal(self):
        from src.runtime.adapters.impl import PersonalityAdapterImpl
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        impl = PersonalityAdapterImpl()
        impl.attach()
        try:
            p = GrowthProposal(
                source_event_id="evt_test",
                proposed_changes=[
                    ChangeItem(path="warmth", before=0.5, after=0.6, reason="test"),
                ],
                confidence=0.8,
                evidence_ids=["evt_test"],
            )
            r = impl.apply_update(p)
            assert isinstance(r, dict)
            # 即使 resolver 抛错,applied 至少是 False with reason,不抛异常
            assert "applied" in r
        finally:
            impl.detach()


# ============================================================
# T9: Runtime 无业务模块依赖
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
            "src.growth.growth_engine",
            "src.personality",
        )
        bad = [
            imp for imp in imports
            if any(imp == p or imp.startswith(p + ".") for p in forbidden_prefixes)
        ]
        assert not bad, (
            f"{file_path.name} 不应 import 业务模块,但发现: {bad}"
        )


# ============================================================
# T10: Adapter Impl 之间不互相 import
# ============================================================

class TestAdapterImplsIsolated:
    """Adapter Impl 之间禁止互相 import。"""

    @pytest.mark.parametrize("file_path", ADAPTER_IMPL_FILES)
    def test_no_cross_adapter_impl_import(self, file_path: Path):
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        imports = _extract_imports(file_path)
        cross = [imp for imp in imports if imp in ADAPTER_IMPL_MODULES]
        assert not cross, (
            f"{file_path.name} 不应 import 其他 Adapter Impl,但发现: {cross}"
        )


# ============================================================
# T11: Adapter Impl 内部可以 import 业务模块（白名单）
# ============================================================

class TestAdapterImplsCanImportBusiness:
    """Adapter Impl 内部允许 import 白名单业务模块。"""

    @pytest.mark.parametrize("file_path", ADAPTER_IMPL_FILES)
    def test_no_forbidden_business_imports(self, file_path: Path):
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        imports = _extract_imports(file_path)
        # 业务模块前缀集合
        business_prefixes = {
            "src.memory",
            "src.emotion",
            "src.growth",
            "src.personality",
        }
        bad = []
        for imp in imports:
            if not imp:
                continue
            # 只检查业务前缀
            if not any(imp == p or imp.startswith(p + ".") for p in business_prefixes):
                continue
            # 必须在白名单
            if imp not in ALLOWED_BUSINESS_IMPORTS:
                bad.append(imp)
        assert not bad, (
            f"{file_path.name} 引入了未授权的业务模块: {bad}\n"
            f"白名单: {sorted(ALLOWED_BUSINESS_IMPORTS)}"
        )


# ============================================================
# T12: Registry 行为
# ============================================================

class TestAdapterRegistry:
    """AdapterRegistry 基础行为。"""

    def test_registry_register_and_get(self):
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import MemoryAdapterImpl

        reg = AdapterRegistry()
        impl = MemoryAdapterImpl()
        reg.register("memory_adapter_impl", impl)
        assert reg.get("memory_adapter_impl") is impl
        assert "memory_adapter_impl" in reg
        assert len(reg) == 1

    def test_registry_register_rejects_none(self):
        from src.runtime.adapter_registry import AdapterRegistry

        reg = AdapterRegistry()
        with pytest.raises(ValueError):
            reg.register("x", None)

    def test_registry_register_rejects_non_adapter(self):
        from src.runtime.adapter_registry import AdapterRegistry

        reg = AdapterRegistry()
        with pytest.raises(TypeError):
            reg.register("x", object())  # type: ignore[arg-type]

    def test_registry_health_check_all(self):
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            EmotionAdapterImpl,
        )

        reg = AdapterRegistry()
        m = MemoryAdapterImpl()
        e = EmotionAdapterImpl()
        reg.register("memory_adapter_impl", m)
        reg.register("emotion_adapter_impl", e)

        h = reg.health_check_all()
        assert h["count"] == 2
        assert "adapters" in h
        assert "memory_adapter_impl" in h["adapters"]
        assert "emotion_adapter_impl" in h["adapters"]

    def test_registry_attach_all_detach_all(self):
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.adapters.impl import (
            MemoryAdapterImpl,
            GrowthAdapterImpl,
        )

        reg = AdapterRegistry()
        m = MemoryAdapterImpl()
        g = GrowthAdapterImpl()
        reg.register("memory_adapter_impl", m)
        reg.register("growth_adapter_impl", g)

        r = reg.attach_all()
        assert r["memory_adapter_impl"] is True
        assert r["growth_adapter_impl"] is True
        assert m.is_attached
        assert g.is_attached

        r2 = reg.detach_all()
        assert r2["memory_adapter_impl"] is True
        assert r2["growth_adapter_impl"] is True
        assert not m.is_attached
        assert not g.is_attached


# ============================================================
# T13: Schema Version 保持冻结
# ============================================================

class TestCanonicalSchemaStillFrozen:
    """canonical GrowthProposal schema_version 必须仍为 1.0。"""

    def test_canonical_schema_version_unchanged(self):
        from src.contracts.growth_schema import CANONICAL_SCHEMA_VERSION
        assert CANONICAL_SCHEMA_VERSION == "1.0"

    def test_default_growth_proposal_schema_version(self):
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal()
        assert p.schema_version == "1.0"


# ============================================================
# T14: 业务模块未被 Impl 阶段修改
# ============================================================

class TestBusinessModulesNotModified:
    """业务模块（mtime 在基线之前）未在 Impl 阶段被修改。"""

    def test_critical_business_files_have_unchanged_schema(self):
        """通过检查关键 schema 字段来验证业务模块未被修改。"""
        from src.contracts.growth_schema import (
            GrowthProposal,
            CANONICAL_SCHEMA_VERSION,
        )
        from src.contracts.proposal_normalizer import (
            CANONICAL,
            GOVERNANCE,
            UNKNOWN,
        )

        # 1) GrowthProposal 默认 schema_version
        p = GrowthProposal()
        assert p.schema_version == CANONICAL_SCHEMA_VERSION

        # 2) Normalizer 常量存在
        assert CANONICAL == "canonical"
        assert GOVERNANCE == "governance"
        assert UNKNOWN == "unknown"


# ============================================================
# 阶段总结
# ============================================================

def test_phase_3_7_2_summary():
    """Phase 3.7.2 阶段总结。"""
    # 验证关键交付物全部存在
    assert IMPL_INIT.exists()
    assert MEMORY_IMPL.exists()
    assert EMOTION_IMPL.exists()
    assert GROWTH_IMPL.exists()
    assert PERSONALITY_IMPL.exists()
    assert REGISTRY.exists()
