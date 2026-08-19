"""
Phase 6.3: Legacy SelfModel 同步桥接 (SelfModelSyncAdapter) 测试

验证：
- SelfModelSyncAdapter 提供统一 combined view
- 不修改 legacy 或 runtime 数据
- 不创建第二事实来源
- 失败隔离
- 数据所有权保持原状
"""
from __future__ import annotations

import os
import sys
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.personality.self_model_sync_adapter import (
    SelfModelSyncAdapter,
    SyncView,
    create_sync_adapter,
)
from src.personality.self_model_adapter import SelfModelAdapter
from src.personality.self_belief import SelfBelief
from src.personality.self_history import SelfHistoryEvent, SelfHistoryEventType
from src.personality.self_reflection import SelfReflectionNote
from src.personality.self_model_v3 import SelfModelV3, NarrativeItem


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def legacy_store_with_data():
    """legacy SelfModelStore 风格的数据对象"""
    from src.personality.self_model_v3 import NarrativeType
    v3 = SelfModelV3(
        identity="浅雾羽依",
        traits={"openness": 0.8, "curiosity": 0.7},
        values={"kindness": 0.9},
        beliefs=[],
        narrative_items=[
            NarrativeItem(text="我第一次学会理解", narrative_type=NarrativeType.ORIGIN),
            NarrativeItem(text="我开始关心用户的感受", narrative_type=NarrativeType.GROWTH),
        ],
    )
    class _LegacyStore:
        def get_active_self_model(self):
            return v3
    return _LegacyStore()


@pytest.fixture
def runtime_adapter_with_data():
    """runtime SelfModelAdapter 风格的数据对象"""
    adapter = SelfModelAdapter(actor="sync_test")
    adapter._beliefs.add(SelfBelief(
        domain="identity", content="我是羽依", confidence=0.7
    ))
    adapter._beliefs.add(SelfBelief(
        domain="preference", content="喜欢安静", confidence=0.5
    ))
    adapter._history.append(SelfHistoryEvent(
        event_type=SelfHistoryEventType.PCR_APPLIED,
        source_type="test", source_id="p1", summary="第一次成长",
    ))
    adapter._reflections.append(SelfReflectionNote(
        trigger_source="manual", reflection_type="identity",
        content="我开始理解自己", confidence=0.6,
    ))
    return adapter


@pytest.fixture
def empty_legacy_store():
    class _EmptyLegacy:
        def get_active_self_model(self):
            return None
    return _EmptyLegacy()


@pytest.fixture
def empty_runtime_adapter():
    return SelfModelAdapter(actor="empty_test")


# ============================================================
# 1. 基本结构测试
# ============================================================

class TestSyncAdapterStructure:
    def test_01_create_empty(self):
        """创建空 sync adapter（不传任何 store）"""
        sync = SelfModelSyncAdapter()
        assert sync.has_legacy() is False
        assert sync.has_runtime() is False

    def test_02_factory_function(self):
        """工厂函数"""
        sync = create_sync_adapter()
        assert isinstance(sync, SelfModelSyncAdapter)

    def test_03_attach_legacy(self):
        """运行时 attach legacy"""
        sync = SelfModelSyncAdapter()
        sync.attach_legacy("legacy_obj")
        assert sync.has_legacy() is True

    def test_04_attach_runtime(self):
        """运行时 attach runtime"""
        sync = SelfModelSyncAdapter()
        sync.attach_runtime("runtime_obj")
        assert sync.has_runtime() is True

    def test_05_get_status(self):
        """状态查询"""
        sync = SelfModelSyncAdapter()
        status = sync.get_status()
        assert status["has_legacy"] is False
        assert status["has_runtime"] is False
        assert "view_call_count" in status


# ============================================================
# 2. Combined view 测试
# ============================================================

class TestCombinedView:
    def test_01_combined_view_with_both(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """两个世界都有数据"""
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        view = sync.get_combined_view()
        # identity 来自 legacy
        assert view.identity == "浅雾羽依"
        assert view.source["identity"] == "legacy"
        # traits 来自 legacy
        assert "openness" in view.traits
        assert view.source["traits"] == "legacy"
        # values 来自 legacy
        assert "kindness" in view.values
        # narratives 来自 legacy
        assert len(view.narratives) >= 1
        assert view.source["narratives"] == "legacy"
        # beliefs 来自 runtime
        assert len(view.beliefs) >= 1
        assert view.source["beliefs"] == "runtime"
        # history 来自 runtime
        assert len(view.history) >= 1
        assert view.source["history"] == "runtime"
        # reflections 来自 runtime
        assert len(view.reflections) >= 1
        assert view.source["reflections"] == "runtime"

    def test_02_combined_view_only_legacy(
        self, legacy_store_with_data, empty_runtime_adapter
    ):
        """仅 legacy 数据"""
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=empty_runtime_adapter,
        )
        view = sync.get_combined_view()
        assert view.identity == "浅雾羽依"
        assert view.source["beliefs"] == "none"
        assert view.source["history"] == "none"

    def test_03_combined_view_only_runtime(
        self, empty_legacy_store, runtime_adapter_with_data
    ):
        """仅 runtime 数据"""
        sync = SelfModelSyncAdapter(
            legacy_store=empty_legacy_store,
            runtime_adapter=runtime_adapter_with_data,
        )
        view = sync.get_combined_view()
        assert view.identity == ""
        assert view.source["identity"] == "none"
        assert view.source["beliefs"] == "runtime"
        assert len(view.beliefs) >= 1

    def test_04_combined_view_empty(self):
        """全部为空"""
        sync = SelfModelSyncAdapter()
        view = sync.get_combined_view()
        assert view.is_empty() is True
        for src in view.source.values():
            assert src == "none"


# ============================================================
# 3. 数据所有权测试（不修改原数据）
# ============================================================

class TestDataOwnership:
    def test_01_does_not_modify_legacy(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """sync 不修改 legacy 数据"""
        legacy_v3_before = legacy_store_with_data.get_active_self_model()
        traits_before = dict(legacy_v3_before.traits)
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        sync.get_combined_view()
        # legacy 数据未变
        assert legacy_v3_before.traits == traits_before

    def test_02_does_not_modify_runtime(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """sync 不修改 runtime 数据"""
        beliefs_count_before = runtime_adapter_with_data.get_beliefs().count()
        history_count_before = runtime_adapter_with_data.get_history().count()
        reflections_count_before = runtime_adapter_with_data.get_reflections().count()
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        sync.get_combined_view()
        # runtime 数据未变
        assert runtime_adapter_with_data.get_beliefs().count() == beliefs_count_before
        assert runtime_adapter_with_data.get_history().count() == history_count_before
        assert runtime_adapter_with_data.get_reflections().count() == reflections_count_before

    def test_03_multiple_views_consistent(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """多次调用应输出一致数据（无副作用）"""
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        view1 = sync.get_combined_view()
        view2 = sync.get_combined_view()
        assert view1.identity == view2.identity
        assert len(view1.beliefs) == len(view2.beliefs)


# ============================================================
# 4. 限制与边界
# ============================================================

class TestLimits:
    def test_01_max_beliefs(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """限制 beliefs 数量"""
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        view = sync.get_combined_view(max_beliefs=1)
        assert len(view.beliefs) <= 1

    def test_02_max_history(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """限制 history 数量"""
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        view = sync.get_combined_view(max_history=1)
        assert len(view.history) <= 1

    def test_03_max_narratives(
        self, legacy_store_with_data, runtime_adapter_with_data
    ):
        """限制 narratives 数量"""
        sync = SelfModelSyncAdapter(
            legacy_store=legacy_store_with_data,
            runtime_adapter=runtime_adapter_with_data,
        )
        view = sync.get_combined_view(max_narratives=1)
        assert len(view.narratives) <= 1


# ============================================================
# 5. 失败隔离
# ============================================================

class TestFailureIsolation:
    def test_01_legacy_exception_isolated(self):
        """legacy 抛异常不影响 runtime 读取"""
        class _BrokenLegacy:
            def get_active_self_model(self):
                raise RuntimeError("legacy broken")
        adapter = SelfModelAdapter(actor="iso_test")
        adapter._beliefs.add(SelfBelief(domain="value", content="y", confidence=0.5))
        sync = SelfModelSyncAdapter(
            legacy_store=_BrokenLegacy(),
            runtime_adapter=adapter,
        )
        view = sync.get_combined_view()
        # runtime 数据仍可读
        assert view.source["beliefs"] == "runtime"
        # legacy 字段为 none
        assert view.source["identity"] == "none"

    def test_02_runtime_exception_isolated(self):
        """runtime 抛异常不影响 legacy 读取"""
        class _BrokenAdapter:
            def get_beliefs(self):
                raise RuntimeError("runtime broken")
            def get_history(self):
                raise RuntimeError("runtime broken")
            def get_reflections(self):
                raise RuntimeError("runtime broken")
        v3 = SelfModelV3(identity="test")
        class _Legacy:
            def get_active_self_model(self):
                return v3
        sync = SelfModelSyncAdapter(
            legacy_store=_Legacy(),
            runtime_adapter=_BrokenAdapter(),
        )
        view = sync.get_combined_view()
        # legacy 数据仍可读
        assert view.identity == "test"
        # runtime 字段为 none
        assert view.source["beliefs"] == "none"

    def test_03_summary_handles_failure(self):
        """get_combined_summary 在异常时仍返回结构"""
        sync = SelfModelSyncAdapter()
        summary = sync.get_combined_summary()
        # 应有 has_legacy / has_runtime 字段
        assert "has_legacy" in summary
        assert "has_runtime" in summary


# ============================================================
# 6. SyncView 数据结构
# ============================================================

class TestSyncView:
    def test_01_to_dict(self):
        """SyncView.to_dict 序列化"""
        view = SyncView(
            identity="test",
            traits={"a": 0.1},
            values={"b": 0.2},
            beliefs=[{"id": "1"}],
            history=[],
            reflections=[],
            narratives=["n1"],
            source={"identity": "legacy"},
        )
        d = view.to_dict()
        assert d["identity"] == "test"
        assert d["beliefs"] == [{"id": "1"}]
        assert d["narratives"] == ["n1"]

    def test_02_is_empty(self):
        """空视图判断"""
        view = SyncView(
            identity="", traits={}, values={}, beliefs=[], history=[],
            reflections=[], narratives=[], source={},
        )
        assert view.is_empty() is True

        view2 = SyncView(
            identity="x", traits={}, values={}, beliefs=[], history=[],
            reflections=[], narratives=[], source={},
        )
        assert view2.is_empty() is False
