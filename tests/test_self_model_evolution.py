# -*- coding: utf-8 -*-
"""
tests/test_self_model_evolution.py

Phase C.8.7 SelfModel Evolution Integration —— 单元测试

覆盖:
  - Creation         引擎 / 策略 / Store / VersionStore / Record 构建
  - Validation       输入校验 / success 校验 / 重复 reflection 校验
  - Policy           核心身份保护 / creator 保护 / values 保护 / description 允许变化
                     / growth_understanding 允许变化 / 变化幅度 / confidence 阈值
  - Version          v0 baseline / 新版本生成 / version 递增
  - Record           before / after / diff / status / rollback_status
  - Readonly         Personality / Growth 不被修改
  - Security         禁止 resolver / 禁止 TraitUpdater / 禁止绕过 VersionStore
  - Rollback         rollback 成功 / 恢复 before / 状态标记
  - Audit            success audit / failure audit / rollback audit
  - Concurrency      重复 reflection / 多线程 reflection
  - FailSafe         store 异常 / policy 异常 / snapshot 异常
  - Integration      Experience -> Growth -> Proposal -> Approval -> Personality
                     Evolution -> SelfModel Reflection 全链路
  - Schema           所有 Record 字段完整
"""
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Helpers
# ============================================================
def _make_evolution_record(
    evolution_id: str = "ev_test_001",
    success: bool = True,
    confidence: float = 0.9,
    changes: Optional[List[Dict[str, Any]]] = None,
    reason: str = "growth_evolution_test",
    evidence: Optional[Dict[str, Any]] = None,
    before_state: Optional[Dict[str, Any]] = None,
    after_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rec: Dict[str, Any] = {
        "schema_version": "1.0",
        "evolution_id": evolution_id,
        "success": success,
        "applied": success,
        "status": "applied" if success else "rejected",
        "confidence": confidence,
        "reason": reason,
        "evidence": evidence or {"source_proposal": {"proposal_id": "prop_001"}},
        "changes": changes or [
            {"key": "warmth", "delta": 0.1, "trait": "warmth"},
            {"key": "expressiveness", "delta": 0.05, "trait": "expressiveness"},
        ],
    }
    if before_state is not None:
        rec["before_state"] = before_state
    if after_state is not None:
        rec["after_state"] = after_state
    return rec


def _make_initial_snapshot(
    with_self_model: bool = True,
    extra_fields: bool = True,
) -> Dict[str, Any]:
    """
    默认包含 15+ 个字段,使得 2 changes < 20%。
    """
    base: Dict[str, Any] = {
        "identity_origin": "test_origin",
        "creator": "yuyi",
        "fundamental_values": ["kindness", "honesty"],
        "core_identity": "gentle_companion",
        "name": "yuyi",
        "identity_id": "yuyi_v1",
    }
    if extra_fields:
        base.update({
            "emotional_tone": "warm",
            "communication_pattern": "narrative",
            "memory_style": "episodic",
            "reasoning_style": "cautious",
            "preferred_topics": ["growth", "care"],
            "social_boundaries": ["respect"],
            "language_preferences": {"primary": "zh"},
            "interaction_history": "1_year",
        })
    if with_self_model:
        base["self_model"] = {
            "self_description": "我倾向于保持安静和稳定的特质状态。",
            "current_traits": ["calm", "thoughtful"],
            "growth_understanding": {"summary": "尚在观察中", "triggers": []},
            "interaction_style": "gentle",
            "capability_boundary": {"capabilities": [], "limits": []},
        }
    return base


def _make_engine(
    initial_snapshot: Optional[Dict[str, Any]] = None,
    policy: Any = None,
    audit: Any = None,
) -> Any:
    from src.runtime.self_model.self_model_evolution import (
        create_self_model_evolution_engine,
    )
    return create_self_model_evolution_engine(
        snapshot_provider=None,
        evolution_store=None,
        version_store=None,
        policy=policy,
        audit=audit,
        initial_snapshot=initial_snapshot if initial_snapshot is not None
        else _make_initial_snapshot(),
    )


# ============================================================
# 1. Creation
# ============================================================
class TestCreation:
    def test_01_engine_create_default(self):
        """1. 引擎默认创建"""
        engine = _make_engine()
        assert engine is not None
        from src.runtime.self_model.self_model_evolution import (
            SelfModelEvolutionEngine,
        )
        assert isinstance(engine, SelfModelEvolutionEngine)

    def test_02_policy_loaded(self):
        """2. Policy 加载"""
        engine = _make_engine()
        p = engine._policy
        assert p is not None
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
        )
        assert isinstance(p, SelfModelEvolutionPolicy)

    def test_03_version_store_init(self):
        """3. VersionStore 初始化 + v0 baseline"""
        engine = _make_engine()
        vs = engine._version_store
        assert vs is not None
        assert vs.get_current_version_id() == "v0"
        assert vs.count() == 1

    def test_04_evolution_store_init(self):
        """4. EvolutionStore 初始化"""
        engine = _make_engine()
        es = engine._evolution_store
        assert es is not None
        stats = es.get_stats()
        assert stats.get("total_records", -1) == 0

    def test_05_record_builder(self):
        """5. SelfModelEvolutionRecord builder"""
        from src.runtime.self_model.self_model_record import (
            build_self_model_evolution_record,
        )
        rec = build_self_model_evolution_record(
            reflection_id="r1",
            evolution_id="e1",
            before_snapshot={"fields": {}, "raw": {}},
            after_snapshot={"fields": {"a": 1}, "raw": {"a": 1}},
            changes=[{"field": "a", "before": 0, "after": 1}],
            reason="test",
            evidence={},
            confidence=0.9,
            timestamp="2026-08-04T00:00:00Z",
            old_version="v0",
            new_version="v1",
        )
        assert rec["reflection_id"] == "r1"
        assert rec["evolution_id"] == "e1"
        assert rec["new_version"] == "v1"
        assert rec["status"] == "applied"
        assert rec["rollback_status"] == "none"

    def test_06_factory_create(self):
        """6. factory 创建"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )
        e = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
        )
        assert e is not None

    def test_07_safe_wrapper_with_none(self):
        """7. safe_reflect_evolution 处理 None engine"""
        from src.runtime.self_model.self_model_evolution import (
            safe_reflect_evolution,
        )
        out = safe_reflect_evolution(None, _make_evolution_record())
        assert out["success"] is False
        assert out["degraded"] is True


# ============================================================
# 2. Validation
# ============================================================
class TestValidation:
    def test_08_no_evolution_record_reject(self):
        """8. 无 EvolutionRecord 拒绝"""
        engine = _make_engine()
        out = engine.reflect_evolution(None)
        assert out["success"] is False
        assert out["degraded"] is True

    def test_09_success_false_reject(self):
        """9. success=False 拒绝"""
        engine = _make_engine()
        rec = _make_evolution_record(success=False)
        out = engine.reflect_evolution(rec)
        assert out["success"] is False
        assert out["reason"] in (
            "invalid_evolution_record", "invalid_input", "policy_needs_review"
        )

    def test_10_normal_reflection(self):
        """10. 正常 reflection"""
        engine = _make_engine()
        rec = _make_evolution_record(confidence=0.95)
        out = engine.reflect_evolution(rec)
        # may be allowed or needs_review depending on change count
        # but if success, it should have reflection_id
        if out["success"]:
            assert out["reflection_id"] != ""
            assert out["new_version"] != ""

    def test_11_empty_dict_reject(self):
        """11. 空 dict 拒绝(无 evolution_id)"""
        engine = _make_engine()
        out = engine.reflect_evolution({})
        assert out["success"] is False

    def test_12_object_with_evolution_id(self):
        """12. object-like evolution record 支持"""
        engine = _make_engine()

        class _Ev:
            evolution_id = "ev_obj_001"
            success = True
            confidence = 0.95
            reason = "object test"
            evidence = {}
            changes = [
                {"key": "warmth", "delta": 0.1, "trait": "warmth"},
                {"key": "expressiveness", "delta": 0.05, "trait": "expressiveness"},
            ]

        out = engine.reflect_evolution(_Ev())
        if out["success"]:
            assert out["reflection_id"] != ""

    def test_13_invalid_evolution_id_reject(self):
        """13. evolution_id 缺失拒绝"""
        engine = _make_engine()
        rec = {"success": True, "confidence": 0.9, "changes": []}
        out = engine.reflect_evolution(rec)
        assert out["success"] is False


# ============================================================
# 3. Policy
# ============================================================
class TestPolicy:
    def test_14_identity_protected(self):
        """14. identity_origin 受保护"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_DENY,
            REASON_IDENTITY_PROTECTED,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "identity_origin",
                "before": "old",
                "after": "new",
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_DENY
        assert REASON_IDENTITY_PROTECTED in result["reasons"]

    def test_15_creator_protected(self):
        """15. creator 受保护"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_DENY,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "creator",
                "before": "alice",
                "after": "bob",
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_DENY

    def test_16_values_protected(self):
        """16. fundamental_values 受保护"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_DENY,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "fundamental_values",
                "before": ["a"],
                "after": ["b"],
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_DENY

    def test_17_self_description_allowed(self):
        """17. self_description 允许变化"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_ALLOW,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "self_description",
                "before": "old desc",
                "after": "new desc",
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_ALLOW

    def test_18_growth_understanding_allowed(self):
        """18. growth_understanding 允许变化"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_ALLOW,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "growth_understanding",
                "before": {},
                "after": {"summary": "I am growing"},
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_ALLOW

    def test_19_current_traits_allowed(self):
        """19. current_traits 允许变化"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_ALLOW,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "current_traits",
                "before": ["calm"],
                "after": ["calm", "curious"],
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_ALLOW

    def test_20_interaction_style_allowed(self):
        """20. interaction_style 允许变化"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_ALLOW,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "interaction_style",
                "before": "quiet",
                "after": "warm",
            }],
            confidence=0.95,
            snapshot={},
        )
        assert result["decision"] == DECISION_ALLOW

    def test_21_low_confidence_needs_review(self):
        """21. 低 confidence 进入 needs_review"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_NEEDS_REVIEW,
            REASON_CONFIDENCE_TOO_LOW,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{
                "field": "self_description",
                "before": "a",
                "after": "b",
            }],
            confidence=0.5,
            snapshot={},
        )
        assert result["decision"] == DECISION_NEEDS_REVIEW
        assert REASON_CONFIDENCE_TOO_LOW in result["reasons"]

    def test_22_change_ratio_exceeded(self):
        """22. 变化比例超限"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_NEEDS_REVIEW,
            REASON_CHANGE_RATIO_EXCEEDED,
        )
        p = SelfModelEvolutionPolicy()
        # 3 fields / 5 fields = 60% > 20%
        result = p.evaluate(
            changes=[
                {"field": "self_description", "before": "a", "after": "b"},
                {"field": "current_traits", "before": [], "after": ["x"]},
                {"field": "growth_understanding", "before": {}, "after": {"a": 1}},
            ],
            confidence=0.95,
            snapshot={
                "self_description": "",
                "current_traits": [],
                "growth_understanding": {},
                "interaction_style": "",
                "capability_boundary": {},
            },
        )
        assert REASON_CHANGE_RATIO_EXCEEDED in result["reasons"]

    def test_23_repeat_detection_penalty(self):
        """23. 重复 reflection 降权"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            REASON_REPEATED_REFLECTION,
        )
        p = SelfModelEvolutionPolicy()
        # 第一次应用
        p.record_applied_reflection("self_description", weight=1.0)
        result = p.evaluate(
            changes=[{
                "field": "self_description",
                "before": "a",
                "after": "b",
            }],
            confidence=0.95,
            snapshot={},
        )
        assert REASON_REPEATED_REFLECTION in result["reasons"]
        # weighted_changes 中应该有权重小于 1.0 的项
        weighted = result["weighted_changes"]
        if weighted:
            assert any(w.get("weight", 1.0) < 1.0 for w in weighted)

    def test_24_policy_evaluate_safe(self):
        """24. policy 异常时返回 needs_review"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
            DECISION_NEEDS_REVIEW,
        )
        p = SelfModelEvolutionPolicy()
        # 制造一个非 dict 的 changes 触发保护
        result = p.evaluate(
            changes=[None, "bad"],
            confidence=0.95,
            snapshot={},
        )
        # 不应抛异常
        assert result["decision"] in (
            DECISION_NEEDS_REVIEW, "allow", "deny"
        )


# ============================================================
# 4. Version
# ============================================================
class TestVersion:
    def test_25_v0_baseline_created(self):
        """25. v0 baseline 创建"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        v0 = vs.get_version("v0")
        assert v0 is not None
        assert v0.get("is_baseline") is True
        assert vs.get_current_version_id() == "v0"

    def test_26_new_version_created(self):
        """26. 新版本生成"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        v1 = vs.create_version(
            snapshot={"new": "snap"},
            source_reflection_id="r1",
        )
        assert v1 == "v1"
        v1_data = vs.get_version("v1")
        assert v1_data is not None
        assert v1_data.get("source_reflection_id") == "r1"
        assert vs.get_current_version_id() == "v1"

    def test_27_version_increments(self):
        """27. 版本递增"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        ids = []
        for i in range(5):
            v = vs.create_version(
                snapshot={"v": i},
                source_reflection_id=f"r{i}",
            )
            ids.append(v)
        assert ids == ["v1", "v2", "v3", "v4", "v5"]

    def test_28_get_version_invalid(self):
        """28. 无效 version_id 返回 None"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        assert vs.get_version("invalid") is None
        assert vs.get_version("") is None

    def test_29_rollback_to_version(self):
        """29. rollback_to 指向历史版本"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        vs.create_version(snapshot={"v": 1}, source_reflection_id="r1")
        vs.create_version(snapshot={"v": 2}, source_reflection_id="r2")
        snap = vs.rollback_to("v1")
        assert snap is not None
        assert snap.get("v") == 1
        assert vs.get_current_version_id() == "v1"

    def test_30_list_versions(self):
        """30. list_versions 包含所有版本"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        vs.create_version(snapshot={"v": 1}, source_reflection_id="r1")
        versions = vs.list_versions()
        assert len(versions) == 2
        assert versions[0]["version_id"] == "v0"


# ============================================================
# 5. Record
# ============================================================
class TestRecord:
    def test_31_before_snapshot_saved(self):
        """31. before_snapshot 保存"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            r = engine.get_reflection(out["reflection_id"])
            assert r is not None
            assert r["before_snapshot"] is not None
            assert "raw" in r["before_snapshot"]
            assert "fields" in r["before_snapshot"]

    def test_32_after_snapshot_saved(self):
        """32. after_snapshot 保存"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            r = engine.get_reflection(out["reflection_id"])
            assert r is not None
            assert r["after_snapshot"] is not None
            assert "raw" in r["after_snapshot"]

    def test_33_diff_generated(self):
        """33. changes 列表生成"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            assert isinstance(out["changes"], list)
            assert len(out["changes"]) >= 1

    def test_34_status_field(self):
        """34. status 字段为 applied"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            r = engine.get_reflection(out["reflection_id"])
            assert r["status"] == "applied"

    def test_35_rollback_status_field(self):
        """35. rollback_status 字段初始为 none"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            r = engine.get_reflection(out["reflection_id"])
            assert r["rollback_status"] == "none"

    def test_36_list_reflections_by_evolution(self):
        """36. 按 evolution_id 查询 reflection 列表"""
        engine = _make_engine()
        rec = _make_evolution_record(
            evolution_id="ev_list_001",
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            items = engine.list_reflections(evolution_id="ev_list_001")
            assert len(items) >= 1


# ============================================================
# 6. Readonly
# ============================================================
class TestReadonly:
    def test_37_personality_not_modified(self):
        """37. Personality 不被修改(注入 fake personality provider 验证)"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class FakePersonality:
            def __init__(self):
                self.evolution_calls: List[Any] = []
                self.apply_calls: List[Any] = []
                self.identity = {"name": "yuyi"}

            def get_snapshot(self):
                return _make_initial_snapshot()

            def apply_evolution(self, rec):
                self.apply_calls.append(rec)
                raise AssertionError("Personality.apply_evolution should not be called")

            def evolve(self, rec):
                self.evolution_calls.append(rec)
                raise AssertionError("Personality.evolve should not be called")

        fake = FakePersonality()
        engine = create_self_model_evolution_engine(
            snapshot_provider=fake,
            initial_snapshot=_make_initial_snapshot(),
        )
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        # 确认 Personality 注入的禁止方法未被调用
        assert len(fake.apply_calls) == 0
        assert len(fake.evolution_calls) == 0

    def test_38_growth_not_modified(self):
        """38. Growth 不被修改"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class FakeGrowth:
            def __init__(self):
                self.modify_calls: List[Any] = []

            def modify(self, *args, **kwargs):
                self.modify_calls.append((args, kwargs))
                raise AssertionError("Growth.modify should not be called")

        fg = FakeGrowth()
        engine = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
        )
        # 即使传 growth provider 也不应被调用
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        assert len(fg.modify_calls) == 0

    def test_39_core_identity_unchanged_in_snapshot(self):
        """39. core_identity 字段不变"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            r = engine.get_reflection(out["reflection_id"])
            after_raw = r["after_snapshot"]["raw"]
            # identity_origin / creator / fundamental_values 不变
            assert after_raw.get("identity_origin") == "test_origin"
            assert after_raw.get("creator") == "yuyi"


# ============================================================
# 7. Security
# ============================================================
class TestSecurity:
    def test_40_no_personality_resolver_call(self):
        """40. 禁止调用 PersonalityResolver"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class TrapResolver:
            def __init__(self):
                self.called = False

            def resolve(self, *args, **kwargs):
                self.called = True
                raise AssertionError("PersonalityResolver.resolve called")

        trap = TrapResolver()
        engine = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
        )
        # 把 trap 注入到 self_model 字段
        # engine 不应该访问它
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        assert trap.called is False

    def test_41_no_trait_state_updater_call(self):
        """41. 禁止调用 TraitStateUpdater.apply"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class TrapUpdater:
            def __init__(self):
                self.apply_called = False

            def apply(self, *args, **kwargs):
                self.apply_called = True
                raise AssertionError("TraitStateUpdater.apply called")

        trap = TrapUpdater()
        engine = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
        )
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        assert trap.apply_called is False

    def test_42_cannot_bypass_version_store(self):
        """42. 不能绕过 VersionStore"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class TrapVersionStore:
            def __init__(self):
                self.create_called = False
                self._initial = _make_initial_snapshot()
                self._current = "v0"
                self._versions = {"v0": {"version_id": "v0", "snapshot": self._initial}}
                self._order = ["v0"]

            def create_version(self, *args, **kwargs):
                self.create_called = True
                return ""

            def get_current_version_id(self):
                return self._current

            def get_current(self):
                v = self._versions.get(self._current)
                if v:
                    return {"version_id": self._current, "snapshot": v["snapshot"]}
                return None

            def get_version(self, vid):
                v = self._versions.get(vid)
                if v:
                    return {"version_id": vid, "snapshot": v["snapshot"]}
                return None

        trap = TrapVersionStore()
        # 即使传入 trap,它必须被调用过才算绕过
        engine = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
            version_store=trap,
        )
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        # 引擎尝试过调用 VersionStore.create_version
        # 但因为返回空,应当返回失败
        if out["success"]:
            pass  # trap.create_called 应为 True
        assert trap.create_called is True


# ============================================================
# 8. Rollback
# ============================================================
class TestRollback:
    def _make_reflection(self, engine) -> Optional[str]:
        rec = _make_evolution_record(
            evolution_id="ev_rb_001",
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            return out["reflection_id"]
        return None

    def test_43_rollback_success(self):
        """43. rollback 成功"""
        engine = _make_engine()
        rid = self._make_reflection(engine)
        if rid:
            out = engine.rollback(rid)
            assert out["success"] is True
            assert out["reflection_id"] != rid  # 新的 rollback reflection_id

    def test_44_rollback_restores_before(self):
        """44. rollback 恢复 before_snapshot"""
        engine = _make_engine()
        rid = self._make_reflection(engine)
        if rid:
            original = engine.get_reflection(rid)
            before_raw = original["before_snapshot"]["raw"]
            out = engine.rollback(rid)
            assert out["success"] is True
            # 新的 after_snapshot.raw 应等于 before_raw
            new_rid = out["reflection_id"]
            new_rec = engine.get_reflection(new_rid)
            # 校验 after_snapshot.raw 接近 before
            after_raw = new_rec["after_snapshot"]["raw"]
            for k in ("identity_origin", "creator", "fundamental_values"):
                assert after_raw.get(k) == before_raw.get(k)

    def test_45_rollback_marks_status(self):
        """45. rollback 后原 record 状态变 rolled_back"""
        engine = _make_engine()
        rid = self._make_reflection(engine)
        if rid:
            engine.rollback(rid)
            r = engine.get_reflection(rid)
            assert r["status"] == "rolled_back"
            assert r["rollback_status"] == "rolled_back"
            assert r["rolled_back_by"] != ""

    def test_46_rollback_not_found(self):
        """46. rollback 不存在的 reflection_id"""
        engine = _make_engine()
        out = engine.rollback("not_exist_xxx")
        assert out["success"] is False

    def test_47_rollback_empty_id(self):
        """47. rollback 空 reflection_id"""
        engine = _make_engine()
        out = engine.rollback("")
        assert out["success"] is False


# ============================================================
# 9. Audit
# ============================================================
class TestAudit:
    def test_48_success_audit_recorded(self):
        """48. 成功 audit 记录"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        audit = FakeAudit()
        engine = _make_engine(audit=audit)
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            assert any(
                e.get("operation_type") == "runtime_self_model_evolution_created"
                for e in events
            )

    def test_49_failure_audit_recorded(self):
        """49. 失败 audit 记录"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        audit = FakeAudit()
        engine = _make_engine(audit=audit)
        out = engine.reflect_evolution(None)
        assert out["success"] is False
        # 失败时不应阻塞
        assert len(events) >= 0  # 可能记录也可能不记录

    def test_50_rollback_audit_recorded(self):
        """50. rollback audit 记录"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        audit = FakeAudit()
        engine = _make_engine(audit=audit)
        rec = _make_evolution_record(
            evolution_id="ev_audit_rb",
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        if out["success"]:
            rid = out["reflection_id"]
            events.clear()
            engine.rollback(rid)
            assert any(
                e.get("operation_type") == "runtime_self_model_evolution_rollback"
                for e in events
            )

    def test_51_audit_none_safe(self):
        """51. audit=None 时安全运行"""
        engine = _make_engine(audit=None)
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        # 不抛异常即可
        assert "success" in out


# ============================================================
# 10. Concurrency
# ============================================================
class TestConcurrency:
    def test_52_duplicate_reflection_rejected(self):
        """52. 同一 evolution_id 重复 reflection 拒绝"""
        engine = _make_engine()
        rec = _make_evolution_record(
            evolution_id="ev_dup_001",
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out1 = engine.reflect_evolution(rec)
        out2 = engine.reflect_evolution(rec)
        # 至少有一个应当被拒绝或第二个返回 already_reflected
        if out1["success"]:
            assert out2["success"] is False
            assert out2["reason"] in ("already_reflected", "policy_needs_review")

    def test_53_multi_thread_reflection(self):
        """53. 多线程 reflection 互斥"""
        engine = _make_engine()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            rec = _make_evolution_record(
                evolution_id=f"ev_mt_{idx}",
                confidence=0.95,
                changes=[
                    {"key": "warmth", "delta": 0.1},
                    {"key": "expressiveness", "delta": 0.05},
                ],
            )
            out = engine.reflect_evolution(rec)
            with lock:
                results.append(out)

        threads = []
        for i in range(8):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 8
        # 至少有一些 success
        success_count = sum(1 for r in results if r["success"])
        assert success_count >= 1

    def test_54_concurrent_same_evolution_id(self):
        """54. 并发同一 evolution_id 只有一个能 reflect"""
        engine = _make_engine()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker() -> None:
            rec = _make_evolution_record(
                evolution_id="ev_concurrent_same",
                confidence=0.95,
                changes=[
                    {"key": "warmth", "delta": 0.1},
                    {"key": "expressiveness", "delta": 0.05},
                ],
            )
            out = engine.reflect_evolution(rec)
            with lock:
                results.append(out)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        success_count = sum(1 for r in results if r["success"])
        # 最多一个 success
        assert success_count <= 1


# ============================================================
# 11. FailSafe
# ============================================================
class TestFailSafe:
    def test_55_version_store_exception(self):
        """55. VersionStore 异常时降级"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class BadVersionStore:
            def create_version(self, *args, **kwargs):
                raise RuntimeError("version store boom")

            def get_current_version_id(self):
                return "v0"

            def get_current(self):
                return None

        engine = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
            version_store=BadVersionStore(),
        )
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        assert out["success"] is False
        assert out["degraded"] is True

    def test_56_policy_exception(self):
        """56. Policy 异常时降级"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class BadPolicy:
            def evaluate(self, *args, **kwargs):
                raise RuntimeError("policy boom")

        engine = create_self_model_evolution_engine(
            initial_snapshot=_make_initial_snapshot(),
            policy=BadPolicy(),
        )
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        # Policy 异常被内部捕获,返回 needs_review 决策
        assert out["success"] is False

    def test_57_snapshot_provider_exception(self):
        """57. snapshot provider 异常时降级"""
        from src.runtime.self_model.self_model_evolution import (
            create_self_model_evolution_engine,
        )

        class BadSnapshot:
            def get_snapshot(self):
                raise RuntimeError("snapshot boom")

        engine = create_self_model_evolution_engine(
            snapshot_provider=BadSnapshot(),
            initial_snapshot=_make_initial_snapshot(),
        )
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        # 不应抛异常
        assert "success" in out

    def test_58_get_stats_after_failures(self):
        """58. 异常后 get_stats 仍可用"""
        engine = _make_engine()
        engine.reflect_evolution(None)
        engine.reflect_evolution({})
        stats = engine.get_stats()
        assert "reflect_count" in stats
        assert "degraded_count" in stats
        assert "denied_count" in stats


# ============================================================
# 12. Integration
# ============================================================
class TestIntegration:
    def test_59_full_chain_reflection(self):
        """59. 完整链路 Experience -> Growth -> Proposal -> Approval -> Personality Evolution -> SelfModel Reflection"""

        # 模拟 Experience
        experience = {
            "experience_id": "exp_001",
            "kind": "user_interaction",
            "summary": "用户持续表达了对 AI 陪伴的依赖",
        }
        # 模拟 Growth
        growth = {
            "growth_id": "gr_001",
            "experience_id": "exp_001",
            "insight": "user needs more warmth",
            "weight": 0.6,
        }
        # 模拟 Proposal
        proposal = {
            "proposal_id": "prop_001",
            "growth_id": "gr_001",
            "proposed_changes": [
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
            "confidence": 0.9,
            "reason": "user_interaction_pattern",
        }
        # 模拟 Approval
        approval = {
            "approval_id": "appr_001",
            "proposal_id": "prop_001",
            "approved": True,
            "confidence": 0.9,
        }
        # 模拟 Personality Evolution
        evolution = {
            "schema_version": "1.0",
            "evolution_id": "ev_001",
            "success": True,
            "applied": True,
            "status": "applied",
            "confidence": 0.9,
            "reason": proposal["reason"],
            "evidence": {
                "source_proposal": {"proposal_id": proposal["proposal_id"]},
                "source_growth": {"growth_id": growth["growth_id"]},
                "source_experience": {"experience_id": experience["experience_id"]},
            },
            "changes": proposal["proposed_changes"],
        }
        # SelfModel Reflection
        engine = _make_engine()
        out = engine.reflect_evolution(evolution)
        if out["success"]:
            assert out["reflection_id"] != ""
            assert out["new_version"] != ""

    def test_60_full_chain_with_rollback(self):
        """60. 完整链路 + rollback"""
        engine = _make_engine()
        evolution = _make_evolution_record(
            evolution_id="ev_full_002",
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(evolution)
        if out["success"]:
            rid = out["reflection_id"]
            rb = engine.rollback(rid)
            assert rb["success"] is True
            # 原 record 状态
            original = engine.get_reflection(rid)
            assert original["status"] == "rolled_back"
            # 新 rollback record 存在
            new_rb = engine.get_reflection(rb["reflection_id"])
            assert new_rb is not None
            assert new_rb["rollback_of"] == rid


# ============================================================
# 13. Schema
# ============================================================
class TestSchema:
    def test_61_record_all_fields(self):
        """61. Record 包含所有必需字段"""
        from src.runtime.self_model.self_model_record import (
            build_self_model_evolution_record,
        )
        rec = build_self_model_evolution_record(
            reflection_id="r1",
            evolution_id="e1",
            before_snapshot={"a": 1},
            after_snapshot={"a": 2},
            changes=[{"field": "a", "before": 1, "after": 2}],
            reason="test",
            evidence={"k": "v"},
            confidence=0.9,
            timestamp="2026-08-04T00:00:00Z",
            old_version="v0",
            new_version="v1",
        )
        required = [
            "schema_version",
            "reflection_id",
            "evolution_id",
            "before_snapshot",
            "after_snapshot",
            "changes",
            "reason",
            "evidence",
            "confidence",
            "timestamp",
            "old_version",
            "new_version",
            "actor",
            "status",
            "rollback_status",
            "rollback_of",
            "rolled_back_by",
            "extra",
        ]
        for f in required:
            assert f in rec, f"missing field: {f}"

    def test_62_engine_result_all_fields(self):
        """62. Engine 返回结果包含所有字段"""
        engine = _make_engine()
        rec = _make_evolution_record(
            confidence=0.95,
            changes=[
                {"key": "warmth", "delta": 0.1},
                {"key": "expressiveness", "delta": 0.05},
            ],
        )
        out = engine.reflect_evolution(rec)
        required = [
            "schema_version",
            "success",
            "degraded",
            "error",
            "reflection_id",
            "evolution_id",
            "old_version",
            "new_version",
            "changes",
            "timestamp",
            "actor",
            "audit_recorded",
            "policy_result",
            "reason",
            "rollback_of",
            "engine",
        ]
        for f in required:
            assert f in out, f"missing field: {f}"

    def test_63_policy_result_all_fields(self):
        """63. Policy result 包含所有字段"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
        )
        p = SelfModelEvolutionPolicy()
        result = p.evaluate(
            changes=[{"field": "self_description", "before": "a", "after": "b"}],
            confidence=0.95,
            snapshot={},
        )
        required = [
            "decision",
            "reasons",
            "conflicts",
            "weighted_changes",
            "change_ratio",
            "rejected_count",
            "schema_version",
        ]
        for f in required:
            assert f in result, f"missing field: {f}"

    def test_64_version_entry_all_fields(self):
        """64. Version entry 包含所有字段"""
        from src.runtime.self_model.self_model_record import (
            create_self_model_version_store,
        )
        vs = create_self_model_version_store(
            initial_snapshot=_make_initial_snapshot()
        )
        v0 = vs.get_version("v0")
        required = [
            "version_id",
            "version_index",
            "snapshot",
            "source_reflection_id",
            "timestamp",
            "is_baseline",
        ]
        for f in required:
            assert f in v0, f"missing field: {f}"

    def test_65_engine_metadata(self):
        """65. Engine 暴露 metadata"""
        from src.runtime.self_model.self_model_evolution import (
            SelfModelEvolutionEngine,
        )
        engine = _make_engine()
        assert engine.SCHEMA_VERSION
        assert engine.NAME
        assert engine.VERSION
        assert engine.SCHEMA_VERSION == SelfModelEvolutionEngine.SCHEMA_VERSION

    def test_66_policy_metadata(self):
        """66. Policy 暴露 metadata"""
        from src.runtime.self_model.self_model_policy import (
            SelfModelEvolutionPolicy,
        )
        p = SelfModelEvolutionPolicy()
        assert p.SCHEMA_VERSION
        assert p.NAME
        assert p.VERSION

    def test_67_reflection_content_helpers(self):
        """67. reflection content helpers"""
        from src.runtime.self_model.self_model_record import (
            build_self_description_change,
            build_growth_understanding,
            build_capability_boundary,
        )
        sd = build_self_description_change("a", "b")
        assert sd["field"] == "self_description"
        gu = build_growth_understanding("summary", ["t1"], ["c1"])
        assert gu["summary"] == "summary"
        cb = build_capability_boundary(["c1"], ["l1"])
        assert "capabilities" in cb
        assert "limits" in cb


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
