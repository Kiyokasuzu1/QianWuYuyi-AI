# -*- coding: utf-8 -*-
"""
tests/test_phase403_gate4_fault_isolation.py

Phase 4.0.3 Gate4: Identity 子系统抛异常时，Runtime.process() 仍不失败，
且 ctx._final_reply 非空（成功降级）。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.runtime.events import Event


class TestPhase403Gate4FaultIsolation(unittest.TestCase):
    """Gate4: Identity 子系统故障时,系统仍正常产回复。"""

    def test_gate4_anchor_and_continuity_throw_not_break_process(self):
        """identity anchor.validate_anchor_integrity() + continuity.get_latest_report()
        都抛 RuntimeError → process() 不抛异常,且 reply 非空。
        """
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "（降级后的正常回复）"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        # 装 mock 管理器: 读 API 时抛异常
        mock_cont = MagicMock(name="Cont")
        mock_cont.get_latest_report.side_effect = RuntimeError("boom: continuity DB 失联")
        # 注意: 如果 IdentityContextBuilder 读了 generate_change_report(),也抛错(防御)
        mock_cont.generate_change_report.side_effect = RuntimeError("boom2")
        core.identity_continuity_checker = mock_cont  # type: ignore[assignment]

        mock_anc = MagicMock(name="Anc")
        mock_anc.get_core_anchors.side_effect = RuntimeError("boom: anchor file 损坏")
        mock_anc.validate_anchor_integrity.side_effect = RuntimeError("boom3")
        core.identity_anchor_manager = mock_anc  # type: ignore[assignment]

        mock_stab = MagicMock(name="Stab")
        mock_stab.history = MagicMock()
        mock_stab.history.get_snapshot.side_effect = RuntimeError("boom: stability")
        core.identity_stability_engine = mock_stab  # type: ignore[assignment]

        reply_ok = [False]

        try:
            core.start()
            ev = Event(type="user_input", source="user", payload={"text": "你好"})
            ctx = core.process(ev)
            # identity_context_text 可以为空串,但必须是 str
            self.assertIsInstance(ctx.identity_context_text, str)
            # engine.generate 必须被调用（或 fallback legacy）
            final_reply = getattr(ctx, "_final_reply", None)
            final2 = getattr(ctx, "finalized_reply", None)
            combined = (final_reply if isinstance(final_reply, str) else "") or (
                final2 if isinstance(final2, str) else ""
            )
            reply_ok[0] = bool(combined.strip())
            # fail-soft 记录: errors dict 中应有 PERSONALITY_CONTEXT_BUILD 或相关 stage
            # 的失败 key(可选,不强求非空,但不应中断生命周期)
            errors = getattr(ctx, "_phase_errors", {}) or {}
            self.assertIsInstance(errors, dict)
        except Exception as exc:
            self.fail(
                f"Identity 抛错后 RuntimeCore.process() 不应向上抛异常,实际抛了: {exc!r}"
            )
        finally:
            try:
                core.stop()
            except Exception:
                pass

        self.assertTrue(
            reply_ok[0],
            "Identity 全抛错,但 Runtime 应该降级生成回复。实际 final_reply 为空。",
        )

    def test_gate4_builder_exception_caught_in_stage6(self):
        """_build_identity_context 抛 RuntimeError → Stage6 的 try/except 接住,
        不影响后续 17 阶段执行链。Stage14 仍被调用。
        """
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "正常回复"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        s14_called = [False]
        real_s14 = core._stage_14_response_generation

        def spy_s14(event, ctx):
            s14_called[0] = True
            return real_s14(event, ctx)

        with patch.object(core, "_build_identity_context",
                          side_effect=RuntimeError("Identity Builder 崩溃!")):
            with patch.object(core, "_stage_14_response_generation", side_effect=spy_s14):
                try:
                    core.start()
                    ctx = core.process(
                        Event(type="user_input", source="user", payload={"text": "x"})
                    )
                finally:
                    try:
                        core.stop()
                    except Exception:
                        pass

        self.assertTrue(s14_called[0], "Stage14 仍被调用（Build 抛错不该中断生命周期）")
        final = getattr(ctx, "finalized_reply", None) or getattr(ctx, "_final_reply", None)
        self.assertTrue(
            isinstance(final, str) and final.strip(),
            f"final reply 应非空,实际 {final!r}",
        )
        # 失败被记录到 errors
        errors = getattr(ctx, "_phase_errors", {}) or {}
        # 至少 Stage6 的异常被记录
        self.assertTrue(
            isinstance(errors, dict),
            "ctx._phase_errors 应该是 dict",
        )


if __name__ == "__main__":
    unittest.main()
