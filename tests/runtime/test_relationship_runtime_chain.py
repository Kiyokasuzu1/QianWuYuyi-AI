# -*- coding: utf-8 -*-
"""
tests/runtime/test_relationship_runtime_chain.py

Phase 4.2-B：Relationship Runtime 接线验收测试。

验收目标（任务卡 Step 4）：
  1. 单次互动 → record_relationship_interaction 被调用、关系事件落库
  2. 连续互动 → shared experience / interaction 累积增加（连续性）
  3. relationship_context 注入 Response（engine ref 路径 kwargs 捕获）
  4. fail-soft：RelationshipEngine 异常 → 回复仍正常生成
  5. 红线：Relationship 不直接修改 Personality / Identity / Core Values

接线形态（任务卡方案 B）：
  Stage 3 Emotion Update 子步骤 Relationship Update
  → record_relationship_interaction（写）
  → C.5 RelationshipRuntimeAdapter 只读快照 → ctx.relationship_snapshot（读）
  → Stage 14 relationship_context（消灭硬编码 {}）
"""

from __future__ import annotations

import copy
import os
import tempfile
import unittest


def _make_event(text: str):
    from src.runtime.events import Event

    return Event(
        type="user_input",
        source="user",
        payload={"text": text, "content": text},
    )


def _make_rc(tmp: str, **extra_cfg):
    from src.runtime.runtime_core import RuntimeCore

    cfg = {
        # 注意：Relationship 四件套装配在 __init__ 中被 adapters_enabled 总闸包裹
        # （runtime_core.py:369 → 672），缺省会静默不装配。
        "adapters_enabled": True,
        "relationship_enabled": True,
        # 4.1D 经历持久化：开启后当轮 experience→memory 映射可得，
        # record 的 evidence_id 才能挂上真实记忆证据（evaluator MIN_EVIDENCE_COUNT=1）
        "experience_enabled": True,
        "experience_journal_path": os.path.join(tmp, "exp_journal.jsonl"),
        "event_driven_enabled": True,
        "memory_store_path": os.path.join(tmp, "mem.json"),
        "growth_proposals_path": os.path.join(tmp, "gp.json"),
        "state_file": os.path.join(tmp, "runtime.json"),
    }
    cfg.update(extra_cfg)
    return RuntimeCore(config=cfg)


class _CaptureEngine:
    """捕获 engine.generate kwargs 的假引擎（Stage 14 降级路径）。"""

    def __init__(self, reply: str = "收到，我在。"):
        self.calls = []
        self._reply = reply

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self._reply


class TestRelationshipRuntimeChain(unittest.TestCase):
    # --------------------------------------------------------
    # Test 1：单次互动 → record 被调用、关系事件落库
    # --------------------------------------------------------
    def test_single_interaction_records_relationship_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)

            # 包装 record 统计真实调用次数
            calls = []
            orig = rc.record_relationship_interaction

            def _spy(**kwargs):
                calls.append(kwargs)
                return orig(**kwargs)

            rc.record_relationship_interaction = _spy  # type: ignore[method-assign]

            ctx = rc.process(_make_event("继续开发羽依项目"))

            self.assertEqual(len(calls), 1, "每轮用户输入应触发一次 record")
            self.assertEqual(calls[0]["user_message"], "继续开发羽依项目")

            model = rc.get_relationship_model_snapshot()
            self.assertIsNotNone(model)
            self.assertGreaterEqual(model["total_interactions"], 1)
            # "开发+项目"命中 collaboration 模式 → shared experience 落库
            self.assertGreaterEqual(model["total_shared_experiences"], 1)

            # 读路径：ctx 关系快照可用
            snap = getattr(ctx, "relationship_snapshot", None)
            self.assertIsInstance(snap, dict)
            self.assertTrue(snap.get("relationship_available"))

    # --------------------------------------------------------
    # Test 2：连续互动 → shared experience / interaction 递增
    # --------------------------------------------------------
    def test_continuous_interactions_accumulate(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)

            rc.process(_make_event("我们一起继续开发羽依项目"))
            model_1 = rc.get_relationship_model_snapshot()
            rc.process(_make_event("我们一直合作优化 Runtime 这个项目"))
            model_2 = rc.get_relationship_model_snapshot()

            self.assertGreater(
                model_2["total_interactions"], model_1["total_interactions"],
                "第二轮后 total_interactions 应递增",
            )
            self.assertGreater(
                model_2["total_shared_experiences"],
                model_1["total_shared_experiences"],
                "第二轮 collaboration 后 shared_experiences 应递增",
            )

            # 状态持久化：repository 双 save 已执行（重新 load 数据仍在）
            state = rc.get_relationship_state_snapshot()
            self.assertIsNotNone(state)
            self.assertGreater(state.get("familiarity", 0.0), 0.0)

    # --------------------------------------------------------
    # Test 3：relationship_context 注入 Response（不再是硬编码 {}）
    # --------------------------------------------------------
    def test_relationship_context_injected_into_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            engine = _CaptureEngine()
            rc._orchestrator_engine_ref = engine  # type: ignore[attr-defined]

            ctx = rc.process(_make_event("我们一起继续开发羽依项目"))

            self.assertEqual(len(engine.calls), 1, "engine ref 路径应被调用一次")
            rel_ctx = engine.calls[0].get("relationship_context")
            self.assertIsInstance(rel_ctx, dict)
            self.assertNotEqual(rel_ctx, {}, "relationship_context 不应再是硬编码 {}")
            self.assertIn("关系概述", rel_ctx)
            self.assertIn("关系阶段", rel_ctx)
            # 回复正常落地
            self.assertEqual(getattr(ctx, "_final_reply", None), "收到，我在。")

    # --------------------------------------------------------
    # Test 4：fail-soft —— RelationshipEngine 异常不影响回复
    # --------------------------------------------------------
    def test_fail_soft_when_engine_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)

            class _BoomEngine:
                def process_interaction(self, **kwargs):
                    raise RuntimeError("boom")

            rc.relationship_intelligence_engine = _BoomEngine()
            engine = _CaptureEngine(reply="兜底正常回复")
            rc._orchestrator_engine_ref = engine  # type: ignore[attr-defined]

            ctx = rc.process(_make_event("我们一起继续开发羽依项目"))

            # 回复仍正常生成（relationship 失败被隔离）
            self.assertEqual(getattr(ctx, "_final_reply", None), "兜底正常回复")
            # relationship_context 仍能产出（读路径不受写路径异常影响）
            rel_ctx = engine.calls[0].get("relationship_context")
            self.assertIsInstance(rel_ctx, dict)
            # Memory / 主流程未被中断：process 正常返回 ctx
            self.assertIsNotNone(ctx)

    # --------------------------------------------------------
    # Test 5：红线 —— Relationship 不直接修改 Personality / Identity
    # --------------------------------------------------------
    def test_red_line_no_personality_or_identity_mutation(self):
        from src.personality.identity_core import IDENTITY_CORE

        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            identity_before = copy.deepcopy(IDENTITY_CORE)

            rc.process(_make_event("我们一起长期合作这个项目，我很相信你"))
            rc.process(_make_event("你一直是我最信任的伙伴，我们继续开发项目"))

            # 1) Identity Core 不变
            self.assertEqual(
                IDENTITY_CORE, identity_before,
                "红线：Relationship 不得修改 Identity Core",
            )
            # 2) SelfModel 待审批队列不被 Relationship 触发
            self.assertEqual(
                getattr(rc, "_pending_self_model_suggestions", []), [],
                "红线：Relationship 不得直接产生 SelfModel 变更提案",
            )
            # 3) 人格提案/成长队列不被 Relationship 直接写入
            ga = getattr(rc, "growth_adapter", None)
            if ga is not None:
                pending = getattr(ga, "pending_proposals", None)
                if pending is not None:
                    self.assertEqual(
                        list(pending), [],
                        "红线：Relationship 不得直接写入 Growth 提案",
                    )


if __name__ == "__main__":
    unittest.main()
