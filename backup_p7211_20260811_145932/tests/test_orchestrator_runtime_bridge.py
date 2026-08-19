# -*- coding: utf-8 -*-
"""
tests/test_orchestrator_runtime_bridge.py

P0-2: 验证 OrchestratorRuntimeBridge 入口探测 + 智能适配

覆盖场景：
  1. 类型 A：runtime 有 process(event) 且返回 final_reply → source="runtime"
  2. 类型 A：runtime 有 process 但 ctx._final_reply 为空 → fallback legacy
  3. 类型 A：process() 抛异常 → fallback legacy，错误记录
  4. 类型 B：runtime 只有 inject_event（无 process）→ 状态更新 + legacy 回复
  5. 类型 B：inject_event() 抛异常 → 不阻断回复，fallback legacy
  6. 类型 C：runtime 为 None → 纯 legacy
  7. 类型 D：runtime 为未知类型对象（无 process 也无 inject）→ 纯 legacy
  8. 统计计数：多次调用 runtime_call_count / runtime_state_count / legacy_call_count
  9. 类型 B：inject_event() 的参数正确（event_type 和 event_data）
 10. 向后兼容：所有新属性都有默认值，不会 AttributeError
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, Optional
from unittest.mock import MagicMock


# ============================================================
# Fake 依赖（避免构造真实 Orchestrator / RuntimeCore）
# ============================================================
class _FakeCtx:
    """类型A Runtime.process() 返回的 ctx 类型占位。"""

    def __init__(self, final_reply: Optional[str] = None):
        if final_reply is not None:
            self._final_reply = final_reply


class _FakeEngine:
    """模拟 ResponseEngine，固定返回一段回复。"""

    FIXED_REPLY = "你好呀~我是羽依"

    def generate(self, **_kwargs: Any) -> str:
        return self.FIXED_REPLY


class _FakeOrch:
    """模拟 Orchestrator，只暴露 bridge 会用到的属性。"""

    def __init__(self) -> None:
        self.engine = _FakeEngine()
        self.target_user_id = "test_user"
        self.history: list = []
        # bridge 可能 getattr 的其他属性
        self.personality_resolver = None
        self.emotion_manager = None
        self.relationship_state = None
        self.memory_store = None
        self.vector_memory = None

    def get_self_model_context(self) -> Optional[str]:
        return None


class _TypeA_Runtime:
    """runtime.py RuntimeCore（有 process()）."""

    def __init__(self, final_reply: Optional[str] = "来自 Runtime 的完整回复") -> None:
        self.final_reply = final_reply
        self.process_calls = 0

    def process(self, event: Any) -> _FakeCtx:
        self.process_calls += 1
        return _FakeCtx(final_reply=self.final_reply)


class _TypeB_Runtime:
    """runtime_core.py RuntimeCore（只有 inject_event 没有 process）."""

    def __init__(self) -> None:
        self.inject_calls: list = []

    def inject_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        self.inject_calls.append((event_type, dict(event_data)))


class _BrokenTypeA_Runtime:
    """类型A但 process() 抛异常."""

    def process(self, event: Any) -> _FakeCtx:
        raise RuntimeError("模拟 Runtime.process 内部错误")


class _BrokenTypeB_Runtime:
    """类型B但 inject_event() 抛异常."""

    def inject_event(self, event_type: str, event_data: Dict[str, Any]) -> None:
        raise RuntimeError("模拟 Runtime.inject_event 内部错误")


class _UnknownRuntime:
    """未知类型：既无 process 也无 inject."""

    pass


# ============================================================
# Tests
# ============================================================
class TestOrchestratorRuntimeBridge(unittest.TestCase):
    def setUp(self) -> None:
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        self.Bridge = OrchestratorRuntimeBridge
        self.orch = _FakeOrch()

    # ============================================================
    # 1. 类型 A：有 process() → 完整生成回复
    # ============================================================
    def test_type_a_full_process_with_reply(self):
        rt = _TypeA_Runtime(final_reply="羽依Runtime回复")
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, "羽依Runtime回复")
        self.assertEqual(bridge.runtime_call_count, 1)
        self.assertEqual(bridge.runtime_state_count, 0)
        self.assertEqual(bridge.legacy_call_count, 0)
        self.assertEqual(bridge.last_reply_source, "runtime")
        self.assertEqual(bridge.last_runtime_mode, "full_process")
        self.assertIsNone(bridge.last_runtime_error)

    def test_type_a_empty_final_reply_fallback_legacy(self):
        rt = _TypeA_Runtime(final_reply=None)
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("你好")

        # 回复来自 legacy
        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        self.assertEqual(bridge.runtime_call_count, 0)  # process 没产出回复
        self.assertEqual(bridge.legacy_call_count, 1)
        self.assertEqual(bridge.last_reply_source, "legacy")
        self.assertIn("empty_final_reply_after_process", bridge.last_runtime_error or "")

    # ============================================================
    # 2. 类型 A：process() 抛异常 → fallback legacy
    # ============================================================
    def test_type_a_process_exception_fallback(self):
        rt = _BrokenTypeA_Runtime()
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        self.assertEqual(bridge.runtime_call_count, 0)
        self.assertEqual(bridge.legacy_call_count, 1)
        self.assertEqual(bridge.last_reply_source, "legacy")
        # 错误信息应带 process_failed 前缀
        self.assertTrue(
            (bridge.last_runtime_error or "").startswith("process_failed:"),
            bridge.last_runtime_error,
        )

    # ============================================================
    # 3. 类型 B：只有 inject_event（runtime_core.py RuntimeCore）
    # ============================================================
    def test_type_b_state_only_with_legacy_reply(self):
        rt = _TypeB_Runtime()
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("测试长内容")

        # 回复来自 legacy
        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        # Runtime 状态计数 +1
        self.assertEqual(bridge.runtime_state_count, 1)
        # Runtime 完整回复计数 = 0
        self.assertEqual(bridge.runtime_call_count, 0)
        # legacy 也 +1（因为回复是 legacy 生成的）
        self.assertEqual(bridge.legacy_call_count, 1)
        # 来源 = runtime_state+legacy
        self.assertEqual(bridge.last_reply_source, "runtime_state+legacy")
        self.assertEqual(bridge.last_runtime_mode, "state_only")
        # 无错误
        self.assertIsNone(bridge.last_runtime_error)

    def test_type_b_inject_event_args_correct(self):
        """验证类型B的 inject_event 参数：event_type='user.input'，content/text/user_id 都有。"""
        rt = _TypeB_Runtime()
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        bridge.handle_message("Hello羽依")

        self.assertEqual(len(rt.inject_calls), 1)
        event_type, event_data = rt.inject_calls[0]
        self.assertEqual(event_type, "user.input")
        self.assertEqual(event_data.get("content"), "Hello羽依")
        self.assertEqual(event_data.get("text"), "Hello羽依")
        self.assertEqual(event_data.get("user_id"), "test_user")

    def test_type_b_inject_event_exception_not_block_reply(self):
        """类型B inject 失败不应阻断回复（legacy 照常生成）。"""
        rt = _BrokenTypeB_Runtime()
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        self.assertEqual(bridge.runtime_state_count, 0)  # 失败不计入
        self.assertEqual(bridge.legacy_call_count, 1)
        self.assertEqual(bridge.last_reply_source, "legacy")
        self.assertTrue(
            (bridge.last_runtime_error or "").startswith("inject_event_failed:"),
            bridge.last_runtime_error,
        )

    # ============================================================
    # 4. 类型 C：Runtime 为 None
    # ============================================================
    def test_type_c_no_runtime_pure_legacy(self):
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=None)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        self.assertEqual(bridge.runtime_call_count, 0)
        self.assertEqual(bridge.runtime_state_count, 0)
        self.assertEqual(bridge.legacy_call_count, 1)
        self.assertEqual(bridge.last_reply_source, "legacy")
        self.assertEqual(bridge.last_runtime_mode, None)
        # Runtime=None 时 last_runtime_error 保持 None（无 Runtime 错误发生）
        self.assertIsNone(bridge.last_runtime_error)
        # last_legacy_reason 记录了为什么走 legacy
        self.assertEqual(bridge.last_legacy_reason, "no_runtime_core")

    # ============================================================
    # 5. 类型 D：未知 Runtime 对象
    # ============================================================
    def test_type_d_unknown_runtime_type(self):
        rt = _UnknownRuntime()
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        self.assertEqual(bridge.runtime_call_count, 0)
        self.assertEqual(bridge.runtime_state_count, 0)
        self.assertEqual(bridge.legacy_call_count, 1)
        self.assertEqual(bridge.last_reply_source, "legacy")
        self.assertTrue(
            (bridge.last_runtime_error or "").startswith("unsupported_runtime_type:"),
            bridge.last_runtime_error,
        )

    # ============================================================
    # 6. 统计计数：多次调用累加
    # ============================================================
    def test_counter_accumulation(self):
        # 先用类型B走 3 次
        rt_b = _TypeB_Runtime()
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt_b)
        for _ in range(3):
            bridge.handle_message("x")
        self.assertEqual(bridge.runtime_state_count, 3)
        self.assertEqual(bridge.legacy_call_count, 3)
        self.assertEqual(bridge.runtime_call_count, 0)

        # 换类型A走 2 次
        rt_a = _TypeA_Runtime(final_reply="回复")
        bridge.set_runtime_core(rt_a)
        for _ in range(2):
            bridge.handle_message("y")
        self.assertEqual(bridge.runtime_call_count, 2)
        # runtime_state 不变
        self.assertEqual(bridge.runtime_state_count, 3)
        # legacy 也不变（类型A不走 legacy）
        self.assertEqual(bridge.legacy_call_count, 3)

        # 换 None 走 1 次
        bridge.set_runtime_core(None)
        bridge.handle_message("z")
        self.assertEqual(bridge.legacy_call_count, 4)

    # ============================================================
    # 7. 向后兼容：新增属性有默认值 / 不抛错
    # ============================================================
    def test_backward_compat_defaults(self):
        bridge = self.Bridge(orchestrator=self.orch)  # runtime=None
        # 所有新属性都可以在 handle_message 之前访问，不抛 AttributeError
        self.assertEqual(bridge.runtime_call_count, 0)
        self.assertEqual(bridge.runtime_state_count, 0)
        self.assertEqual(bridge.legacy_call_count, 0)
        self.assertIsNone(bridge.last_runtime_mode)
        self.assertIsNone(bridge.last_reply_source)
        self.assertIsNone(bridge.last_runtime_error)
        self.assertIsNone(bridge.last_ctx)

    # ============================================================
    # 8. 类型A + ctx._final_reply 是空字符串也要 fallback
    # ============================================================
    def test_type_a_whitespace_final_reply_fallback(self):
        rt = _TypeA_Runtime(final_reply="   \n\t   ")
        bridge = self.Bridge(orchestrator=self.orch, runtime_core=rt)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, _FakeEngine.FIXED_REPLY)
        self.assertEqual(bridge.runtime_call_count, 0)
        self.assertIn("empty_final_reply_after_process", bridge.last_runtime_error or "")


if __name__ == "__main__":
    unittest.main()
