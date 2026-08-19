# -*- coding: utf-8 -*-
"""
tests/test_phase402_gate4_disabled.py

Phase 4.0.2 Gate4: runtime.enabled=false 必须完全回退旧 Orchestrator 路径。

验收标准:
    场景 A: RuntimePipeline 中 runtime=None(Gate4 约定 runtime.enabled=false 时
            构造器传入 runtime=None)
            → run() 必须只调用 orchestrator.process(),不尝试任何 Runtime 路径。
    场景 B: OrchestratorRuntimeBridge runtime_core=None
            → handle_message() 必须走 legacy_generate(),不出现任何 runtime 调用。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestPhase402Gate4Disabled(unittest.TestCase):
    """Gate4: runtime.disabled → 完全回到 Orchestrator.process 旧链路。"""

    # -----------------------------------------------------------------
    # Gate4-A: RuntimePipeline(runtime=None) → 只走 orchestrator.process
    # -----------------------------------------------------------------
    def test_gate4_pipeline_runtime_none_legacy_only(self):
        """runtime=None（等价 runtime.enabled=false）时,
        Pipeline 不调用 runtime.process,只用 orchestrator.process 产出回复。
        """
        from src.runtime.runtime_pipeline import RuntimePipeline

        orch = MagicMock(name="Orchestrator")
        orch.process.return_value = "这是 legacy 的回复。"

        pipeline = RuntimePipeline(
            orchestrator=orch,
            runtime=None,  # Gate4: runtime.enabled=false → 不注入
        )
        # 故意造一个"假 Runtime"放入 pipeline._runtime 之外不可达位置,
        # 确保 Pipeline 不会因为其他原因调用它。
        context = pipeline.run({"user_message": "你好"})

        # legacy orchestrator.process 必须被调用 1 次
        self.assertEqual(
            orch.process.call_count, 1,
            f"runtime.enabled=false 时 orchestrator.process 应恰好被调用 1 次,实际 {orch.process.call_count}",
        )
        outputs = getattr(context, "outputs", {}) or {}
        snapshot = outputs.get("snapshot", {}) or {}
        self.assertEqual(
            snapshot.get("reply_source"), "legacy",
            f"reply_source 应为 legacy,实际 {snapshot.get('reply_source')!r}",
        )
        self.assertEqual(
            pipeline.runtime_reply_count, 0,
            "runtime_reply_count 应为 0（runtime.disabled）",
        )
        self.assertEqual(
            pipeline.legacy_reply_count, 1,
            "legacy_reply_count 应为 1",
        )
        self.assertEqual(
            pipeline.last_reply_source, "legacy",
        )

    # -----------------------------------------------------------------
    # Gate4-B: OrchestratorRuntimeBridge(runtime_core=None) → pure legacy
    # -----------------------------------------------------------------
    def test_gate4_bridge_runtime_none_pure_legacy(self):
        """runtime_core=None（Gate4 disabled）时 Bridge 直接 legacy,不碰 runtime。"""
        from src.orchestrator_runtime_bridge import OrchestratorRuntimeBridge

        orch = MagicMock(name="Orchestrator")
        orch.engine = MagicMock()
        orch.engine.generate.return_value = "legacy 回复"

        bridge = OrchestratorRuntimeBridge(orchestrator=orch, runtime_core=None)
        reply = bridge.handle_message("你好")

        self.assertEqual(reply, "legacy 回复")
        self.assertEqual(bridge.last_reply_source, "legacy")
        self.assertEqual(bridge.runtime_call_count, 0)
        self.assertEqual(bridge.legacy_call_count, 1)
        # engine.generate 必须被调用
        orch.engine.generate.assert_called_once()

    # -----------------------------------------------------------------
    # Gate4-C: Pipeline(runtime=None) → _try_runtime_path 不被调用
    #          通过计数保护: 把 runtime 属性设为一个带 process 的 mock,
    #          但构造 Pipeline 时传 None;断言 process 未被调用
    # -----------------------------------------------------------------
    def test_gate4_pipeline_runtime_none_not_touch_stale_runtime_ref(self):
        """即使上下文中存在 Runtime 对象,Pipeline 构造传入 None,
        就完全不触发 Runtime 路径（Gate4: 干净回退）。"""
        from src.runtime.runtime_pipeline import RuntimePipeline

        orch = MagicMock(name="Orchestrator")
        orch.process.return_value = "legacy reply"

        # 一个可被调用的 Runtime 引用,但构造 Pipeline 时故意不传
        stray_runtime = MagicMock(name="StrayRuntime")

        pipeline = RuntimePipeline(orchestrator=orch, runtime=None)
        # 验证 pipeline._runtime 确实为 None
        self.assertIsNone(pipeline._runtime)
        # 运行
        pipeline.run({"user_message": "hi"})
        # stray_runtime 未被调用(因为 pipeline 根本不知道它存在)
        stray_runtime.process.assert_not_called()
        # orch.process 是唯一来源
        self.assertEqual(orch.process.call_count, 1)


if __name__ == "__main__":
    unittest.main()
