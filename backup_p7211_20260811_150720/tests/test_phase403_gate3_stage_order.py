# -*- coding: utf-8 -*-
"""
tests/test_phase403_gate3_stage_order.py

Phase 4.0.3 Gate3: 阶段顺序 = Stage6 Personality Context → Build Identity Context → Stage14 Response Generation。
额外的 R5 缓解: Stage14 内部对 3 个 identity_*_manager 属性的读取次数必须 = 0。
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.runtime.events import Event


class TestPhase403Gate3StageOrder(unittest.TestCase):
    """Gate3: Stage6 → BuildIdentity → Stage14 调用顺序 + Stage14 属性访问为 0。"""

    def test_gate3_stage_calls_order(self):
        """用 patch 记录调用顺序,断言 order 为 [Stage6, BuildIdentity, Stage14]。"""
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        # 让 engine 有值,不阻塞 Stage14
        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "reply"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        call_sequence: list[str] = []

        real_s6 = core._stage_06_personality_context_build
        real_build = core._build_identity_context
        real_s14 = core._stage_14_response_generation

        def spy_s6(event, ctx):
            call_sequence.append("stage6")
            return real_s6(event, ctx)

        def spy_build(ctx):
            call_sequence.append("build_identity_context")
            return real_build(ctx)

        def spy_s14(event, ctx):
            call_sequence.append("stage14")
            return real_s14(event, ctx)

        with patch.object(core, "_stage_06_personality_context_build", side_effect=spy_s6), \
             patch.object(core, "_build_identity_context", side_effect=spy_build), \
             patch.object(core, "_stage_14_response_generation", side_effect=spy_s14):
            try:
                core.start()
                core.process(Event(type="user_input", source="user", payload={"text": "hi"}))
            finally:
                try:
                    core.stop()
                except Exception:
                    pass

        # 断言顺序存在子序列 stage6 → build_identity_context → stage14
        try:
            i6 = call_sequence.index("stage6")
            ib = call_sequence.index("build_identity_context")
            i14 = call_sequence.index("stage14")
        except ValueError:
            self.fail(
                f"三者未全出现。call_sequence={call_sequence}"
            )
        self.assertLess(i6, ib, f"stage6 应在 build_identity_context 之前。seq={call_sequence}")
        self.assertLess(ib, i14, f"build_identity_context 应在 stage14 之前。seq={call_sequence}")

    def test_gate3_stage14_not_access_identity_manager_attrs(self):
        """R5: Stage14 对 3 个 identity_*_manager 属性的 getattr 访问次数 = 0。

        实现: 用属性 descriptor 计数对 self.identity_continuity_checker、
        identity_anchor_manager、identity_stability_engine 的读取次数；
        然后断言 Stage14 执行期间新增的计数 == 0。
        """
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "r"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]

        # Gate3: 装 mock 引擎,保证 BuildIdentityContext 有数据可读
        mock_cont = MagicMock(name="Cont")
        mock_cont.get_latest_report.return_value = MagicMock(continuity_score=0.9, is_continuous=True,
                                                            trait_stability_score=0.8,
                                                            core_value_stability_score=0.9)
        core.identity_continuity_checker = mock_cont  # type: ignore[assignment]
        mock_anc = MagicMock(name="Anc")
        mock_anc.get_core_anchors.return_value = []
        mock_integrity = MagicMock(is_intact=True, max_weight_drift=0.01)
        mock_anc.validate_anchor_integrity.return_value = mock_integrity
        core.identity_anchor_manager = mock_anc  # type: ignore[assignment]
        mock_stab = MagicMock(name="Stab")
        mock_snap = MagicMock(last_is_stable=True, total_reports=1, unstable_reports=0)
        mock_hist = MagicMock()
        mock_hist.get_snapshot.return_value = mock_snap
        mock_stab.history = mock_hist
        core.identity_stability_engine = mock_stab  # type: ignore[assignment]

        # 对 3 个属性装上 getattr 计数 spy
        target_attrs = [
            "identity_continuity_checker",
            "identity_anchor_manager",
            "identity_stability_engine",
        ]
        counters_before: dict[str, int] = {}
        counters_after: dict[str, int] = {}

        real_s14 = core._stage_14_response_generation
        for attr in target_attrs:
            counters_before[attr] = 0
            counters_after[attr] = 0

        class _CounterSpy:
            """对 getattr(core, attr) 计数 —— 用 __getattribute__ 包装不方便,
            我们直接把 Stage14 内所有 getattr(self, "xxx") 调用的计数换成 property 包装。
            由于 Stage14 是 RuntimeCore 的方法,直接对 Stage14 方法前后对比计数器值:
            Stage14 前读一遍属性 → Stage14 后再读一遍 → 如果 Stage14 有 getattr,
            就会通过 property 包装的计数器增加。

            更可靠的方式: 直接通过 patch 将三个属性包装成 descriptor 计数访问。
            这里用 Mock(wraps=属性值) 不够,因为返回的是同一个 Mock 对象。

            方案: 在 Stage14 开始前记录 "读取次数",在 Stage14 期间通过替换
            core.__class__.<attr> 为属性包装器来计数:
            """
            pass

        # 用 property 级包装: 动态创建类属性(仅单实例级实现通过 __getattr__ 不够)
        # 简化策略: 对整个 Stage14 执行过程中,通过「给实例加 _attr_count」+「替换 Stage14
        # 用 super().__getattribute__」难以实现。用 Mock(wraps=core对象) 更简洁:
        # 但 Stage14 是 self.method, 所以可以用一个更保守的方式:
        # 调用 core._stage_14_response_generation 前后: 比较 access counter,
        # 用 object.__getattribute__ 钩子:

        # --- 用 descriptor: 给 RuntimeCore 类临时注入「计数属性」---
        cls = type(core)
        original_getattribute = cls.__getattribute__

        access_count_dict: dict[str, int] = {a: 0 for a in target_attrs}
        stage14_active_flag = {"active": False}

        def _counting_getattribute(self, item):
            val = original_getattribute(self, item)
            if self is core and stage14_active_flag["active"] and item in access_count_dict:
                access_count_dict[item] += 1
            return val

        def spy_s14_wrapped(event, ctx):
            stage14_active_flag["active"] = True
            try:
                return real_s14(event, ctx)
            finally:
                stage14_active_flag["active"] = False

        # 注意: 改 RuntimeCore.__getattribute__ 是全局类修改,用 try/finally 还原
        try:
            cls.__getattribute__ = _counting_getattribute
            # 先跑一次 process 触发 Stage14 (Stage6 会访问 3 个 identity 管理器,
            # 但 stage14_active_flag 此时是 False,不会计入)
            with patch.object(core, "_stage_14_response_generation", side_effect=spy_s14_wrapped):
                try:
                    core.start()
                    core.process(Event(type="user_input", source="user", payload={"text": "hi"}))
                finally:
                    try:
                        core.stop()
                    except Exception:
                        pass
        finally:
            cls.__getattribute__ = original_getattribute

        # Stage14 期间 3 个属性读取次数都应 == 0
        offending = {a: n for a, n in access_count_dict.items() if n > 0}
        self.assertEqual(
            offending, {},
            f"Stage14 内部访问了 identity_*_manager 属性 {offending!r},"
            " 违反「Stage14 不决定自己是谁」的 R5 边界。",
        )


if __name__ == "__main__":
    unittest.main()
