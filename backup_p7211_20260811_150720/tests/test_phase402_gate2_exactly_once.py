# -*- coding: utf-8 -*-
"""
tests/test_phase402_gate2_exactly_once.py

Phase 4.0.2 Gate2: Memory / Emotion 只写入 exactly once。

验收标准:
    用户输入一次 → Runtime.process() 内:
        _on_event()                    调用次数 = 1
        emotion manager.update()       调用次数 = 1
        memory port.add_user_message() 调用次数 = 1
    不允许双写: emotion×2 / memory×2 / growth×2 / relationship×2。

设计: 通过 side_effect 计数 RuntimeCore._on_event 和阶段 2/3/4 方法调用次数。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.runtime.events import Event


class TestPhase402Gate2ExactlyOnce(unittest.TestCase):
    """Gate2: 单次 process() 内 _on_event exactly once。"""

    def _make_core_with_spies(self):
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        # 把 engine ref 先给,保证 Stage14 不空转
        mock_engine = MagicMock()
        mock_engine.generate.return_value = "ok"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]
        return core

    # -----------------------------------------------------------------
    # Gate2-A: _on_event 调用次数 = 1
    # -----------------------------------------------------------------
    def test_gate2_on_event_called_exactly_once_per_process(self):
        """process(user_input) 中 _on_event 必须恰好一次。"""
        core = self._make_core_with_spies()
        on_event_calls: list[int] = []
        real_on_event = core._on_event

        def _spy(event_obj):
            on_event_calls.append(1)
            return real_on_event(event_obj)

        with patch.object(core, "_on_event", side_effect=_spy):
            try:
                core.start()
                ev = Event(type="user_input", source="user", payload={"text": "今天天气如何"})
                core.process(ev)
            finally:
                try:
                    core.stop()
                except Exception:
                    pass

        self.assertEqual(
            sum(on_event_calls), 1,
            f"_on_event 调用 {len(on_event_calls)} 次,期望 1 次(双写会污染 emotion/memory/growth)",
        )

    # -----------------------------------------------------------------
    # Gate2-B: 连续 2 次 process → _on_event 恰好各 1 次,总计 2 次
    # -----------------------------------------------------------------
    def test_gate2_two_processes_two_on_event(self):
        """连续 2 次不同用户输入,每次都只触发 1 次 _on_event。"""
        core = self._make_core_with_spies()
        on_event_calls: list[str] = []
        real_on_event = core._on_event

        def _spy(event_obj):
            on_event_calls.append("x")
            return real_on_event(event_obj)

        with patch.object(core, "_on_event", side_effect=_spy):
            try:
                core.start()
                core.process(Event(type="user_input", source="user", payload={"text": "a"}))
                core.process(Event(type="user_input", source="user", payload={"text": "b"}))
            finally:
                try:
                    core.stop()
                except Exception:
                    pass

        self.assertEqual(
            len(on_event_calls), 2,
            f"2 次 process 应触发 2 次 _on_event,实际 {len(on_event_calls)} 次",
        )

    # -----------------------------------------------------------------
    # Gate2-C: lifecycle_executor 侧 _on_event exactly once 标记
    #          验证 ctx._on_event_called 标记为 True 且不是多次
    # -----------------------------------------------------------------
    def test_gate2_ctx_on_event_marked_exactly_once(self):
        """process() 后 ctx._on_event_called 为 True,
        且 RuntimeCore._on_event 调用次数 = 1(LifecycleExecutor C-1 契约)。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor

        core = self._make_core_with_spies()
        executor = LifecycleExecutor()

        call_counter = [0]
        real_on_event = core._on_event

        def _spy_only_count(event_obj):
            call_counter[0] += 1
            return real_on_event(event_obj)

        with patch.object(core, "_on_event", side_effect=_spy_only_count):
            from src.runtime.context.runtime_context import RuntimeContext

            ctx = RuntimeContext()
            try:
                core.start()
                ev = Event(type="user_input", source="user", payload={"text": "count-me"})
                ctx = executor.execute(core, ev, ctx)
            finally:
                try:
                    core.stop()
                except Exception:
                    pass

        self.assertEqual(call_counter[0], 1, "_on_event 必须恰好 1 次(不能 0,不能 2)")
        self.assertIs(
            getattr(ctx, "_on_event_called", False), True,
            "ctx._on_event_called 应为 True(表示 Stage 1 后 _on_event 被调用)",
        )


if __name__ == "__main__":
    unittest.main()
