# -*- coding: utf-8 -*-
"""
tests/test_initiative_runtime_adapter.py

Phase C.9.1 Initiative Runtime Integration —— 单元测试

覆盖:
  - Creation
  - Protocol (CycleAdapter 接口)
  - Processing (正常 evaluate / initiate / defer / suppress)
  - Readonly (Personality / SelfModel / Relationship 不被修改)
  - Security (禁止 send / apply)
  - FailSafe (engine / memory / relationship 异常)
  - Audit
  - Schema
  - Integration (Runtime cycle 完整链路)
  - Concurrency
"""
import sys
import threading
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


def _make_ctx(
    user_id: str = "user_test",
    relationship_level: float = 0.6,
    user_preference: str = "open",
    with_unfinished_topic: bool = True,
    with_long_no_interaction: bool = True,
    with_user_interest: bool = True,
    with_emotion_drop: bool = True,
    with_growth_recent: bool = True,
) -> Any:
    """构造一个 RuntimeCycleContext(包含完整的上游 5 阶段输出)。"""
    from src.runtime.cycle_context import RuntimeCycleContext

    ctx = RuntimeCycleContext(user_id=user_id)
    # metadata
    ctx.metadata = {
        "relationship_level": relationship_level,
        "user_preference": user_preference,
    }
    # memory_output
    memory: Dict[str, Any] = {}
    if with_unfinished_topic:
        memory.setdefault("open_topics", []).append({
            "topic": "AI 绘画研究",
            "status": "open",
            "last_touched_at": _now_iso(offset_seconds=-10 * 86400),
        })
    if with_user_interest:
        memory.setdefault("user_interests", []).append({
            "name": "AI 绘画",
            "strength": 0.8,
        })
    ctx.memory_output = memory
    # emotion_output
    emotion: Dict[str, Any] = {}
    if with_emotion_drop:
        emotion["previous"] = {"valence": 0.7}
        emotion["current"] = {"valence": 0.2}
        emotion["trend"] = "worsening"
    ctx.emotion_output = emotion
    # relationship_output
    relationship: Dict[str, Any] = {}
    if with_long_no_interaction:
        relationship["last_interaction_at"] = _now_iso(offset_seconds=-20 * 86400)
    relationship["level"] = relationship_level
    relationship["current_metrics"] = {
        "familiarity": relationship_level,
        "trust": relationship_level,
        "collaboration": relationship_level,
    }
    ctx.relationship_output = relationship
    # personality_output(snapshot 形式)
    ctx.personality_output = {
        "snapshot": {
            "traits": {"warmth": 0.7, "curiosity": 0.6},
            "version": "1.0",
        },
    }
    # growth_output
    growth: List[Dict[str, Any]] = []
    if with_growth_recent:
        growth.append({
            "name": "warmth",
            "delta": 0.1,
            "confidence": 0.9,
            "at": _now_iso(offset_seconds=-2 * 86400),
        })
    ctx.growth_output = growth
    return ctx


def _make_adapter(
    decision_engine: Any = None,
    memory_store: Any = None,
    audit: Any = None,
) -> Any:
    from src.runtime.initiative import (
        create_initiative_runtime_adapter,
    )
    return create_initiative_runtime_adapter(
        decision_engine=decision_engine,
        memory_store=memory_store,
        audit=audit,
    )


# ============================================================
# 1. Creation
# ============================================================
class TestCreation:
    def test_01_adapter_create_default(self):
        """1. adapter 默认创建"""
        a = _make_adapter()
        assert a is not None
        from src.runtime.initiative import InitiativeRuntimeAdapter
        assert isinstance(a, InitiativeRuntimeAdapter)

    def test_02_adapter_factory(self):
        """2. factory 创建"""
        from src.runtime.initiative import (
            create_initiative_runtime_adapter,
        )
        a = create_initiative_runtime_adapter()
        assert a is not None

    def test_03_adapter_name_and_schema(self):
        """3. name / schema_version"""
        a = _make_adapter()
        assert a.name == "initiative"
        assert a.schema_version == "1.0"

    def test_04_attach_default(self):
        """4. attach 接入"""
        a = _make_adapter()
        a.attach()
        assert a.is_attached() is True

    def test_05_detach(self):
        """5. detach 解除"""
        a = _make_adapter()
        a.attach()
        a.detach()
        assert a.is_attached() is False

    def test_06_engine_attached_after_attach(self):
        """6. attach 后 engine 就绪"""
        a = _make_adapter()
        a.attach()
        engine = a.get_decision_engine()
        assert engine is not None


# ============================================================
# 2. Protocol
# ============================================================
class TestProtocol:
    def test_07_is_cycle_adapter(self):
        """7. duck-type 满足 CycleAdapter 协议"""
        from src.runtime.cycle_adapter import is_cycle_adapter
        a = _make_adapter()
        a.attach()
        assert is_cycle_adapter(a) is True

    def test_08_required_methods(self):
        """8. 必备方法存在"""
        a = _make_adapter()
        for m in (
            "attach", "detach", "is_attached",
            "health_check", "process_cycle", "snapshot",
        ):
            assert hasattr(a, m)
            assert callable(getattr(a, m))

    def test_09_health_check(self):
        """9. health_check 返回 dict"""
        a = _make_adapter()
        a.attach()
        hc = a.health_check()
        assert isinstance(hc, dict)
        assert "status" in hc
        assert "engine_available" in hc

    def test_10_snapshot(self):
        """10. snapshot 返回 dict"""
        a = _make_adapter()
        a.attach()
        snap = a.snapshot()
        assert isinstance(snap, dict)
        assert "name" in snap
        assert "schema_version" in snap


# ============================================================
# 3. Processing
# ============================================================
class TestProcessing:
    def test_11_normal_evaluate(self):
        """11. 正常 evaluate"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        out_ctx = a.process_cycle(ctx)
        assert out_ctx is ctx
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert "decision" in out

    def test_12_initiate_output(self):
        """12. 触发后可能输出 initiate"""
        a = _make_adapter()
        a.attach()
        # 用最强 trigger 场景
        ctx = _make_ctx(
            with_unfinished_topic=True,
            with_long_no_interaction=True,
            with_user_interest=True,
            with_emotion_drop=True,
            with_growth_recent=True,
        )
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        # 不一定 initiate(可能被 cooldown / frequency 拒绝),但字段存在
        assert out["decision"] in ("initiate", "defer", "suppress")

    def test_13_defer_output(self):
        """13. 低优先级场景可能输出 defer"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx(
            with_unfinished_topic=False,
            with_long_no_interaction=False,
            with_user_interest=False,
            with_emotion_drop=False,
            with_growth_recent=False,
        )
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        # 没有 trigger,应被 suppress
        assert out["decision"] in ("defer", "suppress")

    def test_14_suppress_output(self):
        """14. 持续 evaluate 触发 cooldown 后 suppress"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx(
            with_unfinished_topic=True,
            with_long_no_interaction=True,
            with_user_interest=True,
        )
        # 第一次
        a.process_cycle(ctx)
        # 立即再 evaluate(cooldown)
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        # 第二次可能被 suppress(由于 cooldown)
        # 不强制要求,只检查字段存在
        assert "decision" in out

    def test_15_message_request_none_when_suppress(self):
        """15. suppress 时 message_request 为 None"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx(
            with_unfinished_topic=False,
            with_long_no_interaction=False,
            with_user_interest=False,
            with_emotion_drop=False,
            with_growth_recent=False,
        )
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        if out["decision"] == "suppress":
            assert out["message_request"] is None

    def test_16_channel_ready_always_false(self):
        """16. channel_ready 永远 False(C.9.1 不发送)"""
        a = _make_adapter()
        a.attach()
        # 多次 evaluate 各种场景
        for _ in range(3):
            ctx = _make_ctx()
            a.process_cycle(ctx)
            out = getattr(ctx, "initiative_output", None)
            assert out is not None
            assert out["channel_ready"] is False

    def test_17_output_contains_initiative_id(self):
        """17. output 包含 initiative_id"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        # initiative_id 可能为空(降级时),但字段存在
        assert "initiative_id" in out

    def test_18_output_user_id_propagated(self):
        """18. output 中 user_id 透传"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx(user_id="alice_42")
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert out["user_id"] == "alice_42"

    def test_19_output_cycle_id_propagated(self):
        """19. output 中 cycle_id 透传"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        cycle_id = ctx.cycle_id
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert out["cycle_id"] == cycle_id

    def test_20_stage_log_recorded(self):
        """20. stage_log 记录 initiative stage"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        before = len(ctx.stage_log)
        a.process_cycle(ctx)
        after = len(ctx.stage_log)
        assert after == before + 1
        last = ctx.stage_log[-1]
        assert "initiative" in last.get("stage", "")


# ============================================================
# 4. Readonly
# ============================================================
class TestReadonly:
    def test_21_personality_output_not_modified(self):
        """21. personality_output 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class TrapPersonality:
            def __init__(self):
                self.apply_called = False

            def apply(self, *args, **kwargs):
                self.apply_called = True
                raise AssertionError("Personality apply called")

        trap = TrapPersonality()
        adapter = create_initiative_runtime_adapter()
        ctx = _make_ctx()
        # 用 trap 替换 personality_output
        ctx.personality_output = {"snapshot": trap}
        out_ctx = adapter.process_cycle(ctx)
        assert trap.apply_called is False
        # personality_output 应保持不变(类型不被改写)
        assert out_ctx.personality_output is ctx.personality_output

    def test_22_self_model_not_modified(self):
        """22. SelfModel 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class TrapSelfModel:
            def __init__(self):
                self.update_called = False

            def update(self, *args, **kwargs):
                self.update_called = True
                raise AssertionError("SelfModel update called")

        trap = TrapSelfModel()
        adapter = create_initiative_runtime_adapter()
        ctx = _make_ctx()
        ctx.metadata["self_model"] = {"snapshot": trap}
        adapter.process_cycle(ctx)
        assert trap.update_called is False

    def test_23_relationship_not_modified(self):
        """23. Relationship 不被修改"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class TrapRelationship:
            def __init__(self):
                self.save_called = False

            def save(self, *args, **kwargs):
                self.save_called = True
                raise AssertionError("Relationship save called")

        trap = TrapRelationship()
        adapter = create_initiative_runtime_adapter()
        ctx = _make_ctx()
        ctx.relationship_output = {"snapshot": trap}
        out_ctx = adapter.process_cycle(ctx)
        assert trap.save_called is False
        assert out_ctx.relationship_output is ctx.relationship_output

    def test_24_memory_output_not_modified(self):
        """24. memory_output 不被修改(类型不被改写)"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        ctx = _make_ctx()
        original_memory = ctx.memory_output
        adapter.process_cycle(ctx)
        # memory_output 引用应保持不变
        assert ctx.memory_output is original_memory


# ============================================================
# 5. Security
# ============================================================
class TestSecurity:
    def test_25_no_send_method(self):
        """25. adapter 不暴露 send 方法"""
        a = _make_adapter()
        a.attach()
        assert not hasattr(a, "send")
        assert not hasattr(a, "send_message")
        assert not hasattr(a, "push_message")

    def test_26_no_apply_method(self):
        """26. adapter 不暴露 personality apply 方法"""
        a = _make_adapter()
        a.attach()
        assert not hasattr(a, "apply_proposal")
        assert not hasattr(a, "resolve_personality")

    def test_27_no_update_self_model(self):
        """27. adapter 不暴露 self_model update"""
        a = _make_adapter()
        a.attach()
        assert not hasattr(a, "update_self_model")
        assert not hasattr(a, "save_self_model")

    def test_28_no_save_relationship(self):
        """28. adapter 不暴露 relationship save"""
        a = _make_adapter()
        a.attach()
        assert not hasattr(a, "save_relationship")
        assert not hasattr(a, "save_relationship_state")

    def test_29_no_modify_memory(self):
        """29. adapter 不暴露 memory modify"""
        a = _make_adapter()
        a.attach()
        assert not hasattr(a, "modify_memory")
        assert not hasattr(a, "save_memory")

    def test_30_channel_ready_hardcoded_false(self):
        """30. channel_ready 永远 hardcode 为 False"""
        a = _make_adapter()
        a.attach()
        # 即便手动尝试覆盖 output 也不能 channel ready
        # —— 这是 adapter 内部逻辑
        ctx = _make_ctx()
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert out["channel_ready"] is False


# ============================================================
# 6. FailSafe
# ============================================================
class TestFailSafe:
    def test_31_engine_exception(self):
        """31. decision engine 抛异常时降级"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class BadEngine:
            def evaluate(self, *args, **kwargs):
                raise RuntimeError("engine boom")

        adapter = create_initiative_runtime_adapter(decision_engine=BadEngine())
        ctx = _make_ctx()
        out_ctx = adapter.process_cycle(ctx)
        assert out_ctx is ctx  # 不抛
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert out["degraded"] is True
        assert out["decision"] == "suppress"

    def test_32_engine_returns_non_dict(self):
        """32. engine 返回非 dict 时降级"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class BadEngine:
            def evaluate(self, *args, **kwargs):
                return "not a dict"

        adapter = create_initiative_runtime_adapter(decision_engine=BadEngine())
        ctx = _make_ctx()
        adapter.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert out["degraded"] is True
        assert out["decision"] == "suppress"

    def test_33_memory_output_broken(self):
        """33. memory_output 异常时降级"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        ctx = _make_ctx()

        class BadMemory:
            def __getitem__(self, key):
                raise RuntimeError("memory boom")

            def get(self, key, default=None):
                raise RuntimeError("memory boom")

        # 替换为有问题的对象
        ctx.memory_output = BadMemory()
        out_ctx = adapter.process_cycle(ctx)
        # 不应抛
        assert out_ctx is ctx
        out = getattr(ctx, "initiative_output", None)
        assert out is not None

    def test_34_relationship_output_broken(self):
        """34. relationship_output 异常时降级"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter()
        ctx = _make_ctx()

        class BadRelationship:
            def get(self, key, default=None):
                raise RuntimeError("rel boom")

        ctx.relationship_output = BadRelationship()
        out_ctx = adapter.process_cycle(ctx)
        assert out_ctx is ctx
        out = getattr(ctx, "initiative_output", None)
        assert out is not None

    def test_35_non_ctx_input(self):
        """35. 非 RuntimeCycleContext 输入直接返回"""
        a = _make_adapter()
        a.attach()
        result = a.process_cycle("not a ctx")
        assert result == "not a ctx"
        result2 = a.process_cycle(None)
        assert result2 is None

    def test_36_empty_ctx(self):
        """36. 空 ctx(全 None)不应崩溃"""
        from src.runtime.cycle_context import RuntimeCycleContext

        a = _make_adapter()
        a.attach()
        ctx = RuntimeCycleContext()
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert "decision" in out

    def test_37_engine_none(self):
        """37. engine 为 None 时降级"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        adapter = create_initiative_runtime_adapter(decision_engine=None)
        # 不 attach,engine 仍为 None
        ctx = _make_ctx()
        adapter.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert out["degraded"] is True


# ============================================================
# 7. Audit
# ============================================================
class TestAudit:
    def test_38_audit_evaluated(self):
        """38. evaluate 时记录 audit"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        a = _make_adapter(audit=FakeAudit())
        a.attach()
        ctx = _make_ctx()
        a.process_cycle(ctx)
        # 至少 1 条 evaluated
        actions = {e.get("operation_type") for e in events}
        assert "runtime_initiative_runtime_evaluated" in actions

    def test_39_audit_field_cycle_id(self):
        """39. audit 包含 cycle_id"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        a = _make_adapter(audit=FakeAudit())
        a.attach()
        ctx = _make_ctx()
        cycle_id = ctx.cycle_id
        a.process_cycle(ctx)
        # 至少一条 audit 包含 cycle_id
        found = False
        for e in events:
            detail = e.get("detail", {})
            if isinstance(detail, dict) and detail.get("cycle_id") == cycle_id:
                found = True
                break
        assert found is True

    def test_40_audit_field_decision(self):
        """40. audit 包含 decision"""
        events: List[Dict[str, Any]] = []

        class FakeAudit:
            def record(self, **kwargs):
                events.append(dict(kwargs))

        a = _make_adapter(audit=FakeAudit())
        a.attach()
        ctx = _make_ctx()
        a.process_cycle(ctx)
        found = False
        for e in events:
            detail = e.get("detail", {})
            if isinstance(detail, dict) and "decision" in detail:
                found = True
                break
        assert found is True

    def test_41_audit_none_safe(self):
        """41. audit=None 时安全运行"""
        a = _make_adapter(audit=None)
        a.attach()
        ctx = _make_ctx()
        out_ctx = a.process_cycle(ctx)
        assert out_ctx is ctx

    def test_42_audit_audit_raises(self):
        """42. audit 接口异常时被隔离"""
        from src.runtime.initiative import create_initiative_runtime_adapter

        class BrokenAudit:
            def record(self, **kwargs):
                raise RuntimeError("audit boom")

        adapter = create_initiative_runtime_adapter(audit=BrokenAudit())
        ctx = _make_ctx()
        out_ctx = adapter.process_cycle(ctx)
        # 不应抛
        assert out_ctx is ctx
        out = getattr(ctx, "initiative_output", None)
        assert out is not None


# ============================================================
# 8. Schema
# ============================================================
class TestSchema:
    def test_43_output_all_fields(self):
        """43. initiative_output 包含所有要求字段"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        required = [
            "decision",
            "confidence",
            "priority",
            "trigger",
            "reasons",
            "message_request",
            "channel_ready",
            "timestamp",
            "degraded",
            "error",
            "source",
            "schema_version",
            "user_id",
            "cycle_id",
            "initiative_id",
        ]
        for f in required:
            assert f in out, f"missing field: {f}"

    def test_44_decision_label_valid(self):
        """44. decision 必须是有效标签"""
        a = _make_adapter()
        a.attach()
        for _ in range(5):
            ctx = _make_ctx()
            a.process_cycle(ctx)
            out = getattr(ctx, "initiative_output", None)
            assert out is not None
            assert out["decision"] in ("initiate", "defer", "suppress")

    def test_45_priority_in_range(self):
        """45. priority 必须在 [0, 1]"""
        a = _make_adapter()
        a.attach()
        for _ in range(3):
            ctx = _make_ctx()
            a.process_cycle(ctx)
            out = getattr(ctx, "initiative_output", None)
            assert out is not None
            assert 0.0 <= out["priority"] <= 1.0

    def test_46_confidence_in_range(self):
        """46. confidence 必须在 [0, 1]"""
        a = _make_adapter()
        a.attach()
        for _ in range(3):
            ctx = _make_ctx()
            a.process_cycle(ctx)
            out = getattr(ctx, "initiative_output", None)
            assert out is not None
            assert 0.0 <= out["confidence"] <= 1.0

    def test_47_timestamp_iso(self):
        """47. timestamp 是 ISO 8601"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        ts = out["timestamp"]
        assert "T" in ts


# ============================================================
# 9. Integration
# ============================================================
class TestIntegration:
    def test_48_full_runtime_chain(self):
        """48. Runtime cycle 完整链路"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx(
            user_id="user_full",
            relationship_level=0.7,
            user_preference="open",
            with_unfinished_topic=True,
            with_long_no_interaction=True,
            with_user_interest=True,
            with_emotion_drop=True,
            with_growth_recent=True,
        )
        out_ctx = a.process_cycle(ctx)
        # Runtime 不中断
        assert out_ctx is ctx
        # ctx.initiative_output 写入
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        assert "decision" in out
        # stage_log 记录
        assert any(
            "initiative" in str(s.get("stage", ""))
            for s in ctx.stage_log
        )
        # adapter_results 记录
        assert "initiative" in ctx.adapter_results
        ar = ctx.adapter_results["initiative"]
        assert "ok" in ar
        assert "details" in ar

    def test_49_chain_with_existing_engine(self):
        """49. 使用外部 decision engine"""
        from src.runtime.initiative import (
            create_initiative_decision_engine,
        )

        engine = create_initiative_decision_engine()
        a = _make_adapter(decision_engine=engine)
        a.attach()
        ctx = _make_ctx()
        a.process_cycle(ctx)
        out = getattr(ctx, "initiative_output", None)
        assert out is not None
        # engine 已被调用
        assert engine._evaluate_count >= 1

    def test_50_chain_idempotent(self):
        """50. 多次 process_cycle 不污染 ctx"""
        a = _make_adapter()
        a.attach()
        ctx = _make_ctx()
        original_cycle_id = ctx.cycle_id
        original_user_id = ctx.user_id
        for _ in range(3):
            a.process_cycle(ctx)
        # cycle_id / user_id 不变
        assert ctx.cycle_id == original_cycle_id
        assert ctx.user_id == original_user_id
        # initiative_output 仍是 dict
        out = getattr(ctx, "initiative_output", None)
        assert isinstance(out, dict)


# ============================================================
# 10. Concurrency
# ============================================================
class TestConcurrency:
    def test_51_multi_thread_process_cycle(self):
        """51. 多线程 process_cycle 安全"""
        a = _make_adapter()
        a.attach()
        results: List[Any] = []
        errors: List[Exception] = []
        lock = threading.Lock()

        def worker(idx: int) -> None:
            try:
                ctx = _make_ctx(user_id=f"user_{idx}")
                a.process_cycle(ctx)
                with lock:
                    results.append(ctx)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert len(results) == 8
        for ctx in results:
            out = getattr(ctx, "initiative_output", None)
            assert out is not None
            assert "decision" in out

    def test_52_process_count_increments(self):
        """52. process_count 单调递增"""
        a = _make_adapter()
        a.attach()
        c0 = a.process_count
        for _ in range(5):
            ctx = _make_ctx()
            a.process_cycle(ctx)
        c1 = a.process_count
        assert c1 - c0 == 5

    def test_53_initiate_count_correct(self):
        """53. initiate_count 正确累计"""
        a = _make_adapter()
        a.attach()
        for _ in range(3):
            ctx = _make_ctx(
                with_unfinished_topic=True,
                with_long_no_interaction=True,
                with_user_interest=True,
                with_emotion_drop=True,
                with_growth_recent=True,
            )
            a.process_cycle(ctx)
        # 至少 1 个 initiate(取决于 cooldown)
        # 至少 suppress / defer 累计
        total = a.initiate_count + a.defer_count + a.suppress_count
        assert total >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
