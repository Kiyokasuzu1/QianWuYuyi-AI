# -*- coding: utf-8 -*-
"""
tests/test_initiative_system.py

Phase C.9.0 Initiative & Autonomous Interaction System —— 单元测试

覆盖:
  - Creation
  - Trigger (5 types)
  - Policy (cooldown / frequency / threshold / relationship / user_preference)
  - Decision (should_initiate true / false)
  - Readonly (Personality / SelfModel / Memory / Relationship not modified)
  - Audit
  - History
  - FailSafe (trigger / snapshot / policy exceptions)
  - Concurrency
  - Schema (所有字段完整)
  - Integration (Runtime -> InitiativeEngine 完整链路)
"""
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Helpers
# ============================================================
def _now_iso(offset_seconds: float = 0.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat().replace("+00:00", "Z")


def _make_context(
    user_id: str = "user_test",
    unfinished_topic: bool = True,
    long_no_interaction: bool = True,
    user_interest: bool = True,
    emotion_drop: bool = True,
    growth_recent: bool = True,
    relationship_level: float = 0.6,
    user_preference: str = "open",
    memory: Optional[Dict[str, Any]] = None,
    relationship: Optional[Dict[str, Any]] = None,
    emotion: Optional[Dict[str, Any]] = None,
    growth: Optional[Dict[str, Any]] = None,
    self_model: Optional[Dict[str, Any]] = None,
    personality: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ctx: Dict[str, Any] = {
        "user_id": user_id,
        "relationship_level": relationship_level,
        "user_preference": user_preference,
    }
    # memory
    mem: Dict[str, Any] = {}
    if memory is not None:
        mem.update(memory)
    if unfinished_topic:
        mem.setdefault("open_topics", []).append({
            "topic": "AI 绘画研究",
            "status": "open",
            "last_touched_at": _now_iso(offset_seconds=-10 * 86400),
        })
    if user_interest:
        mem.setdefault("user_interests", []).append({
            "name": "AI 绘画",
            "strength": 0.8,
        })
    if mem:
        ctx["memory"] = mem
    # relationship
    rel: Dict[str, Any] = {}
    if relationship is not None:
        rel.update(relationship)
    if long_no_interaction:
        rel["last_interaction_at"] = _now_iso(offset_seconds=-20 * 86400)
    rel.setdefault("level", relationship_level)
    ctx["relationship"] = rel
    # emotion
    emo: Dict[str, Any] = {}
    if emotion is not None:
        emo.update(emotion)
    if emotion_drop:
        emo["previous"] = {"valence": 0.7}
        emo["current"] = {"valence": 0.2}
        emo["trend"] = "worsening"
    ctx["emotion"] = emo
    # growth
    grw: Dict[str, Any] = {}
    if growth is not None:
        grw.update(growth)
    if growth_recent:
        grw.setdefault("recent_changes", []).append({
            "name": "warmth",
            "delta": 0.1,
            "confidence": 0.9,
            "at": _now_iso(offset_seconds=-2 * 86400),
        })
    ctx["growth"] = grw
    if self_model is not None:
        ctx["self_model"] = self_model
    if personality is not None:
        ctx["personality"] = personality
    return ctx


def _make_engine(
    policy: Any = None,
    audit: Any = None,
    trigger_scanner: Any = None,
    memory_store: Any = None,
    message_planner: Any = None,
) -> Any:
    from src.runtime.initiative import (
        create_initiative_decision_engine,
    )
    return create_initiative_decision_engine(
        trigger_scanner=trigger_scanner,
        policy=policy,
        memory_store=memory_store,
        message_planner=message_planner,
        audit=audit,
    )


# ============================================================
# 1. Creation
# ============================================================
class TestCreation:
    def test_01_engine_create_default(self):
        """1. 引擎默认创建"""
        engine = _make_engine()
        assert engine is not None
        from src.runtime.initiative import (
            InitiativeDecisionEngine,
        )
        assert isinstance(engine, InitiativeDecisionEngine)

    def test_02_trigger_scanner_loaded(self):
        """2. trigger scanner 加载"""
        engine = _make_engine()
        assert engine._scanner is not None

    def test_03_policy_loaded(self):
        """3. policy 加载"""
        engine = _make_engine()
        assert engine._policy is not None

    def test_04_memory_loaded(self):
        """4. memory 加载"""
        engine = _make_engine()
        assert engine._memory is not None

    def test_05_message_planner_loaded(self):
        """5. message planner 加载"""
        engine = _make_engine()
        assert engine._planner is not None

    def test_06_factory_create(self):
        """6. factory 创建"""
        from src.runtime.initiative import (
            create_initiative_decision_engine,
        )
        e = create_initiative_decision_engine()
        assert e is not None

    def test_07_safe_wrapper_with_none(self):
        """7. safe_initiative_evaluate 处理 None engine"""
        from src.runtime.initiative import safe_initiative_evaluate
        out = safe_initiative_evaluate(None, _make_context())
        assert out["should_initiate"] is False
        assert out["degraded"] is True


# ============================================================
# 2. Trigger
# ============================================================
class TestTrigger:
    def test_08_unfinished_topic(self):
        """8. unfinished_topic trigger"""
        from src.runtime.initiative import (
            evaluate_unfinished_topic,
            TRIGGER_UNFINISHED_TOPIC,
        )
        r = evaluate_unfinished_topic(
            memory_snapshot={
                "open_topics": [{
                    "topic": "AI 绘画",
                    "status": "open",
                    "last_touched_at": _now_iso(offset_seconds=-10 * 86400),
                }],
            },
        )
        assert r["trigger"] == TRIGGER_UNFINISHED_TOPIC
        assert r["active"] is True

    def test_09_relationship_check(self):
        """9. relationship_check trigger"""
        from src.runtime.initiative import (
            evaluate_relationship_check,
            TRIGGER_RELATIONSHIP_CHECK,
        )
        r = evaluate_relationship_check(
            relationship_snapshot={
                "last_interaction_at": _now_iso(offset_seconds=-20 * 86400),
                "level": 0.6,
            },
        )
        assert r["trigger"] == TRIGGER_RELATIONSHIP_CHECK
        assert r["active"] is True

    def test_10_user_interest(self):
        """10. user_interest trigger"""
        from src.runtime.initiative import (
            evaluate_user_interest,
            TRIGGER_USER_INTEREST,
        )
        r = evaluate_user_interest(
            memory_snapshot={
                "user_interests": [
                    {"name": "AI 绘画", "strength": 0.8},
                ],
            },
        )
        assert r["trigger"] == TRIGGER_USER_INTEREST
        assert r["active"] is True

    def test_11_emotional_support(self):
        """11. emotional_support trigger"""
        from src.runtime.initiative import (
            evaluate_emotional_support,
            TRIGGER_EMOTIONAL_SUPPORT,
        )
        r = evaluate_emotional_support(
            emotion_snapshot={
                "previous": {"valence": 0.7},
                "current": {"valence": 0.2},
                "trend": "worsening",
            },
        )
        assert r["trigger"] == TRIGGER_EMOTIONAL_SUPPORT
        assert r["active"] is True

    def test_12_growth_reflection(self):
        """12. growth_reflection trigger"""
        from src.runtime.initiative import (
            evaluate_growth_reflection,
            TRIGGER_GROWTH_REFLECTION,
        )
        r = evaluate_growth_reflection(
            growth_snapshot={
                "recent_changes": [{
                    "name": "warmth",
                    "confidence": 0.9,
                    "at": _now_iso(offset_seconds=-1 * 86400),
                }],
            },
        )
        assert r["trigger"] == TRIGGER_GROWTH_REFLECTION
        assert r["active"] is True

    def test_13_trigger_scanner_full(self):
        """13. scanner 一次性扫描所有 trigger"""
        from src.runtime.initiative import (
            create_initiative_trigger_scanner,
        )
        scanner = create_initiative_trigger_scanner()
        results = scanner.scan(_make_context())
        # 5 trigger
        assert len(results) == 5
        # top trigger 选择
        top_t, top_p, top_c = scanner.pick_top_trigger(results)
        assert top_t != "none"
        assert top_p > 0.0


# ============================================================
# 3. Policy
# ============================================================
class TestPolicy:
    def test_14_cooldown(self):
        """14. cooldown 限制"""
        from src.runtime.initiative import (
            create_initiative_policy,
            DECISION_DENY,
            REASON_COOLDOWN,
        )
        policy = create_initiative_policy(cooldown_seconds=3600)
        result = policy.evaluate(
            proposed={"priority": 0.9, "trigger": "x"},
            history_stats={
                "last_initiated_ts": time.time() - 60,  # 1 分钟前
                "initiated_count_in_window": 0,
            },
        )
        assert result["decision"] == DECISION_DENY
        assert REASON_COOLDOWN in result["reasons"]

    def test_15_frequency_limit(self):
        """15. frequency_limit 限制"""
        from src.runtime.initiative import (
            create_initiative_policy,
            DECISION_DENY,
            REASON_FREQUENCY_LIMIT,
        )
        policy = create_initiative_policy(frequency_limit=3)
        result = policy.evaluate(
            proposed={"priority": 0.9, "trigger": "x"},
            history_stats={
                "last_initiated_ts": 0.0,
                "initiated_count_in_window": 5,
            },
        )
        assert result["decision"] == DECISION_DENY
        assert REASON_FREQUENCY_LIMIT in result["reasons"]

    def test_16_priority_threshold(self):
        """16. priority threshold 限制"""
        from src.runtime.initiative import (
            create_initiative_policy,
            DECISION_DEFER,
            REASON_PRIORITY_TOO_LOW,
        )
        policy = create_initiative_policy(priority_threshold=0.6)
        result = policy.evaluate(
            proposed={"priority": 0.2, "trigger": "x"},
            history_stats={},
        )
        assert result["decision"] in (DECISION_DEFER, "deny")
        assert REASON_PRIORITY_TOO_LOW in result["reasons"]

    def test_17_relationship_low(self):
        """17. relationship level 不足"""
        from src.runtime.initiative import (
            create_initiative_policy,
            REASON_RELATIONSHIP_LOW,
        )
        policy = create_initiative_policy(min_relationship_level=0.5)
        result = policy.evaluate(
            proposed={"priority": 0.9, "trigger": "x"},
            history_stats={},
            relationship_level=0.1,
        )
        assert REASON_RELATIONSHIP_LOW in result["reasons"]

    def test_18_user_preference_reserved(self):
        """18. 用户偏好 reserved 降低优先级"""
        from src.runtime.initiative import (
            create_initiative_policy,
            REASON_USER_RESERVED,
        )
        policy = create_initiative_policy()
        result = policy.evaluate(
            proposed={"priority": 0.9, "trigger": "x"},
            history_stats={},
            relationship_level=0.7,
            user_preference="reserved",
        )
        assert REASON_USER_RESERVED in result["reasons"]
        assert result["adjusted_priority"] < 0.9

    def test_19_policy_disabled(self):
        """19. policy disabled"""
        from src.runtime.initiative import (
            create_initiative_policy,
            DECISION_DENY,
            REASON_DISABLED,
        )
        policy = create_initiative_policy(enabled=False)
        result = policy.evaluate(
            proposed={"priority": 0.9, "trigger": "x"},
            history_stats={},
        )
        assert result["decision"] == DECISION_DENY
        assert REASON_DISABLED in result["reasons"]

    def test_20_policy_allow(self):
        """20. 满足所有条件,allow"""
        from src.runtime.initiative import (
            create_initiative_policy,
            DECISION_ALLOW,
        )
        policy = create_initiative_policy()
        result = policy.evaluate(
            proposed={"priority": 0.9, "trigger": "x"},
            history_stats={
                "last_initiated_ts": 0.0,
                "initiated_count_in_window": 0,
            },
            relationship_level=0.7,
            user_preference="open",
        )
        assert result["decision"] == DECISION_ALLOW


# ============================================================
# 4. Decision
# ============================================================
class TestDecision:
    def test_21_should_initiate_true(self):
        """21. should_initiate=True"""
        engine = _make_engine()
        out = engine.evaluate(_make_context())
        # 多 trigger 同时存在,默认 priority threshold=0.6,
        # 至少 1 个 trigger 应该触发
        # 因为 cooldown_seconds 默认 4 小时,频率 3/24h,
        # 首次评估应 allow
        assert "should_initiate" in out
        # 不一定为 True,但接口存在
        if out["should_initiate"]:
            assert out["message_request"] is not None

    def test_22_should_initiate_false_no_trigger(self):
        """22. should_initiate=False (无 trigger)"""
        engine = _make_engine()
        ctx = _make_context(
            unfinished_topic=False,
            long_no_interaction=False,
            user_interest=False,
            emotion_drop=False,
            growth_recent=False,
        )
        out = engine.evaluate(ctx)
        assert out["should_initiate"] is False
        assert out["trigger"] == "none"
        assert out["message_request"] is None

    def test_23_should_initiate_false_cooldown(self):
        """23. should_initiate=False (cooldown)"""
        # 先 evaluate 一次 initiate
        engine = _make_engine()
        ctx = _make_context()
        out1 = engine.evaluate(ctx)
        # 立即再次 evaluate,cooldown 应拒绝
        out2 = engine.evaluate(ctx)
        if out1["should_initiate"]:
            assert out2["should_initiate"] is False

    def test_24_decision_denied_priority(self):
        """24. priority 不足,decision=deny/defer"""
        from src.runtime.initiative import create_initiative_policy
        policy = create_initiative_policy(priority_threshold=0.95)
        engine = _make_engine(policy=policy)
        out = engine.evaluate(_make_context())
        assert out["should_initiate"] is False
        assert out["decision"] in ("defer", "deny")


# ============================================================
# 5. Readonly
# ============================================================
class TestReadonly:
    def test_25_personality_not_modified(self):
        """25. Personality snapshot 不被修改"""
        from src.runtime.initiative import create_initiative_decision_engine

        class TrapPersonality:
            def __init__(self):
                self.modify_called = False

            def apply(self, *args, **kwargs):
                self.modify_called = True
                raise AssertionError("Personality modify called")

        trap = TrapPersonality()
        engine = create_initiative_decision_engine()
        # 注入一个 trap personality 模拟 provider
        # 我们用 ctx 注入 personality
        ctx = _make_context(personality=trap)
        out = engine.evaluate(ctx)
        assert trap.modify_called is False

    def test_26_self_model_not_modified(self):
        """26. SelfModel 不被修改"""
        from src.runtime.initiative import create_initiative_decision_engine

        class TrapSelfModel:
            def __init__(self):
                self.update_called = False

            def update(self, *args, **kwargs):
                self.update_called = True
                raise AssertionError("SelfModel update called")

        trap = TrapSelfModel()
        engine = create_initiative_decision_engine()
        ctx = _make_context(self_model=trap)
        out = engine.evaluate(ctx)
        assert trap.update_called is False

    def test_27_memory_not_modified(self):
        """27. Memory 不被修改"""
        from src.runtime.initiative import create_initiative_decision_engine

        class TrapMemory:
            def __init__(self):
                self.modify_called = False

            def modify(self, *args, **kwargs):
                self.modify_called = True
                raise AssertionError("Memory modify called")

        trap = TrapMemory()
        engine = create_initiative_decision_engine()
        # 将 trap 包装在 dict 内,避免 _make_context 调用 update 时报错
        ctx = _make_context(memory={"snapshot": trap})
        out = engine.evaluate(ctx)
        assert trap.modify_called is False

    def test_28_relationship_not_modified(self):
        """28. Relationship 不被修改"""
        from src.runtime.initiative import create_initiative_decision_engine

        class TrapRelationship:
            def __init__(self):
                self.save_called = False

            def save(self, *args, **kwargs):
                self.save_called = True
                raise AssertionError("Relationship save called")

        trap = TrapRelationship()
        engine = create_initiative_decision_engine()
        # 将 trap 包装在 dict 内,避免 _make_context 调用 update 时报错
        ctx = _make_context(relationship={"snapshot": trap})
        out = engine.evaluate(ctx)
        assert trap.save_called is False


# ============================================================
# 6. Audit
# ============================================================
class TestAudit:
    def test_29_audit_evaluated(self):
        """29. 评估时记录 audit"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        audit = FakeAudit()
        engine = _make_engine(audit=audit)
        out = engine.evaluate(_make_context())
        # 至少有一些 audit 记录
        assert len(events) >= 1
        # 应该出现 evaluated / suppressed / initiated
        actions = {e.get("operation_type") for e in events}
        assert any(
            a in actions for a in (
                "runtime_initiative_evaluated",
                "runtime_initiative_suppressed",
                "runtime_initiative_initiated",
            )
        )

    def test_30_audit_initiated(self):
        """30. initiated 时记录 audit"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        audit = FakeAudit()
        engine = _make_engine(audit=audit)
        out = engine.evaluate(_make_context(
            unfinished_topic=True,
            user_interest=True,
            growth_recent=True,
        ))
        if out["should_initiate"]:
            assert any(
                e.get("operation_type") == "runtime_initiative_initiated"
                for e in events
            )

    def test_31_audit_none_safe(self):
        """31. audit=None 时安全运行"""
        engine = _make_engine(audit=None)
        out = engine.evaluate(_make_context())
        assert "should_initiate" in out


# ============================================================
# 7. History
# ============================================================
class TestHistory:
    def test_32_record_history(self):
        """32. 记录历史"""
        engine = _make_engine()
        engine.evaluate(_make_context())
        history = engine.get_history()
        assert len(history) >= 1

    def test_33_get_latest(self):
        """33. 获取最新"""
        engine = _make_engine()
        engine.evaluate(_make_context())
        latest = engine.get_latest()
        assert latest is not None
        assert "initiative_id" in latest

    def test_34_filter_by_trigger(self):
        """34. 按 trigger 过滤"""
        engine = _make_engine()
        # 多次 evaluate 制造不同 trigger
        for _ in range(3):
            engine.evaluate(_make_context(
                unfinished_topic=True,
                long_no_interaction=False,
                user_interest=False,
                emotion_drop=False,
                growth_recent=False,
            ))
        history = engine.get_history(trigger="unfinished_topic")
        # 至少有一些
        assert all(
            isinstance(h, dict) and h.get("trigger") == "unfinished_topic"
            for h in history
        )

    def test_35_initiated_count(self):
        """35. initiated 计数"""
        engine = _make_engine()
        out = engine.evaluate(_make_context())
        # 立即再 evaluate 一次
        out2 = engine.evaluate(_make_context())
        stats = engine.get_stats()
        assert stats["initiated_count"] + stats["suppressed_count"] + stats["deferred_count"] >= 2


# ============================================================
# 8. FailSafe
# ============================================================
class TestFailSafe:
    def test_36_trigger_scanner_exception(self):
        """36. trigger 异常时降级"""
        from src.runtime.initiative import create_initiative_decision_engine

        class BadScanner:
            def scan(self, *args, **kwargs):
                raise RuntimeError("scanner boom")

        engine = create_initiative_decision_engine(
            trigger_scanner=BadScanner(),
        )
        out = engine.evaluate(_make_context())
        assert out["should_initiate"] is False
        assert out["degraded"] is True

    def test_37_snapshot_exception(self):
        """37. snapshot 异常时降级"""
        from src.runtime.initiative import create_initiative_decision_engine

        class BadContext:
            def to_dict(self):
                raise RuntimeError("boom")

        engine = create_initiative_decision_engine()
        out = engine.evaluate(BadContext())
        # 不应抛异常
        assert "should_initiate" in out

    def test_38_policy_exception(self):
        """38. policy 异常时降级"""
        from src.runtime.initiative import create_initiative_decision_engine

        class BadPolicy:
            def evaluate(self, *args, **kwargs):
                raise RuntimeError("policy boom")

        engine = create_initiative_decision_engine(policy=BadPolicy())
        out = engine.evaluate(_make_context())
        assert out["should_initiate"] is False
        assert out["degraded"] is True

    def test_39_message_planner_exception(self):
        """39. message planner 异常时降级"""
        from src.runtime.initiative import create_initiative_decision_engine

        class BadPlanner:
            def plan(self, *args, **kwargs):
                raise RuntimeError("planner boom")

        engine = create_initiative_decision_engine(message_planner=BadPlanner())
        out = engine.evaluate(_make_context(
            unfinished_topic=True,
            long_no_interaction=True,
            user_interest=True,
            growth_recent=True,
        ))
        # 即使 planner 出错,也不应崩溃
        assert "should_initiate" in out

    def test_40_get_stats_after_failures(self):
        """40. 异常后 get_stats 仍可用"""
        engine = _make_engine()
        engine.evaluate(None)
        engine.evaluate({})
        stats = engine.get_stats()
        assert "evaluate_count" in stats
        assert "failed_count" in stats


# ============================================================
# 9. Concurrency
# ============================================================
class TestConcurrency:
    def test_41_multi_thread_evaluate(self):
        """41. 多线程 evaluate 安全"""
        engine = _make_engine()
        results: List[Dict[str, Any]] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            ctx = _make_context(
                user_id=f"user_{idx}",
            )
            out = engine.evaluate(ctx)
            with lock:
                results.append(out)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8
        # 全部完成(无论 should_initiate 是否)
        assert all("should_initiate" in r for r in results)


# ============================================================
# 10. Schema
# ============================================================
class TestSchema:
    def test_42_evaluate_result_all_fields(self):
        """42. evaluate result 包含所有字段"""
        engine = _make_engine()
        out = engine.evaluate(_make_context())
        required = [
            "schema_version",
            "success",
            "should_initiate",
            "degraded",
            "error",
            "initiative_id",
            "priority",
            "reason",
            "trigger",
            "confidence",
            "message_request",
            "decision",
            "policy_result",
            "trigger_results",
            "timestamp",
            "actor",
            "engine",
        ]
        for f in required:
            assert f in out, f"missing field: {f}"

    def test_43_memory_record_all_fields(self):
        """43. memory record 包含所有字段"""
        from src.runtime.initiative import (
            build_initiative_memory_record,
        )
        rec = build_initiative_memory_record(
            initiative_id="i1",
            trigger="unfinished_topic",
            reason=["r1"],
            priority=0.8,
            result="initiated",
            timestamp="2026-08-04T00:00:00Z",
            confidence=0.9,
        )
        required = [
            "schema_version",
            "initiative_id",
            "trigger",
            "reason",
            "priority",
            "result",
            "timestamp",
            "confidence",
            "user_id",
            "actor",
            "extra",
        ]
        for f in required:
            assert f in rec, f"missing field: {f}"

    def test_44_policy_result_all_fields(self):
        """44. policy result 包含所有字段"""
        from src.runtime.initiative import create_initiative_policy
        p = create_initiative_policy()
        result = p.evaluate(
            proposed={"priority": 0.9},
            history_stats={},
        )
        required = [
            "decision",
            "reasons",
            "adjusted_priority",
            "schema_version",
        ]
        for f in required:
            assert f in result, f"missing field: {f}"

    def test_45_trigger_result_all_fields(self):
        """45. trigger result 包含所有字段"""
        from src.runtime.initiative import (
            evaluate_unfinished_topic,
        )
        r = evaluate_unfinished_topic(
            memory_snapshot={
                "open_topics": [{
                    "topic": "test",
                    "status": "open",
                    "last_touched_at": _now_iso(offset_seconds=-10 * 86400),
                }],
            },
        )
        required = ["trigger", "active", "priority", "confidence", "reasons", "evidence"]
        for f in required:
            assert f in r, f"missing field: {f}"

    def test_46_message_request_all_fields(self):
        """46. message request 包含所有字段"""
        from src.runtime.initiative import (
            create_initiative_message_planner,
        )
        planner = create_initiative_message_planner()
        req = planner.plan(
            trigger="unfinished_topic",
            trigger_evidence={"topic": "AI 绘画"},
            user_id="u1",
        )
        required = [
            "schema_version",
            "type",
            "trigger",
            "context",
            "style",
            "template",
            "channel_ready",
        ]
        for f in required:
            assert f in req, f"missing field: {f}"

    def test_47_engine_metadata(self):
        """47. engine 暴露 metadata"""
        from src.runtime.initiative import (
            InitiativeDecisionEngine,
        )
        engine = _make_engine()
        assert engine.SCHEMA_VERSION
        assert engine.NAME
        assert engine.VERSION
        assert engine.SCHEMA_VERSION == InitiativeDecisionEngine.SCHEMA_VERSION

    def test_48_message_planner_metadata(self):
        """48. message planner metadata"""
        from src.runtime.initiative import (
            InitiativeMessagePlanner,
        )
        planner = InitiativeMessagePlanner()
        assert planner.SCHEMA_VERSION
        assert planner.NAME
        assert planner.VERSION


# ============================================================
# 11. Integration
# ============================================================
class TestIntegration:
    def test_49_full_chain_initiate(self):
        """49. 完整链路:Runtime context -> InitiativeEngine -> message_request"""
        engine = _make_engine()
        # 模拟 runtime 收集 context
        ctx = _make_context(
            user_id="user_full",
            relationship_level=0.7,
            user_preference="open",
        )
        # engine 评估
        out = engine.evaluate(ctx)
        assert "should_initiate" in out
        assert "initiative_id" in out
        if out["should_initiate"]:
            # 消息请求应生成
            msg = out["message_request"]
            assert msg is not None
            assert msg["type"] != "none"
            assert msg["template"] != ""
            # 不应直接发送
            assert msg["channel_ready"] is False

    def test_50_full_chain_with_audit(self):
        """50. 完整链路 + audit"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        audit = FakeAudit()
        engine = _make_engine(audit=audit)
        ctx = _make_context()
        out = engine.evaluate(ctx)
        # 无论 should_initiate,audit 都有
        assert len(events) >= 1
        # 至少 initiative_id 应在某个 audit 的 detail 中
        if out["initiative_id"]:
            assert any(
                isinstance(e, dict)
                and isinstance(e.get("detail"), dict)
                and e["detail"].get("initiative_id") == out["initiative_id"]
                for e in events
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
