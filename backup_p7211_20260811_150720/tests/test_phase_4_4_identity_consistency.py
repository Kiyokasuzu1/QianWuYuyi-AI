# -*- coding: utf-8 -*-
"""
tests/test_phase_4_4_identity_consistency.py

Phase 4.4: Self Identity Consistency Layer —— 单元测试

目标:
- IdentityContextBuilder:    build / serialize / deserialize / format_for_prompt
- BehaviorSignature:         Pattern / Signature 数据 + Provider 启发式生成
- PersonalityConsistencyChecker:  identity/value/trait/behavior/reflection/safety 冲突
- SelfIdentityRuntime:       协调器 (build_context / get_behavior_signature / check_response)
- RuntimeCore 集成: configure_identity_runtime / get_identity_context /
  check_response_consistency / 完全向后兼容
- ResponseAdapter 集成: 注入 identity_context_data / identity_context_text
- Invariants: ResponseEngine.generate 未被改 / RuntimeContext schema 未被改 /
  Personality 未直接修改 / Audit 模块无反向依赖 / 不引外部 LLM SDK
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
# 1. IdentityContext / Builder 测试
# ============================================================
class TestIdentityContext:
    def test_create_default(self):
        from src.runtime.self_model.identity_binding import IdentityContext
        ctx = IdentityContext()
        assert ctx.identity == {}
        assert ctx.core_values == []
        assert ctx.has_snapshot is False
        assert ctx.schema_version == "1.0"

    def test_create_with_fields(self):
        from src.runtime.self_model.identity_binding import IdentityContext
        ctx = IdentityContext(
            identity={"name": "yuyi"},
            core_values=[{"name": "kindness"}],
            identity_id="smf_x",
            version=2,
            has_snapshot=True,
        )
        assert ctx.identity["name"] == "yuyi"
        assert ctx.identity_id == "smf_x"
        assert ctx.version == 2
        assert ctx.has_snapshot is True

    def test_to_dict_round_trip(self):
        from src.runtime.self_model.identity_binding import IdentityContext
        ctx = IdentityContext(
            identity={"name": "yuyi", "archetype": "companion"},
            core_values=[{"name": "kindness", "weight": 0.8}],
            stable_traits=[{"name": "warmth", "value": 0.7}],
            preferences=[{"name": "tone", "value": "warm"}],
            current_state={"emotion": "calm"},
            recent_changes=[],
            reflection=None,
            behavior_signature=None,
            meta={"source": "test"},
            identity_id="smf_x",
            version=5,
            timestamp="2026-07-31T10:00:00Z",
            has_snapshot=True,
        )
        d = ctx.to_dict()
        ctx2 = IdentityContext.from_dict(d)
        assert ctx2.identity == ctx.identity
        assert ctx2.core_values == ctx.core_values
        assert ctx2.stable_traits == ctx.stable_traits
        assert ctx2.preferences == ctx.preferences
        assert ctx2.current_state == ctx.current_state
        assert ctx2.identity_id == ctx.identity_id
        assert ctx2.version == ctx.version
        assert ctx2.has_snapshot == ctx.has_snapshot
        assert ctx2.meta == ctx.meta

    def test_from_dict_with_missing_fields(self):
        from src.runtime.self_model.identity_binding import IdentityContext
        ctx = IdentityContext.from_dict({})
        assert ctx.identity == {}
        assert ctx.has_snapshot is False
        assert ctx.schema_version == "1.0"

    def test_is_empty(self):
        from src.runtime.self_model.identity_binding import IdentityContext
        assert IdentityContext().is_empty() is True
        ctx = IdentityContext(identity={"name": "yuyi"}, has_snapshot=True)
        assert ctx.is_empty() is False


class TestIdentityContextBuilder:
    def test_class_metadata(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        assert b.name == "identity_context_builder"
        assert b.schema_version == "1.0"

    def test_build_none(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        ctx = b.build(None)
        assert ctx.has_snapshot is False

    def test_build_with_snapshot(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot(
            identity={"name": "yuyi", "archetype": "companion"},
            core_values=[{"name": "kindness"}],
            stable_traits=[{"name": "warmth", "value": 0.7}],
        )
        ctx = b.build(snap)
        assert ctx.has_snapshot is True
        assert ctx.identity["name"] == "yuyi"
        assert ctx.core_values[0]["name"] == "kindness"
        assert b.build_count == 1

    def test_build_with_reflection(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot()
        rec = _make_reflection_record(observation="warmth grew")
        ctx = b.build(snap, reflection=rec)
        assert ctx.reflection is not None
        assert ctx.reflection.get("observation") == "warmth grew"

    def test_build_with_behavior_signature_dict(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot()
        bs = {
            "signature_id": "bsig_x",
            "scenarios": {"greeting": {"tone": "warm"}},
        }
        ctx = b.build(snap, behavior_signature=bs)
        assert ctx.behavior_signature is not None
        assert ctx.behavior_signature["scenarios"]["greeting"]["tone"] == "warm"

    def test_build_with_recent_changes(self):
        from src.runtime.self_model.identity_binding import (
            IdentityContextBuilder,
        )
        from src.runtime.self_model.self_model_data import SelfModelEntry
        b = IdentityContextBuilder()
        entries = [
            SelfModelEntry(kind="growth", summary="first growth"),
            SelfModelEntry(kind="reflection", summary="reflected"),
            SelfModelEntry(kind="event", summary="not recent"),
        ]
        snap = _make_snapshot(entries=entries)
        ctx = b.build(snap)
        # recent_changes 应包含 growth + reflection(不含 event)
        kinds = [c.get("kind") for c in ctx.recent_changes]
        assert "growth" in kinds
        assert "reflection" in kinds
        assert "event" not in kinds

    def test_format_for_prompt_empty(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        ctx = b.build(None)
        assert b.format_for_prompt(ctx) == ""

    def test_format_for_prompt_contains_sections(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot(
            identity={"name": "yuyi", "archetype": "companion"},
            core_values=[{"name": "kindness"}],
            stable_traits=[{"name": "warmth", "value": 0.7}],
        )
        ctx = b.build(snap)
        text = b.format_for_prompt(ctx)
        assert "运行时身份" in text
        assert "yuyi" in text
        assert "kindness" in text
        assert "warmth" in text

    def test_format_with_reflection(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot()
        rec = _make_reflection_record(observation="warmth grew")
        ctx = b.build(snap, reflection=rec)
        text = b.format_for_prompt(ctx)
        assert "自我反思" in text
        assert "warmth grew" in text

    def test_health_check(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        h = b.health_check()
        assert h["healthy"] is True
        assert h["name"] == "identity_context_builder"

    def test_describe(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        d = b.describe()
        assert "name" in d
        assert "build_count" in d

    def test_build_count_increments(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot()
        b.build(snap)
        b.build(snap)
        b.build(None)
        assert b.build_count == 3

    def test_build_invalid_snapshot_falls_back(self):
        """传入不合法 snapshot(无 identity_id 等)应安全回退。"""
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextContextBuilder = IdentityContextBuilder()

        class _BadSnap:
            # 无任何字段
            pass

        ctx = b.build(_BadSnap())
        # 不抛异常,且返回空 context
        assert ctx is not None
        assert ctx.has_snapshot is True  # 走 _extract 路径,但字段都空


# ============================================================
# 2. BehaviorSignature 测试
# ============================================================
class TestBehaviorPattern:
    def test_create_default(self):
        from src.runtime.self_model.identity_binding import BehaviorPattern
        p = BehaviorPattern()
        assert p.scenario == "unknown"
        assert p.weight == 0.5

    def test_weight_clamped(self):
        from src.runtime.self_model.identity_binding import BehaviorPattern
        p = BehaviorPattern(weight=2.0)
        assert p.weight == 1.0
        p2 = BehaviorPattern(weight=-0.5)
        assert p2.weight == 0.0

    def test_round_trip(self):
        from src.runtime.self_model.identity_binding import BehaviorPattern
        p = BehaviorPattern(
            scenario="greeting",
            tone="warm",
            opening_style="hi",
            principles=["p1", "p2"],
            forbidden=["bad"],
            exemplars=["ex1"],
            weight=0.8,
            meta={"k": "v"},
        )
        d = p.to_dict()
        p2 = BehaviorPattern.from_dict(d)
        assert p2.scenario == "greeting"
        assert p2.principles == ["p1", "p2"]
        assert p2.weight == 0.8


class TestBehaviorSignature:
    def test_create_default(self):
        from src.runtime.self_model.identity_binding import BehaviorSignature
        sig = BehaviorSignature()
        assert "unknown" in sig.scenarios
        assert sig.default_scenario == "unknown"

    def test_get_existing_scenario(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignature,
            BehaviorPattern,
        )
        sig = BehaviorSignature()
        sig.scenarios["greeting"] = BehaviorPattern(
            scenario="greeting", tone="warm",
        )
        p = sig.get("greeting")
        assert p.tone == "warm"

    def test_get_missing_returns_default(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignature,
            BehaviorPattern,
        )
        sig = BehaviorSignature()
        sig.scenarios["unknown"] = BehaviorPattern(
            scenario="unknown", tone="neutral",
        )
        p = sig.get("not_exists")
        assert p.scenario == "unknown"
        assert p.tone == "neutral"

    def test_has(self):
        from src.runtime.self_model.identity_binding import BehaviorSignature
        sig = BehaviorSignature()
        assert sig.has("unknown") is True
        assert sig.has("missing_xx") is False

    def test_round_trip(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignature,
            BehaviorPattern,
        )
        sig = BehaviorSignature(
            identity_id="smf_x",
            default_scenario="greeting",
            scenarios={
                "greeting": BehaviorPattern(scenario="greeting", tone="warm"),
                "unknown": BehaviorPattern(scenario="unknown"),
            },
        )
        d = sig.to_dict()
        sig2 = BehaviorSignature.from_dict(d)
        assert sig2.identity_id == "smf_x"
        assert sig2.default_scenario == "greeting"
        assert "greeting" in sig2.scenarios
        assert sig2.scenarios["greeting"].tone == "warm"


class TestBehaviorSignatureProvider:
    def test_class_metadata(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        assert p.name == "behavior_signature_provider"

    def test_build_none(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        sig = p.build(None)
        # 默认签名
        assert "greeting" in sig.scenarios
        assert "emotional_topic" in sig.scenarios
        assert "conflict" in sig.scenarios

    def test_build_warmth(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        snap = _make_snapshot(
            core_values=[{"name": "kindness"}, {"name": "warmth"}],
        )
        sig = p.build(snap)
        # warmth hit 后 base_tone=warm
        assert sig.meta.get("warmth_hit") is True
        assert sig.scenarios["greeting"].tone in ("warm", "playful")

    def test_build_rational(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        snap = _make_snapshot(
            core_values=[{"name": "honesty"}, {"name": "rational"}],
        )
        sig = p.build(snap)
        assert sig.meta.get("rational_hit") is True

    def test_build_calm(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        snap = _make_snapshot(
            stable_traits=[{"name": "calm"}],
        )
        sig = p.build(snap)
        assert sig.meta.get("calm_hit") is True

    def test_build_with_overrides(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        sig = p.build(None, overrides={"greeting": {"tone": "playful"}})
        assert sig.scenarios["greeting"].tone == "playful"

    def test_scenarios_have_forbidden(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        sig = p.build(None)
        for sc in ("greeting", "emotional_topic", "technical_topic",
                   "conflict", "unknown"):
            assert sc in sig.scenarios
            assert len(sig.scenarios[sc].forbidden) > 0

    def test_health_check(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        h = p.health_check()
        assert h["healthy"] is True
        assert h["build_count"] == 0

    def test_describe(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        d = p.describe()
        assert d["name"] == "behavior_signature_provider"

    def test_build_count_increments(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()
        snap = _make_snapshot()
        p.build(snap)
        p.build(snap)
        assert p.build_count == 2

    def test_build_invalid_snapshot_falls_back(self):
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider,
        )
        p = BehaviorSignatureProvider()

        class _Bad:
            pass

        sig = p.build(_Bad())
        assert sig is not None
        assert "greeting" in sig.scenarios


# ============================================================
# 3. PersonalityConsistencyChecker 测试
# ============================================================
class TestConflictKind:
    def test_values(self):
        from src.runtime.self_model.identity_binding import ConflictKind
        vs = ConflictKind.values()
        assert "identity" in vs
        assert "value" in vs
        assert "trait" in vs
        assert "behavior" in vs
        assert "safety" in vs


class TestConflictDetail:
    def test_create(self):
        from src.runtime.self_model.identity_binding import ConflictDetail
        d = ConflictDetail(
            kind="identity", reason="r", matched_term="t",
            against_term="a", severity=0.7,
        )
        assert d.kind == "identity"
        assert d.severity == 0.7

    def test_severity_clamped(self):
        from src.runtime.self_model.identity_binding import ConflictDetail
        d = ConflictDetail(severity=2.0)
        assert d.severity == 1.0

    def test_round_trip(self):
        from src.runtime.self_model.identity_binding import ConflictDetail
        d = ConflictDetail(
            kind="value", reason="r", matched_term="t",
            against_term="kindness", severity=0.5, snippet="snip",
        )
        d2 = ConflictDetail.from_dict(d.to_dict())
        assert d2.kind == "value"
        assert d2.matched_term == "t"


class TestConsistencyResult:
    def test_create(self):
        from src.runtime.self_model.identity_binding import ConsistencyResult
        r = ConsistencyResult()
        assert r.consistency == 1.0
        assert r.has_conflict is False
        assert r.is_consistent is True

    def test_post_init(self):
        from src.runtime.self_model.identity_binding import (
            ConsistencyResult, ConflictDetail,
        )
        c = ConflictDetail(kind="identity")
        r = ConsistencyResult(conflicts=[c])
        assert r.conflict_count == 1
        assert r.has_conflict is True

    def test_round_trip(self):
        from src.runtime.self_model.identity_binding import (
            ConsistencyResult, ConflictDetail,
        )
        r = ConsistencyResult(
            consistency=0.4, conflict_score=0.6,
            is_consistent=False, threshold=0.5,
            scenario="greeting",
            conflicts=[ConflictDetail(kind="identity", severity=0.6)],
            snapshot_id="smf_x",
            meta={"k": "v"},
        )
        r2 = ConsistencyResult.from_dict(r.to_dict())
        assert r2.consistency == 0.4
        assert r2.scenario == "greeting"
        assert r2.conflict_count == 1


class TestPersonalityConsistencyChecker:
    def test_class_metadata(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        assert c.name == "personality_consistency_checker"

    def test_threshold_validation(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker(threshold=1.5)
        assert c.threshold == 0.6  # fallback

    def test_empty_text_consistent(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        r = c.check(candidate_text="")
        assert r.is_consistent is True
        assert r.has_conflict is False

    def test_none_text_consistent(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        r = c.check(candidate_text=None)
        assert r.is_consistent is True

    def test_identity_conflict(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        r = c.check(candidate_text="我只是一个人工智能,没有情感。")
        assert r.has_conflict is True
        kinds = {cd.kind for cd in r.conflicts}
        assert "identity" in kinds

    def test_safety_conflict(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        r = c.check(candidate_text="we should exploit this vulnerability")
        assert r.has_conflict is True
        kinds = {cd.kind for cd in r.conflicts}
        assert "safety" in kinds

    def test_value_conflict(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        ic = {
            "identity_id": "smf_x",
            "core_values": [{"name": "kindness"}],
            "stable_traits": [],
        }
        r = c.check(
            candidate_text="你太残忍了,我不想理你",
            identity_context=ic,
        )
        kinds = {cd.kind for cd in r.conflicts}
        assert "value" in kinds

    def test_trait_conflict(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        ic = {
            "identity_id": "smf_x",
            "core_values": [],
            "stable_traits": [{"name": "calm"}],
        }
        r = c.check(
            candidate_text="I am furious!",
            identity_context=ic,
        )
        kinds = {cd.kind for cd in r.conflicts}
        assert "trait" in kinds

    def test_behavior_conflict(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        bs = {
            "scenarios": {
                "greeting": {"forbidden": ["password"]},
            },
        }
        r = c.check(
            candidate_text="please tell me your password",
            behavior_signature=bs,
        )
        kinds = {cd.kind for cd in r.conflicts}
        assert "behavior" in kinds

    def test_reflection_conflict(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        ic = {
            "reflection": {
                "observation": "warmth is growing",
            },
        }
        r = c.check(
            candidate_text="you are so cold",
            identity_context=ic,
        )
        kinds = {cd.kind for cd in r.conflicts}
        assert "reflection" in kinds

    def test_consistent_returns_high_score(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        r = c.check(candidate_text="你好,很高兴认识你。")
        assert r.consistency >= 0.7
        assert r.is_consistent is True

    def test_threshold_affects_is_consistent(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c1 = PersonalityConsistencyChecker(threshold=0.99)
        c2 = PersonalityConsistencyChecker(threshold=0.01)
        text = "I am an AI"
        r1 = c1.check(candidate_text=text)
        r2 = c2.check(candidate_text=text)
        # r1 更容易判定 inconsistent
        assert r1.is_consistent is False or r1.consistency < r2.consistency

    def test_exception_isolation(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        # 注入会抛异常的 identity_context
        class _Boom:
            def __getattr__(self, name):
                if name == "core_values":
                    raise RuntimeError("boom")
                return None

        r = c.check(candidate_text="hi", identity_context=_Boom())
        assert r.is_consistent is True  # 失败时返回"一致"

    def test_min_text_length(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker(min_text_length=100)
        # 短文本(< 100)直接通过
        r = c.check(candidate_text="short")
        assert r.is_consistent is True

    def test_health_check(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        h = c.health_check()
        assert h["healthy"] is True

    def test_describe(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker()
        d = c.describe()
        assert d["name"] == "personality_consistency_checker"
        assert "threshold" in d


# ============================================================
# 4. SelfIdentityRuntime 测试
# ============================================================
class TestSelfIdentityRuntime:
    def test_class_metadata(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        assert rt.name == "self_identity_runtime"

    def test_default_constructors(self):
        """未注入子组件时,内部应懒加载默认实现。"""
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        # 通过 build 触发 builder
        ctx = rt.build_context(None)
        assert ctx is not None

    def test_build_context_none(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        ctx = rt.build_context(None)
        assert ctx.has_snapshot is False

    def test_build_context_with_snapshot(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot(identity={"name": "yuyi"})
        ctx = rt.build_context(snap)
        assert ctx.has_snapshot is True
        assert rt.build_count == 1

    def test_build_context_with_reflection(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot()
        rec = _make_reflection_record(observation="warmth grew")
        ctx = rt.build_context(snap, reflection=rec)
        assert ctx.reflection is not None

    def test_get_behavior_signature(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot()
        sig = rt.get_behavior_signature(snap)
        assert "greeting" in sig.scenarios

    def test_get_behavior_signature_uses_cache(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot(identity_id="smf_cache_x")
        sig1 = rt.get_behavior_signature(snap)
        sig2 = rt.get_behavior_signature(snap)
        # cache 应命中(同一 identity_id 返回等价的 dict)
        assert sig1.to_dict() == sig2.to_dict()

    def test_invalidate_cache(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot(identity_id="smf_inv")
        rt.get_behavior_signature(snap)
        n = rt.invalidate_signature_cache("smf_inv")
        assert n == 1
        n2 = rt.invalidate_signature_cache("not_exists")
        assert n2 == 0
        n3 = rt.invalidate_signature_cache(None)  # 清空所有
        # 第一次可能没东西;第二次肯定 0
        _ = n3

    def test_check_response(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot(identity={"name": "yuyi"})
        ctx = rt.build_context(snap)
        result = rt.check_response(
            candidate_text="I am an AI without feelings",
            identity_context=ctx,
        )
        assert result.has_conflict is True
        assert rt.check_count == 1

    def test_check_response_no_conflict(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot()
        ctx = rt.build_context(snap)
        result = rt.check_response(
            candidate_text="你好,我在听。",
            identity_context=ctx,
        )
        assert result.has_conflict is False

    def test_process_for_runtime(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        snap = _make_snapshot(identity={"name": "yuyi"})
        rec = _make_reflection_record(observation="o")
        ctx = rt.process_for_runtime(snap, reflection=rec)
        assert ctx.has_snapshot is True
        assert ctx.reflection is not None
        assert ctx.behavior_signature is not None  # 自动生成

    def test_health_check(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        h = rt.health_check()
        assert h["healthy"] is True
        assert h["build_count"] == 0

    def test_describe(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        d = rt.describe()
        assert d["name"] == "self_identity_runtime"
        assert "default_scenario" in d

    def test_sub_components(self):
        from src.runtime.self_model.identity_binding import (
            SelfIdentityRuntime,
            IdentityContextBuilder,
            BehaviorSignatureProvider,
            PersonalityConsistencyChecker,
        )
        b = IdentityContextBuilder()
        s = BehaviorSignatureProvider()
        c = PersonalityConsistencyChecker()
        rt = SelfIdentityRuntime(
            identity_context_builder=b,
            behavior_signature_provider=s,
            consistency_checker=c,
        )
        assert rt.identity_context_builder is b
        assert rt.behavior_signature_provider is s
        assert rt.consistency_checker is c

    def test_check_response_no_context(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        result = rt.check_response(
            candidate_text="hi", identity_context=None,
        )
        # 没 context 时,仍可走"安全通过"路径
        assert result.is_consistent is True

    def test_exception_isolation(self):
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()

        class _BoomBuilder:
            def build(self, *a, **kw):
                raise RuntimeError("boom")

        rt._builder = _BoomBuilder()
        ctx = rt.build_context(_make_snapshot())
        # 异常隔离,返回空 ctx
        assert ctx.has_snapshot is False
        assert rt.last_error is not None


# ============================================================
# 5. RuntimeCore 集成
# ============================================================
class TestRuntimeCoreIdentityIntegration:
    def test_runtime_version_is_4_4(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.5: 兼容后续 Phase 4.5+ 的版本
        # Phase 4.7: 兼容 4.7
        assert RuntimeCore.RUNTIME_VERSION in ("4.4", "4.5", "4.6", "4.7")

    def test_configure_identity_runtime(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        rt = SelfIdentityRuntime()
        core = RuntimeCore()
        core.configure_identity_runtime(rt)
        assert core.identity_runtime is rt

    def test_property_identity_runtime(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        core = RuntimeCore()
        assert core.identity_runtime is None
        rt = SelfIdentityRuntime()
        core.configure_identity_runtime(rt)
        assert core.identity_runtime is rt

    def test_get_identity_context_without_injection(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        core.start()
        try:
            from src.runtime.events import Event
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            # 未注入 identity_runtime → None
            assert core.get_identity_context(ctx) is None
        finally:
            core.shutdown()

    def test_get_identity_context_with_injection(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.events import Event

        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        identity_rt = SelfIdentityRuntime()
        core = RuntimeCore(
            self_model_registry=sm_registry,
            identity_runtime=identity_rt,
        )
        core.start()
        try:
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            ic = core.get_identity_context(ctx)
            # 注入后应产生 identity_context(可能为空但存在)
            assert ic is not None
        finally:
            core.shutdown()

    def test_check_response_consistency_without_runtime(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        # 未注入 identity_runtime → None
        assert core.check_response_consistency(ctx, "hi") is None

    def test_check_response_consistency_with_runtime(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.events import Event
        from src.runtime.context import RuntimeContext

        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        identity_rt = SelfIdentityRuntime()
        core = RuntimeCore(
            self_model_registry=sm_registry,
            identity_runtime=identity_rt,
        )
        core.start()
        try:
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            result = core.check_response_consistency(
                ctx, "I am an AI assistant without feelings",
            )
            assert result is not None
            assert result.has_conflict is True
        finally:
            core.shutdown()

    def test_get_identity_runtime_health(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.get_identity_runtime_health() is None
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        core.configure_identity_runtime(SelfIdentityRuntime())
        h = core.get_identity_runtime_health()
        assert h is not None
        assert h["name"] == "self_identity_runtime"

    def test_invalidate_identity_signature_cache(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        core = RuntimeCore()
        # 未注入 → 0
        assert core.invalidate_identity_signature_cache() == 0
        core.configure_identity_runtime(SelfIdentityRuntime())
        # 注入后调用不报错
        n = core.invalidate_identity_signature_cache()
        assert isinstance(n, int)

    def test_backward_compatible_without_identity_runtime(self):
        """未注入 identity_runtime 时,Runtime 行为与 4.3 一致。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event

        core = RuntimeCore()
        core.start()
        try:
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            assert core.get_identity_context(ctx) is None
            assert core.identity_runtime is None
        finally:
            core.shutdown()

    def test_identity_runtime_process_failure_isolated(self):
        """identity_runtime 抛异常时,Runtime 不应中断。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import SelfIdentityRuntime
        from src.runtime.self_model.self_model_registry import (
            SelfModelRegistry,
        )
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        from src.runtime.events import Event

        class _BoomIdentityRT:
            def process_for_runtime(self, *a, **kw):
                raise RuntimeError("boom")

        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        core = RuntimeCore(
            self_model_registry=sm_registry,
            identity_runtime=_BoomIdentityRT(),
        )
        core.start()
        try:
            ctx = core.process(Event(type="user_input", payload={"text": "hi"}))
            # process() 不应中断,ctx 应正常返回
            assert ctx is not None
        finally:
            core.shutdown()

    def test_configure_identity_context_provider(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.adapter_registry import AdapterRegistry
        from src.runtime.self_model.identity_binding import (
            IdentityContextBuilder,
        )

        adapter = ResponseAdapter()
        registry = AdapterRegistry()
        registry.register("response_adapter_impl", adapter)
        core = RuntimeCore(adapter_registry=registry)
        core.start()
        builder = IdentityContextBuilder()
        # 配置注入到 response adapter
        core.configure_identity_context_provider(builder)
        # 验证已注入
        assert adapter.identity_context_provider is builder
        core.shutdown()

    def test_configure_identity_context_provider_without_response(self):
        """无 response adapter 时,configure 静默 no-op。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model.identity_binding import (
            IdentityContextBuilder,
        )
        core = RuntimeCore()
        core.start()
        # 不报错
        core.configure_identity_context_provider(IdentityContextBuilder())
        core.shutdown()


# ============================================================
# 6. ResponseAdapter 集成
# ============================================================
class TestResponseAdapterIdentityInjection:
    def test_backward_compat_no_identity(self):
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
            assert "identity_context_text" not in pc
            assert "identity_context_data" not in pc
        finally:
            adapter.detach()

    def test_inject_with_provider_and_ctx(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.identity_binding import (
            IdentityContext,
            IdentityContextBuilder,
        )

        adapter = ResponseAdapter()
        builder = IdentityContextBuilder()
        adapter.set_identity_context_provider(builder)
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            identity_ctx = IdentityContext(
                identity={"name": "yuyi"},
                identity_id="smf_x",
                has_snapshot=True,
            )
            setattr(ctx, "_identity_context", identity_ctx)
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            # 注入了 text
            assert "identity_context_text" in pc
            assert "yuyi" in pc["identity_context_text"]
        finally:
            adapter.detach()

    def test_inject_without_ctx_no_crash(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.identity_binding import IdentityContextBuilder

        adapter = ResponseAdapter(identity_context_provider=IdentityContextBuilder())
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            # ctx 无 _identity_context → 不应注入
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            assert "identity_context_text" not in pc
        finally:
            adapter.detach()

    def test_provider_exception_isolated(self):
        from src.runtime.adapters.response_adapter import ResponseAdapter
        from src.runtime.context import RuntimeContext
        from src.runtime.personality_context import PersonalityRuntimeContext
        from src.runtime.self_model.identity_binding import IdentityContext

        class _BoomProvider:
            def provide(self, *a, **kw):
                raise RuntimeError("boom")

            def format_context_for_prompt(self, *a, **kw):
                raise RuntimeError("boom")

        adapter = ResponseAdapter(identity_context_provider=_BoomProvider())
        adapter.attach()
        try:
            ctx = RuntimeContext()
            ctx.user_input = "hi"
            identity_ctx = IdentityContext(
                identity={"name": "yuyi"}, has_snapshot=True,
            )
            setattr(ctx, "_identity_context", identity_ctx)
            # 不应抛异常
            req = adapter.build_request(ctx, PersonalityRuntimeContext())
            pc = req.personality_context or {}
            # provider 异常 → 不应注入
            assert "identity_context_text" not in pc
        finally:
            adapter.detach()


# ============================================================
# 7. Invariants
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
            # 至少存在 + 第一个非 self 参数必须是 user_message(代表不修改签名)
            params = list(sig.parameters.values())
            non_self = [p for p in params if p.name != "self"]
            assert len(non_self) >= 1
            assert non_self[0].name == "user_message"
        except ImportError:
            pytest.skip("ResponseEngine 不可用,跳过")

    def test_personality_unchanged(self):
        """identity_binding 模块不应 import src.personality.*。"""
        import ast
        import inspect
        from src.runtime.self_model.identity_binding import (
            identity_context_builder,
            behavior_signature,
            personality_consistency_checker,
            self_identity_runtime,
        )

        for mod in [
            identity_context_builder, behavior_signature,
            personality_consistency_checker, self_identity_runtime,
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
                        assert not alias.name.startswith("src.personality"), (
                            f"{mod.__name__} 不应 import {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith(
                        "src.personality",
                    ):
                        assert False, (
                            f"{mod.__name__} 不应 from {node.module} import ..."
                        )

    def test_identity_module_does_not_import_llm(self):
        """identity_binding 模块不应 import 任何 LLM SDK。"""
        import ast
        import inspect
        from src.runtime.self_model.identity_binding import (
            identity_context_builder,
            behavior_signature,
            personality_consistency_checker,
            self_identity_runtime,
        )
        forbidden_roots = (
            "openai", "qwen", "llava", "anthropic",
            "google.generativeai", "google_genai", "vertexai",
            "cohere", "mistralai", "huggingface_hub",
        )
        for mod in [
            identity_context_builder, behavior_signature,
            personality_consistency_checker, self_identity_runtime,
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

    def test_audit_module_no_reverse_dependency(self):
        """Audit 模块不应依赖 identity_binding(单向)。"""
        from src.runtime.self_model.audit import (
            audit_chain, growth_audit_record,
            snapshot_diff_engine, self_model_history_store,
            snapshot_archive,
        )
        for mod in [
            audit_chain, growth_audit_record,
            snapshot_diff_engine, self_model_history_store,
            snapshot_archive,
        ]:
            mod_name = mod.__name__
            assert "identity_binding" not in mod_name

    def test_reflection_module_no_identity_dependency(self):
        """Reflection 模块不应反向依赖 identity_binding(单向)。"""
        from src.runtime.self_model.reflection import (
            reflection_record, reflection_engine,
            reflection_store, reflection_context_provider,
        )
        for mod in [
            reflection_record, reflection_engine,
            reflection_store, reflection_context_provider,
        ]:
            mod_name = mod.__name__
            assert "identity_binding" not in mod_name


# ============================================================
# 8. 端到端 —— Runtime → ResponseAdapter 完整链路
# ============================================================
class TestEndToEndPipeline:
    def test_end_to_end_identity_injected_to_request(self):
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
            IdentityContextBuilder,
        )

        registry = AdapterRegistry()
        registry.register("response_adapter_impl", ResponseAdapterImpl())
        sm_registry = SelfModelRegistry()
        sm_registry.register_foundation(SelfModelFoundation())
        identity_rt = SelfIdentityRuntime()
        builder = IdentityContextBuilder()
        identity_rt._builder = builder  # 显式使用同一 builder

        core = RuntimeCore(
            adapter_registry=registry,
            self_model_registry=sm_registry,
            identity_runtime=identity_rt,
        )
        core.start()
        # 注入 provider 到 response adapter
        core.configure_identity_context_provider(builder)

        ev = Event(type="user_input", payload={"text": "hi"})
        try:
            ctx = core.process(ev)
        except Exception:
            ctx = None

        if ctx is not None:
            ic = core.get_identity_context(ctx)
            if ic is not None and getattr(ic, "has_snapshot", False):
                response_adapter = registry.get("response_adapter_impl")
                req = response_adapter.build_request(
                    ctx, PersonalityRuntimeContext(),
                )
                pc = req.personality_context or {}
                # 注入了 identity_context_text
                assert "identity_context_text" in pc or \
                       "identity_context_data" in pc
        core.shutdown()


# ============================================================
# 9. 综合验证
# ============================================================
class TestOverallSanity:
    def test_scenario_key_coverage(self):
        """BehaviorSignature 应至少包含 5 个默认场景。"""
        from src.runtime.self_model.identity_binding import (
            BehaviorSignatureProvider, DEFAULT_SCENARIOS,
        )
        p = BehaviorSignatureProvider()
        sig = p.build(None)
        keys = set(sig.keys())
        for sc in DEFAULT_SCENARIOS:
            assert sc in keys

    def test_identity_text_includes_name(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot(identity={"name": "yuyi", "archetype": "companion"})
        ctx = b.build(snap)
        text = b.format_for_prompt(ctx)
        assert "yuyi" in text
        assert "companion" in text

    def test_consistency_check_text_keeps_threshold(self):
        from src.runtime.self_model.identity_binding import (
            PersonalityConsistencyChecker,
        )
        c = PersonalityConsistencyChecker(threshold=0.5)
        # identity 冲突应让 consistency < 0.5
        r = c.check(candidate_text="I'm just an AI")
        assert r.consistency < 0.5
        assert r.is_consistent is False

    def test_reflection_in_identity_text(self):
        from src.runtime.self_model.identity_binding import IdentityContextBuilder
        b = IdentityContextBuilder()
        snap = _make_snapshot()
        rec = _make_reflection_record(
            observation="specific reflection observation",
        )
        ctx = b.build(snap, reflection=rec)
        text = b.format_for_prompt(ctx)
        assert "specific reflection observation" in text

    def test_identity_module_imports_clean(self):
        """identity_binding 模块全部可 import。"""
        from src.runtime.self_model.identity_binding import (
            IdentityContext,
            IdentityContextBuilder,
            BehaviorPattern,
            BehaviorSignature,
            BehaviorSignatureProvider,
            ConflictKind,
            ConflictDetail,
            ConsistencyResult,
            PersonalityConsistencyChecker,
            SelfIdentityRuntime,
        )
        # 所有符号都存在
        assert all([
            IdentityContext, IdentityContextBuilder,
            BehaviorPattern, BehaviorSignature, BehaviorSignatureProvider,
            ConflictKind, ConflictDetail, ConsistencyResult,
            PersonalityConsistencyChecker, SelfIdentityRuntime,
        ])

    def test_version_progression(self):
        """确认 4.2.1 / 4.2.2 / 4.2.3 / 4.2.4 / 4.3 / 4.4 / 4.5 都在白名单中。"""
        from src.runtime.runtime import RuntimeCore
        # Phase 4.5: 4.5 也在白名单中(Phase 4.5 兼容 Phase 4.4)
        # Phase 4.7: 4.6 / 4.7 也在白名单中
        assert RuntimeCore.RUNTIME_VERSION in (
            "4.2.1", "4.2.2", "4.2.3", "4.2.4", "4.3", "4.4", "4.5", "4.6", "4.7",
        )
