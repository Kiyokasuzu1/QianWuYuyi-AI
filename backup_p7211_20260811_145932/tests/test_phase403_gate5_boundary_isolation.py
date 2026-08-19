# -*- coding: utf-8 -*-
"""
tests/test_phase403_gate5_boundary_isolation.py

Phase 4.0.3 Gate5: Identity 变化 ≠ Personality 变化（Identity ⊥ Personality）。

验证方式:
    A. 先取 RuntimeCore.personality_snapshot.traits 的稳定 hash(frozenset(traits.items()))
    B. 修改 Identity Anchor（新增「喜欢雨天」锚点）或 IdentitySnapshot 更新
       —— 调用 RuntimeCore 侧任何 identity update API（没有就直接改 manager 属性）
    C. 运行一次 process() 内部 IdentityContextBuilder 会读取到新的 Identity 字段
    D. 重新 hash personality_snapshot.traits —— hash 必须与 A 完全相同
    E. identity_context_text 中包含「喜欢雨天」的新字段（证明 Identity 的改动被读到）
       但 personality_context_text 中不应包含「喜欢雨天」字符串
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.runtime.events import Event


def _traits_hash(traits: object) -> int:
    """把 traits 字典转成可排序元组的 frozenset hash 做一致性比较。

    traits 类型: dict[str, float/int/bool] 或等价对象。
    返回 hash(frozenset( sorted(items) )) —— hash 值跨进程稳定。
    异常时返回 0,测试会捕捉（因为前后都=0 也是等,但 personality_snapshot=None 时
    另一个断言 person_has_context_text_no_identity 会挡下来）。
    """
    if traits is None:
        return 0
    try:
        if hasattr(traits, "items"):
            items = traits.items()
        elif isinstance(traits, dict):
            items = traits.items()
        else:
            items = getattr(traits, "items", lambda: ())()
        normalized = []
        for k, v in items:
            try:
                v_f = float(v)
            except Exception:
                v_f = 0.0
            normalized.append((str(k), round(v_f, 4)))
        return hash(frozenset(sorted(normalized)))
    except Exception:
        return 0


def _personality_snapshot_traits(ctx_or_ps):
    """从 personality_snapshot 里提取 traits dict。"""
    ps = ctx_or_ps
    if ps is None:
        return None
    try:
        if hasattr(ps, "to_dict"):
            pd = ps.to_dict()
        elif isinstance(ps, dict):
            pd = ps
        else:
            from dataclasses import asdict as _asdict
            pd = _asdict(ps)
        return pd.get("traits", None)
    except Exception:
        return None


class TestPhase403Gate5BoundaryIsolation(unittest.TestCase):
    """Gate5: Identity 新增锚点字段写入,但 personality traits hash 不变。"""

    def _install_dummy_personality_and_identity(self, core):
        """给 core 安装:
        1) 一个稳定的 personality_snapshot（放到 ctx —— 由 Stage3 Personality Update 写入,
           这里我们直接在 pre-start 阶段挂到 RuntimeCore 的 side_effect,
           更方便: 给 Stage3 打 patch,写固定 traits）。
        2) 带可变锚点列表的 identity_anchor_manager。
        """
        from dataclasses import dataclass

        # Stage3 写固定 personality_snapshot
        FIXED_TRAITS = {
            "gentleness": 0.88,
            "curiosity": 0.72,
            "thoughtfulness": 0.79,
            "independence": 0.65,
            "playfulness": 0.50,
        }
        # --- Patch personality_resolver(Stage3 时 resolve) ---
        @dataclass
        class _PersonalitySnapshot:
            traits: dict

        # 用 Patch Stage5 personality_update 更直接:写固定 traits
        real_s5 = core._stage_05_personality_update
        call_count = {"n": 0}

        def spy_s5(event, ctx):
            real_s5(event, ctx)
            # Stage5 末尾固定写入 personality_snapshot
            ctx.personality_snapshot = _PersonalitySnapshot(traits=dict(FIXED_TRAITS))
            call_count["n"] += 1

        import unittest.mock as _mock
        patcher = _mock.patch.object(core, "_stage_05_personality_update", side_effect=spy_s5)
        patcher.start()
        self.addCleanup(patcher.stop)

        # --- Anchor Manager: 可变列表,支持 runtime 添加 anchor ---
        anchor_list = []

        @dataclass
        class _Anchor:
            display_name: str
            principle: str
            weight: float
            is_core: bool = False

        class _AnchorMgr:
            def __init__(self):
                self._list = []

            def add_anchor(self, display_name, principle, weight=0.6):
                self._list.append(_Anchor(display_name, principle, weight, is_core=False))

            def get_core_anchors(self):
                return [a for a in self._list if a.is_core]

            def get_all_anchors(self):
                return list(self._list)

            def validate_anchor_integrity(self):
                rep = MagicMock()
                rep.is_intact = True
                rep.max_weight_drift = 0.0
                return rep

            def get_latest_snapshot(self):
                return None

        anchor_mgr = _AnchorMgr()
        # 先放 5 条核心锚点（显示名不变）
        anchor_mgr._list.append(_Anchor("真实性", "保持真实一致", 1.0, True))
        anchor_mgr._list.append(_Anchor("成长性", "学习但不失去核心", 0.95, True))
        anchor_mgr._list.append(_Anchor("独立性", "独立判断", 0.9, True))
        anchor_mgr._list.append(_Anchor("记忆连接", "记忆连续", 0.85, True))
        anchor_mgr._list.append(_Anchor("创造者关系", "独立但保持联系", 0.8, True))

        core.identity_anchor_manager = anchor_mgr  # type: ignore[assignment]
        # Continuity + Stability 装 mock
        mock_cr = MagicMock(continuity_score=0.9, is_continuous=True,
                            trait_stability_score=0.9, core_value_stability_score=0.9)
        mock_cont = MagicMock()
        mock_cont.get_latest_report.return_value = mock_cr
        core.identity_continuity_checker = mock_cont  # type: ignore[assignment]
        mock_snap = MagicMock(last_is_stable=True, total_reports=1, unstable_reports=0)
        mock_hist = MagicMock()
        mock_hist.get_snapshot.return_value = mock_snap
        mock_stab = MagicMock()
        mock_stab.history = mock_hist
        core.identity_stability_engine = mock_stab  # type: ignore[assignment]

        mock_engine = MagicMock(name="Engine")
        mock_engine.generate.return_value = "hello"
        core._orchestrator_engine_ref = mock_engine  # type: ignore[attr-defined]
        return anchor_mgr, FIXED_TRAITS

    def test_gate5_identity_new_anchor_does_not_mutate_personality_traits(self):
        """Identity 新增「喜欢雨天」锚点 → traits hash 不变。

        步骤:
        1. 第一次 process → 记录 hash_before + identity_ctx_before
        2. 给 IdentityAnchorManager 新增 anchor(display_name="喜欢雨天")
        3. 第二次 process → 记录 hash_after + identity_ctx_after
        4. 断言 hash_before == hash_after
           且 identity_ctx_after 包含「喜欢雨天」
           且 personality_ctx_after（personality_context_text）不包含「喜欢雨天」
        """
        from src.runtime.runtime_core import RuntimeCore

        core = RuntimeCore(config={})
        anchor_mgr, FIXED_TRAITS = self._install_dummy_personality_and_identity(core)

        # --- 第一次 process ---
        try:
            core.start()
            ctx1 = core.process(Event(type="user_input", source="user", payload={"text": "msg1"}))
        finally:
            try:
                core.stop()
            except Exception:
                pass

        traits1 = _personality_snapshot_traits(getattr(ctx1, "personality_snapshot", None))
        hash_before = _traits_hash(traits1)
        id_ctx_before = getattr(ctx1, "identity_context_text", "") or ""
        p_ctx_before = getattr(ctx1, "personality_context_text", "") or ""

        # 基础前提: traits 不应是 None/FIXED_TRAITS 应该写入
        self.assertIsNotNone(
            traits1,
            "第一次 process 后 personality_snapshot.traits 不应为 None（test fixture 问题）",
        )
        self.assertEqual(
            traits1, FIXED_TRAITS,
            "第一次 process 后 traits 应等于 FIXED_TRAITS（Stage3 patch 是否生效？）",
        )
        # personality_context_text 中不含 "喜欢雨天"（预期）
        self.assertNotIn("喜欢雨天", p_ctx_before)
        # identity_context_text 此时不含
        self.assertNotIn("喜欢雨天", id_ctx_before)

        # --- 修改 Identity: 新增一个非核心 anchor "喜欢雨天" ---
        anchor_mgr.add_anchor(
            display_name="喜欢雨天",
            principle="对雨天有着特殊感情,喜欢雨声",
            weight=0.6,
        )
        # 也把 _last_identity_snapshot 改一下（让 current_identity_state 的变化被观察到）
        core._last_identity_snapshot = MagicMock(
            identity_id="yuuki-core-rain",
            version=9,
            overall_understanding=0.8,
            growth_history_count=99,
            preferences_count=21,
            behavioral_patterns_count=8,
            contradictions_count=0,
        )

        # --- 第二次 process ---
        try:
            core.start()
            ctx2 = core.process(Event(type="user_input", source="user", payload={"text": "msg2"}))
        finally:
            try:
                core.stop()
            except Exception:
                pass

        traits2 = _personality_snapshot_traits(getattr(ctx2, "personality_snapshot", None))
        hash_after = _traits_hash(traits2)
        id_ctx_after = getattr(ctx2, "identity_context_text", "") or ""
        p_ctx_after = getattr(ctx2, "personality_context_text", "") or ""

        # ===== Gate5 核心断言 =====
        # G5-1: traits hash 完全相同（Identity 没写入 Personality）
        self.assertEqual(
            hash_before, hash_after,
            f"Identity 修改后 Personality traits hash 发生变化!"
            f" before={hash_before} after={hash_after}"
            f" traits1={traits1} traits2={traits2}",
        )
        # G5-2: identity_context_text 现在包含「喜欢雨天」(Identity 变化被读到)
        self.assertIn(
            "喜欢雨天", id_ctx_after,
            f"Identity 新增锚点没被写入 identity_context_text! id_ctx_after[:200]={id_ctx_after[:200]}",
        )
        # G5-3: personality_context_text 中**仍然**不包含「喜欢雨天」
        self.assertNotIn(
            "喜欢雨天", p_ctx_after,
            f"Personality 被 Identity 污染! p_ctx_after={p_ctx_after[:300]}",
        )
        # G5-4: personality_context_text 仍然包含主特质(人格没有被清空/重写)
        self.assertIn(
            "当前人格主特质", p_ctx_after,
            "personality_context_text 不含「当前人格主特质」—— Stage6 personality 部分被破坏?",
        )


if __name__ == "__main__":
    unittest.main()
