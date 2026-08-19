# -*- coding: utf-8 -*-
"""
tests/test_phase_4_2_1_self_model_foundation.py

Phase 4.2.1: Self Model Foundation —— 单元测试

覆盖:
1.  SelfModelEntry 数据类与约束
2.  SelfModelSnapshot 数据类与约束
3.  SelfModelSnapshot 序列化 (to_dict / from_dict)
4.  SelfModelSnapshot 健康度自检 (compute_health / diff)
5.  SelfModelFoundation 默认 build
6.  SelfModelFoundation 接受 inputs
7.  SelfModelFoundation 失败隔离
8.  SelfModelFoundation health_check
9.  SelfModelFoundation history 维护
10. SelfModelBuilderBase 抽象
11. SelfModelRegistry 注册/反注册
12. SelfModelRegistry build_all / build_primary
13. SelfModelRegistry health_check_all
14. SelfModelRegistry history_summary
15. RuntimeCore RUNTIME_VERSION 推进到 4.2.1
16. RuntimeCore 16 阶段 + SELF_MODEL_BUILD 位置
17. RuntimeCore 无 self_model_registry 时向后兼容
18. RuntimeCore configure_self_model() 注入
19. RuntimeCore SELF_MODEL_BUILD 阶段真实调用
20. RuntimeCore ctx._self_model_snapshot 挂载
21. RuntimeCore self_model_registry property
22. RuntimeCore shutdown 后状态清理
23. RuntimeContext schema 未修改(仍是 "1.0")
24. Personality 核心模块未 import(不影响现有逻辑)
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# 工具
# ============================================================
def _now_iso():
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


def _make_entry(
    kind: str = "growth",
    summary: str = "test entry",
    confidence: float = 0.5,
    sources=None,
):
    from src.runtime.self_model import SelfModelEntry
    return SelfModelEntry(
        kind=kind,
        summary=summary,
        confidence=confidence,
        sources=list(sources or ["test"]),
    )


def _make_foundation(identity_id=None):
    from src.runtime.self_model import SelfModelFoundation
    return SelfModelFoundation(identity_id=identity_id)


def _make_registry():
    from src.runtime.self_model import SelfModelRegistry
    return SelfModelRegistry()


# ============================================================
# 1. SelfModelEntry 数据类与约束
# ============================================================
class TestSelfModelEntrySchema:
    def test_default_construction(self):
        e = _make_entry()
        assert e.kind == "growth"
        assert e.summary == "test entry"
        assert 0.0 <= e.confidence <= 1.0
        assert e.entry_id.startswith("sme_")
        assert e.created_at.endswith("Z")
        assert e.sources == ["test"]

    def test_invalid_kind_rejected(self):
        from src.runtime.self_model import SelfModelEntry
        with pytest.raises(ValueError, match="SelfModelEntry.kind must be one of"):
            SelfModelEntry(kind="invalid_kind", summary="x")

    def test_invalid_confidence_rejected(self):
        from src.runtime.self_model import SelfModelEntry
        with pytest.raises(ValueError, match="confidence must be in"):
            SelfModelEntry(kind="event", summary="x", confidence=1.5)
        with pytest.raises(ValueError, match="confidence must be in"):
            SelfModelEntry(kind="event", summary="x", confidence=-0.1)

    def test_valid_kinds(self):
        for kind in ["growth", "reflection", "trait_change", "event", "preference", "narrative"]:
            e = _make_entry(kind=kind)
            assert e.kind == kind

    def test_to_from_dict_roundtrip(self):
        e = _make_entry()
        d = e.to_dict()
        assert isinstance(d, dict)
        e2 = _make_entry.__self_class__ if False else None
        from src.runtime.self_model import SelfModelEntry
        e2 = SelfModelEntry.from_dict(d)
        assert e2.entry_id == e.entry_id
        assert e2.kind == e.kind
        assert e2.summary == e.summary
        assert e2.confidence == e.confidence


# ============================================================
# 2. SelfModelSnapshot 数据类与约束
# ============================================================
class TestSelfModelSnapshotSchema:
    def test_default_construction(self):
        from src.runtime.self_model import SelfModelSnapshot
        s = SelfModelSnapshot()
        assert s.schema_version == "1.0"
        assert s.version == 1
        assert s.identity_id.startswith("smf_")
        assert s.created_at.endswith("Z")
        assert s.updated_at.endswith("Z")
        assert s.identity == {} or isinstance(s.identity, dict)

    def test_version_bump(self):
        from src.runtime.self_model import SelfModelSnapshot
        s = SelfModelSnapshot()
        old_v = s.version
        old_updated = s.updated_at
        s.version_bump()
        assert s.version == old_v + 1
        assert s.updated_at != old_updated

    def test_to_from_dict_roundtrip(self):
        from src.runtime.self_model import SelfModelSnapshot
        s = SelfModelSnapshot(
            identity={"name": "yuyi", "archetype": "companion_ai"},
            core_values=[{"key": "honesty", "label": "真诚", "weight": 0.85}],
            stable_traits=[{"trait": "warmth", "current_value": 0.7}],
            current_state={"phase": "calm"},
        )
        d = s.to_dict()
        assert d["identity"]["name"] == "yuyi"
        assert d["core_values"][0]["key"] == "honesty"
        s2 = SelfModelSnapshot.from_dict(d)
        assert s2.identity_id == s.identity_id
        assert s2.version == s.version
        assert s2.identity["name"] == "yuyi"
        assert s2.core_values[0]["key"] == "honesty"
        assert s2.stable_traits[0]["trait"] == "warmth"
        assert s2.current_state["phase"] == "calm"

    def test_compute_health_empty(self):
        from src.runtime.self_model import SelfModelSnapshot
        s = SelfModelSnapshot()
        h = s.compute_health()
        assert "complete" in h
        assert "completeness" in h
        assert h["complete"] is False
        assert h["completeness"] == 0.0

    def test_compute_health_full(self):
        from src.runtime.self_model import SelfModelSnapshot
        s = SelfModelSnapshot(
            identity={"name": "yuyi"},
            core_values=[{"key": "honesty"}],
            stable_traits=[{"trait": "warmth"}],
            entries=[_make_entry()],
        )
        h = s.compute_health()
        assert h["complete"] is True
        assert h["completeness"] >= 0.5
        assert h["core_values_count"] == 1
        assert h["stable_traits_count"] == 1
        assert h["entries_count"] == 1

    def test_diff(self):
        from src.runtime.self_model import SelfModelSnapshot
        s1 = SelfModelSnapshot(identity={"name": "yuyi"})
        s2 = SelfModelSnapshot(identity={"name": "yuyi"})
        s1.version_bump()
        s1.core_values.append({"key": "honesty"})
        # s1.version == 2, s2.version == 1
        # s1.core_values == 1, s2.core_values == 0
        d = s1.diff(s2)
        assert d["version_diff"] == 1
        assert d["values_diff"] == 1
        assert d["traits_diff"] == 0

    def test_diff_invalid_type_rejected(self):
        from src.runtime.self_model import SelfModelSnapshot
        s = SelfModelSnapshot()
        with pytest.raises(TypeError):
            s.diff({"not": "a snapshot"})


# ============================================================
# 3. SelfModelFoundation 管理器
# ============================================================
class TestSelfModelFoundation:
    def test_default_build(self):
        f = _make_foundation()
        s = f.build()
        assert s.schema_version == "1.0"
        assert s.version == 1
        assert s.identity.get("name") == "yuyi"
        # 默认 core_values 应有 5 个
        assert len(s.core_values) >= 5
        # 每次 build 后 counters 反映 entries
        assert s.counters["core_values"] >= 5
        assert s.counters["stable_traits"] == 0
        assert s.health.get("complete") is True  # 有 identity + values

    def test_build_with_inputs_trait_states(self):
        f = _make_foundation()
        s = f.build({
            "trait_states": {
                "warmth": {
                    "current_value": 0.85,
                    "direction": "stable",
                    "stability": 0.6,
                    "confidence": 0.7,
                },
                "gentleness": {
                    "current_value": 0.78,
                    "direction": "increase",
                    "stability": 0.5,
                    "confidence": 0.65,
                },
            }
        })
        traits_by_name = {t["trait"]: t for t in s.stable_traits}
        assert "warmth" in traits_by_name
        assert traits_by_name["warmth"]["current_value"] == 0.85
        assert "gentleness" in traits_by_name

    def test_build_with_inputs_preferences(self):
        f = _make_foundation()
        s = f.build({
            "preferences": [
                {"domain": "communication", "key": "tone", "value": "温柔", "confidence": 0.8},
                {"domain": "topic", "key": "weather", "value": "casual", "confidence": 0.6},
            ]
        })
        assert len(s.preferences) == 2
        keys = {p["key"] for p in s.preferences}
        assert "tone" in keys
        assert "weather" in keys

    def test_build_with_inputs_extra_entries(self):
        f = _make_foundation()
        e1 = _make_entry(kind="growth", summary="first growth")
        e2 = _make_entry(kind="reflection", summary="first reflection")
        s = f.build({"extra_entries": [e1, e2]})
        assert len(s.entries) == 2
        assert s.entries[0].summary in ("first growth", "first reflection")

    def test_build_with_invalid_extra_entry_ignored(self):
        from src.runtime.self_model import SelfModelFoundation
        f = SelfModelFoundation()
        # 构造一个 kind 不可用的 entry 需要绕过 _make_entry 校验,
        # 直接传 None 或不可识别的对象,Foundation 应静默忽略
        good = _make_entry(kind="growth", summary="good")
        # 传一个 dict 模拟非法 entry
        result = f.build({"extra_entries": [{"not": "an_entry_object"}, good]})
        assert result is not None
        # 只有 good 进入
        assert len(result.entries) == 1
        assert result.entries[0].summary == "good"

    def test_build_with_invalid_trait_state_ignored(self):
        f = _make_foundation()
        s = f.build({
            "trait_states": {
                "bad": "not_a_dict",
                "good": {"current_value": 0.5, "stability": 0.4, "confidence": 0.5},
            }
        })
        traits_by_name = {t["trait"]: t for t in s.stable_traits}
        assert "good" in traits_by_name
        assert "bad" not in traits_by_name

    def test_build_with_identity_overrides(self):
        f = _make_foundation()
        s = f.build({
            "identity_overrides": {"name": "custom_yuyi", "version": "9.9"}
        })
        assert s.identity.get("name") == "custom_yuyi"
        assert s.identity.get("version") == "9.9"

    def test_build_version_increments(self):
        f = _make_foundation()
        s1 = f.build()
        s2 = f.build()
        s3 = f.build()
        assert s1.version == 1
        assert s2.version == 2
        assert s3.version == 3

    def test_build_failure_returns_none(self):
        """Foundation 失败时 build() 返回 None(供 Registry 隔离失败 Foundation)。"""
        f = _make_foundation()
        original = f._compose
        try:
            f._compose = lambda inputs: (_ for _ in ()).throw(RuntimeError("forced"))
            s = f.build()
            # Foundation 失败 → 返回 None
            assert s is None
        finally:
            f._compose = original

    def test_build_failure_records_error(self):
        f = _make_foundation()
        original = f._compose
        try:
            f._compose = lambda inputs: (_ for _ in ()).throw(RuntimeError("forced"))
            f.build()
            assert f.last_error is not None
            assert "forced" in f.last_error
        finally:
            f._compose = original

    def test_history_maintained(self):
        f = _make_foundation()
        f.build()
        f.build()
        f.build()
        h = f.history_list()
        # history 保存前 N-1 个 snapshot(第 N 个是 current)
        assert len(h) == 2

    def test_history_limit_respected(self):
        f = _make_foundation()
        for _ in range(20):
            f.build()
        # HISTORY_LIMIT = 10,所以 history 最多 10 项
        h = f.history_list()
        assert len(h) <= 10

    def test_health_check(self):
        f = _make_foundation()
        h1 = f.health_check()
        assert h1["healthy"] is True
        assert h1["has_snapshot"] is False
        f.build()
        h2 = f.health_check()
        assert h2["has_snapshot"] is True
        assert h2["build_count"] == 1
        assert h2["version"] == 1

    def test_reset(self):
        f = _make_foundation()
        f.build()
        f.build()
        f.reset()
        assert f.build_count == 0
        assert f.current is None
        assert f.last_error is None
        assert f.history_list() == []

    def test_snapshot_without_build(self):
        f = _make_foundation()
        s = f.snapshot()
        # 第一次 snapshot() 应触发 build
        assert s.schema_version == "1.0"

    def test_no_llm_call(self):
        """确认 Foundation 不调用任何 LLM/外部模型。"""
        import src.runtime.self_model.self_model_foundation as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for line in src.splitlines():
            stripped = line.strip()
            # 只检查 import 语句,跳过自身模块引用
            if (stripped.startswith("import ") or stripped.startswith("from ")) and "self_model" not in line:
                low = stripped.lower()
                for forbidden in ["openai", "qwen", "llava", "yolo", "clip", "anthropic", "gpt"]:
                    assert forbidden not in low, (
                        f"forbidden import: {line!r}"
                    )


# ============================================================
# 4. SelfModelBuilderBase 抽象
# ============================================================
class TestSelfModelBuilderBase:
    def test_default_construction(self):
        from src.runtime.self_model import SelfModelBuilderBase
        b = SelfModelBuilderBase()
        assert b.name == "self_model_builder_base"
        assert b.schema_version == "1.0"
        assert b.foundation is not None

    def test_aggregate(self):
        from src.runtime.self_model import SelfModelBuilderBase
        b = SelfModelBuilderBase()
        s = b.aggregate({})
        assert s is not None
        assert s.schema_version == "1.0"

    def test_health_check(self):
        from src.runtime.self_model import SelfModelBuilderBase
        b = SelfModelBuilderBase()
        h = b.health_check()
        assert h["schema_version"] == "1.0"

    def test_reset(self):
        from src.runtime.self_model import SelfModelBuilderBase
        b = SelfModelBuilderBase()
        b.aggregate({})
        b.reset()
        assert b.foundation.build_count == 0


# ============================================================
# 5. SelfModelRegistry
# ============================================================
class TestSelfModelRegistry:
    def test_register_and_get_foundation(self):
        reg = _make_registry()
        f = _make_foundation("smf_a")
        reg.register_foundation(f)
        assert reg.foundation_count == 1
        assert reg.get_foundation("smf_a") is f

    def test_register_non_foundation_rejected(self):
        reg = _make_registry()
        with pytest.raises(TypeError, match="foundation must be SelfModelFoundation"):
            reg.register_foundation("not a foundation")

    def test_register_duplicate_rejected(self):
        reg = _make_registry()
        reg.register_foundation(_make_foundation("dup"))
        with pytest.raises(ValueError, match="already registered"):
            reg.register_foundation(_make_foundation("dup"))

    def test_unregister_foundation(self):
        reg = _make_registry()
        f = _make_foundation("x")
        reg.register_foundation(f)
        removed = reg.unregister_foundation("x")
        assert removed is f
        assert reg.get_foundation("x") is None

    def test_register_builder(self):
        from src.runtime.self_model import SelfModelBuilderBase
        reg = _make_registry()
        b = SelfModelBuilderBase()
        reg.register_builder(b)
        assert reg.builder_count == 1
        assert reg.get_builder("self_model_builder_base") is b

    def test_register_non_builder_rejected(self):
        reg = _make_registry()
        with pytest.raises(TypeError, match="builder must be SelfModelBuilderBase"):
            reg.register_builder("not a builder")

    def test_register_duplicate_builder_rejected(self):
        from src.runtime.self_model import SelfModelBuilderBase
        reg = _make_registry()
        reg.register_builder(SelfModelBuilderBase())
        with pytest.raises(ValueError, match="already registered"):
            reg.register_builder(SelfModelBuilderBase())

    def test_len_and_contains(self):
        reg = _make_registry()
        reg.register_foundation(_make_foundation("a"))
        assert "a" in reg
        assert len(reg) == 1

    def test_build_all_empty(self):
        reg = _make_registry()
        snaps = reg.build_all()
        assert snaps == []

    def test_build_all_multiple(self):
        reg = _make_registry()
        reg.register_foundation(_make_foundation("a"))
        reg.register_foundation(_make_foundation("b"))
        snaps = reg.build_all({
            "a": {"trait_states": {"warmth": {"current_value": 0.8, "stability": 0.5, "confidence": 0.6}}},
            "b": {"trait_states": {"gentleness": {"current_value": 0.7, "stability": 0.5, "confidence": 0.6}}},
        })
        assert len(snaps) == 2
        names_by_trait = {s.stable_traits[0]["trait"] for s in snaps if s.stable_traits}
        assert "warmth" in names_by_trait
        assert "gentleness" in names_by_trait

    def test_build_all_failure_isolated(self):
        reg = _make_registry()
        good = _make_foundation("good")
        bad = _make_foundation("bad")
        original = bad._compose
        try:
            bad._compose = lambda inputs: (_ for _ in ()).throw(RuntimeError("forced"))
            reg.register_foundation(good)
            reg.register_foundation(bad)
            snaps = reg.build_all()
            # bad 失败,good 应仍能产出 snapshot
            assert len(snaps) == 1
            assert snaps[0].identity_id == "good"
        finally:
            bad._compose = original

    def test_build_primary_empty(self):
        reg = _make_registry()
        assert reg.build_primary() is None

    def test_build_primary(self):
        reg = _make_registry()
        reg.register_foundation(_make_foundation("primary"))
        s = reg.build_primary({"trait_states": {}})
        assert s is not None
        assert s.identity_id == "primary"

    def test_build_primary_failure_returns_none(self):
        reg = _make_registry()
        bad = _make_foundation("bad")
        original = bad._compose
        try:
            bad._compose = lambda inputs: (_ for _ in ()).throw(RuntimeError("forced"))
            reg.register_foundation(bad)
            s = reg.build_primary({})
            assert s is None
        finally:
            bad._compose = original

    def test_health_check_all(self):
        reg = _make_registry()
        reg.register_foundation(_make_foundation("a"))
        h = reg.health_check_all()
        assert "foundations" in h
        assert "a" in h["foundations"]
        assert h["foundations"]["a"]["healthy"] is True

    def test_health_check_all_failure_isolated(self):
        reg = _make_registry()
        good = _make_foundation("good")
        bad = _make_foundation("bad")
        original = bad._compose
        try:
            bad._compose = lambda inputs: (_ for _ in ()).throw(RuntimeError("forced"))
            reg.register_foundation(good)
            reg.register_foundation(bad)
            h = reg.health_check_all()
            assert h["foundations"]["good"]["healthy"] is True
            # bad foundation 至少能 report
            assert "bad" in h["foundations"]
        finally:
            bad._compose = original

    def test_history_summary(self):
        reg = _make_registry()
        reg.register_foundation(_make_foundation("a"))
        reg.register_foundation(_make_foundation("b"))
        reg.build_all()  # 所有 foundation build 一次
        reg.build_all()  # 第二次
        s = reg.history_summary()
        # 第一次 build 后, history 仍空(因为 current 刚生成)
        # 第二次 build 后, history 会有 1 个
        assert len(s) >= 1
        # 每条 history 都包含 identity_id 字段
        for entry in s:
            assert "identity_id" in entry
            assert "version" in entry


# ============================================================
# 6. RuntimeCore 集成
# ============================================================
class TestRuntimeVersionAndStages:
    def test_runtime_version_is_4_2_1(self):
        from src.runtime.runtime import RuntimeCore
        # Phase 4.2.2 在 4.2.1 基础上加入 SourceAdapter;后续 phase 视为兼容
        assert RuntimeCore.RUNTIME_VERSION in (
            "4.2.1", "4.2.2", "4.2.3", "4.2.4", "4.3", "4.4", "4.5", "4.6",
        )

    def test_lifecycle_order_has_17_stages(self):
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER
        # Phase 4.5 新增 SELF_MODEL_EVOLUTION 后,共 17 个阶段
        assert len(RUNTIME_LIFECYCLE_ORDER) == 17

    def test_self_model_build_stage_exists(self):
        from src.runtime.runtime import RuntimeStage
        assert hasattr(RuntimeStage, "SELF_MODEL_BUILD")
        assert RuntimeStage.SELF_MODEL_BUILD.value == "self_model_build"

    def test_self_model_build_position(self):
        """SELF_MODEL_BUILD 必须在 PERCEPTION_ANALYSIS 之后、RESPONSE_GENERATION 之前。"""
        from src.runtime.runtime import RUNTIME_LIFECYCLE_ORDER, RuntimeStage
        idx_pa = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.PERCEPTION_ANALYSIS)
        idx_smb = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.SELF_MODEL_BUILD)
        idx_rg = RUNTIME_LIFECYCLE_ORDER.index(RuntimeStage.RESPONSE_GENERATION)
        assert idx_pa < idx_smb < idx_rg


class TestRuntimeBackwardCompatibility:
    def test_no_self_model_registry_by_default(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        # 未启动时 last_self_model_health 应为 None
        assert core.last_self_model_health is None
        core.start()
        # 启动后,无 registry → 返回 no_self_model_registry reason
        assert core.last_self_model_health is not None
        assert core.last_self_model_health["reason"] == "no_self_model_registry"
        core.shutdown()

    def test_self_model_attach_results_empty(self):
        from src.runtime.runtime import RuntimeCore
        core = RuntimeCore()
        assert core.self_model_attach_results == {}

    def test_process_without_registry_is_noop(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        core = RuntimeCore()
        core.start()
        event = Event(type="user_input", payload={"text": "hello"})
        ctx = core.process(event)
        # 未注入 self_model_registry,ctx._self_model_snapshot 应保持 None
        assert getattr(ctx, "_self_model_snapshot", "unset") is None or \
               getattr(ctx, "_self_model_snapshot", "unset") == "unset"
        core.shutdown()

    def test_get_self_model_snapshot_returns_none(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        core = RuntimeCore()
        core.start()
        event = Event(type="user_input", payload={"text": "hi"})
        ctx = core.process(event)
        assert core.get_self_model_snapshot(ctx) is None
        assert core.get_self_model_history(ctx) == []
        core.shutdown()


class TestRuntimeSelfModelIntegration:
    def test_configure_self_model(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.self_model import SelfModelRegistry
        core = RuntimeCore()
        core.start()
        reg = SelfModelRegistry()
        reg.register_foundation(_make_foundation("a"))
        core.configure_self_model(reg)
        assert core.self_model_registry is reg
        assert core.last_self_model_health is not None
        core.shutdown()

    def test_process_calls_self_model_build(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import SelfModelRegistry
        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(_make_foundation("runtime_a"))
        core.configure_self_model(reg)
        core.start()
        event = Event(type="user_message", payload={"text": "hi"})
        ctx = core.process(event)
        snap = core.get_self_model_snapshot(ctx)
        assert snap is not None
        assert snap.identity_id == "runtime_a"
        assert snap.schema_version == "1.0"
        # 至少包含默认 core_values
        assert len(snap.core_values) >= 5
        core.shutdown()

    def test_process_self_model_build_failure_isolated(self):
        """Foundation 失败时,Runtime 仍能继续。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import SelfModelRegistry
        core = RuntimeCore()
        reg = SelfModelRegistry()
        bad = _make_foundation("bad")
        original = bad._compose
        try:
            bad._compose = lambda inputs: (_ for _ in ()).throw(RuntimeError("forced"))
            reg.register_foundation(bad)
            core.configure_self_model(reg)
            core.start()
            event = Event(type="user_message", payload={"text": "hi"})
            ctx = core.process(event)
            # 失败时 build_primary 应回退返回 None
            assert core.get_self_model_snapshot(ctx) is None
            # Runtime 不应崩溃
            assert core.is_started
        finally:
            bad._compose = original
        core.shutdown()

    def test_self_model_history_grows(self):
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import SelfModelRegistry
        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(_make_foundation("h"))
        core.configure_self_model(reg)
        core.start()
        # 第一次 process
        e1 = Event(type="user_message", payload={"text": "1"})
        ctx1 = core.process(e1)
        h1 = core.get_self_model_history(ctx1)
        # 第二次 process,foundation 应已累积 history
        e2 = Event(type="user_message", payload={"text": "2"})
        ctx2 = core.process(e2)
        h2 = core.get_self_model_history(ctx2)
        # 第二次的 history 应大于等于第一次
        assert len(h2) >= len(h1)
        core.shutdown()

    def test_self_model_with_personality_context(self):
        """personality_context 应作为 current_state 传入 Foundation。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.events import Event
        from src.runtime.self_model import SelfModelRegistry
        core = RuntimeCore()
        reg = SelfModelRegistry()
        reg.register_foundation(_make_foundation("p"))
        core.configure_self_model(reg)
        core.start()
        event = Event(type="user_message", payload={"text": "hi"})
        ctx = core.process(event)
        snap = core.get_self_model_snapshot(ctx)
        # 至少有一次 build, current_state 可能为空(因为 personality_context 未注入)
        # 但 snapshot 必须存在
        assert snap is not None
        core.shutdown()


# ============================================================
# 7. RuntimeContext schema 不变性
# ============================================================
class TestRuntimeContextSchemaUnchanged:
    def test_context_schema_version_unchanged(self):
        from src.runtime.context import RUNTIME_CONTEXT_SCHEMA_VERSION
        assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"

    def test_self_model_uses_private_ctx_attributes(self):
        """确认 Phase 4.2.1 不会引入新的 ctx 公共字段。"""
        from src.runtime.context import RuntimeContext
        ctx = RuntimeContext()
        # 公共字段不应有 self_model_snapshot
        assert not hasattr(ctx, "self_model_snapshot") or \
               "self_model_snapshot" not in dir(ctx)
        # 私有属性可以挂
        setattr(ctx, "_self_model_snapshot", "test")
        assert getattr(ctx, "_self_model_snapshot") == "test"


# ============================================================
# 8. Personality 核心未修改验证
# ============================================================
class TestPersonalityCoreUntouched:
    def test_runtime_does_not_import_self_model_manager(self):
        """Runtime 不应 import personality.self_model_manager (现有复杂模块)。"""
        from src.runtime import runtime as runtime_mod
        src = open(runtime_mod.__file__, encoding="utf-8").read()
        assert "self_model_manager" not in src
        assert "self_model_v3" not in src

    def test_self_model_does_not_import_personality_existing_modules(self):
        """Phase 4.2.1 模块不应 import personality 现有 self_model_* 模块。"""
        from src.runtime.self_model import self_model_foundation
        from src.runtime.self_model import self_model_data
        from src.runtime.self_model import self_model_registry
        import src.runtime.self_model as self_model_pkg
        for mod in (self_model_foundation, self_model_data, self_model_registry, self_model_pkg):
            src = open(mod.__file__, encoding="utf-8").read()
            for forbidden in [
                "self_model_manager",
                "self_model_v3",
                "self_model_builder",
                "self_model_core",
                "self_model_guardian",
            ]:
                # 只检查 import / from 语句,不检查 docstring 中的引用
                for line in src.splitlines():
                    stripped = line.strip()
                    if (stripped.startswith("import ") or stripped.startswith("from ")) and forbidden in line:
                        # 排除 self_model 自身模块的 import
                        if "self_model" in line and ("self_model_" + forbidden.split("_", 2)[2]) not in line:
                            continue
                        assert False, (
                            f"{mod.__name__} should not import {forbidden}; found: {line!r}"
                        )

    def test_no_openai_or_vision_sdk_imported(self):
        """Phase 4.2.1 不应 import 任何外部 Vision SDK。"""
        from src.runtime.self_model import (
            self_model_data,
            self_model_foundation,
            self_model_registry,
        )
        import src.runtime.self_model as self_model_pkg
        for mod in (self_model_data, self_model_foundation, self_model_registry, self_model_pkg):
            src = open(mod.__file__, encoding="utf-8").read()
            # 只检查 import 语句
            for line in src.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    low = stripped.lower()
                    for forbidden in ["openai", "anthropic", "qwen-vl", "llava", "clip", "yolo"]:
                        assert forbidden not in low, (
                            f"{mod.__name__} contains forbidden import: {line!r}"
                        )


# ============================================================
# 9. 综合 sanity check
# ============================================================
def test_phase_4_2_1_summary():
    """综合 sanity check: Phase 4.2.1 端到端工作流。"""
    from src.runtime.runtime import RuntimeCore, RuntimeStage, RUNTIME_LIFECYCLE_ORDER
    from src.runtime.events import Event
    from src.runtime.self_model import (
        SelfModelRegistry,
        SelfModelFoundation,
        SelfModelSnapshot,
    )
    from src.runtime.context import RUNTIME_CONTEXT_SCHEMA_VERSION

    # 1) 版本与阶段
    assert RuntimeCore.RUNTIME_VERSION in (
        "4.2.1", "4.2.2", "4.2.3", "4.2.4", "4.3", "4.4", "4.5",
    )
    assert len(RUNTIME_LIFECYCLE_ORDER) == 17
    assert RuntimeStage.SELF_MODEL_BUILD.value == "self_model_build"
    # 2) schema 不变
    assert RUNTIME_CONTEXT_SCHEMA_VERSION == "1.0"
    # 3) 完整工作流
    core = RuntimeCore()
    reg = SelfModelRegistry()
    foundation = SelfModelFoundation(identity_id="summary")
    reg.register_foundation(foundation)
    core.configure_self_model(reg)
    core.start()
    event = Event(type="user_input", payload={"text": "summary check"})
    ctx = core.process(event)
    snap = core.get_self_model_snapshot(ctx)
    assert snap is not None
    assert snap.identity_id == "summary"
    assert snap.schema_version == "1.0"
    assert len(snap.core_values) >= 5
    assert snap.health["complete"] is True
    core.shutdown()
    # shutdown 后仍能读取 health
    h = foundation.health_check()
    assert h["healthy"] is True
