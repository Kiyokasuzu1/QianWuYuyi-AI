# -*- coding: utf-8 -*-
"""
tests/test_reply_source_preservation.py

P0-1: 验证 Orchestrator._generate_reply 的 _last_reply_source 保留逻辑

核心场景：
  1. 类型A (bridge 返回 reply)     → source="runtime"
  2. 类型B (bridge 状态更新+legacy) → source="runtime_state+legacy"（不被 legacy_generate 覆盖）
  3. 类型C (bridge=None)            → source="legacy"
  4. bridge 异常                    → source="legacy"
  5. runtime_stats() 包含新字段
  6. runtime_state_count 正确计数
"""

from __future__ import annotations

import unittest
from typing import Any, Optional
from unittest.mock import MagicMock, patch


# ============================================================
# 轻量 Orchestrator：只初始化 _generate_reply 需要的字段
# 跳过真实 __init__（需要 config / MemoryStore / Personality 等重依赖）
# ============================================================
class _LightOrchestrator:
    """模拟 Orchestrator 的 _generate_reply 行为，只保留必要字段。"""

    def __init__(self) -> None:
        self._runtime_bridge: Any = None
        self._runtime_status: str = "disabled"
        self._last_reply_source: Optional[str] = None
        self._runtime_call_count: int = 0
        self._legacy_call_count: int = 0
        self._last_runtime_error: Optional[str] = None
        self._last_legacy_reason: Optional[str] = None
        self._last_runtime_mode: Optional[str] = None
        self._runtime_state_count: int = 0

    # 从 Orchestrator 复制 _generate_reply 逻辑（保持一致）
    def _generate_reply(
        self,
        user_message: str,
        chat_memories: list,
        personality_context: Any,
        emotion_ctx: Any,
        relationship_ctx: Any,
        prompt_blocks: list,
        conversation_id: str,
    ) -> str:
        _bridge_source: Optional[str] = None
        try:
            if self._runtime_bridge is not None:
                runtime_reply = self._runtime_bridge.handle_message(user_message)
                if runtime_reply is not None and isinstance(runtime_reply, str) and runtime_reply.strip():
                    self._runtime_call_count += 1
                    self._last_reply_source = "runtime"
                    self._last_runtime_mode = self._runtime_bridge.last_runtime_mode
                    return runtime_reply
                self._last_runtime_error = (
                    self._runtime_bridge.last_runtime_error
                    or self._runtime_bridge.last_legacy_reason
                    or "empty_runtime_reply"
                )
                _bridge_source = self._runtime_bridge.last_reply_source
        except Exception as exc:  # noqa: BLE001
            self._last_runtime_error = repr(exc)

        reply = self.legacy_generate(
            user_message=user_message,
            chat_memories=chat_memories,
            personality_context=personality_context,
            emotion_ctx=emotion_ctx,
            relationship_ctx=relationship_ctx,
            prompt_blocks=prompt_blocks,
            conversation_id=conversation_id,
        )
        if _bridge_source == "runtime_state+legacy":
            self._last_reply_source = "runtime_state+legacy"
            self._last_runtime_mode = "state_only"
            self._runtime_state_count += 1
        return reply

    def legacy_generate(self, **kwargs: Any) -> str:
        """模拟 legacy_generate：设 _last_reply_source='legacy' 并返回回复。"""
        self._legacy_call_count += 1
        self._last_reply_source = "legacy"
        return "legacy_reply"

    def runtime_stats(self) -> dict:
        return {
            "status": self._runtime_status,
            "runtime_call_count": self._runtime_call_count,
            "runtime_state_count": self._runtime_state_count,
            "legacy_call_count": self._legacy_call_count,
            "last_reply_source": self._last_reply_source,
            "last_runtime_mode": self._last_runtime_mode,
            "last_runtime_error": self._last_runtime_error,
            "last_legacy_reason": self._last_legacy_reason,
        }


# ============================================================
# Fake Bridge（模拟 OrchestratorRuntimeBridge）
# ============================================================
class _FakeBridge:
    """模拟 bridge 的关键属性。"""

    def __init__(
        self,
        reply: Optional[str] = None,
        last_reply_source: str = "legacy",
        last_runtime_mode: Optional[str] = None,
        last_runtime_error: Optional[str] = None,
        last_legacy_reason: Optional[str] = None,
    ) -> None:
        self._reply = reply
        self._last_reply_source = last_reply_source
        self._last_runtime_mode = last_runtime_mode
        self._last_runtime_error = last_runtime_error
        self._last_legacy_reason = last_legacy_reason

    def handle_message(self, user_message: str) -> Optional[str]:
        return self._reply

    @property
    def last_reply_source(self) -> Optional[str]:
        return self._last_reply_source

    @property
    def last_runtime_mode(self) -> Optional[str]:
        return self._last_runtime_mode

    @property
    def last_runtime_error(self) -> Optional[str]:
        return self._last_runtime_error

    @property
    def last_legacy_reason(self) -> Optional[str]:
        return self._last_legacy_reason


class _BrokenBridge:
    """handle_message 抛异常的 bridge。"""

    last_reply_source = None
    last_runtime_mode = None
    last_runtime_error = None
    last_legacy_reason = None

    def handle_message(self, user_message: str) -> Optional[str]:
        raise RuntimeError("bridge exploded")


# ============================================================
# Tests
# ============================================================
class TestReplySourcePreservation(unittest.TestCase):

    def setUp(self) -> None:
        self.orch = _LightOrchestrator()

    def _call_generate(self) -> str:
        return self.orch._generate_reply(
            user_message="test",
            chat_memories=[],
            personality_context=None,
            emotion_ctx={},
            relationship_ctx={},
            prompt_blocks=[],
            conversation_id="test-conv",
        )

    # ============================================================
    # 1. 类型A：bridge 返回完整回复
    # ============================================================
    def test_type_a_runtime_reply(self):
        bridge = _FakeBridge(
            reply="runtime回复",
            last_reply_source="runtime",
            last_runtime_mode="full_process",
        )
        self.orch._runtime_bridge = bridge
        reply = self._call_generate()

        self.assertEqual(reply, "runtime回复")
        self.assertEqual(self.orch._last_reply_source, "runtime")
        self.assertEqual(self.orch._runtime_call_count, 1)
        self.assertEqual(self.orch._runtime_state_count, 0)
        self.assertEqual(self.orch._legacy_call_count, 0)
        self.assertEqual(self.orch._last_runtime_mode, "full_process")

    # ============================================================
    # 2. 类型B：bridge 状态更新 + legacy 回复（核心测试）
    # ============================================================
    def test_type_b_source_not_overwritten(self):
        """P0-1 核心：legacy_generate 设 'legacy' 后，_generate_reply 恢复 'runtime_state+legacy'。"""
        bridge = _FakeBridge(
            reply=None,  # bridge 不生成回复
            last_reply_source="runtime_state+legacy",
            last_runtime_mode="state_only",
            last_runtime_error=None,
            last_legacy_reason="state_only_needs_legacy_reply",
        )
        self.orch._runtime_bridge = bridge
        reply = self._call_generate()

        # 回复来自 legacy
        self.assertEqual(reply, "legacy_reply")
        # 关键：_last_reply_source 不被覆盖为 "legacy"
        self.assertEqual(self.orch._last_reply_source, "runtime_state+legacy")
        # runtime_state_count +1
        self.assertEqual(self.orch._runtime_state_count, 1)
        # runtime_call_count 不变（类型A才+1）
        self.assertEqual(self.orch._runtime_call_count, 0)
        # legacy_call_count +1
        self.assertEqual(self.orch._legacy_call_count, 1)
        # mode 保留
        self.assertEqual(self.orch._last_runtime_mode, "state_only")

    def test_type_b_multiple_calls_state_count_accumulates(self):
        """多次类型B调用，runtime_state_count 累加。"""
        bridge = _FakeBridge(
            reply=None,
            last_reply_source="runtime_state+legacy",
            last_runtime_mode="state_only",
        )
        self.orch._runtime_bridge = bridge
        for _ in range(5):
            self._call_generate()

        self.assertEqual(self.orch._runtime_state_count, 5)
        self.assertEqual(self.orch._legacy_call_count, 5)
        self.assertEqual(self.orch._runtime_call_count, 0)

    # ============================================================
    # 3. 类型C：bridge=None → 纯 legacy
    # ============================================================
    def test_type_c_no_bridge_pure_legacy(self):
        self.orch._runtime_bridge = None
        reply = self._call_generate()

        self.assertEqual(reply, "legacy_reply")
        self.assertEqual(self.orch._last_reply_source, "legacy")
        self.assertEqual(self.orch._runtime_call_count, 0)
        self.assertEqual(self.orch._runtime_state_count, 0)
        self.assertEqual(self.orch._legacy_call_count, 1)
        self.assertIsNone(self.orch._last_runtime_mode)

    # ============================================================
    # 4. bridge 异常 → fallback legacy
    # ============================================================
    def test_bridge_exception_fallback_legacy(self):
        self.orch._runtime_bridge = _BrokenBridge()
        reply = self._call_generate()

        self.assertEqual(reply, "legacy_reply")
        self.assertEqual(self.orch._last_reply_source, "legacy")
        self.assertEqual(self.orch._runtime_state_count, 0)
        self.assertEqual(self.orch._legacy_call_count, 1)
        self.assertIsNotNone(self.orch._last_runtime_error)

    # ============================================================
    # 5. bridge 返回空字符串 → fallback legacy，source 不保留
    # ============================================================
    def test_bridge_empty_reply_fallback_legacy(self):
        bridge = _FakeBridge(
            reply="   ",  # 空白字符串
            last_reply_source="legacy",
            last_runtime_mode=None,
            last_runtime_error="empty_final_reply",
        )
        self.orch._runtime_bridge = bridge
        reply = self._call_generate()

        self.assertEqual(reply, "legacy_reply")
        self.assertEqual(self.orch._last_reply_source, "legacy")
        self.assertEqual(self.orch._runtime_state_count, 0)

    # ============================================================
    # 6. runtime_stats() 包含新字段
    # ============================================================
    def test_runtime_stats_has_new_fields(self):
        stats = self.orch.runtime_stats()
        self.assertIn("runtime_state_count", stats)
        self.assertIn("last_runtime_mode", stats)
        self.assertEqual(stats["runtime_state_count"], 0)
        self.assertIsNone(stats["last_runtime_mode"])

    def test_runtime_stats_after_type_b_call(self):
        bridge = _FakeBridge(
            reply=None,
            last_reply_source="runtime_state+legacy",
            last_runtime_mode="state_only",
        )
        self.orch._runtime_bridge = bridge
        self._call_generate()

        stats = self.orch.runtime_stats()
        self.assertEqual(stats["runtime_state_count"], 1)
        self.assertEqual(stats["last_runtime_mode"], "state_only")
        self.assertEqual(stats["last_reply_source"], "runtime_state+legacy")

    # ============================================================
    # 7. 混合调用：A → B → C → A，计数正确
    # ============================================================
    def test_mixed_calls_counters(self):
        # A: 2次
        bridge_a = _FakeBridge(reply="A回复", last_reply_source="runtime", last_runtime_mode="full_process")
        self.orch._runtime_bridge = bridge_a
        self._call_generate()
        self._call_generate()
        self.assertEqual(self.orch._runtime_call_count, 2)

        # B: 3次
        bridge_b = _FakeBridge(
            reply=None,
            last_reply_source="runtime_state+legacy",
            last_runtime_mode="state_only",
        )
        self.orch._runtime_bridge = bridge_b
        self._call_generate()
        self._call_generate()
        self._call_generate()
        self.assertEqual(self.orch._runtime_state_count, 3)

        # C: 1次
        self.orch._runtime_bridge = None
        self._call_generate()

        # 最终计数
        self.assertEqual(self.orch._runtime_call_count, 2)
        self.assertEqual(self.orch._runtime_state_count, 3)
        self.assertEqual(self.orch._legacy_call_count, 4)  # B(3) + C(1)


if __name__ == "__main__":
    unittest.main()
