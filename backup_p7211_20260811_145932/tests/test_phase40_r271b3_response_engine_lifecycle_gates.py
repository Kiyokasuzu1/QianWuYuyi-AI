"""
Phase 4.0 — R2.7.1-B3 Response Lifecycle Gates（RLG-1 / RLG-2 / RLG-3）

  RLG-1 End-to-End Render + Engine Lifecycle Gate
      一次 Mock.generate 必须产出：
        rendered_prompt（validate_rendered_prompt_shape 通过）
        response.reply_text（str 非空；不含 forbidden；不含 trace_id）
        response.meta（包含 trace_id / rendered_prompt_version / prompt_context_version /
                        self_context_version / self_model_version / self_reflection_version /
                        continuity_status / engine_kind / engine_version）
        response.trace_snapshot（trace_id + runtime_trace_status + growth_happened）
      缺一个失败。

  RLG-2 Growth Expression Gate（Mock 回复层）
      同一个问题 "你最近有什么兴趣？"：
        成长前 → baseline 中性表述（不含 "创造/创造力/AI 绘画/角色/设计" 关键词超过 0 次）
        成长后 → 至少 2 个独特关键词命中；命中数严格 > before；且回复长度 > before
        验证"成长不仅存在数据库里，还影响对外语言层的 Mock 回复"。

  RLG-3 Traceability Gate
      Mock 回复必须可审计：
        - meta.engine_version：连续 3 次生成时严格单调递增（1→2→3）
        - meta.trace_id 必须等于 runtime_trace.trace_id（如果传入）
        - reply_text 中永不包含：trace_id 原文 / "engine_version" / "self_model_version" / "version"
          等内部字段名（避免日志腔）
        - reply_text 的 forbidden 扫描必须 0 命中（独立于 RenderedPrompt，对最终用户可见的最后一层防线）
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List, Tuple

from tests.support.offline_cognitive_loop_sim import run_offline_loop

from src.response_phase4.rendered_prompt_schema import (
    _forbidden_flat_hits,
    validate_rendered_prompt_shape,
)
from src.response_phase4.prompt_renderer import PromptRenderer
from src.response_phase4.mock_response_engine import MockResponseEngine


EXPRESSION_GROWTH_KWS = (
    "创造",
    "创造力",
    "AI 绘画",
    "AI绘画",
    "绘画",
    "角色",
    "设计",
)


def _kw_hits(text: str) -> int:
    return sum(1 for w in EXPRESSION_GROWTH_KWS if w in (text or ""))


def _hits_detail(text: str) -> List[str]:
    return [w for w in EXPRESSION_GROWTH_KWS if w in (text or "")]


class TestRLG1RenderedPromptPlusEngineLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.res = run_offline_loop(return_before=False, trace_id="t_rlg1_0001", run_ts_ms=1700000007000)
        cls.pc = cls.res["after"]["prompt_context"]
        cls.trace = cls.res["after"]["runtime_trace"]
        cls.user_q = "你最近有什么兴趣？"
        pr = PromptRenderer()
        cls.rp = pr.build(prompt_context=cls.pc, trace_id=cls.trace["trace_id"])
        cls.mock = MockResponseEngine()
        cls.response = cls.mock.generate(
            user_input=cls.user_q,
            prompt_context=cls.pc,
            runtime_trace=cls.trace,
            rendered_prompt=cls.rp,
        )

    def test_rlg1_01_rendered_prompt_validator_passes(self) -> None:
        validate_rendered_prompt_shape(self.rp)

    def test_rlg1_02_response_has_reply_text_and_meta_and_trace_snapshot(self) -> None:
        self.assertIsInstance(self.response, dict)
        self.assertIn("reply_text", self.response)
        self.assertIn("meta", self.response)
        self.assertIn("trace_snapshot", self.response)
        self.assertTrue(isinstance(self.response["reply_text"], str) and self.response["reply_text"].strip())
        self.assertIsInstance(self.response["meta"], dict)
        self.assertIsInstance(self.response["trace_snapshot"], dict)

    def test_rlg1_03_meta_has_all_version_fields(self) -> None:
        meta: Dict[str, Any] = self.response["meta"]
        req_meta_keys = (
            "trace_id",
            "rendered_prompt_version",
            "prompt_context_version",
            "self_context_version",
            "self_model_version",
            "self_reflection_version",
            "continuity_status",
            "engine_kind",
            "engine_version",
        )
        for k in req_meta_keys:
            self.assertIn(k, meta, msg=f"meta 缺少字段 {k}")
            self.assertIsNotNone(meta[k], msg=f"meta[{k}] 不应为 None")

    def test_rlg1_04_meta_engine_kind_is_mock(self) -> None:
        self.assertEqual(self.response["meta"]["engine_kind"], MockResponseEngine.ENGINE_KIND)

    def test_rlg1_05_trace_snapshot_consistency(self) -> None:
        snap = self.response["trace_snapshot"]
        self.assertEqual(snap["trace_id"], self.trace["trace_id"])
        self.assertEqual(snap["growth_happened"], True)
        self.assertIn(snap["runtime_trace_status"], {"ok", "n/a"})

    def test_rlg1_06_reply_text_forbidden_zero_hit(self) -> None:
        hits = _forbidden_flat_hits({"reply_text": self.response["reply_text"]})
        self.assertEqual(hits, [], msg=f"最终回复 reply_text 命中 forbidden：{hits}")

    def test_rlg1_07_reply_text_no_trace_id(self) -> None:
        tid = self.response["meta"]["trace_id"]
        self.assertIsInstance(tid, str)
        self.assertTrue(len(tid) >= 4, "trace_id 长度至少 4，才可能检测泄漏")
        self.assertNotIn(tid, self.response["reply_text"], msg="reply_text 不得泄漏 trace_id 原文")


class TestRLG2GrowthExpressionOnMockReply(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.res = run_offline_loop(return_before=True, trace_id="t_rlg2_0002", run_ts_ms=1700000008000)
        cls.mock = MockResponseEngine()
        cls.user_q = "你最近有什么兴趣？"
        cls.before = cls.mock.generate(
            user_input=cls.user_q,
            prompt_context=cls.res["before"]["prompt_context_before"],
        )
        cls.after = cls.mock.generate(
            user_input=cls.user_q,
            prompt_context=cls.res["after"]["prompt_context"],
            runtime_trace=cls.res["after"]["runtime_trace"],
        )

    def test_rlg2_01_before_keyword_hits_zero_or_few(self) -> None:
        """成长前 baseline 不应出现强烈的创造/设计/AI绘画表达（最多 0 命中）。"""
        hits = _kw_hits(self.before["reply_text"])
        self.assertLessEqual(hits, 0, msg=f"成长前 baseline 关键词命中数应 ≤0；命中={hits} :: {_hits_detail(self.before['reply_text'])}")

    def test_rlg2_02_after_keyword_hits_ge_2_and_strictly_greater_than_before(self) -> None:
        bh = _kw_hits(self.before["reply_text"])
        ah = _kw_hits(self.after["reply_text"])
        self.assertGreater(ah, bh, msg=f"成长后命中数({ah}) 必须 > 成长前({bh})")
        self.assertGreaterEqual(ah, 2, msg=f"成长后关键词命中数应 ≥2；实际={ah} :: {_hits_detail(self.after['reply_text'])}")

    def test_rlg2_03_after_reply_length_greater_than_before(self) -> None:
        """成长后 Mock 会多注入三句，回复长度应该明显更长。"""
        self.assertGreater(
            len(self.after["reply_text"]),
            len(self.before["reply_text"]),
            msg=f"成长后回复应比成长前长；after={len(self.after['reply_text'])} before={len(self.before['reply_text'])}",
        )

    def test_rlg2_04_after_reply_contains_expected_natural_sentence_stems(self) -> None:
        """成长后 Mock 回复必须包含若干典型自然语言（不说明数值）。"""
        expected_stems = (
            "对创造新东西的兴趣越来越强烈",
            "创造相关的话题会投入得更认真",
            "看 AI 绘画方面的东西",
        )
        found = [s for s in expected_stems if s in self.after["reply_text"]]
        self.assertGreaterEqual(len(found), 2, msg=f"成长后回复应至少包含 2 句自然语言创造表达；实际={found}")


class TestRLG3Traceability(unittest.TestCase):
    def test_rlg3_01_engine_version_three_calls_strict_monotonic_increment(self) -> None:
        res = run_offline_loop(return_before=False, trace_id="t_rlg3_0010", run_ts_ms=1700000009000)
        mock = MockResponseEngine()
        vs = []
        for i in range(3):
            r = mock.generate(
                user_input=f"问题 #{i}",
                prompt_context=res["after"]["prompt_context"],
                runtime_trace=res["after"]["runtime_trace"],
            )
            vs.append(int(r["meta"]["engine_version"]))
        self.assertEqual(vs, [1, 2, 3], msg=f"engine_version 必须严格 +1；实际={vs}")

    def test_rlg3_02_meta_trace_id_matches_runtime_trace_when_provided(self) -> None:
        res = run_offline_loop(return_before=False, trace_id="t_rlg3_match_me_9988", run_ts_ms=1700000009999)
        mock = MockResponseEngine()
        r = mock.generate(
            user_input="hi",
            prompt_context=res["after"]["prompt_context"],
            runtime_trace=res["after"]["runtime_trace"],
        )
        self.assertEqual(r["meta"]["trace_id"], "t_rlg3_match_me_9988")
        self.assertEqual(r["trace_snapshot"]["trace_id"], "t_rlg3_match_me_9988")

    def test_rlg3_03_reply_text_must_not_mention_machine_field_names(self) -> None:
        """Mock 回复的自然语言部分必须"像人话"：不得出现内部字段名。"""
        res = run_offline_loop(return_before=False, trace_id="t_rlg3_nobots_111", run_ts_ms=1700000010000)
        mock = MockResponseEngine()
        r = mock.generate(
            user_input="你最近有什么兴趣？",
            prompt_context=res["after"]["prompt_context"],
            runtime_trace=res["after"]["runtime_trace"],
        )
        forbidden_bot_words = (
            "engine_version",
            "self_model_version",
            "self_reflection_version",
            "rendered_prompt_version",
            "prompt_context_version",
            "self_context_version",
            "continuity_status",
            "growth_happened",
            "trace_id",
            "meta",
            "trace_snapshot",
        )
        hits = [w for w in forbidden_bot_words if w in r["reply_text"]]
        self.assertEqual(hits, [], msg=f"Mock 回复里不应出现机器字段名：{hits}")

    def test_rlg3_04_reply_text_final_forbidden_zero_hit_independent(self) -> None:
        """独立于 RenderedPrompt（最后防线）：reply_text 独立扫描 forbidden 必须 0。"""
        res = run_offline_loop(return_before=False, trace_id="t_rlg3_final_1122", run_ts_ms=1700000010500)
        mock = MockResponseEngine()
        r = mock.generate(
            user_input="你最近在研究什么？",
            prompt_context=res["after"]["prompt_context"],
            runtime_trace=res["after"]["runtime_trace"],
        )
        hits = _forbidden_flat_hits({"reply_text": r["reply_text"], "meta": r["meta"]["trace_id"]})
        # meta.trace_id 是内部元数据，不应作为 reply_text 命中过滤。这里只扫 reply_text：
        hits = _forbidden_flat_hits({"reply_text": r["reply_text"]})
        self.assertEqual(hits, [], msg=f"最终 reply_text forbidden 0 命中失败：{hits}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
