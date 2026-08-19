# -*- coding: utf-8 -*-
"""
tests/runtime/test_relationship_communication_strategy.py

Phase 4.2-C：Relationship Context → Communication Strategy 验收测试。

验收目标（任务卡）：
  1. 策略差异：新用户 = 基础解释策略；长期协作用户 = 高级解释策略
  2. 身份不变：IDENTITY_CORE 完全一致
  3. 人格不污染：Relationship 不修改 Personality / Core values / Identity
  4. 红线：策略块禁止亲密化/占有式措辞；Boundary 违规整块丢弃
  5. fail-soft：relationship 关闭时无策略、回复正常

实现形态（方案 C）：
  Stage 14 前静态派生 communication_strategy（系统规则，无 LLM、无评分），
  注入 req_kwargs + context_prompt_blocks 两条现成通道。
"""

from __future__ import annotations

import copy
import os
import tempfile
import unittest
from unittest.mock import patch


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
        "adapters_enabled": True,
        "relationship_enabled": True,
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
    def __init__(self, reply: str = "收到。"):
        self.calls = []
        self._reply = reply

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self._reply


# 驱动关系阶段升级的互动语句（命中 extractor collaboration / trust_building 模式）
_COLLAB_MSGS = [
    "我们一起继续开发羽依项目",
    "我们一直合作优化 Runtime 这个项目",
    "我们一起设计新的阶段管线",
    "我们长期合作打磨这个项目",
    "我们一起开发记忆模块",
    "我们一起合作项目架构",
    "我们一直共同改进羽依",
    "我们一起开发关系模块",
    "我们长期一起迭代项目",
    "我们一起合作完成这个阶段",
]
_TRUST_MSGS = [
    "我很相信你，放心把项目交给你",
    "我觉得你很可靠，稳定又值得信任",
    "我相信你的判断，一直很放心",
    "你真的很可靠，我信任你",
    "我相信你能处理好这些",
]


def _drive_collaboration(rc, collab_rounds: int = 10, trust_rounds: int = 5):
    """用真实互动把关系阶段推到 stable（trust>=0.55 且 familiarity>=0.5）。"""
    for i in range(collab_rounds):
        rc.process(_make_event(_COLLAB_MSGS[i % len(_COLLAB_MSGS)]))
    for i in range(trust_rounds):
        rc.process(_make_event(_TRUST_MSGS[i % len(_TRUST_MSGS)]))


def _strategy_block_of(kwargs) -> str:
    for blk in kwargs.get("context_prompt_blocks") or []:
        content = blk.get("content", "")
        if "【互动策略参考】" in content:
            return content
    return ""


class TestCommunicationStrategy(unittest.TestCase):
    # --------------------------------------------------------
    # Test 1：策略差异 —— 新用户 basic vs 长期协作 advanced
    # --------------------------------------------------------
    def test_strategy_divergence_new_vs_collaborated(self):
        # A：全新用户
        with tempfile.TemporaryDirectory() as tmp_a:
            rc_a = _make_rc(tmp_a)
            ctx_a = rc_a.process(_make_event("你好，今天想聊聊"))
            strat_a = getattr(ctx_a, "communication_strategy", {})
            self.assertEqual(strat_a.get("explanation_depth"), "basic")
            self.assertEqual(strat_a.get("context_reference"), "minimal")
            self.assertFalse(strat_a.get("avoid_redundancy"))
            self.assertEqual(strat_a.get("shared_experience_usage"), "none")

        # B：长期协作用户（真实互动驱动到 stable）
        with tempfile.TemporaryDirectory() as tmp_b:
            rc_b = _make_rc(tmp_b)
            _drive_collaboration(rc_b)
            state = rc_b.get_relationship_state_snapshot()
            self.assertIn(
                state.get("relationship_stage"), ("stable", "deep_collaboration"),
                f"驱动后关系阶段应达 stable，实际={state.get('relationship_stage')}",
            )
            ctx_b = rc_b.process(_make_event("怎么优化 Runtime 的 Stage 14？"))
            strat_b = getattr(ctx_b, "communication_strategy", {})
            self.assertEqual(strat_b.get("explanation_depth"), "advanced")
            self.assertEqual(strat_b.get("context_reference"), "direct")
            self.assertTrue(strat_b.get("avoid_redundancy"))
            self.assertEqual(strat_b.get("shared_experience_usage"), "optional")
            self.assertTrue(strat_b.get("shared_experience_topics"))

    # --------------------------------------------------------
    # Test 2：策略块进入 Response（context_prompt_blocks）
    # --------------------------------------------------------
    def test_strategy_block_injected_into_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            _drive_collaboration(rc)
            engine = _CaptureEngine()
            rc._orchestrator_engine_ref = engine  # type: ignore[attr-defined]

            ctx = rc.process(_make_event("怎么优化 Runtime 的 Stage 14？"))

            self.assertEqual(len(engine.calls), 1)
            block = _strategy_block_of(engine.calls[0])
            self.assertTrue(block, "应存在【互动策略参考】system 块")
            self.assertIn("共同经历", block)
            self.assertIn("解释深度", block)
            # 回复正常落地
            self.assertEqual(getattr(ctx, "_final_reply", None), "收到。")

    # --------------------------------------------------------
    # Test 3：红线 —— 无亲密化措辞；Boundary 违规整块丢弃
    # --------------------------------------------------------
    def test_red_line_no_intimacy_and_boundary_drop(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            _drive_collaboration(rc)
            engine = _CaptureEngine()
            rc._orchestrator_engine_ref = engine  # type: ignore[attr-defined]

            rc.process(_make_event("继续聊项目"))
            block = _strategy_block_of(engine.calls[-1])
            self.assertTrue(block)
            for forbidden in ("更亲密", "更依赖", "主人", "好感度", "亲密度"):
                self.assertNotIn(
                    forbidden, block,
                    f"红线：策略块不得包含「{forbidden}」",
                )

            # Boundary 非 SAFE → 策略块整块丢弃，回复仍正常
            from src.relationship.relationship_boundary import BoundaryLevel

            class _FakeResult:
                level = BoundaryLevel.BLOCK

            with patch(
                "src.relationship.relationship_boundary.RelationshipBoundary"
                ".check_expression",
                return_value=_FakeResult(),
            ):
                rc.process(_make_event("继续聊项目"))
            dropped_block = _strategy_block_of(engine.calls[-1])
            self.assertEqual(
                dropped_block, "",
                "Boundary 违规时策略块必须整块丢弃",
            )
            # 回复仍正常生成（fail-soft）
            self.assertTrue(engine.calls[-1]["user_message"])

    # --------------------------------------------------------
    # Test 4：身份与人格不污染
    # --------------------------------------------------------
    def test_identity_and_personality_untouched(self):
        from src.personality.identity_core import IDENTITY_CORE

        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp)
            identity_before = copy.deepcopy(IDENTITY_CORE)

            _drive_collaboration(rc)
            rc.process(_make_event("怎么优化 Runtime 的 Stage 14？"))

            self.assertEqual(
                IDENTITY_CORE, identity_before,
                "红线：策略派生不得修改 Identity Core",
            )
            self.assertEqual(
                getattr(rc, "_pending_self_model_suggestions", []), [],
                "红线：策略派生不得产生 SelfModel 变更提案",
            )
            pr = getattr(rc, "personality_resolver", None)
            if pr is not None and hasattr(pr, "get_trait_states"):
                traits = pr.get_trait_states()
                self.assertIsNotNone(traits, "人格特质读取不应被策略派生破坏")

    # --------------------------------------------------------
    # Test 5：fail-soft —— relationship 关闭 → 无策略、回复正常
    # --------------------------------------------------------
    def test_fail_soft_when_relationship_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = _make_rc(tmp, relationship_enabled=False)
            engine = _CaptureEngine(reply="正常回复")
            rc._orchestrator_engine_ref = engine  # type: ignore[attr-defined]

            ctx = rc.process(_make_event("你好"))

            self.assertEqual(getattr(ctx, "_final_reply", None), "正常回复")
            strat = getattr(ctx, "communication_strategy", {})
            self.assertEqual(strat, {}, "relationship 关闭时策略必须为空")
            self.assertEqual(_strategy_block_of(engine.calls[0]), "")


if __name__ == "__main__":
    unittest.main()
