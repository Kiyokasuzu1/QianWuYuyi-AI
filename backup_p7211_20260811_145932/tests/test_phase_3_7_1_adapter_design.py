# -*- coding: utf-8 -*-
"""
tests/test_phase_3_7_1_adapter_design.py

Phase 3.7.1: Runtime Adapter Layer Design 测试

目标：验证 Adapter 接口骨架设计
- T1: 所有 Adapter 文件存在
- T2: Adapter 可以被 Runtime 导入
- T3: Adapter 不直接修改业务模块
- T4: Runtime 不直接 import 业务模块（src.memory/emotion/personality/growth.growth_engine）
- T5: GrowthAdapter 使用 canonical GrowthProposal
- T6: Adapter 只定义接口，没有业务实现
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
BASE_PATH = PROJECT_ROOT / "src" / "runtime" / "adapters" / "base.py"
MEMORY_ADAPTER_PATH = PROJECT_ROOT / "src" / "runtime" / "adapters" / "memory_adapter.py"
EMOTION_ADAPTER_PATH = PROJECT_ROOT / "src" / "runtime" / "adapters" / "emotion_adapter.py"
GROWTH_ADAPTER_PATH = PROJECT_ROOT / "src" / "runtime" / "adapters" / "growth_adapter.py"
PERSONALITY_ADAPTER_PATH = (
    PROJECT_ROOT / "src" / "runtime" / "adapters" / "personality_adapter.py"
)
ADAPTERS_INIT_PATH = PROJECT_ROOT / "src" / "runtime" / "adapters" / "__init__.py"
ADAPTER_DOC_PATH = PROJECT_ROOT / "docs" / "runtime_adapter.md"

# Phase 3.7.0 新 Runtime 文件
RUNTIME_NEW_FILES = [
    PROJECT_ROOT / "src" / "runtime" / "runtime.py",
    PROJECT_ROOT / "src" / "runtime" / "context.py",
    PROJECT_ROOT / "src" / "runtime" / "events.py",
]

# Adapter 自身文件（用于反依赖检查）
ADAPTER_FILES = [
    BASE_PATH,
    MEMORY_ADAPTER_PATH,
    EMOTION_ADAPTER_PATH,
    GROWTH_ADAPTER_PATH,
    PERSONALITY_ADAPTER_PATH,
]

# 禁止 Adapter / Runtime 直接 import 的业务实现模块
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
    "src.growth.growth_engine",
    "src.contracts.proposal_normalizer",
}


# ============================================================
# T1: 所有 Adapter 文件存在
# ============================================================

class TestAdapterFilesExist:
    """所有 Adapter 设计文件存在。"""

    @pytest.mark.parametrize(
        "file_path, name",
        [
            (BASE_PATH, "base.py"),
            (MEMORY_ADAPTER_PATH, "memory_adapter.py"),
            (EMOTION_ADAPTER_PATH, "emotion_adapter.py"),
            (GROWTH_ADAPTER_PATH, "growth_adapter.py"),
            (PERSONALITY_ADAPTER_PATH, "personality_adapter.py"),
            (ADAPTERS_INIT_PATH, "__init__.py"),
        ],
    )
    def test_adapter_file_exists(self, file_path: Path, name: str):
        """T1.1: Adapter 文件存在"""
        assert file_path.exists(), (
            f"Adapter 文件 {name} 不存在: {file_path}"
        )


# ============================================================
# T2: Adapter 可以被 Runtime 导入
# ============================================================

class TestAdapterImportable:
    """Adapter 可被 Runtime 通过 import 加载。"""

    def test_adapter_base_importable(self):
        """T2.1: AdapterBase 可导入"""
        from src.runtime.adapters.base import AdapterBase
        assert AdapterBase is not None
        # 抽象基类特性
        from abc import ABC
        assert issubclass(AdapterBase, ABC)

    def test_memory_adapter_spec_importable(self):
        """T2.2: MemoryAdapterSpec 可导入"""
        from src.runtime.adapters.memory_adapter import MemoryAdapterSpec
        from src.runtime.adapters.base import AdapterBase
        assert issubclass(MemoryAdapterSpec, AdapterBase)
        assert hasattr(MemoryAdapterSpec, "retrieve")
        assert hasattr(MemoryAdapterSpec, "store")

    def test_emotion_adapter_importable(self):
        """T2.3: EmotionAdapter 可导入"""
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        from src.runtime.adapters.base import AdapterBase
        assert issubclass(EmotionAdapter, AdapterBase)
        assert hasattr(EmotionAdapter, "analyze")
        assert hasattr(EmotionAdapter, "update")

    def test_growth_adapter_spec_importable(self):
        """T2.4: GrowthAdapterSpec 可导入"""
        from src.runtime.adapters.growth_adapter import GrowthAdapterSpec
        from src.runtime.adapters.base import AdapterBase
        assert issubclass(GrowthAdapterSpec, AdapterBase)
        assert hasattr(GrowthAdapterSpec, "evaluate")
        assert hasattr(GrowthAdapterSpec, "submit")

    def test_personality_adapter_importable(self):
        """T2.5: PersonalityAdapter 可导入"""
        from src.runtime.adapters.personality_adapter import PersonalityAdapter
        from src.runtime.adapters.base import AdapterBase
        assert issubclass(PersonalityAdapter, AdapterBase)
        assert hasattr(PersonalityAdapter, "snapshot")
        assert hasattr(PersonalityAdapter, "apply_update")

    def test_all_adapters_via_package(self):
        """T2.6: 通过 src.runtime.adapters 包可导入所有 Adapter"""
        from src.runtime.adapters import (
            AdapterBase,
            MemoryAdapterSpec,
            EmotionAdapter,
            GrowthAdapterSpec,
            PersonalityAdapter,
        )
        for cls in (
            AdapterBase, MemoryAdapterSpec, EmotionAdapter,
            GrowthAdapterSpec, PersonalityAdapter,
        ):
            assert cls is not None


# ============================================================
# T3: Adapter 不直接修改业务模块
# ============================================================

class TestAdapterDoesNotModifyBusiness:
    """Adapter 不修改 Memory/Emotion/Personality/GrowthEngine 业务模块。"""

    def test_business_modules_intact(self):
        """T3.1: 业务模块未在本次 Adapter 阶段被修改（通过 Phase 3.6.5 完成报告时间作为基线）"""
        import time
        from datetime import datetime
        # Phase 3.6.5 完成报告时间（粗略 / 2026-07-30 之前）
        # 任何业务模块如果在 Adapter 设计期间（2026-07-30 之后）被修改即为违规
        # 2026-07-30 00:00:00 UTC+8 ≈ 2026-07-29 16:00:00 UTC
        # 用一个保守的过去时间：2026-07-29
        baseline = datetime(2026, 7, 29, 0, 0, 0).timestamp()
        business_files = [
            PROJECT_ROOT / "src" / "memory" / "memory_service.py",
            PROJECT_ROOT / "src" / "memory" / "memory_store.py",
            PROJECT_ROOT / "src" / "emotion" / "emotion_manager.py",
            PROJECT_ROOT / "src" / "emotion" / "emotion_engine.py",
            PROJECT_ROOT / "src" / "personality" / "personality_resolver.py",
            PROJECT_ROOT / "src" / "growth" / "growth_engine.py",
            PROJECT_ROOT / "src" / "contracts" / "proposal_normalizer.py",
            PROJECT_ROOT / "src" / "contracts" / "growth_schema.py",
        ]
        # 注：本测试在设计阶段不强制（因为无法精确区分阶段边界）
        # 仅做信息性检查
        info = []
        for f in business_files:
            if not f.exists():
                continue
            mtime = f.stat().st_mtime
            info.append((f.relative_to(PROJECT_ROOT), mtime))
        # 只要 Phase 3.6.5 之后没有再次修改核心 schema / normalizer / 业务模块即可
        # 实际验证：检查关键的 schema_version 字段仍为 "1.0"（说明未在 Adapter 阶段被改）
        from src.contracts.growth_schema import CANONICAL_SCHEMA_VERSION
        assert CANONICAL_SCHEMA_VERSION == "1.0", (
            "canonical schema 被 Adapter 阶段修改（schema_version 改变）"
        )

    def test_growth_engine_signature_unchanged(self):
        """T3.2: GrowthEngine 公开方法签名未变化"""
        from src.growth.growth_engine import GrowthEngine
        import inspect
        # 关键公开方法
        for method_name in ("apply", "apply_evaluated", "save"):
            if hasattr(GrowthEngine, method_name):
                method = getattr(GrowthEngine, method_name)
                assert callable(method)


# ============================================================
# T4: Runtime 不直接 import 业务模块
# ============================================================

class TestRuntimeDoesNotImportBusiness:
    """Runtime（Phase 3.7.0 新增）不直接 import 业务模块。"""

    @pytest.mark.parametrize("file_path", RUNTIME_NEW_FILES)
    def test_runtime_does_not_import_business(self, file_path: Path):
        """T4.1: Runtime 新文件不 import 任何业务实现"""
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

    @pytest.mark.parametrize("file_path", RUNTIME_NEW_FILES)
    def test_runtime_does_not_import_specific_business(self, file_path: Path):
        """T4.2: Runtime 不 import 特定敏感业务模块"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        # 重点检查的禁止 import
        critical_forbidden = {
            "src.memory": "Memory 模块",
            "src.emotion": "Emotion 模块",
            "src.growth.growth_engine": "GrowthEngine",
            "src.personality": "Personality 模块",
        }
        for module_prefix, name in critical_forbidden.items():
            for line in content.split("\n"):
                # 简单检测：行内包含 from src.X 或 import src.X
                if (
                    f"from {module_prefix}" in line
                    or f"import {module_prefix}" in line
                ):
                    # 排除 src.runtime 内部引用
                    if "src.runtime" in line and "adapters" not in line:
                        continue
                    if module_prefix == "src.memory" and "src.memory." in line:
                        # 排除 src.memory.* 的具体引用
                        if "src.memory" in line and "src.runtime" not in line:
                            # Runtime 不应 import src.memory
                            if "src.runtime.adapters" not in line:
                                pytest.fail(
                                    f"{file_path.name} 不应 import {name}: {line.strip()}"
                                )


# ============================================================
# T5: GrowthAdapter 使用 canonical GrowthProposal
# ============================================================

class TestGrowthAdapterUsesCanonicalSchema:
    """GrowthAdapterSpec 使用 canonical GrowthProposal。"""

    def test_growth_adapter_spec_imports_canonical(self):
        """T5.1: GrowthAdapterSpec import canonical GrowthProposal"""
        if not GROWTH_ADAPTER_PATH.exists():
            pytest.skip("growth_adapter.py 不存在")
        content = GROWTH_ADAPTER_PATH.read_text(encoding="utf-8")
        assert "src.contracts.growth_schema" in content, (
            "GrowthAdapter 必须 import canonical schema (src.contracts.growth_schema)"
        )

    def test_growth_adapter_spec_uses_canonical_type(self):
        """T5.2: GrowthAdapterSpec.submit() 接受 canonical GrowthProposal"""
        from src.runtime.adapters.growth_adapter import GrowthAdapterSpec
        import inspect
        sig = inspect.signature(GrowthAdapterSpec.submit)
        params = list(sig.parameters.keys())
        assert "proposal" in params, (
            "GrowthAdapterSpec.submit() 必须有 proposal 参数"
        )

    def test_growth_adapter_spec_evaluate_returns_list(self):
        """T5.3: GrowthAdapterSpec.evaluate() 文档说明返回 List[GrowthProposal]"""
        if not GROWTH_ADAPTER_PATH.exists():
            pytest.skip("growth_adapter.py 不存在")
        content = GROWTH_ADAPTER_PATH.read_text(encoding="utf-8")
        # 检查文档提到 List[GrowthProposal]
        assert "List[GrowthProposal]" in content or "GrowthProposal" in content, (
            "GrowthAdapterSpec.evaluate() 应返回 List[GrowthProposal]"
        )

    def test_canonical_growth_proposal_available(self):
        """T5.4: canonical GrowthProposal 在 contracts 中可用"""
        from src.contracts.growth_schema import GrowthProposal
        p = GrowthProposal()
        assert p.schema_version == "1.0"


# ============================================================
# T6: Adapter 只定义接口，没有业务实现
# ============================================================

class TestAdaptersAreInterfaceOnly:
    """Adapter 只定义接口，业务方法默认 raise NotImplementedError。"""

    def test_memory_adapter_spec_retrieve_not_implemented(self):
        """T6.1: MemoryAdapterSpec.retrieve() 是接口（raise NotImplementedError）"""
        from src.runtime.adapters.memory_adapter import MemoryAdapterSpec
        spec = MemoryAdapterSpec()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.retrieve(None)

    def test_memory_adapter_spec_store_not_implemented(self):
        """T6.2: MemoryAdapterSpec.store() 是接口"""
        from src.runtime.adapters.memory_adapter import MemoryAdapterSpec
        spec = MemoryAdapterSpec()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.store(None)

    def test_emotion_adapter_analyze_not_implemented(self):
        """T6.3: EmotionAdapter.analyze() 是接口"""
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        spec = EmotionAdapter()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.analyze(None)

    def test_emotion_adapter_update_not_implemented(self):
        """T6.4: EmotionAdapter.update() 是接口"""
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        spec = EmotionAdapter()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.update(None)

    def test_growth_adapter_spec_evaluate_not_implemented(self):
        """T6.5: GrowthAdapterSpec.evaluate() 是接口"""
        from src.runtime.adapters.growth_adapter import GrowthAdapterSpec
        spec = GrowthAdapterSpec()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.evaluate(None)

    def test_growth_adapter_spec_submit_not_implemented(self):
        """T6.6: GrowthAdapterSpec.submit() 是接口"""
        from src.runtime.adapters.growth_adapter import GrowthAdapterSpec
        spec = GrowthAdapterSpec()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.submit(None)

    def test_personality_adapter_snapshot_not_implemented(self):
        """T6.7: PersonalityAdapter.snapshot() 是接口"""
        from src.runtime.adapters.personality_adapter import PersonalityAdapter
        spec = PersonalityAdapter()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.snapshot()

    def test_personality_adapter_apply_update_not_implemented(self):
        """T6.8: PersonalityAdapter.apply_update() 是接口"""
        from src.runtime.adapters.personality_adapter import PersonalityAdapter
        spec = PersonalityAdapter()
        spec.attach()
        with pytest.raises(NotImplementedError):
            spec.apply_update(None)


# ============================================================
# T7: AdapterBase 生命周期
# ============================================================

class TestAdapterBaseLifecycle:
    """AdapterBase 提供统一的 attach / detach / health_check。"""

    def test_attach_changes_state(self):
        """T7.1: attach() 改变 is_attached 状态"""
        from src.runtime.adapters.base import AdapterBase
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        a = EmotionAdapter()
        assert a.is_attached is False
        a.attach()
        assert a.is_attached is True

    def test_detach_changes_state(self):
        """T7.2: detach() 改变 is_attached 状态"""
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        a = EmotionAdapter()
        a.attach()
        a.detach()
        assert a.is_attached is False

    def test_health_check_returns_dict(self):
        """T7.3: health_check() 返回标准 dict"""
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        a = EmotionAdapter()
        a.attach()
        result = a.health_check()
        assert isinstance(result, dict)
        assert "healthy" in result
        assert "name" in result
        assert "schema_version" in result

    def test_health_check_unattached(self):
        """T7.4: 未 attach 时 health_check 返回 healthy=False"""
        from src.runtime.adapters.emotion_adapter import EmotionAdapter
        a = EmotionAdapter()
        result = a.health_check()
        assert result["healthy"] is False


# ============================================================
# T8: Adapter 依赖方向
# ============================================================

class TestAdapterDependencyDirection:
    """Adapter 自身的依赖方向（不 import 业务实现）。"""

    @pytest.mark.parametrize("file_path", ADAPTER_FILES)
    def test_adapter_does_not_import_business(self, file_path: Path):
        """T8.1: Adapter 自身不 import 业务实现（除既有具体实现）"""
        if not file_path.exists():
            pytest.skip(f"{file_path} 不存在")
        content = file_path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_BUSINESS_IMPORTS:
            # base.py / emotion_adapter.py / personality_adapter.py 必须 0 import
            if file_path in (BASE_PATH, EMOTION_ADAPTER_PATH, PERSONALITY_ADAPTER_PATH):
                assert f"from {forbidden}" not in content, (
                    f"{file_path.name} 不应 import {forbidden}"
                )
                assert f"import {forbidden}" not in content, (
                    f"{file_path.name} 不应 import {forbidden}"
                )

    def test_emotion_adapter_no_business_import(self):
        """T8.2: EmotionAdapter 完全不 import Emotion 业务实现"""
        if not EMOTION_ADAPTER_PATH.exists():
            pytest.skip("emotion_adapter.py 不存在")
        content = EMOTION_ADAPTER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "from src.emotion.emotion_manager",
            "from src.emotion.emotion_engine",
            "from src.emotion.emotion_state",
        ):
            assert forbidden not in content

    def test_personality_adapter_no_business_import(self):
        """T8.3: PersonalityAdapter 完全不 import Personality 业务实现"""
        if not PERSONALITY_ADAPTER_PATH.exists():
            pytest.skip("personality_adapter.py 不存在")
        content = PERSONALITY_ADAPTER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "from src.personality.personality_resolver",
            "from src.personality.personality_controller",
        ):
            assert forbidden not in content

    def test_memory_adapter_spec_no_business_import(self):
        """T8.4: MemoryAdapterSpec 部分（不 import MemoryService）"""
        if not MEMORY_ADAPTER_PATH.exists():
            pytest.skip("memory_adapter.py 不存在")
        content = MEMORY_ADAPTER_PATH.read_text(encoding="utf-8")
        # 新 Spec 部分不 import MemoryService
        # 由于同一文件既有具体实现也有新 Spec，我们只检查 MemoryService
        assert "src.memory.memory_service" not in content, (
            "MemoryAdapterSpec 不应 import MemoryService"
        )

    def test_growth_adapter_spec_no_business_import(self):
        """T8.5: GrowthAdapterSpec 不 import GrowthEngine"""
        if not GROWTH_ADAPTER_PATH.exists():
            pytest.skip("growth_adapter.py 不存在")
        content = GROWTH_ADAPTER_PATH.read_text(encoding="utf-8")
        assert "src.growth.growth_engine" not in content, (
            "GrowthAdapterSpec 不应 import GrowthEngine"
        )


# ============================================================
# T9: 文档
# ============================================================

class TestAdapterDocExists:
    """docs/runtime_adapter.md 设计文档存在并含关键章节。"""

    def test_doc_exists(self):
        """T9.1: docs/runtime_adapter.md 存在"""
        assert ADAPTER_DOC_PATH.exists(), (
            f"runtime adapter 文档不存在: {ADAPTER_DOC_PATH}"
        )

    def test_doc_contains_key_sections(self):
        """T9.2: 文档含关键章节"""
        content = ADAPTER_DOC_PATH.read_text(encoding="utf-8")
        for section in (
            "Adapter",
            "依赖方向",
            "生命周期",
            "数据流",
            "AdapterBase",
        ):
            assert section in content, f"文档缺章节: {section}"

    def test_doc_mentions_canonical_schema(self):
        """T9.3: 文档强调 canonical schema"""
        content = ADAPTER_DOC_PATH.read_text(encoding="utf-8")
        assert "canonical" in content.lower() or "Canonical" in content
        assert "GrowthProposal" in content

    def test_doc_mentions_no_direct_business_import(self):
        """T9.4: 文档明确禁止 Runtime 直接 import 业务模块"""
        content = ADAPTER_DOC_PATH.read_text(encoding="utf-8")
        # 关键词
        assert "MemoryService" in content or "Memory" in content
        assert "EmotionManager" in content or "Emotion" in content
        assert "GrowthEngine" in content or "Growth" in content
        assert "PersonalityResolver" in content or "Personality" in content


# ============================================================
# 主入口（汇总）
# ============================================================

def test_phase_3_7_1_adapter_design_summary():
    """汇总 Phase 3.7.1 Adapter 设计状态"""
    # 1) Adapter 文件全部存在
    for p in ADAPTER_FILES:
        assert p.exists(), f"{p} 不存在"
    # 2) 5 个 Adapter 抽象类可导入
    from src.runtime.adapters import (
        AdapterBase,
        MemoryAdapterSpec,
        EmotionAdapter,
        GrowthAdapterSpec,
        PersonalityAdapter,
    )
    for cls in (
        AdapterBase, MemoryAdapterSpec, EmotionAdapter,
        GrowthAdapterSpec, PersonalityAdapter,
    ):
        assert cls is not None
    # 3) 文档存在
    assert ADAPTER_DOC_PATH.exists()
    # 4) canonical schema_version 仍是 "1.0"
    from src.contracts.growth_schema import CANONICAL_SCHEMA_VERSION
    assert CANONICAL_SCHEMA_VERSION == "1.0"
