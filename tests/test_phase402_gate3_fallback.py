# -*- coding: utf-8 -*-
"""
tests/test_phase402_gate3_fallback.py

Phase 4.0.2 Gate3: Runtime 失败时必须 fallback 到 legacy_generate()。

验收标准:
    场景 A: RuntimePipeline 中 runtime.process() → 抛异常
            → Pipeline 必须退到 orchestrator.process() 产出正常回复。
    场景 B: OrchestratorRuntimeBridge._try_runtime() 中 runtime.process() 抛异常
            → Bridge 必须 fallback 到 _legacy_generate(),产出正常回复。
    场景 C: Runtime.process() 返回空回复 → 必须 fallback legacy。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestPhase402Gate3Fallback(unittest.TestCase):
    """Gate3: Runtime 失败 → fallback legacy 正常回复。"""

    # -----------------------------------------------------------------
    # Gate3-A: RuntimePipeline 内部 Runtime.process() 抛异常 → fallback legacy
    # -----------------------------------------------------------------
    def test_gate3_pipeline_runtime_exception_fallback_legacy(self):
        """Runtime.process() 抛异常 → Pipeline 必须回落到 orchestrator.process(),
        且 reply_source='legacy',最终 reply 不为空。
        """
        from src.runtime.runtime_pipeline import RuntimePipeline

        bad_runtime = MagicMock(name="BrokenRuntime")
        bad_runtime.process.side_effect = RuntimeError("boom: LLM 服务器失联")

        orch = MagicMock(name="Orchestrator")
        orch.process.return_value = "抱歉,现在网络出了点问题,但我还在。"

        pipeline = RuntimePipeline(orchestrator=orch, runtime=bad_runtime)
        context = pipeline.run({"user_message": "你好"})

        outputs = getattr(context, "outputs", {}) or {}
        snapshot = outputs.get("snapshot", {}) or {}
        reply = snapshot.get("reply", "")
        source = snapshot.get("reply_source")

        # legacy orchestrator.process 必须被调用(因为 runtime 抛错)
        orch.process.assert_called_once()
        self.assertTrue(
            isinstance(reply, str) and reply.strip(),
            f"runtime 抛错后 legacy 回复仍为空: reply={reply!r}",
        )
        self.assertEqual(
            source, "legacy",
            f"runtime 抛错后 reply_source 应为 legacy,实际 {source!r}",
        )
        self.assertEqual(
            pipeline.runtime_reply_count, 0,
            "runtime 抛错后 runtime_reply_count 应为 0",
        )
        self.assertEqual(
            pipeline.legacy_reply_count, 1,
            "runtime 抛错后 legacy_reply_count 应为 1",
        )

    # -----------------------------------------------------------------
    # Gate3-B: Runtime.process() 返回空 final_reply → fallback legacy
    # -----------------------------------------------------------------
    def test_gate3_pipeline_runtime_empty_reply_fallback_legacy(self):
        """Runtime.process() 返回无 final_reply 的 ctx → fallback legacy。"""
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.runtime_pipeline import RuntimePipeline

        empty_runtime = MagicMock(name="EmptyRuntime")
        empty_ctx = RuntimeContext()
        empty_ctx._final_reply = None  # type: ignore[attr-defined]
        empty_runtime.process.return_value = empty_ctx

        orch = MagicMock(name="Orchestrator")
        orch.process.return_value = "嗨,我收到了。"

        pipeline = RuntimePipeline(orchestrator=orch, runtime=empty_runtime)
        context = pipeline.run({"user_message": "嗨"})

        outputs = getattr(context, "outputs", {}) or {}
        snapshot = outputs.get("snapshot", {}) or {}
        self.assertEqual(snapshot.get("reply_source"), "legacy")
        self.assertTrue(orch.process.called)
        self.assertEqual(pipeline.runtime_reply_count, 0)
        self.assertEqual(pipeline.legacy_reply_count, 1)

    # -----------------------------------------------------------------
    # Gate3-C: OrchestratorRuntimeBridge 无鸭子类型 → 直接 process() 抛错
    #          → fallback legacy 且 last_runtime_error 非空
    # -----------------------------------------------------------------
    def test_gate3_bridge_no_ducktyping_process_exception_fallback(self):
        """Bridge 不再 hasattr 判断,直接 runtime.process() 抛错 → fallback legacy。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        bad_runtime = MagicMock(name="BrokenRuntimeForBridge")
        bad_runtime.process.side_effect = RuntimeError("internal: Stage 10 抛异常")

        orch = MagicMock(name="Orchestrator")
        # legacy_generate 读 orch.engine.generate
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "（从兜底逻辑生成的回复）"

        bridge = OrchestratorRuntimeBridge(orchestrator=orch, runtime_core=bad_runtime)
        reply = bridge.handle_message("你好")

        self.assertEqual(
            bridge.last_reply_source, "legacy",
            f"Bridge runtime 抛错后应为 legacy,实际 {bridge.last_reply_source!r}",
        )
        self.assertIsNotNone(
            bridge.last_runtime_error,
            "last_runtime_error 应记录错误原因",
        )
        self.assertTrue(
            isinstance(reply, str) and reply.strip(),
            "fallback legacy 后 reply 不应为空",
        )
        orch.engine.generate.assert_called_once()

    # -----------------------------------------------------------------
    # Gate3-D: Runtime.process() 成功且有 final_reply → 不 fallback legacy
    #          作为 Gate3 的对照组,防止 fallback 过于激进
    # -----------------------------------------------------------------
    def test_gate3_control_runtime_success_no_fallback(self):
        """Runtime 成功生成回复 → legacy 不应被调用。"""
        from src.runtime.context.runtime_context import RuntimeContext
        from src.runtime.runtime_pipeline import RuntimePipeline

        good_runtime = MagicMock(name="GoodRuntime")
        good_ctx = RuntimeContext()
        good_ctx._final_reply = "嗨!我来自 Runtime 的 Stage14。"  # type: ignore[attr-defined]
        good_runtime.process.return_value = good_ctx

        orch = MagicMock(name="Orchestrator")
        orch.process.return_value = "legacy fallback 不应该被触发"

        pipeline = RuntimePipeline(orchestrator=orch, runtime=good_runtime)
        context = pipeline.run({"user_message": "你好"})

        outputs = getattr(context, "outputs", {}) or {}
        snapshot = outputs.get("snapshot", {}) or {}
        self.assertEqual(snapshot.get("reply_source"), "runtime")
        self.assertFalse(
            orch.process.called,
            "Runtime 成功时 legacy orchestrator.process 不应被调用",
        )
        self.assertEqual(pipeline.runtime_reply_count, 1)
        self.assertEqual(pipeline.legacy_reply_count, 0)


if __name__ == "__main__":
    unittest.main()
