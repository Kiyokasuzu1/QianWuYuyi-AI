# -*- coding: utf-8 -*-
"""
tests/test_phase_4_0_1_lifecycle_executor.py

Phase 4.0.1 SPEC v0.2 §3.1-1：LifecycleExecutor 核心调度逻辑。

目标：
  - 17 阶段按顺序执行（断言 last_stage_order）
  - Stage 1 RECEIVE_EVENT 后 exactly once 调 core._on_event()（P0-修改3）
  - 单个阶段异常 fail-soft：不中断后续阶段，错误写入 ctx._phase_errors
  - Stage 7-13 缺失对应方法时视为 no-op（不抛错）

设计原则：
- 不依赖 RuntimeCore 重型实现，使用 mock Core 验证调度逻辑
- 最小依赖，测试快速
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock


class TestLifecycleExecutorStageOrder(unittest.TestCase):
    """LifecycleExecutor.execute(): 17 阶段按 RUNTIME_LIFECYCLE_ORDER 顺序执行。"""

    def test_execute_runs_17_stages_in_order(self):
        """last_stage_order 长度=17，且与 RUNTIME_LIFECYCLE_ORDER 枚举名顺序一致。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER
        from src.runtime.context.runtime_context import RuntimeContext

        executor = LifecycleExecutor()
        # mock core：对每个 _stage_XX 方法记录调用顺序
        core = MagicMock()
        called_stages: List[str] = []

        from src.runtime.lifecycle_executor import STAGE_TO_METHOD_NAME
        for _stage, method_name in STAGE_TO_METHOD_NAME:
            def _make_tracker(mn: str, record: list) -> Any:
                def _tracker(*args, **kwargs):
                    record.append(mn)
                return _tracker
            setattr(core, method_name, _make_tracker(method_name, called_stages))

        # 无 _on_event 依赖也可跑（本测试不关心 on_event）
        core._on_event = MagicMock()

        ctx = RuntimeContext()
        executor.execute(core, event=None, ctx=ctx)

        # 断言 1：恰好调用 17 次 stage 方法
        self.assertEqual(len(called_stages), 17)
        # 断言 2：last_stage_order 记录的是 stage.name 顺序
        self.assertEqual(len(executor.last_stage_order), 17)
        self.assertEqual(
            executor.last_stage_order,
            [s.name for s in RUNTIME_LIFECYCLE_ORDER],
            "阶段顺序必须与 RUNTIME_LIFECYCLE_ORDER 严格一致",
        )
        # 断言 3：调用的方法名顺序与冻结映射一致
        expected_methods = [mn for _, mn in STAGE_TO_METHOD_NAME]
        self.assertEqual(called_stages, expected_methods)
        # 断言 4：execution_count 递增
        self.assertEqual(executor.execution_count, 1)

    def test_missing_stage_method_is_noop(self):
        """Stage 7-13 方法不存在 → 视为 no-op，不抛错，后续阶段继续。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor, STAGE_TO_METHOD_NAME
        from src.runtime.stages import RUNTIME_LIFECYCLE_ORDER, RuntimeStage
        from src.runtime.context.runtime_context import RuntimeContext

        executor = LifecycleExecutor()
        core = MagicMock(spec=[])  # 空 spec，没有任何 _stage_XX 方法

        class _Ctx:
            _phase_errors: Dict[str, str] = {}
            current_stage: Any = None
            _on_event_call_count: int = 0

        # 即使 core 上没有任何 _stage_XX，execute 也必须走完 17 阶段不崩溃
        ctx = _Ctx()
        result = executor.execute(core, event=None, ctx=ctx)  # type: ignore[arg-type]
        self.assertIs(result, ctx)
        # last_stage_order 仍然记录全部 17 个（即使方法不存在，阶段仍被遍历）
        self.assertEqual(
            executor.last_stage_order,
            [s.name for s in RUNTIME_LIFECYCLE_ORDER],
        )


class TestLifecycleExecutorOnEventExactlyOnce(unittest.TestCase):
    """P0-修改3：Stage 1 RECEIVE_EVENT 后 exactly once 调 core._on_event。"""

    def test_on_event_called_exactly_once_per_execute(self):
        """一次 execute() → _on_event 调用次数必须恰好 1。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor, STAGE_TO_METHOD_NAME
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.events import Event

        executor = LifecycleExecutor()
        core = MagicMock()
        # 给 core 所有 stage 方法空壳
        for _s, mn in STAGE_TO_METHOD_NAME:
            setattr(core, mn, MagicMock())

        # 自定义 _on_event，记录调用次数
        on_event_calls: List[Any] = []
        core._on_event = lambda *a, **k: on_event_calls.append((a, k))

        ctx = RuntimeContext()
        event = Event(
            type="user.input",
            source="test",
            payload={"text": "你好"},
        )
        executor.execute(core, event=event, ctx=ctx)
        # 断言 1：_on_event 恰好被调 1 次
        self.assertEqual(
            len(on_event_calls), 1,
            "_on_event 必须 exactly once，当前调了 %d 次（数据污染风险）"
            % len(on_event_calls),
        )
        # 断言 2：adapted 对象具有 event_type="user.input" 和 data=dict
        args, _kwargs = on_event_calls[0]
        adapted = args[0]
        self.assertEqual(getattr(adapted, "event_type", None), "user.input")
        self.assertEqual(getattr(adapted, "data", None), {"text": "你好"})
        # 断言 3：ctx._on_event_call_count == 1（标记字段用于 test 断言）
        self.assertEqual(getattr(ctx, "_on_event_call_count", 0), 1)
        self.assertTrue(bool(getattr(ctx, "_on_event_called", False)))

    def test_on_event_none_event_degrades(self):
        """event=None 时也 exactly once 调 _on_event（传 tick 事件）。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor, STAGE_TO_METHOD_NAME
        from src.runtime.context.runtime_context import RuntimeContext

        executor = LifecycleExecutor()
        core = MagicMock()
        for _s, mn in STAGE_TO_METHOD_NAME:
            setattr(core, mn, MagicMock())

        calls: List[Any] = []
        core._on_event = lambda *a, **k: calls.append(a)
        ctx = RuntimeContext()
        executor.execute(core, event=None, ctx=ctx)
        self.assertEqual(len(calls), 1)
        adapted = calls[0][0]
        self.assertEqual(getattr(adapted, "event_type", None), "tick")


class TestLifecycleExecutorFailSoft(unittest.TestCase):
    """单个阶段异常 → 记录到 ctx._phase_errors，不中断后续阶段。"""

    def test_stage_exception_recorded_not_raised(self):
        """Stage 3 抛 RuntimeError → execute() 不抛外层，错误记录到 phase_errors。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor, STAGE_TO_METHOD_NAME
        from src.runtime.stages import RuntimeStage
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.events import Event

        executor = LifecycleExecutor()
        core = MagicMock()
        # 所有 stage 空壳
        for _s, mn in STAGE_TO_METHOD_NAME:
            setattr(core, mn, MagicMock())

        # 故意让 Stage 3 (EMOTION_UPDATE) 抛错
        def _boom(*args, **kwargs):
            raise RuntimeError("emotion stage failure")
        core._stage_03_emotion_update.side_effect = _boom
        # 记录 _on_event
        core._on_event = MagicMock()
        # 记录最后阶段（RESPONSE）是否被调（证明没中断）
        stage_16_calls: List[Any] = []
        def _track_16(*a, **k):
            stage_16_calls.append(True)
            # 注意：不能调用原 core._stage_16_response（会再次触发 side_effect → 递归）
        core._stage_16_response.side_effect = _track_16

        ctx = RuntimeContext()
        event = Event(type="x", source="t", payload={"text": "hi"})
        # 断言 1：外层不抛错
        try:
            executor.execute(core, event=event, ctx=ctx)
        except Exception as e:  # noqa: BLE001
            self.fail(f"fail-soft 违约：execute() 向外抛了 {e!r}")
        # 断言 2：阶段 3 的错误在 errors 表中
        errors: Dict[str, str] = getattr(ctx, "_phase_errors", {})
        self.assertIn(RuntimeStage.EMOTION_UPDATE.name, errors)
        self.assertIn("emotion stage failure", errors[RuntimeStage.EMOTION_UPDATE.name])
        # 断言 3：最后阶段仍然执行（fail-soft 不中断）
        self.assertEqual(len(stage_16_calls), 1)
        # 断言 4：全部 17 个阶段仍被记录（顺序不受影响）
        self.assertEqual(len(executor.last_stage_order), 17)
        self.assertEqual(
            executor.last_stage_order[-1], RuntimeStage.RESPONSE.name,
        )

    def test_on_event_exception_is_fail_soft(self):
        """_on_event 内部异常 → 记录但不中断阶段链。"""
        from src.runtime.lifecycle_executor import LifecycleExecutor, STAGE_TO_METHOD_NAME
        from src.runtime.stages import RuntimeStage
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.events import Event

        executor = LifecycleExecutor()
        core = MagicMock()
        for _s, mn in STAGE_TO_METHOD_NAME:
            setattr(core, mn, MagicMock())
        # _on_event 抛错
        def _boom_on_event(*a, **k):
            raise ValueError("_on_event broken")
        core._on_event = _boom_on_event

        stage_16_calls: List[bool] = []
        def _track(*a, **k):
            stage_16_calls.append(True)
        core._stage_16_response.side_effect = _track

        ctx = RuntimeContext()
        executor.execute(
            core,
            event=Event(type="t", source="t", payload={}),
            ctx=ctx,
        )
        # 仍然跑到最后阶段
        self.assertEqual(len(stage_16_calls), 1)
        self.assertEqual(executor.last_stage_order[-1], RuntimeStage.RESPONSE.name)


# =====================================================================
# pytest 入口
# =====================================================================
if __name__ == "__main__":
    unittest.main()
