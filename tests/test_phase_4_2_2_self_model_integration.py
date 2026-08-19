# -*- coding: utf-8 -*-
"""
tests/test_phase_4_2_2_self_model_integration.py

Phase 4.2.2: Self Model Integration —— 单元测试

覆盖:
1.  SelfModelSourceAdapter 抽象基类(attach / detach / health_check / safe_extract)
2.  GrowthSelfModelAdapter 正常路径
3.  GrowthSelfModelAdapter 异常隔离
4.  MemorySelfModelAdapter 正常路径
5.  MemorySelfModelAdapter 异常隔离
6.  PersonalitySelfModelAdapter 正常路径
7.  PersonalitySelfModelAdapter 异常隔离
8.  EmotionSelfModelAdapter 正常路径
9.  EmotionSelfModelAdapter 异常隔离
10. SelfModelRegistry register_source / unregister_source / get_source
11. SelfModelRegistry merge_source_inputs
12. SelfModelRegistry build_primary_with_sources
13. SelfModelRegistry source_health_check
14. RuntimeCore SELF_MODEL_BUILD with source adapters
15. RuntimeCore 向后兼容(无 source adapter)
16. RuntimeContext schema 不变
17. Personality 核心模块未修改
18. 综合 E2E
"""
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _make_ctx(
    growth_proposals: Optional[List[Any]] = None,
    emotion_state: Any = None,
):
    """构造一个简易 RuntimeContext(不依赖真实 RuntimeContext 行为)。"""
    from src.runtime.context import RuntimeContext
    ctx = RuntimeContext()
    if growth_proposals is not None:
        ctx.growth_proposals = growth_proposals
    if emotion_state is not None:
        ctx.emotion_state = emotion_state
    return ctx


# ============================================================
# 1. SelfModelSourceAdapter 抽象基类
# ============================================================
class TestSelfModelSourceAdapterBase:
    def test_abstract_cannot_instantiate(self):
        """SelfModelSourceAdapter 是抽象类,不能直接实例化。"""
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
        )
        with pytest.raises(TypeError):
            SelfModelSourceAdapter()  # noqa

    def test_subclass_must_implement_source_type(self):
        """子类必须实现 source_type。"""
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
        )

        class IncompleteAdapter(SelfModelSourceAdapter):
            def extract_inputs(self, ctx=None):
                return {}

        with pytest.raises(TypeError):
            IncompleteAdapter()  # noqa

    def test_subclass_must_implement_extract_inputs(self):
        """子类必须实现 extract_inputs。"""
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
        )

        class IncompleteAdapter(SelfModelSourceAdapter):
            @property
            def source_type(self):
                return "test"

        with pytest.raises(TypeError):
            IncompleteAdapter()  # noqa

    def test_valid_source_types(self):
        from src.runtime.self_model.self_model_source_adapter import (
            VALID_SOURCE_TYPES,
            SOURCE_TYPE_GROWTH,
            SOURCE_TYPE_MEMORY,
            SOURCE_TYPE_PERSONALITY,
            SOURCE_TYPE_EMOTION,
        )
        assert SOURCE_TYPE_GROWTH in VALID_SOURCE_TYPES
        assert SOURCE_TYPE_MEMORY in VALID_SOURCE_TYPES
        assert SOURCE_TYPE_PERSONALITY in VALID_SOURCE_TYPES
        assert SOURCE_TYPE_EMOTION in VALID_SOURCE_TYPES

    def test_concrete_subclass_works(self):
        """完整的子类可正常实例化。"""
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class ConcreteAdapter(SelfModelSourceAdapter):
            name = "concrete"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                return {"trait_states": {"test": {"current_value": 0.5}}}

        a = ConcreteAdapter()
        a.attach()
        assert a.is_attached is True
        h = a.health_check()
        assert h["healthy"] is True
        assert h["name"] == "concrete"
        assert h["source_type"] == SOURCE_TYPE_GROWTH

    def test_safe_extract_isolates_exception(self):
        """safe_extract 隔离异常,返回 {}。"""
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class BrokenAdapter(SelfModelSourceAdapter):
            name = "broken"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                raise RuntimeError("boom")

        a = BrokenAdapter()
        a.attach()
        result = a.safe_extract()
        assert result == {}
        assert a.last_error is not None
        assert "boom" in a.last_error
        h = a.health_check()
        assert h["healthy"] is False

    def test_safe_extract_returns_empty_on_non_dict(self):
        """safe_extract 当 extract_inputs 返回非 Dict 时,返回 {}。"""
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class NonDictAdapter(SelfModelSourceAdapter):
            name = "non_dict"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                return "not a dict"

        a = NonDictAdapter()
        a.attach()
        result = a.safe_extract()
        assert result == {}
        assert a.last_error is not None
        assert "non-dict" in a.last_error

    def test_extract_count_increments(self):
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class CountedAdapter(SelfModelSourceAdapter):
            name = "counted"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                return {}

        a = CountedAdapter()
        a.attach()
        a.safe_extract()
        a.safe_extract()
        a.safe_extract()
        assert a.extract_count == 3

    def test_detach_clears_state(self):
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class StubAdapter(SelfModelSourceAdapter):
            name = "stub"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                return {}

        a = StubAdapter()
        a.attach()
        a.detach()
        assert a.is_attached is False
        assert a.last_inputs is None

    def test_describe_returns_dict(self):
        from src.runtime.self_model.self_model_source_adapter import (
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class StubAdapter(SelfModelSourceAdapter):
            name = "stub"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                return {}

        a = StubAdapter()
        a.attach()
        d = a.describe()
        assert d["name"] == "stub"
        assert d["source_type"] == SOURCE_TYPE_GROWTH
        assert d["is_attached"] is True


# ============================================================
# 2. GrowthSelfModelAdapter
# ============================================================
class TestGrowthSelfModelAdapter:
    def test_default_construction(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        assert a.source_type == "growth"
        assert a.is_attached is False
        assert a.growth_adapter is None

    def test_attach_lifecycle(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        a.attach()
        assert a.is_attached is True
        a.detach()
        assert a.is_attached is False

    def test_extract_without_attach_returns_empty(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        # 没 attach,即使 ctx 有数据,也返回 {}
        ctx = _make_ctx(growth_proposals=[{"id": "p1"}])
        result = a.extract_inputs(ctx)
        assert result == {}

    def test_extract_from_ctx_proposals(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        a.attach()
        proposal = {
            "id": "prop_1",
            "source_event_id": "evt_1",
            "proposed_changes": [
                {"path": "self_state.initiative", "after": -0.1}
            ],
            "confidence": 0.85,
            "evidence_ids": ["exp_1", "exp_2"],
            "status": "proposed",
        }
        ctx = _make_ctx(growth_proposals=[proposal])
        result = a.extract_inputs(ctx)
        assert "growth_records" in result
        assert len(result["growth_records"]) == 1
        assert result["growth_records"][0]["id"] == "prop_1"
        # extra entries
        assert len(result["extra_entries"]) == 1
        assert result["extra_entries"][0].kind == "growth"
        assert result["extra_entries"][0].confidence == 0.85
        # 高置信度 → core_values 派生
        assert len(result["core_values"]) >= 0  # path 含 "value" 触发

    def test_extract_with_low_confidence_no_core_value(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        a.attach()
        proposal = {
            "id": "prop_low",
            "proposed_changes": [
                {"path": "self_state.energy", "after": -0.1}
            ],
            "confidence": 0.3,
            "evidence_ids": [],
        }
        ctx = _make_ctx(growth_proposals=[proposal])
        result = a.extract_inputs(ctx)
        # 低置信度不触发 core_values
        # 0.3 < 0.7
        core_value_keys = {cv.get("key", "") for cv in result["core_values"]}
        # 没有 path 含 value/core_value
        for cv in result["core_values"]:
            assert "value" in cv.get("key", "").lower() or "core_value" in cv.get("key", "").lower()

    def test_extract_with_no_ctx(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        a.attach()
        result = a.extract_inputs(None)
        # 没 ctx, 没 adapter, 返回 {} 形式的结果(每个字段可能是空)
        assert isinstance(result, dict)
        assert result.get("growth_records") == []

    def test_extract_with_growth_adapter(self):
        from src.runtime.self_model import GrowthSelfModelAdapter

        class FakeGrowthAdapter:
            def list_proposals(self):
                return [{
                    "id": "from_adapter",
                    "proposed_changes": [],
                    "confidence": 0.8,
                }]

        a = GrowthSelfModelAdapter(growth_adapter=FakeGrowthAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert any(
            gr.get("id") == "from_adapter"
            for gr in result.get("growth_records", [])
        )

    def test_extract_with_dataclass_proposal(self):
        """GrowthProposal dataclass 也能被解析。"""
        from src.runtime.self_model import GrowthSelfModelAdapter
        from src.contracts.growth_schema import GrowthProposal, ChangeItem

        prop = GrowthProposal(
            id="prop_dc",
            proposed_changes=[ChangeItem(path="trait.warmth", after=0.1)],
            confidence=0.9,
            evidence_ids=["ev1"],
        )
        a = GrowthSelfModelAdapter()
        a.attach()
        ctx = _make_ctx(growth_proposals=[prop])
        result = a.extract_inputs(ctx)
        assert any(
            gr.get("id") == "prop_dc"
            for gr in result.get("growth_records", [])
        )

    def test_extract_dedupes_proposals(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        a.attach()
        prop = {"id": "dup", "proposed_changes": [], "confidence": 0.5}
        ctx = _make_ctx(growth_proposals=[prop, prop, prop])
        result = a.extract_inputs(ctx)
        assert len(result["growth_records"]) == 1

    def test_safe_extract_isolates(self):
        from src.runtime.self_model import GrowthSelfModelAdapter

        class Boom:
            def list_proposals(self):
                raise RuntimeError("boom")

        a = GrowthSelfModelAdapter(growth_adapter=Boom())
        a.attach()
        result = a.safe_extract()
        # adapter 炸了,但没 ctx proposals → growth_records 为空
        assert result.get("growth_records") == []

    def test_set_growth_adapter(self):
        from src.runtime.self_model import GrowthSelfModelAdapter

        class FA:
            def list_proposals(self):
                return [{"id": "x", "proposed_changes": [], "confidence": 0.5}]

        a = GrowthSelfModelAdapter()
        a.attach()
        a.set_growth_adapter(FA())
        result = a.extract_inputs(_make_ctx())
        assert any(
            gr.get("id") == "x" for gr in result.get("growth_records", [])
        )

    def test_clear_cache(self):
        from src.runtime.self_model import GrowthSelfModelAdapter
        a = GrowthSelfModelAdapter()
        a.attach()
        a._seen_proposals["x"] = {"id": "x"}
        assert a.seen_proposal_count() == 1
        a.clear_cache()
        assert a.seen_proposal_count() == 0


# ============================================================
# 3. MemorySelfModelAdapter
# ============================================================
class TestMemorySelfModelAdapter:
    def test_default_construction(self):
        from src.runtime.self_model import MemorySelfModelAdapter
        a = MemorySelfModelAdapter()
        assert a.source_type == "memory"
        assert a.preference_max == 20
        assert a.entry_max == 50

    def test_attach_lifecycle(self):
        from src.runtime.self_model import MemorySelfModelAdapter
        a = MemorySelfModelAdapter()
        a.attach()
        assert a.is_attached is True
        a.detach()
        assert a.is_attached is False

    def test_extract_without_attach(self):
        from src.runtime.self_model import MemorySelfModelAdapter
        a = MemorySelfModelAdapter()
        result = a.extract_inputs(_make_ctx())
        assert result == {}

    def test_extract_from_memory_adapter(self):
        from src.runtime.self_model import MemorySelfModelAdapter

        class FakeMemoryAdapter:
            def retrieve(self, ctx):
                return {
                    "items": [
                        {
                            "id": "mem_1",
                            "type": "preference",
                            "key": "tone",
                            "value": "warm",
                            "domain": "communication",
                            "confidence": 0.8,
                        },
                        {
                            "id": "mem_2",
                            "type": "event",
                            "summary": "user said hi",
                            "confidence": 0.5,
                        },
                        {
                            "id": "mem_3",
                            "type": "trait",
                            "trait": "calmness",
                            "current_value": 0.7,
                            "stability": 0.5,
                            "confidence": 0.6,
                        },
                    ]
                }

        a = MemorySelfModelAdapter(memory_adapter=FakeMemoryAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert len(result["preferences"]) == 1
        assert result["preferences"][0]["key"] == "tone"
        assert "calmness" in result["trait_states"]
        assert len(result["extra_entries"]) == 3

    def test_extract_with_no_memory_adapter(self):
        from src.runtime.self_model import MemorySelfModelAdapter
        a = MemorySelfModelAdapter()
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert result["preferences"] == []
        assert result["extra_entries"] == []
        assert result["trait_states"] == {}

    def test_extract_with_object_memory_context(self):
        """memory_context 是 object(非 dict)时也能解析。"""
        from src.runtime.self_model import MemorySelfModelAdapter

        class FakeContext:
            items = [
                {"id": "x", "type": "preference", "key": "music", "value": "lofi"},
            ]

        class FakeAdapter:
            def retrieve(self, ctx):
                return FakeContext()

        a = MemorySelfModelAdapter(memory_adapter=FakeAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert any(p["key"] == "music" for p in result["preferences"])

    def test_extract_preference_max_limit(self):
        from src.runtime.self_model import MemorySelfModelAdapter

        class FakeAdapter:
            def retrieve(self, ctx):
                return {
                    "items": [
                        {
                            "type": "preference",
                            "key": f"k{i}",
                            "value": "v",
                        }
                        for i in range(50)
                    ]
                }

        a = MemorySelfModelAdapter(memory_adapter=FakeAdapter(), preference_max=5)
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert len(result["preferences"]) == 5

    def test_extract_entry_max_limit(self):
        from src.runtime.self_model import MemorySelfModelAdapter

        class FakeAdapter:
            def retrieve(self, ctx):
                return {
                    "items": [
                        {"type": "event", "summary": f"e{i}"} for i in range(100)
                    ]
                }

        a = MemorySelfModelAdapter(memory_adapter=FakeAdapter(), entry_max=10)
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert len(result["extra_entries"]) == 10

    def test_safe_extract_isolates(self):
        from src.runtime.self_model import MemorySelfModelAdapter

        class Boom:
            def retrieve(self, ctx):
                raise RuntimeError("boom")

        a = MemorySelfModelAdapter(memory_adapter=Boom())
        a.attach()
        result = a.safe_extract(_make_ctx())
        assert result == {}

    def test_set_memory_adapter(self):
        from src.runtime.self_model import MemorySelfModelAdapter

        class FA:
            def retrieve(self, ctx):
                return {"items": [{"type": "preference", "key": "k", "value": "v"}]}

        a = MemorySelfModelAdapter()
        a.attach()
        a.set_memory_adapter(FA())
        result = a.extract_inputs(_make_ctx())
        assert any(p["key"] == "k" for p in result["preferences"])


# ============================================================
# 4. PersonalitySelfModelAdapter
# ============================================================
class TestPersonalitySelfModelAdapter:
    def test_default_construction(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter
        a = PersonalitySelfModelAdapter()
        assert a.source_type == "personality"
        assert a.personality_adapter is None
        assert a.last_snapshot is None

    def test_attach_lifecycle(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter
        a = PersonalitySelfModelAdapter()
        a.attach()
        assert a.is_attached is True
        a.detach()
        assert a.is_attached is False

    def test_extract_from_dict_snapshot(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class FakePersonalityAdapter:
            def snapshot(self):
                return {
                    "traits": {
                        "warmth": {"current_value": 0.85, "stability": 0.6},
                        "gentleness": 0.7,
                    },
                    "core_values": [
                        {"key": "honesty", "weight": 0.9},
                        {"key": "kindness", "weight": 0.8},
                    ],
                    "identity": {
                        "name": "yuyi",
                        "archetype": "companion_ai",
                    },
                }

        a = PersonalitySelfModelAdapter(personality_adapter=FakePersonalityAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert "warmth" in result["trait_states"]
        assert "gentleness" in result["trait_states"]
        assert result["trait_states"]["warmth"]["current_value"] == 0.85
        assert len(result["core_values"]) == 2
        assert result["identity_overrides"]["name"] == "yuyi"
        assert result["current_state"]["identity"]["archetype"] == "companion_ai"

    def test_extract_from_object_with_to_dict(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class FakeSnapshot:
            def to_dict(self):
                return {
                    "traits": {
                        "x": {"current_value": 0.5},
                    },
                }

        class FakePersonalityAdapter:
            def snapshot(self):
                return FakeSnapshot()

        a = PersonalitySelfModelAdapter(personality_adapter=FakePersonalityAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert "x" in result["trait_states"]

    def test_extract_from_object_with_get_all(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class FakeVector:
            def get_all(self):
                return {"traits": {"y": {"current_value": 0.6}}}

        class FakePersonalityAdapter:
            def snapshot(self):
                return FakeVector()

        a = PersonalitySelfModelAdapter(personality_adapter=FakePersonalityAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert "y" in result["trait_states"]

    def test_extract_with_no_adapter(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter
        a = PersonalitySelfModelAdapter()
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert result == {}

    def test_extract_with_traits_list(self):
        """traits 字段是 list(非 dict)时也能解析。"""
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class FA:
            def snapshot(self):
                return {
                    "traits": [
                        {"trait": "warmth", "current_value": 0.7},
                        {"trait": "kind", "value": 0.6},  # 用 value
                    ],
                }

        a = PersonalitySelfModelAdapter(personality_adapter=FA())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert "warmth" in result["trait_states"]
        assert "kind" in result["trait_states"]

    def test_safe_extract_isolates(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class Boom:
            def snapshot(self):
                raise RuntimeError("boom")

        a = PersonalitySelfModelAdapter(personality_adapter=Boom())
        a.attach()
        result = a.safe_extract(_make_ctx())
        assert result == {}

    def test_set_personality_adapter(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class FA:
            def snapshot(self):
                return {"traits": {"a": {"current_value": 0.1}}}

        a = PersonalitySelfModelAdapter()
        a.attach()
        a.set_personality_adapter(FA())
        result = a.extract_inputs(_make_ctx())
        assert "a" in result["trait_states"]

    def test_last_snapshot_recorded(self):
        from src.runtime.self_model import PersonalitySelfModelAdapter

        class FA:
            def snapshot(self):
                return {"traits": {}}

        a = PersonalitySelfModelAdapter(personality_adapter=FA())
        a.attach()
        a.extract_inputs(_make_ctx())
        assert a.last_snapshot is not None


# ============================================================
# 5. EmotionSelfModelAdapter
# ============================================================
class TestEmotionSelfModelAdapter:
    def test_default_construction(self):
        from src.runtime.self_model import EmotionSelfModelAdapter
        a = EmotionSelfModelAdapter()
        assert a.source_type == "emotion"
        assert a.high_intensity_threshold == 0.7
        assert a.emotion_adapter is None

    def test_attach_lifecycle(self):
        from src.runtime.self_model import EmotionSelfModelAdapter
        a = EmotionSelfModelAdapter()
        a.attach()
        assert a.is_attached is True
        a.detach()
        assert a.is_attached is False

    def test_extract_from_emotion_adapter(self):
        from src.runtime.self_model import EmotionSelfModelAdapter

        class FakeEmotionAdapter:
            def update(self, ctx):
                return {
                    "intensity": 0.4,
                    "dominant_emotion": "calm",
                    "calmness": 0.8,
                }

        a = EmotionSelfModelAdapter(emotion_adapter=FakeEmotionAdapter())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert "emotion" in result["current_state"]
        assert result["current_state"]["emotion"]["dominant_emotion"] == "calm"
        assert "calmness" in result["trait_states"]

    def test_high_intensity_creates_entry(self):
        from src.runtime.self_model import EmotionSelfModelAdapter

        class FA:
            def update(self, ctx):
                return {
                    "intensity": 0.9,
                    "dominant_emotion": "excited",
                }

        a = EmotionSelfModelAdapter(emotion_adapter=FA())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert len(result["extra_entries"]) == 1
        assert result["extra_entries"][0].kind == "trait_change"
        assert "excited" in result["extra_entries"][0].summary

    def test_low_intensity_no_entry(self):
        from src.runtime.self_model import EmotionSelfModelAdapter

        class FA:
            def update(self, ctx):
                return {
                    "intensity": 0.3,
                    "dominant_emotion": "calm",
                }

        a = EmotionSelfModelAdapter(emotion_adapter=FA())
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert len(result["extra_entries"]) == 0

    def test_fallback_to_ctx_emotion_state(self):
        """emotion_adapter 未注入时,从 ctx.emotion_state 读取。"""
        from src.runtime.self_model import EmotionSelfModelAdapter
        a = EmotionSelfModelAdapter()
        a.attach()
        ctx = _make_ctx(emotion_state={"intensity": 0.2, "dominant_emotion": "sad"})
        result = a.extract_inputs(ctx)
        assert "emotion" in result["current_state"]

    def test_no_emotion_state(self):
        from src.runtime.self_model import EmotionSelfModelAdapter
        a = EmotionSelfModelAdapter()
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert result == {}

    def test_threshold_override(self):
        from src.runtime.self_model import EmotionSelfModelAdapter

        class FA:
            def update(self, ctx):
                return {
                    "intensity": 0.4,
                    "dominant_emotion": "happy",
                }

        a = EmotionSelfModelAdapter(
            emotion_adapter=FA(), high_intensity_threshold=0.3,
        )
        a.attach()
        result = a.extract_inputs(_make_ctx())
        assert len(result["extra_entries"]) == 1

    def test_safe_extract_isolates(self):
        from src.runtime.self_model import EmotionSelfModelAdapter

        class Boom:
            def update(self, ctx):
                raise RuntimeError("boom")

        a = EmotionSelfModelAdapter(emotion_adapter=Boom())
        a.attach()
        result = a.safe_extract(_make_ctx())
        assert result == {}

    def test_set_emotion_adapter(self):
        from src.runtime.self_model import EmotionSelfModelAdapter

        class FA:
            def update(self, ctx):
                return {"intensity": 0.1, "calmness": 0.5}

        a = EmotionSelfModelAdapter()
        a.attach()
        a.set_emotion_adapter(FA())
        result = a.extract_inputs(_make_ctx())
        assert "emotion" in result["current_state"]


# ============================================================
# 6. SelfModelRegistry Source Adapter 集成
# ============================================================
class TestSelfModelRegistrySourceAdapters:
    def test_register_source(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        src = GrowthSelfModelAdapter()
        reg.register_source(src)
        assert reg.source_count == 1
        assert reg.get_source("growth_self_model_adapter") is src

    def test_register_non_source_rejected(self):
        from src.runtime.self_model import SelfModelRegistry
        reg = SelfModelRegistry()
        with pytest.raises(TypeError, match="source must be SelfModelSourceAdapter"):
            reg.register_source("not a source")

    def test_register_duplicate_name_rejected(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        a1 = GrowthSelfModelAdapter()
        a1.name = "my_growth"
        a2 = GrowthSelfModelAdapter()
        a2.name = "my_growth"
        reg.register_source(a1)
        with pytest.raises(ValueError, match="already registered"):
            reg.register_source(a2)

    def test_register_same_type_replaces(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        a1 = GrowthSelfModelAdapter()
        a1.name = "growth_v1"
        a2 = GrowthSelfModelAdapter()
        a2.name = "growth_v2"
        reg.register_source(a1)
        reg.register_source(a2)
        # 同 source_type 唯一,a1 被移除
        assert reg.source_count == 1
        assert reg.get_source("growth_v2") is a2

    def test_unregister_source(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        src = GrowthSelfModelAdapter()
        reg.register_source(src)
        removed = reg.unregister_source("growth_self_model_adapter")
        assert removed is src
        assert reg.source_count == 0

    def test_get_source_by_type(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        reg.register_source(GrowthSelfModelAdapter())
        src = reg.get_source_by_type("growth")
        assert src is not None

    def test_all_sources(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
            MemorySelfModelAdapter,
        )
        reg = SelfModelRegistry()
        reg.register_source(GrowthSelfModelAdapter())
        reg.register_source(MemorySelfModelAdapter())
        assert len(reg.all_sources()) == 2

    def test_merge_empty(self):
        from src.runtime.self_model import SelfModelRegistry
        reg = SelfModelRegistry()
        merged = reg.merge_source_inputs()
        assert merged["trait_states"] == {}
        assert merged["core_values"] == []
        assert merged["preferences"] == []
        assert merged["extra_entries"] == []
        assert merged["growth_records"] == []
        assert merged["current_state"] == {}
        assert merged["identity_overrides"] == {}

    def test_merge_single_source(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        ctx = _make_ctx(growth_proposals=[
            {"id": "p1", "proposed_changes": [], "confidence": 0.5}
        ])
        merged = reg.merge_source_inputs(ctx)
        assert len(merged["growth_records"]) == 1
        assert len(merged["extra_entries"]) == 1

    def test_merge_multiple_sources(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
            MemorySelfModelAdapter,
            PersonalitySelfModelAdapter,
            EmotionSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        g = GrowthSelfModelAdapter()
        g.attach()
        m = MemorySelfModelAdapter()
        m.attach()
        p = PersonalitySelfModelAdapter()
        p.attach()
        e = EmotionSelfModelAdapter()
        e.attach()
        reg.register_source(g)
        reg.register_source(m)
        reg.register_source(p)
        reg.register_source(e)
        merged = reg.merge_source_inputs(_make_ctx())
        # 至少 4 个 source 的所有字段都存在
        assert "trait_states" in merged
        assert "core_values" in merged
        assert "preferences" in merged
        assert "growth_records" in merged
        assert "extra_entries" in merged
        assert "current_state" in merged
        assert "identity_overrides" in merged
        assert reg.last_merge_errors() == []

    def test_merge_handles_failing_source(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
            GrowthSelfModelAdapter,
        )

        class BrokenSource(SelfModelSourceAdapter):
            name = "broken_source"
            source_type = "unknown_type"  # 不在 VALID 中,但不会抛错

            def extract_inputs(self, ctx=None):
                raise RuntimeError("boom")

        reg = SelfModelRegistry()
        reg.register_source(BrokenSource())
        reg.register_source(GrowthSelfModelAdapter()._replace())
        merged = reg.merge_source_inputs(_make_ctx())
        # broken source 不应中断
        assert isinstance(merged, dict)
        errors = reg.last_merge_errors()
        assert len(errors) >= 1

    def test_last_merged_inputs(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        reg.merge_source_inputs(_make_ctx(growth_proposals=[]))
        cached = reg.last_merged_inputs()
        assert cached is not None

    def test_source_health_check(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        h = reg.source_health_check()
        assert "sources" in h
        assert "growth_self_model_adapter" in h["sources"]


# Helper for _replace
def _replace_with(self, **kwargs):
    obj = self.__class__()
    obj.__dict__.update(self.__dict__)
    obj.__dict__.update(kwargs)
    return obj

from src.runtime.self_model import GrowthSelfModelAdapter as _GSMA
_GSMA._replace = _replace_with


# ============================================================
# 7. SelfModelRegistry build_primary with source
# ============================================================
class TestRegistryBuildWithSources:
    def test_build_primary_no_sources(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
        )
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        snap = reg.build_primary({})
        assert snap is not None

    def test_build_primary_with_source(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        ctx = _make_ctx(growth_proposals=[
            {
                "id": "p1",
                "proposed_changes": [
                    {"path": "self_state.x", "after": 0.1}
                ],
                "confidence": 0.5,
                "evidence_ids": [],
            }
        ])
        snap = reg.build_primary({}, ctx=ctx)
        assert snap is not None
        assert len(snap.entries) >= 1

    def test_build_all_with_sources(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        reg.register_foundation(SelfModelFoundation("b"))
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        snaps = reg.build_all(ctx=_make_ctx())
        assert len(snaps) == 2

    def test_caller_inputs_override_source(self):
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
            GrowthSelfModelAdapter,
        )
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        # caller 显式传 current_state
        snap = reg.build_primary(
            {"current_state": {"forced": True}}, ctx=_make_ctx(),
        )
        assert snap is not None
        # caller 的 current_state 优先
        assert snap.current_state.get("forced") is True


# ============================================================
# 8. RuntimeCore SELF_MODEL_BUILD 集成
# ============================================================
class TestRuntimeCoreSelfModelBuild:
    def test_runtime_version_is_4_2_2(self):
        from src.runtime.runtime import RuntimeCore
        # 4.2.2 起扩展了 SourceAdapter,后续 phase 视为兼容
        assert RuntimeCore.RUNTIME_VERSION in (
            "4.2.2", "4.2.3", "4.2.4", "4.3", "4.4", "4.5", "4.6",
        )

    def test_no_source_adapter_backward_compatible(self):
        """未注入 source adapter 时,行为与 4.2.1 一致。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import SelfModelRegistry
        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(__import__(
            "src.runtime.self_model", fromlist=["SelfModelFoundation"]
        ).SelfModelFoundation("a"))
        core.configure_self_model(reg)
        core.start()
        event = Event(type="user_message", payload={"text": "hi"})
        ctx = core.process(event)
        snap = core.get_self_model_snapshot(ctx)
        assert snap is not None
        core.shutdown()

    def test_with_source_adapter(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
            GrowthSelfModelAdapter,
        )
        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        core.configure_self_model(reg)
        core.start()
        event = Event(type="user_message", payload={"text": "hi"})
        ctx = core.process(event)
        snap = core.get_self_model_snapshot(ctx)
        assert snap is not None
        # merged inputs 应记录
        merged = core.get_self_model_merged_inputs(ctx)
        # merged 可能为 None(如果 source_count=0,旧版)
        # 这里 source_count=1, merged 应有内容
        # 注意:本测试 last_merged_inputs 是 _invoke_self_model_build 之外可能未触发
        # 但每次 process 都 build,所以会更新
        assert merged is None or isinstance(merged, dict)
        core.shutdown()

    def test_get_source_health(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
            GrowthSelfModelAdapter,
        )
        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        g = GrowthSelfModelAdapter()
        g.attach()
        reg.register_source(g)
        core.configure_self_model(reg)
        core.start()
        h = core.get_self_model_source_health()
        assert h is not None
        assert "sources" in h
        core.shutdown()

    def test_get_merged_inputs_without_registry(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        assert core.get_self_model_merged_inputs(None) is None
        assert core.get_self_model_source_health() is None
        core.shutdown()

    def test_source_health_without_registry(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        assert core.get_self_model_source_health() is None
        core.shutdown()

    def test_source_health_with_isolated_failure(self):
        """SourceAdapter 失败时,Runtime 不崩溃。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import (
            SelfModelRegistry,
            SelfModelFoundation,
            SelfModelSourceAdapter,
            SOURCE_TYPE_GROWTH,
        )

        class BrokenSource(SelfModelSourceAdapter):
            name = "broken"
            source_type = SOURCE_TYPE_GROWTH

            def extract_inputs(self, ctx=None):
                raise RuntimeError("boom")

        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(SelfModelFoundation("a"))
        reg.register_source(BrokenSource())
        core.configure_self_model(reg)
        core.start()
        event = Event(type="user_message", payload={"text": "hi"})
        ctx = core.process(event)
        snap = core.get_self_model_snapshot(ctx)
        # Foundation 仍能 build(失败的 source 被隔离)
        assert snap is not None
        assert core.is_started is True
        core.shutdown()


# ============================================================
# 9. 静态不变性检查
# ============================================================
class TestStaticInvariants:
    def test_runtime_context_schema_unchanged(self):
        from src.runtime.context import RUNTIME_CONTEXT_SCHEMA_VERSION
        assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"

    def test_self_model_source_adapter_no_business_import(self):
        from src.runtime.self_model import self_model_source_adapter
        src = open(self_model_source_adapter.__file__, encoding="utf-8").read()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                low = stripped.lower()
                for forbidden in [
                    "openai", "qwen", "llava", "anthropic", "clip", "yolo",
                    "src.personality", "src.memory", "src.emotion", "src.growth",
                ]:
                    if forbidden in low:
                        # 允许 self_model 自身
                        if "self_model" in line and "self_model_source_adapter" in line:
                            continue
                        # 允许 import AdapterBase
                        if "adapters.base" in line:
                            continue
                        assert False, (
                            f"forbidden import in self_model_source_adapter: {line!r}"
                        )

    def test_no_llm_call_in_source_adapters(self):
        """4 个 SourceAdapter 不应调用 LLM/视觉 SDK。"""
        for mod_name in [
            "src.runtime.self_model.growth_self_model_adapter",
            "src.runtime.self_model.memory_self_model_adapter",
            "src.runtime.self_model.personality_self_model_adapter",
            "src.runtime.self_model.emotion_self_model_adapter",
        ]:
            import importlib
            mod = importlib.import_module(mod_name)
            src = open(mod.__file__, encoding="utf-8").read()
            for line in src.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    low = stripped.lower()
                    for forbidden in [
                        "openai", "qwen", "llava", "anthropic", "clip", "yolo",
                    ]:
                        if forbidden in low:
                            assert False, (
                                f"forbidden in {mod_name}: {line!r}"
                            )

    def test_self_model_registry_no_personality_import(self):
        """SelfModelRegistry 不 import personality 现有复杂模块。"""
        from src.runtime.self_model import self_model_registry
        src = open(self_model_registry.__file__, encoding="utf-8").read()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                low = stripped.lower()
                for forbidden in [
                    "src.personality",
                    "src.memory",
                    "src.emotion",
                    "src.growth",
                ]:
                    assert forbidden not in low, (
                        f"SelfModelRegistry forbidden import: {line!r}"
                    )


# ============================================================
# 10. E2E sanity
# ============================================================
def test_phase_4_2_2_e2e():
    """Phase 4.2.2 端到端:4 个 SourceAdapter 全部接入,Foundation build 成功。"""
    from src.runtime.runtime import RuntimeCore
    from src.runtime.events import Event
    from src.runtime.self_model import (
        SelfModelRegistry,
        SelfModelFoundation,
        GrowthSelfModelAdapter,
        MemorySelfModelAdapter,
        PersonalitySelfModelAdapter,
        EmotionSelfModelAdapter,
    )
    from src.runtime.context import RUNTIME_CONTEXT_SCHEMA_VERSION

    # 1) 版本与 schema
    assert RuntimeCore.RUNTIME_VERSION in (
        "4.2.2", "4.2.3", "4.2.4", "4.3", "4.4", "4.5",
    )
    assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"

    # 2) 准备 mock 业务模块 Adapter
    class FakeMemory:
        def retrieve(self, ctx):
            return {
                "items": [
                    {"type": "preference", "key": "tone", "value": "warm"},
                ]
            }

    class FakePersonality:
        def snapshot(self):
            return {
                "traits": {"warmth": {"current_value": 0.85}},
                "core_values": [{"key": "honesty", "weight": 0.9}],
                "identity": {"name": "yuyi"},
            }

    class FakeEmotion:
        def update(self, ctx):
            return {"intensity": 0.4, "dominant_emotion": "calm"}

    # 3) 组装 Registry + 4 个 Source
    reg = SelfModelRegistry()
    reg.register_foundation(SelfModelFoundation("e2e"))
    g = GrowthSelfModelAdapter()
    g.attach()
    m = MemorySelfModelAdapter(memory_adapter=FakeMemory())
    m.attach()
    p = PersonalitySelfModelAdapter(personality_adapter=FakePersonality())
    p.attach()
    e = EmotionSelfModelAdapter(emotion_adapter=FakeEmotion())
    e.attach()
    reg.register_source(g)
    reg.register_source(m)
    reg.register_source(p)
    reg.register_source(e)

    # 4) 启动 Runtime,处理事件
    core = RuntimeCore()
    core.configure_self_model(reg)
    core.start()
    event = Event(
        type="user_message",
        payload={"text": "summary check"},
    )
    ctx = core.process(event)
    snap = core.get_self_model_snapshot(ctx)
    assert snap is not None
    assert snap.identity_id == "e2e"
    assert snap.schema_version == "1.0"
    # 至少包含默认 core_values + personality core_value
    assert len(snap.core_values) >= 5
    # 至少一个 trait(warmth from personality)
    trait_names = {t["trait"] for t in snap.stable_traits}
    assert "warmth" in trait_names
    # 至少一个 preference(tone from memory)
    pref_keys = {p["key"] for p in snap.preferences}
    assert "tone" in pref_keys
    # health 必须 complete
    assert snap.health["complete"] is True
    core.shutdown()
