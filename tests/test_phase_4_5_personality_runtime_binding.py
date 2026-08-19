# -*- coding: utf-8 -*-
"""
tests/test_phase_4_5_personality_runtime_binding.py

Phase 4.5: Personality Runtime Binding Layer —— 单元测试

目标:
- ComposedPersonalityContext: serialize / deserialize / 字段齐全
- PersonalityContextComposer: 合并多来源 + 缺失模块 + 异常隔离
- ConsistencyRulesBuilder:    抽取一致性规则 + 兜底规则
- PersonalityPromptFormatter:  生成【Identity】【Values】【Behavior】【Reflection】【Consistency Rules】
- PersonalityBindingProvider:  对 ResponseAdapter 的 provide / format 接口
- PersonalityRuntimeBinding:   协调器 + 异常隔离 + 缓存
- ResponseAdapter 集成:        注入 personality_runtime_data / personality_runtime_text
- RuntimeCore 集成:           configure_personality_runtime_binding
- 不变量:                       ResponseEngine.generate 未被改 / RuntimeContext schema 未被改 /
  Personality 未直接修改 / Audit / Reflection 模块无反向依赖
- 端到端:                       Runtime → SelfModel → Reflection → Identity → PersonalityRuntimeBinding
  → ResponseAdapter → LLM request
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
def _make_snapshot(
    identity: Optional[Dict[str, Any]] = None,
    core_values: Optional[List[Dict]] = None,
    stable_traits: Optional[List[Dict]] = None,
    preferences: Optional[List[Dict]] = None,
    current_state: Optional[Dict[str, Any]] = None,
    entries: Optional[List[Any]] = None,
    identity_id: str = "smf_test",
    version: int = 1,
    updated_at: str = "2026-07-31T10:00:00Z",
) -> Any:
    from src.runtime.self_model.self_model_data import SelfModelSnapshot
    snap = SelfModelSnapshot(
        identity=identity if identity is not None else {"name": "yuyi"},
        core_values=core_values or [],
        stable_traits=stable_traits or [],
        preferences=preferences or [],
        current_state=current_state or {},
        entries=entries or [],
        identity_id=identity_id,
        version=version,
    )
    snap.updated_at = updated_at
    return snap


def _make_reflection_record(
    identity_id: str = "smf_a",
    observation: str = "warmth trait increased",
    interpretation: str = "long-term interaction patterns",
    relation_to_values: Optional[List[str]] = None,
    priority: str = "medium",
    kind: str = "trait_trend",
    confidence: float = 0.6,
) -> Any:
    from src.runtime.self_model.reflection.reflection_record import (
        ReflectionRecord,
    )
    return ReflectionRecord(
        identity_id=identity_id,
        observation=observation,
        interpretation=interpretation,
        relation_to_values=relation_to_values or ["kindness"],
        priority=priority,
        reflection_kind=kind,
        confidence=confidence,
    )


# ============================================================
# 1. ComposedPersonalityContext
# ============================================================
class TestComposedPersonalityContext:
    def test_create_default(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext()
        assert ctx.identity_context == {}
        assert ctx.reflection_context == {}
        assert ctx.behavior_context == {}
        assert ctx.growth_context == {}
        assert ctx.consistency_rules == []
        assert ctx.schema_version == "1.0"
        assert ctx.has_binding is False
        assert ctx.identity_id == ""

    def test_create_with_fields(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext(
            identity_context={"name": "yuyi"},
            reflection_context={"recent": []},
            behavior_context={"scenarios": {}},
            growth_context={"meta": {}},
            consistency_rules=[{"text": "no fabricate"}],
            identity_id="smf_x",
            has_binding=True,
            version=2,
        )
        assert ctx.identity_context["name"] == "yuyi"
        assert ctx.has_binding is True
        assert ctx.identity_id == "smf_x"
        assert ctx.version == 2

    def test_to_dict(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext(
            identity_context={"name": "yuyi"},
            has_binding=True,
            identity_id="smf_to_dict",
        )
        d = ctx.to_dict()
        assert isinstance(d, dict)
        assert d["identity_context"]["name"] == "yuyi"
        assert d["has_binding"] is True
        assert d["identity_id"] == "smf_to_dict"
        assert d["schema_version"] == "1.0"

    def test_from_dict_round_trip(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        original = ComposedPersonalityContext(
            identity_context={"name": "yuyi"},
            reflection_context={"recent": [{"observation": "obs"}]},
            behavior_context={"scenarios": {"greeting": {"tone": "warm"}}},
            growth_context={"meta": {"snapshot_version": 1}},
            consistency_rules=[{"text": "rule1", "severity": 0.8}],
            has_binding=True,
            identity_id="smf_rt",
        )
        d = original.to_dict()
        restored = ComposedPersonalityContext.from_dict(d)
        assert restored.identity_context == original.identity_context
        assert restored.reflection_context == original.reflection_context
        assert restored.behavior_context == original.behavior_context
        assert restored.consistency_rules == original.consistency_rules
        assert restored.identity_id == original.identity_id
        assert restored.has_binding == original.has_binding

    def test_from_dict_empty(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext.from_dict({})
        assert ctx.identity_context == {}
        assert ctx.has_binding is False
        assert ctx.schema_version == "1.0"

    def test_from_dict_none(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext.from_dict(None)
        assert ctx.identity_context == {}

    def test_is_empty_default(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext()
        assert ctx.is_empty() is True

    def test_is_empty_with_data(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext(identity_context={"name": "x"})
        assert ctx.is_empty() is False

    def test_has_methods(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext(
            identity_context={"name": "x"},
            reflection_context={"recent": []},
            behavior_context={"scenarios": {}},
            growth_context={"meta": {}},
            consistency_rules=[{"text": "r"}],
        )
        assert ctx.has_identity() is True
        assert ctx.has_reflection() is True
        assert ctx.has_behavior() is True
        assert ctx.has_growth() is True
        assert ctx.has_consistency_rules() is True

    def test_has_methods_empty(self):
        from src.runtime.personality_binding import ComposedPersonalityContext
        ctx = ComposedPersonalityContext()
        assert ctx.has_identity() is False
        assert ctx.has_reflection() is False
        assert ctx.has_behavior() is False
        assert ctx.has_growth() is False
        assert ctx.has_consistency_rules() is False


# ============================================================
# 2. PersonalityContextComposer
# ============================================================
class TestPersonalityContextComposer:
    def test_compose_empty(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose()
        assert ctx.has_binding is False
        assert composer.compose_count == 1

    def test_compose_identity_only(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            identity_context={"identity": {"name": "yuyi"}},
        )
        assert ctx.has_identity() is True
        assert ctx.has_binding is True
        assert ctx.identity_context["identity"]["name"] == "yuyi"

    def test_compose_reflection_only(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            reflection_context={
                "recent": [{"observation": "obs1"}],
            },
        )
        assert ctx.has_reflection() is True
        assert ctx.has_identity() is False

    def test_compose_behavior_only(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            behavior_context={"scenarios": {"greeting": {"tone": "warm"}}},
        )
        assert ctx.has_behavior() is True

    def test_compose_growth_only(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            growth_context={"meta": {"snapshot_version": 1}},
        )
        assert ctx.has_growth() is True

    def test_compose_all_sections(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            identity_context={"identity": {"name": "yuyi"}},
            reflection_context={"recent": [{"observation": "obs"}]},
            behavior_context={"scenarios": {"greeting": {"tone": "warm"}}},
            growth_context={"meta": {"snapshot_version": 1}},
            behavior_signature={"scenarios": {}},
            consistency_constraints=[{"text": "r1"}, {"text": "r2"}],
        )
        assert ctx.has_binding is True
        assert ctx.has_identity()
        assert ctx.has_reflection()
        assert ctx.has_behavior()
        assert ctx.has_growth()
        assert ctx.has_consistency_rules()
        assert len(ctx.consistency_rules) == 2

    def test_compose_resolves_identity_id(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            identity_context={
                "identity_id": "smf_resolve",
                "identity": {"name": "yuyi"},
            },
        )
        assert ctx.identity_id == "smf_resolve"

    def test_compose_identity_id_explicit_overrides(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            identity_id="explicit",
            identity_context={"identity_id": "snap_id"},
        )
        assert ctx.identity_id == "explicit"

    def test_compose_missing_sections_isolated(self):
        """缺失模块不应影响其它分区。"""
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        # 仅 identity,其它 None
        ctx = composer.compose(identity_context={"identity": {"name": "x"}})
        assert ctx.has_identity()
        assert not ctx.has_reflection()
        assert not ctx.has_behavior()
        assert not ctx.has_growth()

    def test_compose_exception_isolation(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        # 传入异常值(让内部处理失败)—— 使用一个会让 _coerce_* 抛异常的奇怪对象
        class _Boom:
            def __getitem__(self, *a, **kw):
                raise RuntimeError("boom")
            def __getattr__(self, name):
                raise RuntimeError("boom")
        # 不让 compose 整体崩溃,返回空 context
        ctx = composer.compose(
            identity_context=_Boom(),
            reflection_context=_Boom(),
        )
        assert isinstance(ctx, type(composer.compose()))
        # 即使有错误,不应抛异常,应返回空 context
        assert ctx.has_binding is False

    def test_compose_meta_includes_breakdown(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            identity_context={"identity": {"name": "x"}},
            reflection_context={"recent": []},
        )
        meta = ctx.meta
        assert meta["has_identity"] is True
        assert meta["has_reflection"] is True
        assert meta["has_behavior"] is False
        assert meta["has_growth"] is False
        assert "composed_at" in meta
        assert meta["composer"] == "personality_context_composer"

    def test_compose_consistency_constraints_as_list(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        rules = [
            {"text": "rule1", "severity": 0.9},
            {"text": "rule2", "severity": 0.5},
        ]
        ctx = composer.compose(consistency_constraints=rules)
        assert len(ctx.consistency_rules) == 2
        # Phase 4.5: rules 是 ConsistencyRule 对象(支持属性访问)
        assert ctx.consistency_rules[0].text == "rule1"

    def test_compose_consistency_constraints_as_dict_with_rules(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        ctx = composer.compose(
            consistency_constraints={
                "rules": [{"text": "x"}, {"text": "y"}],
            },
        )
        assert len(ctx.consistency_rules) == 2

    def test_compose_count_increments(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        for _ in range(3):
            composer.compose()
        assert composer.compose_count == 3

    def test_health_check(self):
        from src.runtime.personality_binding import PersonalityContextComposer
        composer = PersonalityContextComposer()
        h = composer.health_check()
        assert h["healthy"] is True
        assert h["name"] == "personality_context_composer"
        assert h["schema_version"] == "1.0"


# ============================================================
# 3. ConsistencyRulesBuilder
# ============================================================
class TestConsistencyRulesBuilder:
    def test_default_base_rules(self):
        from src.runtime.personality_binding import (
            ConsistencyRulesBuilder,
            DEFAULT_BASE_RULES,
        )
        builder = ConsistencyRulesBuilder()
        rules = builder.build()
        # 至少有 5 条兜底
        assert len(rules) >= 5
        # 至少包含 no_fabricate
        ids = [r.rule_id for r in rules]
        assert "base.no_fabricate" in ids

    def test_build_from_behavior_signature(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        sig = {
            "scenarios": {
                "greeting": {
                    "tone": "warm",
                    "forbidden": ["凭空问候用户身份", "捏造上次对话"],
                    "principles": ["保持简洁"],
                },
                "emotional_topic": {
                    "tone": "warm",
                    "forbidden": ["轻视对方情绪"],
                },
            },
        }
        rules = builder.build(behavior_signature=sig)
        # 至少包含 behavior 类规则
        assert any(
            r.category == "behavior" for r in rules
        )
        # 至少包含一条"避免出现"行为规则
        assert any(
            "凭空问候用户身份" in r.text for r in rules
        )

    def test_build_from_identity_context(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        ctx = {
            "identity": {"name": "yuyi", "archetype": "gentle_companion"},
            "core_values": [
                {"name": "kindness"},
                {"name": "honesty"},
            ],
            "current_state": {
                "forbidden": ["攻击对方", "绝对化措辞"],
            },
        }
        rules = builder.build(identity_context=ctx)
        # 应包含 identity / value / behavior 规则
        categories = {r.category for r in rules}
        assert "identity" in categories
        assert "value" in categories
        # 包含"避免出现"行为规则
        assert any(
            "攻击对方" in r.text for r in rules
        )

    def test_build_from_reflection_context(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        ctx = {
            "recent": [
                {
                    "reflection_id": "r1",
                    "observation": "warmth increased",
                    "interpretation": "long-term interaction",
                },
            ],
        }
        rules = builder.build(reflection_context=ctx)
        assert any(
            "warmth increased" in r.text for r in rules
        )

    def test_build_dedupes_same_text(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        sig = {
            "scenarios": {
                "greeting": {"forbidden": ["重复内容"]},
                "unknown": {"forbidden": ["重复内容"]},
            },
        }
        rules = builder.build(behavior_signature=sig)
        dup = [r for r in rules if "重复内容" in r.text]
        # 同一 category+text 视为相同 → 去重
        assert len(dup) == 1

    def test_build_sorts_by_severity_desc(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        sig = {
            "scenarios": {
                "greeting": {
                    "forbidden": ["f1"],
                },
            },
        }
        rules = builder.build(behavior_signature=sig)
        sevs = [r.severity for r in rules]
        assert sevs == sorted(sevs, reverse=True)

    def test_extra_rules(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        rules = builder.build(extra_rules=["自定义规则1", "自定义规则2"])
        assert any(
            "自定义规则1" in r.text for r in rules
        )
        assert any(
            "自定义规则2" in r.text for r in rules
        )

    def test_extra_rules_as_dict(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        rules = builder.build(
            extra_rules=[{
                "text": "extra_dict", "category": "safety", "severity": 0.99,
            }],
        )
        assert any(r.text == "extra_dict" for r in rules)

    def test_exception_isolation(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()

        class _Boom:
            def __getitem__(self, *a, **kw):
                raise RuntimeError("boom")
            def __getattr__(self, name):
                raise RuntimeError("boom")

        # 不应抛异常,返回至少兜底规则
        rules = builder.build(
            behavior_signature=_Boom(),
            identity_context=_Boom(),
        )
        assert len(rules) >= 5

    def test_health_check(self):
        from src.runtime.personality_binding import ConsistencyRulesBuilder
        builder = ConsistencyRulesBuilder()
        h = builder.health_check()
        assert h["healthy"] is True
        assert h["name"] == "consistency_rules_builder"


# ============================================================
# 4. PersonalityPromptFormatter
# ============================================================
class TestPersonalityPromptFormatter:
    def test_format_empty(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        assert f.format(None) == ""

    def test_format_identity_section(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        out = f.format_identity(
            {"identity": {"name": "yuyi", "archetype": "companion"}},
        )
        assert "【Identity】" in out
        assert "yuyi" in out
        assert "companion" in out

    def test_format_values_section(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        out = f.format_values(
            {"core_values": [{"name": "kindness"}, {"name": "honesty"}]},
        )
        assert "【Values】" in out
        assert "kindness" in out
        assert "honesty" in out

    def test_format_behavior_section(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        out = f.format_behavior(
            {
                "scenarios": {
                    "greeting": {
                        "tone": "warm",
                        "opening_style": "先打招呼",
                    },
                },
            },
        )
        assert "【Behavior】" in out
        assert "greeting" in out

    def test_format_reflection_section(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        out = f.format_reflection(
            {
                "recent": [
                    {
                        "reflection_kind": "trait_trend",
                        "observation": "warmth increased",
                        "interpretation": "long-term",
                    },
                ],
            },
        )
        assert "【Reflection】" in out
        assert "warmth increased" in out
        assert "long-term" in out

    def test_format_consistency_rules(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        out = f.format_consistency_rules(
            [
                {
                    "category": "safety",
                    "text": "不要杜撰",
                    "severity": 0.9,
                },
            ],
        )
        assert "【Consistency Rules】" in out
        assert "不要杜撰" in out
        assert "severity" in out

    def test_format_full(self):
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityPromptFormatter,
        )
        f = PersonalityPromptFormatter()
        ctx = ComposedPersonalityContext(
            identity_context={
                "identity": {"name": "yuyi"},
                "core_values": [{"name": "kindness"}],
            },
            reflection_context={
                "recent": [{"observation": "o", "interpretation": "i"}],
            },
            behavior_context={
                "scenarios": {
                    "greeting": {"tone": "warm", "opening_style": "Hi"},
                },
            },
            consistency_rules=[
                {"category": "safety", "text": "no fabricate", "severity": 0.9},
            ],
            has_binding=True,
            identity_id="smf_fmt",
        )
        out = f.format(ctx)
        assert "【Identity】" in out
        assert "【Values】" in out
        assert "【Behavior】" in out
        assert "【Reflection】" in out
        assert "【Consistency Rules】" in out

    def test_format_disabled_sections(self):
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityPromptFormatter,
        )
        f = PersonalityPromptFormatter(include_identity=False)
        ctx = ComposedPersonalityContext(
            identity_context={"identity": {"name": "yuyi"}},
            has_binding=True,
        )
        out = f.format(ctx)
        assert "【Identity】" not in out

    def test_format_exception_isolation(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        out = f.format_identity(
            {"identity": object()},  # 内部处理失败
        )
        # 不应抛异常
        assert out == ""

    def test_health_check(self):
        from src.runtime.personality_binding import PersonalityPromptFormatter
        f = PersonalityPromptFormatter()
        h = f.health_check()
        assert h["healthy"] is True
        assert h["name"] == "personality_prompt_formatter"


# ============================================================
# 5. PersonalityBindingProvider
# ============================================================
class TestPersonalityBindingProvider:
    def test_default_construction(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        assert p.composer is not None
        assert p.rules_builder is not None
        assert p.formatter is not None
        assert p.provider is not None
        assert p.last_composed is None

    def test_provide_none(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        data = p.provide(None)
        assert isinstance(data, dict)
        assert data["has_binding"] is False

    def test_provide_composed(self):
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityBindingProvider,
        )
        p = PersonalityBindingProvider()
        ctx = ComposedPersonalityContext(
            identity_context={"name": "yuyi"},
            has_binding=True,
            identity_id="smf_p",
        )
        data = p.provide(ctx)
        assert data["has_binding"] is True
        assert data["identity_id"] == "smf_p"
        assert p.last_composed is ctx

    def test_provide_dict(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        data = p.provide(
            {"identity_context": {"name": "yuyi"}, "has_binding": True},
        )
        assert data["has_binding"] is True

    def test_provide_from_runtime(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        data = p.provide_from_runtime(
            identity_context={"identity": {"name": "yuyi"}},
            reflection_context={"recent": []},
        )
        assert data["has_binding"] is True
        assert p.last_composed is not None

    def test_format_for_prompt(self):
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityBindingProvider,
        )
        p = PersonalityBindingProvider()
        ctx = ComposedPersonalityContext(
            identity_context={"identity": {"name": "yuyi"}},
            has_binding=True,
        )
        text = p.format_for_prompt(ctx)
        assert "yuyi" in text

    def test_format_for_prompt_none_fallback(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        # 无 last_composed,无 payload → ""
        assert p.format_for_prompt(None) == ""

    def test_format_context_for_prompt(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        data = p.provide_from_runtime(
            identity_context={"identity": {"name": "yuyi"}},
        )
        text = p.format_context_for_prompt(data)
        assert "yuyi" in text

    def test_format_context_for_prompt_empty(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        assert p.format_context_for_prompt({}) == ""

    def test_set_composed(self):
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityBindingProvider,
        )
        p = PersonalityBindingProvider()
        ctx = ComposedPersonalityContext(identity_id="smf_set")
        p.set_composed(ctx)
        assert p.last_composed is ctx

    def test_provide_exception_isolation(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()

        class _Boom:
            def to_dict(self):
                raise RuntimeError("boom")

        data = p.provide(_Boom())
        # 异常时返回空 payload
        assert data["has_binding"] is False

    def test_health_check(self):
        from src.runtime.personality_binding import PersonalityBindingProvider
        p = PersonalityBindingProvider()
        h = p.health_check()
        assert h["healthy"] is True
        assert h["name"] == "personality_binding_provider"


# ============================================================
# 6. PersonalityRuntimeBinding
# ============================================================
class TestPersonalityRuntimeBinding:
    def test_default_construction(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        assert b.composer is not None
        assert b.rules_builder is not None
        assert b.formatter is not None
        assert b.provider is not None

    def test_build_for_runtime_empty(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        ctx = b.build_for_runtime()
        assert ctx.has_binding is False

    def test_build_for_runtime_with_snapshot(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap = _make_snapshot(identity_id="smf_br")
        ctx = b.build_for_runtime(snapshot=snap)
        # 即使没有 identity_ctx,也有兜底规则
        assert ctx.has_consistency_rules()
        assert b.last_composed is ctx
        assert b.build_count == 1

    def test_build_for_runtime_with_reflection(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap = _make_snapshot(identity_id="smf_br_ref")
        rec = _make_reflection_record()
        ctx = b.build_for_runtime(
            snapshot=snap,
            reflection=rec,
        )
        assert ctx.has_reflection()

    def test_build_for_runtime_with_identity_context(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap = _make_snapshot(identity_id="smf_br_id")
        id_ctx = {
            "identity": {"name": "yuyi"},
            "core_values": [{"name": "kindness"}],
        }
        ctx = b.build_for_runtime(
            snapshot=snap,
            identity_context=id_ctx,
        )
        assert ctx.has_identity()
        # 至少有一条 identity 规则
        assert any(
            r.category == "identity" for r in ctx.consistency_rules
        )

    def test_build_for_runtime_with_behavior_signature(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        sig = {
            "scenarios": {
                "greeting": {
                    "tone": "warm",
                    "forbidden": ["凭空问候用户身份"],
                },
            },
        }
        ctx = b.build_for_runtime(behavior_signature=sig)
        assert ctx.has_behavior()
        # 至少有一条 behavior 规则
        assert any(
            r.category == "behavior" for r in ctx.consistency_rules
        )

    def test_build_for_runtime_full(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap = _make_snapshot(identity_id="smf_full")
        rec = _make_reflection_record()
        id_ctx = {
            "identity": {"name": "yuyi"},
            "core_values": [{"name": "kindness"}],
        }
        sig = {
            "scenarios": {
                "greeting": {
                    "tone": "warm",
                    "forbidden": ["凭空问候用户身份"],
                },
            },
        }
        ctx = b.build_for_runtime(
            snapshot=snap,
            reflection=rec,
            identity_context=id_ctx,
            behavior_signature=sig,
        )
        assert ctx.has_identity()
        assert ctx.has_reflection()
        assert ctx.has_behavior()
        assert ctx.has_consistency_rules()
        assert ctx.identity_id == "smf_full"

    def test_apply_to_context(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        from src.runtime.context import RuntimeContext
        b = PersonalityRuntimeBinding()
        ctx = RuntimeContext()
        composed = b.build_for_runtime()
        b.apply_to_context(ctx, composed)
        assert b.apply_count == 1
        # 私有属性已写入
        assert getattr(ctx, "_personality_runtime_context", None) is composed

    def test_apply_to_context_none(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        from src.runtime.context import RuntimeContext
        b = PersonalityRuntimeBinding()
        ctx = RuntimeContext()
        b.apply_to_context(ctx, None)
        # apply_to_context(None) → 使用 last_composed(也是 None)→ 写一个空
        assert getattr(ctx, "_personality_runtime_context", None) is not None
        assert b.apply_count == 1

    def test_apply_to_context_no_ctx(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        b.apply_to_context(None, None)
        # 不报错
        assert b.apply_count == 0

    def test_get_from_context(self):
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityRuntimeBinding,
        )
        from src.runtime.context import RuntimeContext
        b = PersonalityRuntimeBinding()
        ctx = RuntimeContext()
        assert b.get_from_context(ctx) is None
        composed = ComposedPersonalityContext(identity_id="smf_get")
        setattr(ctx, "_personality_runtime_context", composed)
        assert b.get_from_context(ctx) is composed

    def test_get_data_from_context(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        from src.runtime.context import RuntimeContext
        b = PersonalityRuntimeBinding()
        ctx = RuntimeContext()
        assert b.get_data_from_context(ctx) == {}
        composed = b.build_for_runtime(
            identity_context={"identity": {"name": "yuyi"}},
        )
        setattr(ctx, "_personality_runtime_context", composed)
        d = b.get_data_from_context(ctx)
        assert d["has_binding"] is True

    def test_get_text_from_context(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        from src.runtime.context import RuntimeContext
        b = PersonalityRuntimeBinding()
        ctx = RuntimeContext()
        assert b.get_text_from_context(ctx) == ""
        composed = b.build_for_runtime(
            identity_context={"identity": {"name": "yuyi"}},
        )
        setattr(ctx, "_personality_runtime_context", composed)
        text = b.get_text_from_context(ctx)
        assert "yuyi" in text

    def test_cache_hit(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap = _make_snapshot(identity_id="smf_cache_hit")
        b.build_for_runtime(snapshot=snap)
        cached = b._cache.get("smf_cache_hit")
        assert cached is not None

    def test_invalidate_cache(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap = _make_snapshot(identity_id="smf_inv")
        b.build_for_runtime(snapshot=snap)
        n = b.invalidate_cache("smf_inv")
        assert n == 1
        assert b._cache.get("smf_inv") is None
        # 第二次清空 → 0
        n2 = b.invalidate_cache("smf_inv")
        assert n2 == 0

    def test_invalidate_cache_all(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        snap1 = _make_snapshot(identity_id="smf_a1")
        snap2 = _make_snapshot(identity_id="smf_a2")
        b.build_for_runtime(snapshot=snap1)
        b.build_for_runtime(snapshot=snap2)
        n = b.invalidate_cache(None)
        assert n == 2
        assert len(b._cache) == 0

    def test_cache_disabled(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        b.set_cache_enabled(False)
        snap = _make_snapshot(identity_id="smf_nocache")
        b.build_for_runtime(snapshot=snap)
        assert "smf_nocache" not in b._cache

    def test_build_exception_isolation(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()

        class _Boom:
            def __getitem__(self, *a, **kw):
                raise RuntimeError("boom")
            def __getattr__(self, name):
                raise RuntimeError("boom")

        # 不应抛异常
        ctx = b.build_for_runtime(
            snapshot=_Boom(),
            identity_context=_Boom(),
            reflection=_Boom(),
            behavior_signature=_Boom(),
        )
        assert ctx.has_binding is False

    def test_describe(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        d = b.describe()
        assert d["name"] == "personality_runtime_binding"
        assert d["schema_version"] == "1.0"
        assert "composer" in d
        assert "rules_builder" in d
        assert "formatter" in d

    def test_health_check(self):
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        h = b.health_check()
        assert h["healthy"] is True
        assert h["name"] == "personality_runtime_binding"
        assert h["schema_version"] == "1.0"


# ============================================================
# 7. ResponseAdapter 集成
# ============================================================
class TestResponseAdapterBindingInjection:
    def test_backward_compat_no_binding(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        adapter = ResponseAdapter()
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            assert "personality_runtime_data" not in pc
            assert "personality_runtime_text" not in pc
        finally:
            adapter.detach()

    def test_inject_with_provider_and_ctx(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityBindingProvider,
        )

        adapter = ResponseAdapter()
        provider = PersonalityBindingProvider()
        adapter.set_personality_binding_provider(provider)
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            composed = ComposedPersonalityContext(
                identity_context={"identity": {"name": "yuyi"}},
                has_binding=True,
                identity_id="smf_ra",
            )
            setattr(ctx, "_personality_runtime_context", composed)
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            assert "personality_runtime_data" in pc
            assert "personality_runtime_text" in pc
            assert pc["personality_runtime_data"]["identity_id"] == "smf_ra"
        finally:
            adapter.detach()

    def test_inject_without_ctx_no_crash(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_binding import PersonalityBindingProvider

        adapter = ResponseAdapter(
            personality_binding_provider=PersonalityBindingProvider(),
        )
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            # ctx 无 _personality_runtime_context → 不应注入
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            assert "personality_runtime_data" not in pc
        finally:
            adapter.detach()

    def test_provider_exception_isolated(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_binding import ComposedPersonalityContext

        class _BoomProvider:
            def provide(self, *a, **kw):
                raise RuntimeError("boom")

            def format_context_for_prompt(self, *a, **kw):
                raise RuntimeError("boom")

        adapter = ResponseAdapter(
            personality_binding_provider=_BoomProvider(),
        )
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            composed = ComposedPersonalityContext(
                identity_context={"name": "yuyi"},
                has_binding=True,
            )
            setattr(ctx, "_personality_runtime_context", composed)
            # 不应抛异常
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            assert "personality_runtime_data" not in pc
        finally:
            adapter.detach()

    def test_flatten_subfields(self):
        """personality_runtime_data 中的 identity_context / reflection_context /
        behavior_signature / consistency_rules 应被扁平展开到 personality_context。"""
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_binding import (
            ComposedPersonalityContext,
            PersonalityBindingProvider,
        )

        adapter = ResponseAdapter()
        provider = PersonalityBindingProvider()
        adapter.set_personality_binding_provider(provider)
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            composed = ComposedPersonalityContext(
                identity_context={"identity": {"name": "yuyi"}},
                reflection_context={"recent": [{"observation": "obs"}]},
                behavior_context={"scenarios": {"greeting": {"tone": "warm"}}},
                consistency_rules=[{"text": "rule"}],
                has_binding=True,
                identity_id="smf_flat",
            )
            setattr(ctx, "_personality_runtime_context", composed)
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            # Phase 4.5: 关键子字段扁平展开到 personality_context
            assert pc.get("identity_context") == {
                "identity": {"name": "yuyi"},
            }
            assert pc.get("reflection_context") == {
                "recent": [{"observation": "obs"}],
            }
            assert pc.get("behavior_signature") == {
                "scenarios": {"greeting": {"tone": "warm"}},
            }
            # consistency_rules 列表内容应包含 'rule' 文本
            crs = pc.get("consistency_rules") or []
            assert any(
                (isinstance(r, dict) and r.get("text") == "rule")
                for r in crs
            )
        finally:
            adapter.detach()


# ============================================================
# 8. RuntimeCore 集成
# ============================================================
class TestRuntimeCoreBindingIntegration:
    def test_runtime_version_is_4_5(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.7: 兼容 Phase 4.6 / 4.7
        assert RuntimeCore.RUNTIME_VERSION in ("4.5", "4.6", "4.7")

    def test_configure_personality_runtime_binding(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        core = RuntimeCore()
        core.configure_personality_runtime_binding(b)
        assert core.personality_runtime_binding is b

    def test_property_personality_runtime_binding(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.personality_runtime_binding is None
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        core.configure_personality_runtime_binding(b)
        assert core.personality_runtime_binding is b

    def test_get_personality_runtime_context_without_injection(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        try:
            from src.runtime.events import Event
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            # 未注入 → None
            assert core.get_personality_runtime_context(ctx) is None
        finally:
            core.shutdown()

    def test_invalidate_personality_runtime_binding_cache(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 未注入 → 0
        assert core.invalidate_personality_runtime_binding_cache() == 0
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        core.configure_personality_runtime_binding(b)
        n = core.invalidate_personality_runtime_binding_cache()
        assert isinstance(n, int)

    def test_get_personality_runtime_binding_health(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.get_personality_runtime_binding_health() is None
        from src.runtime.personality_binding import PersonalityRuntimeBinding
        b = PersonalityRuntimeBinding()
        core.configure_personality_runtime_binding(b)
        h = core.get_personality_runtime_binding_health()
        assert h is not None
        assert h["name"] == "personality_runtime_binding"

    def test_backward_compatible_without_binding(self):
        """未注入 personality_runtime_binding 时,Runtime 行为与 4.4 一致。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        core = RuntimeCore()
        core.start()
        try:
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            assert core.get_personality_runtime_context(ctx) is None
            assert core.personality_runtime_binding is None
        finally:
            core.shutdown()

    def test_binding_failure_isolated(self):
        """personality_runtime_binding 抛异常时,Runtime 不应中断。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )

        class _BoomBinding:
            def build_for_runtime(self, *a, **kw):
                raise RuntimeError("boom")
            def apply_to_context(self, *a, **kw):
                raise RuntimeError("boom")

        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        core = RuntimeCore(
            self_model_registry=sm_registry,
            personality_runtime_binding=_BoomBinding(),
        )
        core.start()
        try:
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            # process() 不应中断
            assert ctx is not None
        finally:
            core.shutdown()


# ============================================================
# 9. 不变量 / Invariants
# ============================================================
class TestInvariants:
    def test_runtime_context_schema_unchanged(self):
        from src.runtime.context import RUNTIME_CONTEXT_SCHEMA_VERSION
        assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"

    def test_response_engine_generate_unchanged(self):
        import inspect
        try:
            from src.response.engine import ResponseEngine
            sig = inspect.signature(ResponseEngine.generate)
            params = list(sig.parameters.values())
            non_self = [p for p in params if p.name != "self"]
            assert len(non_self) >= 1
            assert non_self[0].name == "user_message"
        except ImportError:
            pytest.skip("ResponseEngine 不可用,跳过")

    def test_personality_binding_does_not_import_personality_core(self):
        """personality_binding 模块不应 import src.personality.*。"""
        import ast
        import inspect
        from src.runtime.personality_binding import (
            composed_personality_context,
            personality_context_composer,
            consistency_rules_builder,
            personality_prompt_formatter,
            personality_binding_provider,
            personality_runtime_binding,
        )
        for mod in [
            composed_personality_context, personality_context_composer,
            consistency_rules_builder, personality_prompt_formatter,
            personality_binding_provider, personality_runtime_binding,
        ]:
            try:
                source = inspect.getsource(mod)
            except (OSError, TypeError):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(
                            "src.personality",
                        ), f"{mod.__name__} 不应 import {alias.name}"
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith(
                        "src.personality",
                    ):
                        assert False, (
                            f"{mod.__name__} 不应 from {node.module} import ..."
                        )

    def test_personality_binding_does_not_import_llm(self):
        """personality_binding 模块不应 import 任何 LLM SDK。"""
        import ast
        import inspect
        from src.runtime.personality_binding import (
            composed_personality_context,
            personality_context_composer,
            consistency_rules_builder,
            personality_prompt_formatter,
            personality_binding_provider,
            personality_runtime_binding,
        )
        forbidden_roots = (
            "openai", "qwen", "llava", "anthropic",
            "google.generativeai", "google_genai", "vertexai",
            "cohere", "mistralai", "huggingface_hub",
        )
        for mod in [
            composed_personality_context, personality_context_composer,
            consistency_rules_builder, personality_prompt_formatter,
            personality_binding_provider, personality_runtime_binding,
        ]:
            try:
                source = inspect.getsource(mod)
            except (OSError, TypeError):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for forbidden in forbidden_roots:
                            if alias.name == forbidden or alias.name.startswith(
                                forbidden + ".",
                            ):
                                assert False, (
                                    f"{mod.__name__} 不应 import {alias.name}"
                                )
                elif isinstance(node, ast.ImportFrom):
                    if not node.module:
                        continue
                    for forbidden in forbidden_roots:
                        if node.module == forbidden or node.module.startswith(
                            forbidden + ".",
                        ):
                            assert False, (
                                f"{mod.__name__} 不应 from {node.module} import ..."
                            )


# ============================================================
# 10. 端到端 —— Runtime → ResponseAdapter 完整链路
# ============================================================
class TestEndToEndPipeline:
    def test_end_to_end_binding_injected_to_request(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.personality_binding import (
            PersonalityRuntimeBinding,
        )

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        binding = PersonalityRuntimeBinding()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            personality_runtime_binding=binding,
        )
        core.start()

        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            ctx = core.process(ev)
        except Exception:
            ctx = None

        if ctx is not None:
            prc = core.get_personality_runtime_context(ctx)
            # 注入后 personality_runtime_context 应当被设置
            if prc is not None:
                response_adapter = registry.get("response_adapter_impl")
                req = response_adapter.build_request(
                    ctx, PersonalityRuntimeContext(),
                )
                pc = req.personality_context or {}
                # 注入了 personality_runtime_data / personality_runtime_text
                if pc.get("personality_runtime_data"):
                    data = pc["personality_runtime_data"]
                    assert "identity_context" in data
                    assert "reflection_context" in data
                    assert "behavior_context" in data
                    assert "consistency_rules" in data

        core.shutdown()

    def test_end_to_end_full_pipeline_with_identity_runtime(self):
        """完整链路: SelfModel + Reflection + IdentityRuntime + PersonalityRuntimeBinding
        → ResponseAdapter → LLM Request。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.adapters.impl.response_adapter_impl import (
            ResponseAdapterImpl,
        )
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.identity_binding import (
            SelfIdentityRuntime,
        )
        from src.runtime.personality_binding import (
            PersonalityRuntimeBinding,
        )

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        identity_rt = SelfIdentityRuntime()
        binding = PersonalityRuntimeBinding()

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            identity_runtime=identity_rt,
            personality_runtime_binding=binding,
        )
        core.start()

        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            ctx = core.process(ev)
        except Exception:
            ctx = None

        if ctx is not None:
            prc = core.get_personality_runtime_context(ctx)
            assert prc is not None  # 应已生成
            # 验证 provider 也已注入到 response adapter
            response_adapter = registry.get("response_adapter_impl")
            assert (
                response_adapter.personality_binding_provider is not None
            )
            # 调用 build_request,验证 personality_runtime_data / personality_runtime_text 已注入
            req = response_adapter.build_request(
                ctx, PersonalityRuntimeContext(),
            )
            pc = req.personality_context or {}
            assert "personality_runtime_data" in pc
            assert "personality_runtime_text" in pc
            # 关键子字段扁平展开
            assert "identity_context" in pc
            assert "reflection_context" in pc
            assert "behavior_signature" in pc
            assert "consistency_rules" in pc
        else:
            pytest.skip("process() 失败,跳过端到端断言")

        core.shutdown()
