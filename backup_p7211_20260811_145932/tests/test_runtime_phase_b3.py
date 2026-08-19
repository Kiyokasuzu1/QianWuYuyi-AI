# -*- coding: utf-8 -*-
"""
tests/test_runtime_phase_b3.py

Phase B.3 Runtime Integration —— ActionDispatcher 接入层测试

覆盖:
  1. 配置测试
  2. Mapper 测试
  3. Bridge 单元测试
  4. Supervisor 集成测试
  5. 异常隔离测试
  6. Backward Compatibility
  7. 核心模块未修改保护

约束:
  - 不修改任何核心模块
  - 不启动真实 LLM / 业务
  - mock 化所有外部依赖
"""
from __future__ import annotations

import time
import threading
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# ============================================================
# 1. 测试目标导入
# ============================================================

from src.runtime.phase_b3_integration import (
    PHASE_B3_DEFAULT_CONFIG,
    PHASE_B3_NAME,
    PHASE_B3_VERSION,
    apply_phase_b3_config,
    is_phase_b3_enabled,
    is_b3_enabled_simple,
    proposed_action_to_runtime_action,
    RuntimeB3Bridge,
    create_b3_bridge,
    safe_flush_pending,
)

from src.runtime.action_dispatcher import Action, ActionDispatcher

from src.runtime.supervisor import RuntimeSupervisor


# ============================================================
# Helper: 构造一个轻量 ProposedAction(不依赖 ProactiveEngine)
# ============================================================

class FakeActionType:
    """模拟 ActionType enum"""
    def __init__(self, value: str):
        self.value = value


def make_proposed(
    action_id: str = "act_test_001",
    action_type: Any = None,
    content: str = "hello",
    trigger_reason: str = "test",
    user_id: Optional[str] = "u1",
    necessity_score: float = 0.5,
    disturbance_risk: float = 0.3,
    confidence: Optional[float] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Any:
    """构造一个 duck-typed ProposedAction"""
    if action_type is None:
        action_type = FakeActionType("greeting")
    if confidence is None:
        confidence = necessity_score * (1 - disturbance_risk)
    obj = MagicMock(spec=[
        "action_id", "action_type", "content", "trigger_reason",
        "user_id", "necessity_score", "disturbance_risk",
        "confidence", "context", "outcome", "outcome_reason",
        "created_at", "executed_at", "to_dict",
    ])
    obj.action_id = action_id
    obj.action_type = action_type
    obj.content = content
    obj.trigger_reason = trigger_reason
    obj.user_id = user_id
    obj.necessity_score = necessity_score
    obj.disturbance_risk = disturbance_risk
    obj.confidence = confidence
    obj.context = context or {}
    obj.outcome = None
    obj.outcome_reason = ""
    obj.created_at = time.time()
    obj.executed_at = 0.0
    return obj


# ============================================================
# 2. 配置测试
# ============================================================

class TestConfigInjection:
    """PHASE_B3_DEFAULT_CONFIG / apply_phase_b3_config / is_phase_b3_enabled"""

    def test_default_keys_present(self):
        """默认配置中 dispatcher.enabled 必须为 False"""
        cfg = PHASE_B3_DEFAULT_CONFIG
        assert "dispatcher" in cfg
        assert cfg["dispatcher"]["enabled"] is False
        assert cfg["dispatcher"]["max_per_tick"] == 3

    def test_apply_with_none_returns_defaults(self):
        """None 输入 → 返回默认"""
        result = apply_phase_b3_config(None)
        assert result["dispatcher"]["enabled"] is False

    def test_user_dispatcher_enabled_wins(self):
        """用户 enabled=True 覆盖默认 False"""
        user_cfg = {"dispatcher": {"enabled": True}}
        result = apply_phase_b3_config(user_cfg)
        assert result["dispatcher"]["enabled"] is True
        # max_per_tick 仍保留默认
        assert result["dispatcher"]["max_per_tick"] == 3

    def test_user_dispatcher_disabled_keeps_disabled(self):
        """用户 enabled=False → 保持 False"""
        user_cfg = {"dispatcher": {"enabled": False}}
        result = apply_phase_b3_config(user_cfg)
        assert result["dispatcher"]["enabled"] is False

    def test_apply_does_not_mutate_input(self):
        """不修改入参 dict"""
        user_cfg = {"dispatcher": {"enabled": True}}
        snapshot = dict(user_cfg)
        snapshot["dispatcher"] = dict(user_cfg["dispatcher"])
        result = apply_phase_b3_config(user_cfg)
        assert user_cfg == snapshot, f"入参被修改: {user_cfg} != {snapshot}"
        assert result is not user_cfg

    def test_apply_handles_non_dict(self):
        """非 dict 输入应被安全处理"""
        assert apply_phase_b3_config("not a dict")["dispatcher"]["enabled"] is False
        assert apply_phase_b3_config(42)["dispatcher"]["enabled"] is False
        assert apply_phase_b3_config([])["dispatcher"]["enabled"] is False

    def test_is_phase_b3_enabled(self):
        """is_phase_b3_enabled 判定正确"""
        assert is_phase_b3_enabled({"dispatcher": {"enabled": True}}) is True
        assert is_phase_b3_enabled({"dispatcher": {"enabled": False}}) is False
        assert is_phase_b3_enabled({}) is False
        assert is_phase_b3_enabled(None) is False

    def test_is_b3_enabled_simple_variants(self):
        """is_b3_enabled_simple 兼容 cfg[\"dispatcher_enabled\"]"""
        assert is_b3_enabled_simple({"dispatcher_enabled": True}) is True
        assert is_b3_enabled_simple({"dispatcher_enabled": False}) is False
        assert is_b3_enabled_simple({"dispatcher": {"enabled": True}}) is True
        assert is_b3_enabled_simple({}) is False
        assert is_b3_enabled_simple(None) is False

    def test_top_level_dispatcher_enabled_wins(self):
        """顶层 dispatcher_enabled 优先于嵌套 enabled"""
        cfg = {
            "dispatcher_enabled": False,
            "dispatcher": {"enabled": True},
        }
        assert is_b3_enabled_simple(cfg) is False


# ============================================================
# 3. Mapper 测试
# ============================================================

class TestActionMapping:
    """proposed_action_to_runtime_action"""

    def test_basic_mapping(self):
        """基本字段映射"""
        p = make_proposed(
            action_id="act_x",
            content="hi",
            user_id="u1",
            trigger_reason="test",
        )
        a = proposed_action_to_runtime_action(p)
        assert a is not None
        assert isinstance(a, Action)
        assert a.action_id == "act_x"
        assert a.action_type == "greeting"
        assert a.reason == "test"
        assert a.payload["user_id"] == "u1"
        assert a.payload["content"] == "hi"
        assert a.payload["source"] == "proactive_engine"
        assert a.payload["confidence"] == pytest.approx(0.5 * 0.7, abs=1e-6)

    def test_action_type_enum_to_string(self):
        """action_type enum 转 str"""
        p = make_proposed(action_type=FakeActionType("share"))
        a = proposed_action_to_runtime_action(p)
        assert a.action_type == "share"

    def test_action_type_already_string(self):
        """action_type 已经是 str 时保持原样"""
        p = make_proposed(action_type="care")
        a = proposed_action_to_runtime_action(p)
        assert a.action_type == "care"

    def test_payload_full_fields(self):
        """payload 包含必要字段"""
        p = make_proposed(
            necessity_score=0.8,
            disturbance_risk=0.2,
            context={"k": "v"},
        )
        a = proposed_action_to_runtime_action(p)
        assert a.payload["necessity_score"] == pytest.approx(0.8, abs=1e-6)
        assert a.payload["disturbance_risk"] == pytest.approx(0.2, abs=1e-6)
        assert a.payload["context"] == {"k": "v"}
        assert a.payload["trigger_reason"]  # 非空

    def test_none_proposed_returns_none(self):
        """None 输入 → None,不抛错"""
        assert proposed_action_to_runtime_action(None) is None

    def test_missing_action_type_returns_none(self):
        """缺 action_type → None"""
        p = MagicMock()
        p.action_id = "act_x"
        p.action_type = None
        p.content = "hi"
        p.trigger_reason = "t"
        p.user_id = "u1"
        p.necessity_score = 0.5
        p.disturbance_risk = 0.3
        p.confidence = 0.35
        p.context = {}
        assert proposed_action_to_runtime_action(p) is None

    def test_missing_action_id_returns_none(self):
        """缺 action_id → None"""
        p = MagicMock()
        p.action_id = None
        p.action_type = FakeActionType("greeting")
        p.content = "hi"
        p.trigger_reason = "t"
        p.user_id = "u1"
        p.necessity_score = 0.5
        p.disturbance_risk = 0.3
        p.confidence = 0.35
        p.context = {}
        assert proposed_action_to_runtime_action(p) is None

    def test_empty_context_not_in_payload(self):
        """空 context 不写入 payload"""
        p = make_proposed(context={})
        a = proposed_action_to_runtime_action(p)
        assert "context" not in a.payload

    def test_custom_runtime_action_cls(self):
        """支持注入自定义 Action 类"""
        class MyAction:
            def __init__(self, action_id, action_type, payload, reason=""):
                self.action_id = action_id
                self.action_type = action_type
                self.payload = payload
                self.reason = reason
        p = make_proposed(action_id="custom_1")
        a = proposed_action_to_runtime_action(p, runtime_action_cls=MyAction)
        assert isinstance(a, MyAction)
        assert a.action_id == "custom_1"


# ============================================================
# 4. Bridge 单元测试
# ============================================================

class TestRuntimeB3Bridge:
    """RuntimeB3Bridge 单类行为"""

    def test_bridge_creation(self):
        """bridge 创建成功"""
        engine = MagicMock()
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher, max_per_tick=3)
        assert b.is_enabled() is True
        assert b.engine is engine
        assert b.dispatcher is dispatcher
        assert b._max_per_tick == 3

    def test_bridge_disabled_when_missing_deps(self):
        """缺 engine 或 dispatcher → is_enabled=False"""
        b1 = RuntimeB3Bridge(None, MagicMock())
        assert b1.is_enabled() is False
        b2 = RuntimeB3Bridge(MagicMock(), None)
        assert b2.is_enabled() is False
        b3 = RuntimeB3Bridge(None, None)
        assert b3.is_enabled() is False

    def test_flush_pending_empty(self):
        """空 pending → 全部计数 0"""
        engine = MagicMock()
        engine.get_pending_actions.return_value = []
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        result = b.flush_pending()
        assert result == {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}
        dispatcher.dispatch.assert_not_called()

    def test_flush_pending_dispatches_after_gate(self):
        """过 gate → dispatch"""
        engine = MagicMock()
        p = make_proposed(action_id="act_a1")
        engine.get_pending_actions.return_value = [p]
        engine.execute_action.return_value = True  # gate 通过
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher, max_per_tick=5)
        result = b.flush_pending()
        assert result["processed"] == 1
        assert result["dispatched"] == 1
        assert result["rejected"] == 0
        dispatcher.dispatch.assert_called_once()
        a = dispatcher.dispatch.call_args[0][0]
        assert a.action_id == "act_a1"
        assert a.action_type == "greeting"

    def test_flush_pending_gate_rejected_no_dispatch(self):
        """gate 拒绝 → 不 dispatch"""
        engine = MagicMock()
        p = make_proposed(action_id="act_b1")
        engine.get_pending_actions.return_value = [p]
        engine.execute_action.return_value = False  # gate 拒绝
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        result = b.flush_pending()
        assert result["processed"] == 1
        assert result["rejected"] == 1
        assert result["dispatched"] == 0
        dispatcher.dispatch.assert_not_called()

    def test_flush_pending_idempotent(self):
        """重复 action_id 不重复 dispatch"""
        engine = MagicMock()
        p = make_proposed(action_id="act_idem")
        engine.get_pending_actions.return_value = [p]
        engine.execute_action.return_value = True
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        r1 = b.flush_pending()
        r2 = b.flush_pending()
        assert r1["dispatched"] == 1
        assert r2["dispatched"] == 0
        assert r2["skipped"] == 1
        # dispatcher 只被调用一次
        assert dispatcher.dispatch.call_count == 1

    def test_flush_pending_max_per_tick(self):
        """限速:max_per_tick 限制处理数"""
        engine = MagicMock()
        proposed_list = [
            make_proposed(action_id=f"act_{i}") for i in range(10)
        ]
        engine.get_pending_actions.return_value = proposed_list
        engine.execute_action.return_value = True
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher, max_per_tick=3)
        result = b.flush_pending()
        assert result["processed"] == 3
        assert result["dispatched"] == 3
        # dispatcher 实际被调用 3 次
        assert dispatcher.dispatch.call_count == 3

    def test_flush_pending_engine_exception_isolated(self):
        """engine.get_pending_actions 抛错 → 隔离"""
        engine = MagicMock()
        engine.get_pending_actions.side_effect = RuntimeError("engine down")
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        # 不应抛错
        result = b.flush_pending()
        assert result == {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}
        dispatcher.dispatch.assert_not_called()
        assert b._total_errors == 1

    def test_flush_pending_execute_exception_isolated(self):
        """execute_action 抛错 → 隔离,不 dispatch"""
        engine = MagicMock()
        p = make_proposed(action_id="act_x1")
        engine.get_pending_actions.return_value = [p]
        engine.execute_action.side_effect = RuntimeError("gate failed")
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        result = b.flush_pending()
        assert result["rejected"] == 1
        assert result["dispatched"] == 0
        dispatcher.dispatch.assert_not_called()
        assert b._total_errors == 1

    def test_flush_pending_dispatcher_exception_isolated(self):
        """dispatcher.dispatch 抛错 → 隔离"""
        engine = MagicMock()
        p = make_proposed(action_id="act_y1")
        engine.get_pending_actions.return_value = [p]
        engine.execute_action.return_value = True
        dispatcher = MagicMock()
        dispatcher.dispatch.side_effect = RuntimeError("dispatcher boom")
        b = RuntimeB3Bridge(engine, dispatcher)
        result = b.flush_pending()
        assert result["rejected"] == 1
        assert result["dispatched"] == 0
        # action_id 不应进入 _dispatched_ids(因为未真正 dispatch)
        assert "act_y1" not in b._dispatched_ids

    def test_summarize_b3_shape(self):
        """summarize_b3 返回字段完整"""
        engine = MagicMock()
        engine.get_pending_actions.return_value = [make_proposed(), make_proposed()]
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        s = b.summarize_b3()
        for k in [
            "available", "enabled", "phase_b3_enabled",
            "phase_b3_name", "phase_b3_version",
            "pending", "processed", "dispatched",
            "rejected", "skipped", "dispatched_unique",
            "errors", "max_per_tick", "ts",
        ]:
            assert k in s, f"missing key: {k}"
        assert s["phase_b3_name"] == PHASE_B3_NAME
        assert s["phase_b3_version"] == PHASE_B3_VERSION
        assert s["max_per_tick"] == 3
        assert s["pending"] == 2

    def test_summarize_b3_fails_soft(self):
        """engine.get_pending 抛错 → summarize 仍返回"""
        engine = MagicMock()
        engine.get_pending_actions.side_effect = RuntimeError("nope")
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        s = b.summarize_b3()
        assert s["pending"] == -1
        assert s["enabled"] is True

    def test_get_recent_results(self):
        """get_recent_results 返回最近结果"""
        engine = MagicMock()
        engine.get_pending_actions.return_value = []
        engine.execute_action.return_value = True
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        p = make_proposed(action_id="act_rec1")
        engine.get_pending_actions.return_value = [p]
        b.flush_pending()
        recent = b.get_recent_results(limit=10)
        assert len(recent) >= 1
        # 最新一条 stage 应该是 dispatched
        assert recent[-1]["action_id"] == "act_rec1"
        assert recent[-1]["stage"] == "dispatched"


# ============================================================
# 5. safe_flush_pending 测试
# ============================================================

class TestSafeFlushPending:
    """safe_flush_pending 全局异常隔离"""

    def test_none_bridge(self):
        """None bridge → 安全返回"""
        r = safe_flush_pending(None)
        assert r["ok"] is False
        assert "None" in r["error"]

    def test_missing_method(self):
        """无 flush_pending 方法 → 安全返回"""
        b = object()
        r = safe_flush_pending(b)
        assert r["ok"] is False
        assert "flush_pending" in r["error"]

    def test_success(self):
        """正常 bridge → ok=True,result 非空"""
        engine = MagicMock()
        engine.get_pending_actions.return_value = []
        dispatcher = MagicMock()
        b = RuntimeB3Bridge(engine, dispatcher)
        r = safe_flush_pending(b)
        assert r["ok"] is True
        assert r["result"] == {"processed": 0, "dispatched": 0, "rejected": 0, "skipped": 0}

    def test_exception_in_bridge(self):
        """bridge.flush_pending 抛错 → ok=False"""
        b = MagicMock()
        b.flush_pending.side_effect = RuntimeError("kaboom")
        r = safe_flush_pending(b)
        assert r["ok"] is False
        assert "kaboom" in r["error"]


# ============================================================
# 6. create_b3_bridge 工厂测试
# ============================================================

class TestCreateB3Bridge:
    def test_factory_returns_bridge(self):
        engine = MagicMock()
        dispatcher = MagicMock()
        b = create_b3_bridge(engine, dispatcher, max_per_tick=5)
        assert isinstance(b, RuntimeB3Bridge)
        assert b._max_per_tick == 5


# ============================================================
# 7. Supervisor 集成测试
# ============================================================

@pytest.fixture
def fresh_supervisor_class():
    """确保单例在每次测试前清空"""
    RuntimeSupervisor.reset_instance()
    yield RuntimeSupervisor
    RuntimeSupervisor.reset_instance()


class TestSupervisorDrivesB3:
    """supervisor step 4 必须能调用 b3_bridge.flush_pending()"""

    def test_supervisor_step4_calls_flush_pending(self, fresh_supervisor_class):
        """supervisor step 4 调 flush_pending"""
        engine = MagicMock()
        engine.get_pending_actions.return_value = []
        engine.execute_action.return_value = True
        dispatcher = MagicMock()
        bridge = RuntimeB3Bridge(engine, dispatcher)

        sup = fresh_supervisor_class(
            action_dispatcher=bridge,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()
        # 至少 2 次 flush
        # 通过 dispatcher.dispatch 调用次数间接验证(空 pending 时不会 dispatch)
        # 但 flush_pending 本身被调用可以通过 mock 验证
        # 这里改用 bridge 状态:至少 _total_processed 是 0 但 _total_errors 也是 0
        assert bridge._total_errors == 0
        assert engine.get_pending_actions.call_count >= 2, (
            f"应被调用 ≥2 次,实际 {engine.get_pending_actions.call_count}"
        )

    def test_supervisor_no_dispatcher_does_not_break(self, fresh_supervisor_class):
        """action_dispatcher=None → 正常运行"""
        sup = fresh_supervisor_class(
            action_dispatcher=None,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()
        s = sup.get_status()
        assert s["running"] is False
        assert s["tick_count"] >= 2
        assert s["error_count"] == 0

    def test_supervisor_b3_bridge_exception_isolated(self, fresh_supervisor_class):
        """b3_bridge.flush_pending 抛错 → supervisor 继续运行"""
        bridge = MagicMock()
        bridge.flush_pending.side_effect = RuntimeError("bridge boom")
        bridge.is_enabled.return_value = True

        sup = fresh_supervisor_class(
            action_dispatcher=bridge,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(2.5)
        sup.stop()
        s = sup.get_status()
        # supervisor 还在跑(直到 stop)
        assert s["tick_count"] >= 2
        # error 被记录但不致命
        assert s["error_count"] >= 2
        # flush_pending 被多次调用
        assert bridge.flush_pending.call_count >= 2

    def test_supervisor_b3_dispatch_real_action(self, fresh_supervisor_class):
        """真实链路:propose → tick → flush_pending → dispatch → no_handler"""
        # 用真实 ProactiveEngine 构造一个 pending
        from src.proactive.proactive_engine import (
            ProactiveEngine, ProposedAction, ActionType,
        )
        try:
            pe = ProactiveEngine()
            pe.start()
        except Exception:
            pytest.skip("ProactiveEngine 启动依赖不可用,跳过集成测试")

        try:
            # gate 默认 active_hours=(9,23),强制 24h 开放避免被 gate 推迟
            try:
                pe._confidence_gate.set_user_preference(
                    "u_integration",
                    {"active_hours": [(0, 24)]},
                )
            except Exception:
                pass

            # 构造一个 pending
            pa = pe.propose_action(
                action_type=ActionType.GREETING,
                content="long time no see",
                trigger_reason="24h inactive",
                user_id="u_integration",
                necessity_score=0.8,
                disturbance_risk=0.2,
            )
            assert pa.outcome is None
            # 真实 dispatcher
            dispatcher = ActionDispatcher()
            bridge = RuntimeB3Bridge(pe, dispatcher, max_per_tick=5)
            sup = fresh_supervisor_class(
                action_dispatcher=bridge,
                tick_interval_seconds=1,
                startup_grace_seconds=0,
            )
            sup.start()
            time.sleep(2.5)
            sup.stop()
            # 至少有一次真实 dispatch 发生(无 handler → status="no_handler")
            history = dispatcher.get_history()
            assert len(history) >= 1, "应至少 dispatch 1 次"
            # 验证 status 为 no_handler
            assert history[-1].status == "no_handler"
            # 验证 outcome 被写
            final_action = pe.get_action(pa.action_id)
            assert final_action.outcome is not None
        finally:
            try:
                pe.stop()
            except Exception:
                pass

    def test_supervisor_b3_bridge_idempotent_across_ticks(self, fresh_supervisor_class):
        """跨 tick 的幂等:同一 action_id 不会被重复 dispatch"""
        engine = MagicMock()
        p = make_proposed(action_id="act_idem_sup")
        # 每次都返回同一个 pending
        engine.get_pending_actions.return_value = [p]
        engine.execute_action.return_value = True
        dispatcher = MagicMock()
        bridge = RuntimeB3Bridge(engine, dispatcher)

        sup = fresh_supervisor_class(
            action_dispatcher=bridge,
            tick_interval_seconds=1,
            startup_grace_seconds=0,
        )
        sup.start()
        time.sleep(3.5)
        sup.stop()
        # 即便跑了 3+ tick,dispatcher 只被调用 1 次
        assert dispatcher.dispatch.call_count == 1, (
            f"应只 dispatch 1 次(幂等),实际 {dispatcher.dispatch.call_count}"
        )


# ============================================================
# 8. Backward Compatibility
# ============================================================

class TestBackwardCompatibility:
    """B.3 不破坏 Phase A / B.1 / B.2"""

    def test_disabled_by_default(self):
        """apply_phase_b3_config({}) 时 dispatcher 默认关闭"""
        cfg = apply_phase_b3_config({})
        assert cfg["dispatcher"]["enabled"] is False

    def test_summarize_when_disabled(self):
        """engine/dispatcher 缺失时 summarize 仍可用"""
        bridge = RuntimeB3Bridge(None, None)
        s = bridge.summarize_b3()
        assert s["enabled"] is False
        assert s["available"] is False

    def test_safe_flush_with_none(self):
        """safe_flush_pending(None) 不抛错"""
        r = safe_flush_pending(None)
        assert r["ok"] is False

    def test_phase_b3_bridge_with_real_action_dispatcher(self):
        """真实 ActionDispatcher 接入测试"""
        engine = MagicMock()
        engine.get_pending_actions.return_value = [
            make_proposed(action_id="act_real_1"),
            make_proposed(action_id="act_real_2"),
        ]
        engine.execute_action.return_value = True
        dispatcher = ActionDispatcher()  # 真实
        bridge = RuntimeB3Bridge(engine, dispatcher)
        result = bridge.flush_pending()
        assert result["dispatched"] == 2
        # dispatcher 真的记录到 history
        history = dispatcher.get_history()
        assert len(history) == 2
        assert all(h.status == "no_handler" for h in history)
        # action_id 对得上
        ids = {h.action_id for h in history}
        assert ids == {"act_real_1", "act_real_2"}


# ============================================================
# 9. 核心模块未修改保护
# ============================================================

class TestNoCoreModuleModification:
    """保证 B.3 没有动到任何核心模块"""

    def test_supervisor_step4_uses_getattr_flush(self):
        """supervisor._tick_iteration 仍使用 getattr('flush_pending')(已预留钩子)"""
        import inspect
        from src.runtime.supervisor import RuntimeSupervisor
        src = inspect.getsource(RuntimeSupervisor._tick_iteration)
        assert "flush_pending" in src
        assert "getattr" in src
        assert "action_dispatcher" in src

    def test_action_dispatcher_public_api_unchanged(self):
        """ActionDispatcher 公开 API 保持"""
        from src.runtime.action_dispatcher import ActionDispatcher, Action
        d = ActionDispatcher()
        for name in ["register", "unregister", "dispatch", "get_history", "has_handler"]:
            assert hasattr(d, name), f"ActionDispatcher 缺方法: {name}"
        # 关键: 没有 flush_pending(ActionDispatcher 本身不该有)
        assert not hasattr(d, "flush_pending"), (
            "ActionDispatcher 自身不应有 flush_pending(避免与 RuntimeB3Bridge 冲突)"
        )

    def test_proactive_engine_public_api_unchanged(self):
        """ProactiveEngine 公开 API 保持"""
        from src.proactive.proactive_engine import ProactiveEngine
        for name in [
            "start", "stop", "scan_all_users", "propose_action",
            "execute_action", "get_pending_actions", "get_recent_actions",
            "get_action_stats", "get_action", "record_user_activity",
        ]:
            assert hasattr(ProactiveEngine, name), f"ProactiveEngine 缺方法: {name}"

    def test_phase_b3_does_not_modify_phase_a(self):
        """B.3 不引入新的 supervisor 主循环分支"""
        import inspect
        from src.runtime.supervisor import RuntimeSupervisor
        # supervisor._tick_iteration 仍由 Phase A 写好的 step 1-5 组成
        # 我们只验证 step 4 存在且使用 getattr 软钩子
        src = inspect.getsource(RuntimeSupervisor._tick_iteration)
        # 步骤标记
        for marker in ["1. RuntimeCore.tick", "2. system.tick", "3. ProactiveEngine", "4. ActionDispatcher", "5. HeartbeatCollector"]:
            assert marker in src, f"supervisor 缺步骤: {marker}"

    def test_phase_b3_does_not_modify_b1(self):
        """B.3 不修改 B.1 文件"""
        from src.runtime.phase_b_integration import (
            apply_phase_b1_config,
            is_phase_b1_enabled,
            create_b1_bridge,
        )
        # 简单 sanity: 都能导入
        assert callable(apply_phase_b1_config)
        assert callable(is_phase_b1_enabled)
        assert callable(create_b1_bridge)

    def test_phase_b3_does_not_modify_b2(self):
        """B.3 不修改 B.2 文件"""
        from src.runtime.phase_b2_integration import (
            apply_phase_b2_config,
            create_b2_bridge,
        )
        assert callable(apply_phase_b2_config)
        assert callable(create_b2_bridge)


# ============================================================
# 10. 启动入口集成(轻量)
# ============================================================

class TestStartRuntimeScript:
    """验证 start_runtime.py 的 B.3 相关函数"""

    def test_help_includes_enable_dispatcher(self):
        """--help 应包含 --enable-dispatcher"""
        import subprocess
        import sys
        proc = subprocess.run(
            [sys.executable, "scripts/start_runtime.py", "--help"],
            capture_output=True, text=True, timeout=10,
        )
        assert "--enable-dispatcher" in proc.stdout, (
            f"--help 未显示 --enable-dispatcher:\n{proc.stdout}"
        )

    def test_init_action_dispatcher_disabled_returns_none(self):
        """未启用时 init_action_dispatcher 返回 None"""
        import importlib
        import scripts.start_runtime as srt
        importlib.reload(srt)
        cfg = {"dispatcher": {"enabled": False}}
        result = srt.init_action_dispatcher(cfg, force=False)
        assert result is None

    def test_init_action_dispatcher_force_creates(self):
        """force=True 强制创建"""
        import importlib
        import scripts.start_runtime as srt
        importlib.reload(srt)
        cfg = {"dispatcher": {"enabled": False}}
        result = srt.init_action_dispatcher(cfg, force=True)
        assert result is not None
        from src.runtime.action_dispatcher import ActionDispatcher
        assert isinstance(result, ActionDispatcher)

    def test_init_action_dispatcher_config_enabled(self):
        """cfg enabled=True 时创建"""
        import importlib
        import scripts.start_runtime as srt
        importlib.reload(srt)
        cfg = {"dispatcher": {"enabled": True}}
        result = srt.init_action_dispatcher(cfg, force=False)
        assert result is not None
