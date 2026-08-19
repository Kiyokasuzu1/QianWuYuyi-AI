# -*- coding: utf-8 -*-
"""
tests/test_phase403_gate1_identity_context_in_chain.py

Phase 4.0.3 Gate1: Identity Context 必须真进入 engine.generate() 调用链。

验证方式:
    1. 构造 RuntimeCore + 带真实 Identity 子系统（或 Mock 出合理的只读 API）
    2. 把 core._orchestrator_engine_ref 替换成 MagicMock，捕获 generate() 调用
    3. 运行一次 core.process(ev)
    4. 断言 generate.assert_called_once() 且 kwargs['identity_context'] 非空 str.strip() > 0
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.runtime.events import Event


class TestPhase403Gate1IdentityContextInChain(unittest.TestCase):
    """Gate1: engine.generate 收到的 identity_context 非空。"""

    def _make_runtime_with_identity_engines(self):
        """构造 RuntimeCore + 安装 3 个 Identity 只读引擎（mock）。"""
        from src.runtime.runtime_core import RuntimeCore
        from dataclasses import dataclass

        core = RuntimeCore(config={
            "identity_continuity_enabled": False,  # 用我们自己的 mock 实例，避免真实装配
            "identity_anchor_enabled": False,
            "identity_stability_enabled": False,
        })

        # --- Mock IdentityAnchorManager ---
        @dataclass
        class _MockAnchor:
            display_name: str
            principle: str
            weight: float
            is_core: bool = True

        mock_anchor_mgr = MagicMock(name="AnchorManager")
        mock_anchor_mgr.get_core_anchors.return_value = [
            _MockAnchor(display_name="真实性", principle="保持自我表达的真实与一致性", weight=1.0),
            _MockAnchor(display_name="成长性", principle="从互动中学习但不失去自我", weight=0.95),
            _MockAnchor(display_name="独立性", principle="拥有独立的判断和意志", weight=0.9),
        ]
        # integrity report
        mock_report = MagicMock(name="IntegrityReport")
        mock_report.is_intact = True
        mock_report.max_weight_drift = 0.02
        mock_anchor_mgr.validate_anchor_integrity.return_value = mock_report
        core.identity_anchor_manager = mock_anchor_mgr  # type: ignore[assignment]

        # --- Mock ContinuityReport ---
        mock_continuity_report = MagicMock(name="ContinuityReport")
        mock_continuity_report.continuity_score = 0.91
        mock_continuity_report.is_continuous = True
        mock_continuity_report.trait_stability_score = 0.88
        mock_continuity_report.core_value_stability_score = 0.93
        mock_cont = MagicMock(name="ContinuityChecker")
        mock_cont.get_latest_report.return_value = mock_continuity_report
        core.identity_continuity_checker = mock_cont  # type: ignore[assignment]

        # --- Mock Stability ---
        mock_snap = MagicMock(name="StabilitySnapshot")
        mock_snap.last_is_stable = True
        mock_snap.total_reports = 12
        mock_snap.unstable_reports = 1
        mock_hist = MagicMock(name="StabilityHistory")
        mock_hist.get_snapshot.return_value = mock_snap
        mock_stab = MagicMock(name="StabilityEngine")
        mock_stab.history = mock_hist
        core.identity_stability_engine = mock_stab  # type: ignore[assignment]

        # --- Mock IdentitySnapshot ---
        mock_snapshot = MagicMock(name="IdentitySnapshot")
        mock_snapshot.identity_id = "yuuki-qianwu-v1"
        mock_snapshot.version = 3
        mock_snapshot.overall_understanding = 0.76
        mock_snapshot.growth_history_count = 42
        mock_snapshot.preferences_count = 18
        mock_snapshot.behavioral_patterns_count = 7
        mock_snapshot.contradictions_count = 0
        core._last_identity_snapshot = mock_snapshot  # type: ignore[attr-defined]

        return core

    def test_gate1_engine_generate_identity_context_non_empty(self):
        """core.process → Stage14 调 engine.generate → identity_context 非空且是 str。"""
        core = self._make_runtime_with_identity_engines()

        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "你好呀,很高兴见到你!"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        try:
            core.start()
            ev = Event(type="user_input", source="user", payload={"text": "你好"})
            ctx = core.process(ev)
        finally:
            try:
                core.stop()
            except Exception:
                pass

        self.assertTrue(
            mock_engine.generate.called,
            "engine.generate 未被调用（Stage14 走了哪条路径？）",
        )
        call_args, call_kwargs = mock_engine.generate.call_args
        identity_ctx_arg = call_kwargs.get("identity_context")
        self.assertIsInstance(
            identity_ctx_arg, str,
            f"identity_context 应是 str,实际类型 {type(identity_ctx_arg).__name__}",
        )
        self.assertTrue(
            len(identity_ctx_arg.strip()) > 0,
            f"identity_context 是空字符串；ctx.identity_context_text={ctx.identity_context_text!r}",
        )
        # 同步验证 ctx.has_identity_context()
        self.assertTrue(
            ctx.has_identity_context(),
            "RuntimeContext.has_identity_context() 应为 True",
        )

    def test_gate1_no_identity_engines_still_runs(self):
        """对照组: 3 个 Identity 引擎都为 None → ctx.identity_context_text == ""
        但 process() 仍成功（Gate4 的前置条件）。"""
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        # 3 个 identity 引擎保持 None
        self.assertIsNone(core.identity_continuity_checker)
        self.assertIsNone(core.identity_anchor_manager)
        self.assertIsNone(core.identity_stability_engine)

        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "ok"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        try:
            core.start()
            ctx = core.process(Event(type="user_input", source="user", payload={"text": "hi"}))
        finally:
            try:
                core.stop()
            except Exception:
                pass

        # identity_context_text 可以是空串但不能是 None
        self.assertIsInstance(ctx.identity_context_text, str)
        self.assertEqual(ctx.identity_context_text, "")
        # engine.generate 仍被调用
        self.assertTrue(mock_engine.generate.called)
        call_args, call_kwargs = mock_engine.generate.call_args
        identity_ctx_arg = call_kwargs.get("identity_context")
        # Stage14 传 identity_context=None 或 "" 都算合法降级
        ok = (identity_ctx_arg is None) or (
            isinstance(identity_ctx_arg, str) and not identity_ctx_arg.strip()
        )
        self.assertTrue(ok, f"identity_context 应为 None/空 str,实际 {identity_ctx_arg!r}")


if __name__ == "__main__":
    unittest.main()
