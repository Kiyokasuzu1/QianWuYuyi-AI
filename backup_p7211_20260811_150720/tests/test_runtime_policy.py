# -*- coding: utf-8 -*-
"""
tests/test_runtime_policy.py

Phase C.10.7 — Runtime Policy Engine 测试

覆盖:
1. RuntimeDecision 基础行为
2. PolicyContext 构造与字段
3. PolicyRule Protocol / 内置规则(Safe / Maintenance / Disabled / AlwaysAllow)
4. PolicyEngine 评估、优先级、回调、统计
5. 异常降级(rule 抛错 / engine 抛错 / engine 为 None)
6. Runtime 集成(未注入 policy_engine 不改变行为)
7. Runtime 集成(注入 policy_engine 后 ctx._policy_decisions 被填充)
8. EventBus 集成(RuntimePolicyDecisionEvent)
9. 默认行为保护(无 policy_engine 时一切照旧)

目标:
- >= 30 个测试用例
- 全部通过
- 现有 380+ 测试保持通过(向后兼容)

强约束:
- 不修改 src/runtime 业务逻辑结构
- 不删除任何已有测试
- 不修改 src/memory / src/growth / src/personality / src/self_model
- 不修改 src/control/state / src/control/manager / src/control/api
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil
from typing import Any, Dict, List, Optional

import pytest


# ============================================================
# 路径设置
# ============================================================
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ============================================================
# Fixtures
# ============================================================
@pytest.fixture(autouse=True)
def _reset_policy_state():
    """每个 test 前重置 Policy 默认实例。"""
    from src.runtime.policy import (
        reset_default_policy_engine_for_testing,
    )
    try:
        reset_default_policy_engine_for_testing()
    except Exception:
        pass
    yield


def _make_simple_event(payload: Optional[Dict[str, Any]] = None):
    """构造一个简单 Event 对象。"""
    try:
        from src.runtime.events import Event

        return Event(
            type="user_message",
            payload=payload or {"text": "hello", "content": "hello"},
        )
    except Exception:
        return {"type": "user_message", "payload": payload or {"text": "hello"}}


# ============================================================
# Test: RuntimeDecision 基础
# ============================================================
class TestRuntimeDecisionBasic:
    """RuntimeDecision 基础行为测试。"""

    def test_default_decision_is_allow(self):
        """默认 RuntimeDecision 是 allow。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision()
        assert d.allowed is True
        assert d.module == ""
        assert d.readonly is False
        assert d.mode == "normal"
        assert d.throttle == 1.0

    def test_allow_factory(self):
        """RuntimeDecision.allow() 工厂。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision.allow("memory", reason="ok", mode="normal")
        assert d.allowed is True
        assert d.module == "memory"
        assert d.reason == "ok"
        assert d.readonly is False
        assert d.throttle == 1.0

    def test_deny_factory(self):
        """RuntimeDecision.deny() 工厂。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision.deny("growth", reason="blocked", mode="safe")
        assert d.allowed is False
        assert d.module == "growth"
        assert d.reason == "blocked"
        assert d.mode == "safe"
        assert d.throttle == 0.0
        assert d.is_denied is True

    def test_readonly_factory(self):
        """RuntimeDecision.readonly_decision() 工厂。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision.readonly_decision("memory", reason="read_only", mode="safe")
        assert d.allowed is True
        assert d.readonly is True
        assert d.is_readonly is True
        assert d.throttle == 0.5

    def test_to_dict(self):
        """to_dict 返回完整序列化。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision.allow("memory", reason="r", metadata={"a": 1})
        dct = d.to_dict()
        assert dct["module"] == "memory"
        assert dct["allowed"] is True
        assert dct["reason"] == "r"
        assert dct["mode"] == "normal"
        assert dct["metadata"]["a"] == 1

    def test_with_throttle(self):
        """with_throttle 返回新 decision,throttle 被覆盖。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision.allow("memory")
        d2 = d.with_throttle(0.25)
        assert d2.throttle == 0.25
        assert d is not d2
        # 原始 decision 不变
        assert d.throttle == 1.0

    def test_with_metadata(self):
        """with_metadata 写入额外 metadata。"""
        from src.runtime.policy import RuntimeDecision

        d = RuntimeDecision.allow("memory")
        d2 = d.with_metadata("rule", "SafeModeRule")
        assert d2.metadata.get("rule") == "SafeModeRule"
        # 原始不变
        assert "rule" not in (d.metadata or {})

    def test_mode_normalization(self):
        """mode 自动规范化,未知值回退到 unknown。"""
        from src.runtime.policy import RuntimeDecision, _normalize_mode

        assert _normalize_mode("normal") == "normal"
        assert _normalize_mode("safe") == "safe"
        assert _normalize_mode("maintenance") == "maintenance"
        assert _normalize_mode("garbage") == "unknown"
        assert _normalize_mode(None) == "unknown"

    def test_default_allow_decision_fallback(self):
        """default_allow_decision 返回带 fallback 标记的允许决策。"""
        from src.runtime.policy import default_allow_decision

        d = default_allow_decision("memory")
        assert d.allowed is True
        assert d.metadata.get("fallback") is True
        assert d.module == "memory"


# ============================================================
# Test: PolicyContext 基础
# ============================================================
class TestPolicyContextBasic:
    """PolicyContext 构造与字段测试。"""

    def test_default_construction(self):
        """默认构造,所有字段为空。"""
        from src.runtime.policy import PolicyContext

        ctx = PolicyContext()
        assert ctx.module == ""
        assert ctx.cycle_id == ""
        assert ctx.runtime_mode == "unknown"
        assert ctx.is_safe_mode is False
        assert ctx.is_maintenance_mode is False
        assert ctx.control_state == {}
        assert ctx.module_enabled is None

    def test_from_runtime_basic(self):
        """PolicyContext.from_runtime 解析 control_context。"""
        from src.runtime.policy import PolicyContext
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )

        ctx = PolicyContext.from_runtime(
            module="memory",
            control_context=RuntimeControlContext(provider=NullControlStateProvider()),
        )
        assert ctx.module == "memory"
        assert ctx.cycle_id != ""
        assert ctx.runtime_mode in ("normal", "safe", "maintenance", "unknown")
        assert ctx.module_enabled is True

    def test_from_runtime_safe_mode(self):
        """Safe Mode 下,is_safe_mode=True,runtime_mode=safe。"""
        from src.runtime.policy import PolicyContext

        class FakeProvider:
            def is_enabled(self, m): return True
            def is_safe_mode(self): return True
            def is_maintenance_mode(self): return False

        ctx = PolicyContext.from_runtime(
            module="growth",
            control_context=FakeProvider(),
        )
        assert ctx.is_safe_mode is True
        assert ctx.runtime_mode == "safe"

    def test_from_runtime_maintenance_mode(self):
        """Maintenance Mode 下,is_maintenance_mode=True。"""
        from src.runtime.policy import PolicyContext

        class FakeProvider:
            def is_enabled(self, m): return True
            def is_safe_mode(self): return False
            def is_maintenance_mode(self): return True

        ctx = PolicyContext.from_runtime(
            module="growth",
            control_context=FakeProvider(),
        )
        assert ctx.is_maintenance_mode is True
        assert ctx.runtime_mode == "maintenance"

    def test_from_runtime_disabled_module(self):
        """module_enabled=False 时,module_enabled 字段被设置。"""
        from src.runtime.policy import PolicyContext

        class FakeProvider:
            def is_enabled(self, m): return False
            def is_safe_mode(self): return False
            def is_maintenance_mode(self): return False

        ctx = PolicyContext.from_runtime(
            module="memory",
            control_context=FakeProvider(),
        )
        assert ctx.module_enabled is False
        assert ctx.is_module_allowed() is False

    def test_is_module_allowed_unknown_is_true(self):
        """module_enabled=None 时,is_module_allowed 返回 True(默认 allow)。"""
        from src.runtime.policy import PolicyContext

        ctx = PolicyContext()
        assert ctx.is_module_allowed() is True

    def test_to_dict_safe(self):
        """to_dict 序列化不抛错。"""
        from src.runtime.policy import PolicyContext

        ctx = PolicyContext(module="memory", cycle_id="c1", runtime_mode="safe", is_safe_mode=True)
        dct = ctx.to_dict()
        assert dct["module"] == "memory"
        assert dct["cycle_id"] == "c1"
        assert dct["is_safe_mode"] is True


# ============================================================
# Test: PolicyRule
# ============================================================
class TestPolicyRules:
    """PolicyRule 协议与内置规则测试。"""

    def test_always_allow_evaluates(self):
        """AlwaysAllowRule 始终返回 allow。"""
        from src.runtime.policy import AlwaysAllowRule, PolicyContext

        rule = AlwaysAllowRule()
        d = rule.evaluate(PolicyContext(module="memory"))
        assert d is not None
        assert d.allowed is True

    def test_safe_mode_blocks_growth(self):
        """SafeModeRule 在 safe 模式下拒绝 growth。"""
        from src.runtime.policy import SafeModeRule, PolicyContext

        rule = SafeModeRule()
        ctx = PolicyContext(module="growth", is_safe_mode=True)
        d = rule.evaluate(ctx)
        assert d is not None
        assert d.allowed is False
        assert d.mode == "safe"

    def test_safe_mode_allows_basic_chat(self):
        """SafeModeRule 允许基础聊天(未在 blocked / readonly 列表中)。"""
        from src.runtime.policy import SafeModeRule, PolicyContext

        rule = SafeModeRule()
        ctx = PolicyContext(module="chat", is_safe_mode=True)
        d = rule.evaluate(ctx)
        assert d is not None
        assert d.allowed is True
        assert d.mode == "safe"

    def test_safe_mode_readonly_emotion(self):
        """SafeModeRule 对 emotion 返回 readonly。"""
        from src.runtime.policy import SafeModeRule, PolicyContext

        rule = SafeModeRule()
        ctx = PolicyContext(module="emotion", is_safe_mode=True)
        d = rule.evaluate(ctx)
        assert d is not None
        assert d.allowed is True
        assert d.readonly is True

    def test_safe_mode_not_triggered_in_normal(self):
        """SafeModeRule 在非 safe 模式下不命中。"""
        from src.runtime.policy import SafeModeRule, PolicyContext

        rule = SafeModeRule()
        ctx = PolicyContext(module="growth", is_safe_mode=False)
        d = rule.evaluate(ctx)
        assert d is None

    def test_maintenance_blocks_growth(self):
        """MaintenanceRule 在 maintenance 模式下拒绝 growth。"""
        from src.runtime.policy import MaintenanceRule, PolicyContext

        rule = MaintenanceRule()
        ctx = PolicyContext(module="growth", is_maintenance_mode=True)
        d = rule.evaluate(ctx)
        assert d is not None
        assert d.allowed is False
        assert d.mode == "maintenance"

    def test_maintenance_allows_health(self):
        """MaintenanceRule 允许 health。"""
        from src.runtime.policy import MaintenanceRule, PolicyContext

        rule = MaintenanceRule()
        ctx = PolicyContext(module="health", is_maintenance_mode=True)
        d = rule.evaluate(ctx)
        assert d is not None
        assert d.allowed is True

    def test_maintenance_not_triggered_in_normal(self):
        """MaintenanceRule 在非 maintenance 模式下不命中。"""
        from src.runtime.policy import MaintenanceRule, PolicyContext

        rule = MaintenanceRule()
        ctx = PolicyContext(module="growth", is_maintenance_mode=False)
        d = rule.evaluate(ctx)
        assert d is None

    def test_disabled_module_rule_denies(self):
        """DisabledModuleRule 在 module_enabled=False 时拒绝。"""
        from src.runtime.policy import DisabledModuleRule, PolicyContext

        rule = DisabledModuleRule()
        ctx = PolicyContext(module="memory", module_enabled=False)
        d = rule.evaluate(ctx)
        assert d is not None
        assert d.allowed is False

    def test_disabled_module_rule_no_match_when_enabled(self):
        """DisabledModuleRule 在 module_enabled=True 时不命中。"""
        from src.runtime.policy import DisabledModuleRule, PolicyContext

        rule = DisabledModuleRule()
        ctx = PolicyContext(module="memory", module_enabled=True)
        d = rule.evaluate(ctx)
        assert d is None

    def test_rule_priority_default(self):
        """默认规则优先级:Maintenance > Safe > Disabled > AlwaysAllow。"""
        from src.runtime.policy import build_default_rules

        rules = build_default_rules()
        names = [r.name for r in rules]
        # Maintenance 最高,AlwaysAllow 最低
        assert names[0] == "MaintenanceRule"
        assert names[-1] == "AlwaysAllowRule"

    def test_rule_exception_isolated(self):
        """规则抛错时,evaluate() 返回 None(fail-soft)。"""
        from src.runtime.policy import BasePolicyRule, PolicyContext, safe_call_rule

        class BadRule(BasePolicyRule):
            def evaluate(self, ctx):
                raise RuntimeError("boom")

        d = safe_call_rule(BadRule(), PolicyContext(module="x"))
        assert d is None


# ============================================================
# Test: PolicyEngine
# ============================================================
class TestPolicyEngine:
    """PolicyEngine 评估、优先级、回调、统计测试。"""

    def test_engine_construct_with_default_rules(self):
        """默认构造加载 4 条默认规则。"""
        from src.runtime.policy import PolicyEngine

        engine = PolicyEngine()
        health = engine.health_check()
        assert health["rules_count"] == 4

    def test_engine_no_default_rules(self):
        """use_default_rules=False 时不加载默认规则。"""
        from src.runtime.policy import PolicyEngine

        engine = PolicyEngine(use_default_rules=False)
        assert engine.health_check()["rules_count"] == 0

    def test_engine_evaluate_normal_module(self):
        """Normal 模式下,evaluate 返回 allow。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        ctx = PolicyContext(module="memory", runtime_mode="normal")
        d = engine.evaluate("memory", context=ctx)
        assert d.allowed is True
        assert d.mode == "normal"

    def test_engine_evaluate_safe_mode(self):
        """Safe 模式下,growth 被拒绝。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        ctx = PolicyContext(module="growth", is_safe_mode=True, runtime_mode="safe")
        d = engine.evaluate("growth", context=ctx)
        assert d.allowed is False
        assert d.mode == "safe"

    def test_engine_evaluate_maintenance_mode(self):
        """Maintenance 模式下,memory 被拒绝。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        ctx = PolicyContext(
            module="memory", is_maintenance_mode=True, runtime_mode="maintenance"
        )
        d = engine.evaluate("memory", context=ctx)
        assert d.allowed is False
        assert d.mode == "maintenance"

    def test_engine_evaluate_priority(self):
        """优先级高的规则先评估(可被自定义规则覆盖)。"""
        from src.runtime.policy import (
            PolicyEngine, PolicyContext, RuntimeDecision, BasePolicyRule
        )

        class HighPriorityDeny(BasePolicyRule):
            def evaluate(self, ctx):
                if ctx.module == "memory":
                    return RuntimeDecision.deny(
                        module="memory", reason="high_priority_block", mode="normal"
                    )
                return None

        engine = PolicyEngine(rules=[HighPriorityDeny()], use_default_rules=True)
        d = engine.evaluate("memory", context=PolicyContext(module="memory"))
        assert d.allowed is False
        assert d.reason == "high_priority_block"

    def test_engine_callback_invoked(self):
        """评估完成后,callback 被调用。"""
        from src.runtime.policy import (
            PolicyEngine, PolicyContext, RuntimeDecision
        )

        seen: List[RuntimeDecision] = []

        def cb(decision, ctx):
            seen.append(decision)

        engine = PolicyEngine()
        engine.add_callback(cb)
        engine.evaluate("memory", context=PolicyContext(module="memory"))
        assert len(seen) == 1
        assert seen[0].module == "memory"

    def test_engine_callback_exception_isolated(self):
        """callback 抛错不影响 engine。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        def bad_cb(decision, ctx):
            raise RuntimeError("cb boom")

        engine = PolicyEngine()
        engine.add_callback(bad_cb)
        # 不应抛错
        d = engine.evaluate("memory", context=PolicyContext(module="memory"))
        assert d.allowed is True

    def test_engine_evaluate_all(self):
        """evaluate_all 批量评估。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        ctx = PolicyContext(
            is_safe_mode=True, runtime_mode="safe",
        )
        result = engine.evaluate_all(
            ["memory", "growth", "emotion", "chat"], context=ctx
        )
        assert result["memory"].allowed is True
        assert result["memory"].readonly is True
        assert result["growth"].allowed is False
        assert result["emotion"].allowed is True
        assert result["emotion"].readonly is True
        assert result["chat"].allowed is True

    def test_engine_which_allowed_denied(self):
        """which_allowed / which_denied 工具方法。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        ctx = PolicyContext(is_safe_mode=True, runtime_mode="safe")
        allowed = engine.which_allowed(
            ["memory", "growth", "emotion", "chat"], context=ctx
        )
        denied = engine.which_denied(
            ["memory", "growth", "emotion", "chat"], context=ctx
        )
        assert "growth" in denied
        assert "growth" not in allowed

    def test_engine_engine_exception_failsafe(self):
        """evaluate 内部异常 → 返回 default_allow_decision。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        # 注入一个抛错的规则
        class BadRule:
            name = "BadRule"
            priority = 9999
            def evaluate(self, ctx):
                raise RuntimeError("explode")

        engine.add_rule(BadRule(), priority=99999)
        d = engine.evaluate("memory", context=PolicyContext(module="memory"))
        # 仍然返回有效 decision(不应抛错)
        assert d is not None
        assert isinstance(d.allowed, bool)

    def test_engine_stats_updated(self):
        """评估后 stats 计数增加。"""
        from src.runtime.policy import PolicyEngine, PolicyContext

        engine = PolicyEngine()
        engine.evaluate("memory", context=PolicyContext(module="memory"))
        engine.evaluate("growth", context=PolicyContext(
            module="growth", is_safe_mode=True, runtime_mode="safe"
        ))
        stats = engine.get_stats()
        assert stats["evaluate_total"] == 2
        assert stats["evaluate_allow"] >= 1
        assert stats["evaluate_deny"] >= 1

    def test_engine_remove_rule(self):
        """remove_rule 按 name 移除规则。"""
        from src.runtime.policy import PolicyEngine, MaintenanceRule

        engine = PolicyEngine()
        n0 = engine.health_check()["rules_count"]
        removed = engine.remove_rule("MaintenanceRule")
        assert removed == 1
        assert engine.health_check()["rules_count"] == n0 - 1


# ============================================================
# Test: RuntimeCore Policy Engine 集成
# ============================================================
class TestRuntimeCorePolicyIntegration:
    """RuntimeCore 集成 PolicyEngine 测试。"""

    def test_default_no_policy_engine_unchanged(self):
        """默认未注入 policy_engine,Runtime 行为完全不变。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext

        core = RuntimeCore()
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        # 现有 lifecycle 完整
        assert isinstance(ctx, RuntimeContext)
        # 没有 policy_decisions
        assert getattr(ctx, "_policy_decisions", None) is None
        core.shutdown()

    def test_configure_policy_engine(self):
        """configure_policy_engine 注入 engine 后,property 可见。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        core = RuntimeCore()
        assert core.policy_engine is None
        engine = PolicyEngine()
        core.configure_policy_engine(engine)
        assert core.policy_engine is engine

    def test_configure_policy_engine_reset(self):
        """configure_policy_engine(None) 关闭集成。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        core = RuntimeCore()
        core.configure_policy_engine(PolicyEngine())
        core.configure_policy_engine(None)
        assert core.policy_engine is None

    def test_policy_decisions_stored_in_ctx(self):
        """注入 policy_engine 后,ctx._policy_decisions 包含 7 个模块的决策。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        core = RuntimeCore()
        core.configure_policy_engine(PolicyEngine())
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        decisions = getattr(ctx, "_policy_decisions", None)
        assert isinstance(decisions, dict)
        assert "memory" in decisions
        assert "emotion" in decisions
        assert "growth" in decisions
        assert "runtime" in decisions
        core.shutdown()

    def test_policy_decision_allows_normal_modules(self):
        """Normal 模式下,所有模块的决策都是 allowed=True。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        core = RuntimeCore()
        core.configure_policy_engine(PolicyEngine())
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        decisions = ctx._policy_decisions
        for m in ["memory", "emotion", "growth", "initiative", "dream", "live2d", "runtime"]:
            assert decisions[m]["allowed"] is True
        core.shutdown()

    def test_get_policy_decision_returns_dict(self):
        """get_policy_decision 返回对应模块的决策 dict。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        core = RuntimeCore()
        core.configure_policy_engine(PolicyEngine())
        core.start()
        ev = _make_simple_event()
        ctx = core.process(ev)
        d = core.get_policy_decision("memory", ctx=ctx)
        assert d is not None
        assert d["module"] == "memory"
        assert "allowed" in d
        core.shutdown()

    def test_get_policy_decision_missing(self):
        """get_policy_decision 查无 ctx 时返回 None。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        core = RuntimeCore()
        core.configure_policy_engine(PolicyEngine())
        # 未 process 过,get_policy_decision 返回 None
        assert core.get_policy_decision("memory", ctx=None) is None

    def test_policy_engine_failure_failsafe(self):
        """policy_engine 抛错时,fail-soft,Runtime 仍然 process 成功。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context import RuntimeContext

        class BadEngine:
            name = "BadEngine"
            def evaluate(self, module, context=None):
                raise RuntimeError("engine boom")
            def health_check(self): return {}

        core = RuntimeCore()
        core.configure_policy_engine(BadEngine())
        core.start()
        ev = _make_simple_event()
        # 不应抛错
        ctx = core.process(ev)
        assert isinstance(ctx, RuntimeContext)
        # policy_decisions 可能为空,但 lifecycle 正常
        core.shutdown()

    def test_is_module_allowed_via_policy_unset(self):
        """未注入 policy_engine 时,_is_module_allowed_via_policy 返回 None。"""
        from src.runtime.runtime import RuntimeCore

        core = RuntimeCore()
        result = core._is_module_allowed_via_policy("memory")
        assert result is None

    def test_is_module_allowed_via_policy_set(self):
        """注入 policy_engine 后,_is_module_allowed_via_policy 返回 bool。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine, PolicyContext

        core = RuntimeCore()
        engine = PolicyEngine()
        core.configure_policy_engine(engine)
        # normal 模式 → True
        ctx = PolicyContext(module="memory", runtime_mode="normal")
        result = core._is_module_allowed_via_policy("memory", ctx=ctx)
        assert result is True

    def test_is_module_allowed_via_policy_safe_mode(self):
        """Safe 模式下,_is_module_allowed_via_policy 对 growth 返回 False。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine, PolicyContext

        core = RuntimeCore()
        core.configure_policy_engine(PolicyEngine())
        ctx = PolicyContext(
            module="growth", is_safe_mode=True, runtime_mode="safe"
        )
        result = core._is_module_allowed_via_policy("growth", ctx=ctx)
        assert result is False


# ============================================================
# Test: EventBus Integration
# ============================================================
class TestEventBusIntegration:
    """EventBus 集成 RuntimePolicyDecisionEvent 测试。"""

    def test_policy_decision_event_defined(self):
        """RuntimePolicyDecisionEvent 已定义。"""
        from src.events.events import RuntimePolicyDecisionEvent, EventType

        assert hasattr(EventType, "RUNTIME_POLICY_DECISION")
        ev = RuntimePolicyDecisionEvent(
            module="memory", allowed=True, reason="ok", mode="normal"
        )
        assert ev.event_type == EventType.RUNTIME_POLICY_DECISION
        assert ev.module == "memory"
        assert ev.allowed is True

    def test_policy_event_published_on_process(self):
        """process() 中注入 policy_engine 时,RuntimePolicyDecisionEvent 被发布。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine
        from src.events.events import EventType, RuntimePolicyDecisionEvent

        received: list = []
        try:
            from src.events.bus import subscribe_event

            def handler(ev):
                received.append(ev)

            subscribe_event(EventType.RUNTIME_POLICY_DECISION, handler)
        except Exception:
            pytest.skip("EventBus not available")

        try:
            core = RuntimeCore()
            core.configure_policy_engine(PolicyEngine())
            core.start()
            ev = _make_simple_event()
            ctx = core.process(ev)
            # 至少 1 个事件被收到
            assert len(received) >= 1
            # 事件类型正确
            for e in received:
                assert e.event_type == EventType.RUNTIME_POLICY_DECISION
        finally:
            try:
                from src.events.bus import unsubscribe_event
                unsubscribe_event(EventType.RUNTIME_POLICY_DECISION, handler)
            except Exception:
                pass


# ============================================================
# Test: 默认行为回归保护
# ============================================================
class TestDefaultBehaviorPreserved:
    """未注入 policy_engine 时,默认行为完全保留。"""

    def test_no_policy_engine_no_ctx_decisions(self):
        """无 policy_engine,ctx._policy_decisions 不存在。"""
        from src.runtime.runtime import RuntimeCore

        core = RuntimeCore()
        core.start()
        ctx = core.process(_make_simple_event())
        assert getattr(ctx, "_policy_decisions", None) is None
        core.shutdown()

    def test_no_policy_engine_no_event_published(self):
        """无 policy_engine,不发布 RuntimePolicyDecisionEvent。"""
        from src.runtime.runtime import RuntimeCore
        from src.events.events import EventType

        received: list = []
        try:
            from src.events.bus import subscribe_event

            def handler(ev):
                received.append(ev)

            subscribe_event(EventType.RUNTIME_POLICY_DECISION, handler)
        except Exception:
            pytest.skip("EventBus not available")

        try:
            core = RuntimeCore()
            core.start()
            core.process(_make_simple_event())
            assert len(received) == 0
        finally:
            try:
                from src.events.bus import unsubscribe_event
                unsubscribe_event(EventType.RUNTIME_POLICY_DECISION, handler)
            except Exception:
                pass

    def test_is_module_allowed_unchanged(self):
        """_is_module_allowed() 在无 policy_engine 时,使用 control_context 路径。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.context.control_context import (
            RuntimeControlContext,
            NullControlStateProvider,
        )

        core = RuntimeCore()
        # 用 NullProvider
        core.configure_control_context(RuntimeControlContext(
            provider=NullControlStateProvider()
        ))
        # 默认全部 allowed
        for m in ["memory", "emotion", "growth"]:
            assert core._is_module_allowed(m) is True

    def test_policy_engine_optional_in_init(self):
        """RuntimeCore.__init__ 接受 policy_engine 参数(可选)。"""
        from src.runtime.runtime import RuntimeCore
        from src.runtime.policy import PolicyEngine

        # 不传 policy_engine
        core1 = RuntimeCore()
        assert core1.policy_engine is None

        # 传 policy_engine
        engine = PolicyEngine()
        core2 = RuntimeCore(policy_engine=engine)
        assert core2.policy_engine is engine

    def test_default_policy_engine_singleton(self):
        """get_default_policy_engine 返回单例。"""
        from src.runtime.policy import (
            get_default_policy_engine,
            reset_default_policy_engine_for_testing,
        )

        e1 = get_default_policy_engine()
        e2 = get_default_policy_engine()
        assert e1 is e2
        # 重置后是新对象
        e3 = reset_default_policy_engine_for_testing()
        assert e3 is not e1
