# -*- coding: utf-8 -*-
"""
tests/runtime/test_self_model_stage_chain.py

Phase 4.1b / 4.1C：SelfModel 生命周期 Stage 10-13 链路测试（持久化版本）。

本文件把 4.1b 临时验证脚本（G1-G16，已删除）固化为正式测试，并按
Phase 4.1C 策略注入后的新语义更新三条断言：

  - G2 语义更新（4.1C）：Stage 10 入队 **不再** 设置 ctx._self_model_changed；
    changed 只由 Stage 12 实际应用（allow_applied）时设置。
    原因：入队 ≠ 变化，否则被 deny 的提案会触发 Stage 13 空落盘。
  - G9 语义更新（4.1C）：policy deny 的提案不仅「不应用」，还必须
    **出队（reject）** 且 Stage 13 绝不落盘。
  - G16 语义更新（4.1C）：整链 validation 标记必须包含
    policy_decision（Dict[suggestion_id, decision]）逐条决策记录。

新增（4.1C 验收要求）：
  - G17/G18：policy_engine=None 时所有免审批提案 held_no_policy 挂起，
    不应用、不出队、不标记 changed（fail-safe，4.1b 的宽松直批已移除）。

约束遵守：不修改 SelfModelEvolutionPolicy；不修改 RuntimeCore 业务逻辑；
仅新增/更新测试断言；无新增 Runtime/SelfModel 系统。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict
from unittest.mock import patch

from src.contracts.self_model_schema import (
    CoreValue,
    SelfModelChangeSuggestion,
)
from src.personality.trait_state import create_trait_state
from src.runtime.context.runtime_context import RuntimeContext
from src.runtime.self_model.self_model_policy import (
    DEFAULT_EVOLVABLE_FIELDS,
    DEFAULT_IDENTITY_PROTECTED_FIELDS,
    SelfModelEvolutionPolicy,
)


# =====================================================================
# 测试辅助
# =====================================================================

def _make_config(tmp: str) -> Dict[str, Any]:
    """与 tests/test_self_model_system.py 一致的最小可用配置。"""
    return {
        "adapters_enabled": True,
        "experience_enabled": True,
        "cognitive_enabled": False,
        "memory_store_path": os.path.join(tmp, "memory_chain.json"),
        "growth_proposals_path": os.path.join(tmp, "proposals_chain.json"),
        "eval_min_confidence": 0.3,
        "eval_min_evidence_count": 1,
        "sm_min_pattern_confidence": 0.2,
        "sm_require_approval": True,
        "tick_interval_seconds": 1,
        "state_file": os.path.join(tmp, "rt_state_chain.json"),
    }


def _big_snapshot(n: int = 10) -> Dict[str, Any]:
    """≥10 个顶层字段的 SelfModel 快照：1 条 change 时 ratio=0.1 ≤ 0.20，
    确保 allow 用例不会被 change_ratio 误伤成 needs_review。"""
    return {f"field_{i}": {"v": i} for i in range(n)}


def _allow_suggestion(sid_suffix: str = "a") -> SelfModelChangeSuggestion:
    """一条满足 allow 条件的提案：可演化字段 + 高置信 + 免审批。"""
    return SelfModelChangeSuggestion(
        source_type="reflection_insight",
        source_id=f"ins_allow_{sid_suffix}",
        update_stable_traits={"warmth": {"delta": 0.01, "confidence": 0.9}},
        confidence=0.95,
        requires_approval=False,
    )


def _deny_suggestion(sid_suffix: str = "d") -> SelfModelChangeSuggestion:
    """一条必然 deny 的提案：add_core_values 映射到保护字段 fundamental_values。"""
    return SelfModelChangeSuggestion(
        source_type="reflection_insight",
        source_id=f"ins_deny_{sid_suffix}",
        add_core_values=[CoreValue(value_id="cv_x", name="外来价值观")],
        confidence=0.95,
        requires_approval=False,
    )


class _FakeAdapter:
    """Stage 13 边界的记录型假 adapter（只测接线，不测真实持久化）。"""

    def __init__(self, has_p: bool = True):
        self._has_p = has_p
        self.save_calls: list = []

    def has_persistence(self) -> bool:
        return self._has_p

    def save_state(self, note: str = "") -> Dict[str, Any]:
        self.save_calls.append(note)
        return {"ok": True, "note": note}


class _RuntimeChainCase(unittest.TestCase):
    """提供 RuntimeCore 实例构建与 warmth 特质预置的基类。"""

    def _make_rc(self, tmp: str):
        from src.runtime.runtime_core import RuntimeCore

        return RuntimeCore(config=_make_config(tmp))

    def _prime_warmth(self, rc) -> float:
        """refresh 出 warmth 特质，返回初始值（供 allow 应用前后比较）。"""
        rc.refresh_self_model(trait_states={
            "warmth": create_trait_state("warmth", 0.7),
        })
        return next(
            s.current_value
            for s in rc.self_model_manager.identity.stable_traits
            if s.trait == "warmth"
        )


# =====================================================================
# Stage 10：SelfModel Evolution（G1-G4，G2 为 4.1C 语义更新）
# =====================================================================

class TestStage10Evolution(_RuntimeChainCase):

    def test_g1_suggestion_enqueued(self):
        """G1：insights → 建议生成 → 入队 _pending_self_model_suggestions。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            sug = _allow_suggestion()
            with patch.object(rc, "get_insights", return_value=[{"k": 1}]), \
                 patch.object(rc, "generate_self_model_suggestion_from_insights",
                              return_value=[sug]):
                rc._stage_10_self_model_evolution(None, ctx)
            self.assertIn(sug, rc._pending_self_model_suggestions)
            self.assertEqual(ctx.self_model_chain["evolution"]["enqueued"], 1)

    def test_g2_enqueue_does_not_mark_changed(self):
        """G2（4.1C 语义更新）：Stage 10 入队 **不** 设置 _self_model_changed。

        4.1b 旧语义：入队即 changed（会导致 deny 提案触发 Stage 13 空落盘）。
        4.1C 新语义：changed 只由 Stage 12 实际应用时设置。
        """
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            sug = _allow_suggestion()
            with patch.object(rc, "get_insights", return_value=[{"k": 1}]), \
                 patch.object(rc, "generate_self_model_suggestion_from_insights",
                              return_value=[sug]):
                rc._stage_10_self_model_evolution(None, ctx)
            self.assertEqual(len(rc._pending_self_model_suggestions), 1)
            self.assertFalse(
                getattr(ctx, "_self_model_changed", False),
                "4.1C：入队不等于变化，_self_model_changed 不应在 Stage 10 设置",
            )

    def test_g3_chain_mark_fields(self):
        """G3：evolution 链标记包含 insights/generated/enqueued/pending。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            with patch.object(rc, "get_insights", return_value=[{"k": 1}]), \
                 patch.object(rc, "generate_self_model_suggestion_from_insights",
                              return_value=[_allow_suggestion()]):
                rc._stage_10_self_model_evolution(None, ctx)
            mark = ctx.self_model_chain["evolution"]
            for key in ("insights", "generated", "enqueued", "pending"):
                self.assertIn(key, mark)

    def test_g4_same_id_dedup(self):
        """G4：同 suggestion_id 重复入队被去重。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            sug = _allow_suggestion()
            with patch.object(rc, "get_insights", return_value=[{"k": 1}]), \
                 patch.object(rc, "generate_self_model_suggestion_from_insights",
                              return_value=[sug]):
                rc._stage_10_self_model_evolution(None, ctx)
                rc._stage_10_self_model_evolution(None, ctx)
            self.assertEqual(len(rc._pending_self_model_suggestions), 1)
            self.assertEqual(ctx.self_model_chain["evolution"]["enqueued"], 0)


# =====================================================================
# Stage 11：SelfModel Reflection（G5）
# =====================================================================

class TestStage11Reflection(_RuntimeChainCase):

    def test_g5_scheduler_skip_recorded(self):
        """G5：调度器 skip 路径写入链标记（reflection 非每轮强制）。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            # 本配置下 reflection_scheduler 未装配（None），Stage 11 会提前返回；
            # 赋真值哨兵使阶段体执行（只测接线，调度逻辑由 patch 接管）。
            rc.reflection_scheduler = object()
            with patch.object(rc, "run_scheduled_reflection",
                              return_value={"skipped": True, "reason": "interval"}):
                rc._stage_11_self_model_reflection(None, ctx)
            self.assertEqual(
                ctx.self_model_chain["reflection"]["status"], "skipped",
            )


# =====================================================================
# Stage 12：SelfModel Validation（G6-G12，G9 为 4.1C 语义更新）
# =====================================================================

class TestStage12Validation(_RuntimeChainCase):

    def test_policy_injected_on_init(self):
        """4.1C 装配验收：__init__ 必须注入真实 SelfModelEvolutionPolicy。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            self.assertIsInstance(rc.policy_engine, SelfModelEvolutionPolicy)

    def test_g6_allow_applies_and_sets_changed(self):
        """G6：policy allow → accept 应用 → 出队 → changed 由本阶段设置。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            before_v = self._prime_warmth(rc)
            sug = _allow_suggestion()
            rc._pending_self_model_suggestions.append(sug)
            with patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot()):
                rc._stage_12_self_model_validation(None, ctx)
            after_v = next(
                s.current_value
                for s in rc.self_model_manager.identity.stable_traits
                if s.trait == "warmth"
            )
            self.assertGreater(after_v, before_v, "allow 提案应真实应用到 manager")
            self.assertEqual(len(rc._pending_self_model_suggestions), 0)
            self.assertTrue(getattr(ctx, "_self_model_changed", False))
            decisions = ctx.self_model_chain["validation"]["policy_decision"]
            self.assertEqual(decisions[sug.suggestion_id], "allow_applied")

    def test_g7_requires_approval_waits_forever(self):
        """G7：requires_approval=True 永远留队等待，连跑两轮不泄漏不应用。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            before_v = self._prime_warmth(rc)
            sug = _allow_suggestion()
            sug.requires_approval = True  # 显式：走外部审批
            rc._pending_self_model_suggestions.append(sug)
            with patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot()):
                rc._stage_12_self_model_validation(None, ctx)
                rc._stage_12_self_model_validation(None, ctx)
            self.assertEqual(len(rc._pending_self_model_suggestions), 1)
            decisions = ctx.self_model_chain["validation"]["policy_decision"]
            self.assertEqual(decisions[sug.suggestion_id], "waiting_approval")
            after_v = next(
                s.current_value
                for s in rc.self_model_manager.identity.stable_traits
                if s.trait == "warmth"
            )
            self.assertEqual(after_v, before_v, "等待审批的提案不得应用")

    def test_g8_deny_rejected_and_dequeued(self):
        """G8：policy deny（保护字段）→ reject 出队，不应用、不标记 changed。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            sug = _deny_suggestion()
            rc._pending_self_model_suggestions.append(sug)
            with patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot()):
                rc._stage_12_self_model_validation(None, ctx)
            self.assertEqual(
                len(rc._pending_self_model_suggestions), 0,
                "4.1C：deny 提案必须出队（4.1b 旧语义是留队）",
            )
            self.assertFalse(getattr(ctx, "_self_model_changed", False))
            validation = ctx.self_model_chain["validation"]
            self.assertEqual(validation["rejected"], 1)
            self.assertEqual(
                validation["policy_decision"][sug.suggestion_id], "deny_rejected",
            )

    def test_g9_deny_never_persists(self):
        """G9（4.1C 语义更新）：deny 提案永不落盘。

        4.1b 旧语义仅断言「deny 不应用」；4.1C 要求 deny 后 Stage 13
        也不得有 save_state 调用（因为 changed 未被设置）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            rc._pending_self_model_suggestions.append(_deny_suggestion())
            adapter = _FakeAdapter(has_p=True)
            rc._self_model_adapter = adapter
            with patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot()):
                rc._stage_12_self_model_validation(None, ctx)
            rc._stage_13_self_model_persistence(None, ctx)
            self.assertEqual(
                adapter.save_calls, [],
                "deny 提案不得触发任何 save_state（永不落盘）",
            )
            self.assertEqual(
                ctx.self_model_chain["persistence"]["reason"], "no_change",
            )

    def test_g10_low_confidence_needs_review_held(self):
        """G10：置信度低于阈值 → needs_review → 挂起留队，不应用。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            before_v = self._prime_warmth(rc)
            sug = _allow_suggestion()
            sug.confidence = 0.5  # < MIN_CONFIDENCE_FOR_APPLY (0.85)
            rc._pending_self_model_suggestions.append(sug)
            with patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot()):
                rc._stage_12_self_model_validation(None, ctx)
            self.assertEqual(len(rc._pending_self_model_suggestions), 1)
            decisions = ctx.self_model_chain["validation"]["policy_decision"]
            self.assertEqual(decisions[sug.suggestion_id], "held_needs_review")
            after_v = next(
                s.current_value
                for s in rc.self_model_manager.identity.stable_traits
                if s.trait == "warmth"
            )
            self.assertEqual(after_v, before_v)

    def test_g11_change_dicts_field_mapping(self):
        """G11（4.1C 修正锁定）：_suggestion_to_change_dicts 输出的 field
        必须落在策略认识的集合内（EVOLVABLE ∪ PROTECTED），
        且 add_core_values 映射到保护字段 fundamental_values。"""
        from src.runtime.runtime_core import RuntimeCore

        valid_fields = DEFAULT_EVOLVABLE_FIELDS | DEFAULT_IDENTITY_PROTECTED_FIELDS
        sug = _deny_suggestion()
        changes = RuntimeCore._suggestion_to_change_dicts(sug)
        self.assertTrue(changes)
        for ch in changes:
            self.assertIn(
                ch["field"], valid_fields,
                f"映射字段 {ch['field']} 不在策略字段集内（4.1b 错位 bug 回归）",
            )
        fields = {ch["field"] for ch in changes}
        self.assertIn("fundamental_values", fields)

    def test_g12_mixed_batch_policy_decision_dict(self):
        """G12：混合批次逐条决策——allow 应用 / deny 出队 / 低置信挂起。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            self._prime_warmth(rc)
            sug_allow = _allow_suggestion("m1")
            sug_deny = _deny_suggestion("m2")
            sug_hold = _allow_suggestion("m3")
            sug_hold.confidence = 0.5
            rc._pending_self_model_suggestions.extend(
                [sug_allow, sug_deny, sug_hold],
            )
            with patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot(30)):
                rc._stage_12_self_model_validation(None, ctx)
            decisions = ctx.self_model_chain["validation"]["policy_decision"]
            self.assertEqual(decisions[sug_allow.suggestion_id], "allow_applied")
            self.assertEqual(decisions[sug_deny.suggestion_id], "deny_rejected")
            self.assertEqual(decisions[sug_hold.suggestion_id], "held_needs_review")
            remaining = [s.suggestion_id for s in rc._pending_self_model_suggestions]
            self.assertEqual(remaining, [sug_hold.suggestion_id])

    # -----------------------------------------------------------------
    # 4.1C 新增验收：policy=None 挂起行为（fail-safe）
    # -----------------------------------------------------------------
    def test_g17_policy_none_holds_all(self):
        """G17（4.1C 新增）：policy_engine=None 时免审批提案全部
        held_no_policy 挂起——不应用、不出队、不标记 changed。
        （4.1b 的宽松直批分支已移除，方向为 fail-safe。）"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            rc.policy_engine = None
            rc._policy_engine = None
            ctx = RuntimeContext()
            before_v = self._prime_warmth(rc)
            sug = _allow_suggestion()
            rc._pending_self_model_suggestions.append(sug)
            rc._stage_12_self_model_validation(None, ctx)
            self.assertEqual(len(rc._pending_self_model_suggestions), 1)
            decisions = ctx.self_model_chain["validation"]["policy_decision"]
            self.assertEqual(decisions[sug.suggestion_id], "held_no_policy")
            self.assertEqual(
                ctx.self_model_chain["validation"]["policy"], "no",
            )
            self.assertFalse(getattr(ctx, "_self_model_changed", False))
            after_v = next(
                s.current_value
                for s in rc.self_model_manager.identity.stable_traits
                if s.trait == "warmth"
            )
            self.assertEqual(after_v, before_v, "无 policy 时不得应用任何提案")

    def test_g18_policy_none_stable_across_rounds(self):
        """G18（4.1C 新增）：policy=None 挂起状态跨轮稳定——连跑两轮
        提案仍留队，不会泄漏应用或丢失。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            rc.policy_engine = None
            rc._policy_engine = None
            ctx = RuntimeContext()
            self._prime_warmth(rc)
            rc._pending_self_model_suggestions.append(_allow_suggestion())
            rc._stage_12_self_model_validation(None, ctx)
            rc._stage_12_self_model_validation(None, ctx)
            self.assertEqual(len(rc._pending_self_model_suggestions), 1)
            self.assertFalse(getattr(ctx, "_self_model_changed", False))


# =====================================================================
# Stage 13：SelfModel Persistence（G13-G15）
# =====================================================================

class TestStage13Persistence(_RuntimeChainCase):

    def test_g13_changed_saves(self):
        """G13：本轮有实际变化（changed=True）→ save_state 落盘并记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            ctx._self_model_changed = True
            adapter = _FakeAdapter(has_p=True)
            rc._self_model_adapter = adapter
            rc._stage_13_self_model_persistence(None, ctx)
            self.assertEqual(len(adapter.save_calls), 1)
            self.assertTrue(ctx.self_model_chain["persistence"]["saved"])

    def test_g14_no_change_skips(self):
        """G14：无变化 → 跳过落盘并记录原因 no_change。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            adapter = _FakeAdapter(has_p=True)
            rc._self_model_adapter = adapter
            rc._stage_13_self_model_persistence(None, ctx)
            self.assertEqual(adapter.save_calls, [])
            mark = ctx.self_model_chain["persistence"]
            self.assertFalse(mark["saved"])
            self.assertEqual(mark["reason"], "no_change")

    def test_g15_no_persistence_attached(self):
        """G15：adapter 未接持久化 → 记录 no_persistence_attached，不调用 save。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            ctx._self_model_changed = True
            adapter = _FakeAdapter(has_p=False)
            rc._self_model_adapter = adapter
            rc._stage_13_self_model_persistence(None, ctx)
            self.assertEqual(adapter.save_calls, [])
            self.assertEqual(
                ctx.self_model_chain["persistence"]["reason"],
                "no_persistence_attached",
            )


# =====================================================================
# 整链可追踪（G16，4.1C 语义更新：validation 必须含 policy_decision）
# =====================================================================

class TestSelfModelChainTraceability(_RuntimeChainCase):

    def test_g16_full_chain_marks_with_policy_decision(self):
        """G16（4.1C 语义更新）：Stage 10→11→12→13 一轮跑完，
        ctx.self_model_chain 四段标记齐全，且 validation 含
        policy_decision 逐条决策字典；allow 应用驱动 Stage 13 落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            rc = self._make_rc(tmp)
            ctx = RuntimeContext()
            self._prime_warmth(rc)
            sug = _allow_suggestion()
            adapter = _FakeAdapter(has_p=True)
            rc._self_model_adapter = adapter
            # 同 G5：本配置下 reflection_scheduler 未装配，赋真值哨兵使 Stage 11 执行
            rc.reflection_scheduler = object()
            with patch.object(rc, "get_insights", return_value=[{"k": 1}]), \
                 patch.object(rc, "generate_self_model_suggestion_from_insights",
                              return_value=[sug]), \
                 patch.object(rc, "run_scheduled_reflection",
                              return_value={"skipped": True}), \
                 patch.object(rc, "get_self_model_full",
                              return_value=_big_snapshot()):
                rc._stage_10_self_model_evolution(None, ctx)
                rc._stage_11_self_model_reflection(None, ctx)
                rc._stage_12_self_model_validation(None, ctx)
                rc._stage_13_self_model_persistence(None, ctx)
            chain = ctx.self_model_chain
            for stage in ("evolution", "reflection", "validation", "persistence"):
                self.assertIn(stage, chain, f"链标记缺少 {stage}")
            # 4.1C：policy_decision 逐条决策是 validation 标记的必需组成
            decisions = chain["validation"].get("policy_decision")
            self.assertIsInstance(decisions, dict)
            self.assertEqual(decisions.get(sug.suggestion_id), "allow_applied")
            self.assertEqual(len(adapter.save_calls), 1)
            self.assertTrue(chain["persistence"]["saved"])


if __name__ == "__main__":
    unittest.main()
