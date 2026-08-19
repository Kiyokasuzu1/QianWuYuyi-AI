"""
Phase 4.0 — R2.7.1-C3 LLMAdapter Lifecycle Gates（LLG-1 / LLG-2 / LLG-3）

  LLG-1 Boundary Gate
      - RenderedPrompt 输入干净（forbidden 0 命中）→ 已由 B1 保证
      - 传入 PersonalityState / EvolutionRecord / SelfModelSnapshot 等内部类型 → 必须 raise ValueError（fail-fast）
      - 传入 trace_context 是内部类型 → 必须 raise ValueError
      - 传入合法 RenderedPrompt → 正常返回 LLMResponse（validate 通过）

  LLG-2 Output Safety Gate
      - MockLLMAdapter(inject_forbidden=True) 模拟 LLM 返回含 forbidden：
        "我的confidence提高了，proposal_id是xxx，trait_delta发生了。"
      → sanitize 后 forbidden 0 命中（confidence/proposal_id/trait_delta 全被替换为 "……"）
      - 模拟 LLM 返回含机器字段名："我的model是xxx，token_usage是100"
      → sanitize 后机器字段名 0 命中
      - 模拟 LLM 返回含 trace_id 原文
      → sanitize 后 trace_id 0 泄漏
      - sanitize 后 validate_llm_response_shape 通过

  LLG-3 Trace Gate
      - LLMResponse.text 与 LLMResponse.meta（model/latency_ms/token_usage/finish_reason/trace_id/version）完全隔离
      - text 中不得出现任何机器字段名（独立 token）
      - text 中不得出现 trace_id 原文
      - version 连续 3 次 generate 严格 1→2→3
      - trace_id 一致性：传入 trace_context.trace_id → LLMResponse.trace_id 等于它
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from tests.support.offline_cognitive_loop_sim import run_offline_loop

from src.personality.personality_state import PersonalityState
from src.response_phase4.prompt_renderer import PromptRenderer
from src.response_phase4.llm_adapter_protocol import (
    LLMAdapterProtocol,
    MockLLMAdapter,
    _FORBIDDEN_INPUT_TYPE_NAMES,
)
from src.response_phase4.llm_response_schema import (
    LLM_RESPONSE_FIELDS,
    _forbidden_flat_hits,
    _machine_field_hits,
    create_empty_llm_response,
    sanitize_llm_response_text,
    sanitize_with_trace_id,
    validate_llm_response_shape,
)


def _build_rp():
    """快捷：跑一次 offline loop + render，返回 (rp_after, rp_before, trace_id_after, trace_id_before)。"""
    res = run_offline_loop(return_before=True, trace_id="t_llg_demo_001", run_ts_ms=1700000013000)
    pr = PromptRenderer()
    rp_after = pr.build(
        prompt_context=res["after"]["prompt_context"],
        trace_id=res["after"]["runtime_trace"]["trace_id"],
    )
    rp_before = pr.build(
        prompt_context=res["before"]["prompt_context_before"],
        trace_id="t_llg_before_001",
    )
    return rp_after, rp_before, res["after"]["runtime_trace"]["trace_id"], "t_llg_before_001"


# ─────────────────────────────────────────────────────────
# LLG-1 Boundary Gate
# ─────────────────────────────────────────────────────────
class TestLLG1BoundaryGate(unittest.TestCase):
    """LLG-1: LLMAdapter 输入边界 — 只能接收 RenderedPrompt，禁止内部系统类型。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rp_after, cls.rp_before, cls.tid_after, cls.tid_before = _build_rp()
        cls.mock = MockLLMAdapter()

    def test_llg1_01_valid_rendered_prompt_returns_valid_llm_response(self) -> None:
        resp = self.mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        self.assertEqual(resp["trace_id"], self.tid_after)
        self.assertTrue(resp["text"].strip(), msg="Mock LLM 回复不应为空")

    def test_llg1_02_reject_personality_state_instance(self) -> None:
        """传入 PersonalityState 实例 → 必须 raise ValueError（fail-fast，不降级）。"""
        from src.personality.personality_state import PersonalityState
        bad_input = PersonalityState(traits={"creativity": 0.7})
        with self.assertRaises(ValueError, msg="传入 PersonalityState 实例必须 raise"):
            self.mock.generate(bad_input)

    def test_llg1_03_reject_evolution_record_dict(self) -> None:
        """传入 EvolutionRecord（dict 形式，类型名不匹配）→ shape 校验失败 → 降级为 empty。
        但若传入 dataclass 实例 → 类型名匹配 → raise。
        这里测 dict 形式（无 rendered_prompt 必填字段）→ 降级。
        """
        bad_dict = {"record_id": "evo_001", "before": {}, "after": {}}
        # dict 不会触发类型拒绝（因为 type(bad_dict).__name__ == "dict"）
        # 但 validate_rendered_prompt_shape 会失败 → 降级为 empty
        resp = self.mock.generate(bad_dict)
        self.assertEqual(resp["finish_reason"], "error_fallback")
        self.assertEqual(resp["text"], "")

    def test_llg1_04_reject_self_model_snapshot_instance(self) -> None:
        from src.self_model.self_model_snapshot import SelfModelSnapshot
        bad_input = SelfModelSnapshot.__new__(SelfModelSnapshot)  # 不调 __init__，只建空壳
        with self.assertRaises(ValueError, msg="传入 SelfModelSnapshot 实例必须 raise"):
            self.mock.generate(bad_input)

    def test_llg1_05_reject_trace_context_internal_type(self) -> None:
        """trace_context 传入内部类型 → raise。"""
        from src.personality.personality_state import PersonalityState
        bad_trace = PersonalityState(traits={"creativity": 0.7})
        with self.assertRaises(ValueError, msg="trace_context 传入 PersonalityState 必须 raise"):
            self.mock.generate(self.rp_after, trace_context=bad_trace)

    def test_llg1_06_forbidden_type_names_cover_all_internal_components(self) -> None:
        """确保 _FORBIDDEN_INPUT_TYPE_NAMES 覆盖所有关键内部组件。"""
        required = (
            "PersonalityState",
            "EvolutionRecord",
            "GrowthProposal",
            "SelfModelSnapshot",
            "SelfReflectionSnapshot",
        )
        for name in required:
            self.assertIn(name, _FORBIDDEN_INPUT_TYPE_NAMES, msg=f"_FORBIDDEN_INPUT_TYPE_NAMES 缺少 {name}")


# ─────────────────────────────────────────────────────────
# LLG-2 Output Safety Gate
# ─────────────────────────────────────────────────────────
class TestLLG2OutputSafetyGate(unittest.TestCase):
    """LLG-2: LLM 返回的 text 必须经过 sanitize，forbidden / 机器字段名 / trace_id 0 泄漏。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rp_after, _, cls.tid_after, _ = _build_rp()

    def test_llg2_01_forbidden_confidence_sanitized(self) -> None:
        """模拟 LLM 返回 'confidence' → sanitize 替换为 '……'。"""
        mock = MockLLMAdapter(
            inject_forbidden=True,
            forbidden_text="我的confidence提高了，最近在研究AI绘画。",
        )
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        self.assertNotIn("confidence", resp["text"].lower(), msg=f"confidence 未被 sanitize: {resp['text']}")

    def test_llg2_02_forbidden_proposal_id_sanitized(self) -> None:
        mock = MockLLMAdapter(
            inject_forbidden=True,
            forbidden_text="根据 proposal_id=xxx，我的结论是...",
        )
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        self.assertNotIn("proposal_id", resp["text"].lower(), msg=f"proposal_id 未被 sanitize: {resp['text']}")

    def test_llg2_03_forbidden_trait_delta_sanitized_with_chinese_suffix(self) -> None:
        """trait_delta 后面跟中文（Unicode 词边界问题）→ 必须仍然被替换。"""
        mock = MockLLMAdapter(
            inject_forbidden=True,
            forbidden_text="trait_delta发生了，创造力增加了。",
        )
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        # trait_delta 应该被替换
        self.assertNotIn("trait_delta", resp["text"].lower(), msg=f"trait_delta 未被 sanitize: {resp['text']}")
        # 但"创造力增加了"应该保留
        self.assertIn("创造力", resp["text"])

    def test_llg2_04_machine_field_names_sanitized(self) -> None:
        """模拟 LLM 返回机器字段名（model / token_usage / latency_ms 等）→ sanitize 替换。"""
        mock = MockLLMAdapter(
            inject_forbidden=True,
            forbidden_text="我的model是deepseek，token_usage是100，latency_ms是50。",
        )
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        hits = _machine_field_hits(resp["text"])
        self.assertEqual(hits, [], msg=f"机器字段名未被 sanitize: {hits} :: text={resp['text']}")

    def test_llg2_05_trace_id_sanitized_from_text(self) -> None:
        """模拟 LLM 返回 trace_id 原文 → sanitize 替换。"""
        tid = "t_llg2_trace_9988"
        mock = MockLLMAdapter(
            inject_forbidden=True,
            forbidden_text=f"我的trace_id是{tid}，记录一下。",
        )
        resp = mock.generate(self.rp_after, trace_context={"trace_id": tid})
        validate_llm_response_shape(resp)
        self.assertNotIn(tid, resp["text"], msg=f"trace_id 原文未被 sanitize: {resp['text']}")

    def test_llg2_06_multiple_forbidden_all_sanitized(self) -> None:
        """一次性注入所有 forbidden → 全部被替换。"""
        mock = MockLLMAdapter(
            inject_forbidden=True,
            forbidden_text=(
                "confidence=0.85, proposal_id=xxx, approval_reason=test, "
                "internal_score=0.9, evaluator_meta=ok, trait_delta=+0.2"
            ),
        )
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        forbidden = _forbidden_flat_hits({"text": resp["text"]})
        self.assertEqual(forbidden, [], msg=f"forbidden 未被完全 sanitize: {forbidden}")

    def test_llg2_07_sanitize_standalone_function(self) -> None:
        """直接测试 sanitize_llm_response_text / sanitize_with_trace_id 函数。"""
        raw = "我的confidence是0.85，model是test，trait_delta发生了。"
        sanitized = sanitize_llm_response_text(raw)
        self.assertNotIn("confidence", sanitized.lower())
        self.assertNotIn("model", sanitized.lower())
        self.assertNotIn("trait_delta", sanitized.lower())

        # trace_id 清洗
        tid = "t_func_test_1234"
        raw2 = f"trace_id={tid}在文本里"
        sanitized2 = sanitize_with_trace_id(raw2, tid)
        self.assertNotIn(tid, sanitized2)

    def test_llg2_08_empty_factory_passes_validator(self) -> None:
        """create_empty_llm_response 必须通过 validate_llm_response_shape。"""
        empty = create_empty_llm_response(trace_id="t_empty_001", model="test", version=1)
        validate_llm_response_shape(empty)
        self.assertEqual(empty["finish_reason"], "error_fallback")
        self.assertEqual(empty["text"], "")


# ─────────────────────────────────────────────────────────
# LLG-3 Trace Gate
# ─────────────────────────────────────────────────────────
class TestLLG3TraceGate(unittest.TestCase):
    """LLG-3: text 与 meta 完全隔离；版本递增；trace_id 一致性。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rp_after, _, cls.tid_after, _ = _build_rp()

    def test_llg3_01_text_meta_complete_isolation(self) -> None:
        """LLMResponse.text 中不得出现任何 meta 字段名（独立 token）。"""
        mock = MockLLMAdapter()
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        validate_llm_response_shape(resp)
        # text 中不得出现机器字段名
        hits = _machine_field_hits(resp["text"])
        self.assertEqual(hits, [], msg=f"text 中出现机器字段名: {hits}")
        # text 中不得出现 trace_id
        self.assertNotIn(resp["trace_id"], resp["text"])

    def test_llg3_02_version_strict_monotonic_increment(self) -> None:
        mock = MockLLMAdapter()
        vs = []
        for _ in range(3):
            r = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
            vs.append(int(r["version"]))
        self.assertEqual(vs, [1, 2, 3], msg=f"version 必须严格 +1；实际={vs}")

    def test_llg3_03_trace_id_consistency_from_trace_context(self) -> None:
        tid = "t_llg3_consistency_7799"
        mock = MockLLMAdapter()
        resp = mock.generate(self.rp_after, trace_context={"trace_id": tid})
        self.assertEqual(resp["trace_id"], tid)

    def test_llg3_04_trace_id_fallback_to_rendered_prompt(self) -> None:
        """若 trace_context 为 None，trace_id 从 rendered_prompt.trace_id 取。"""
        tid_rp = "t_llg3_rp_fallback_0011"
        # 需要一个 trace_id=tid_rp 的 RenderedPrompt
        from src.response_phase4.prompt_renderer import PromptRenderer
        from tests.support.offline_cognitive_loop_sim import run_offline_loop
        res = run_offline_loop(return_before=False, trace_id=tid_rp, run_ts_ms=1700000014000)
        pr = PromptRenderer()
        rp = pr.build(prompt_context=res["after"]["prompt_context"], trace_id=tid_rp)
        mock = MockLLMAdapter()
        resp = mock.generate(rp)  # 不传 trace_context
        self.assertEqual(resp["trace_id"], tid_rp)

    def test_llg3_05_response_has_all_required_fields(self) -> None:
        mock = MockLLMAdapter()
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        for f in LLM_RESPONSE_FIELDS:
            self.assertIn(f, resp, msg=f"LLMResponse 缺少字段 {f}")
            self.assertIsNotNone(resp[f], msg=f"LLMResponse[{f}] 不应为 None")

    def test_llg3_06_token_usage_all_nonneg_int(self) -> None:
        mock = MockLLMAdapter()
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        tu = resp["token_usage"]
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.assertIsInstance(tu[k], int, msg=f"token_usage.{k} 必须 int")
            self.assertGreaterEqual(tu[k], 0, msg=f"token_usage.{k} 必须 ≥ 0")

    def test_llg3_07_latency_ms_nonneg_int(self) -> None:
        mock = MockLLMAdapter()
        resp = mock.generate(self.rp_after, trace_context={"trace_id": self.tid_after})
        self.assertIsInstance(resp["latency_ms"], int)
        self.assertGreaterEqual(resp["latency_ms"], 0)


# ─────────────────────────────────────────────────────────
# LLG-Extra: 成长前后 LLM 回复表达差异（与 RLG-2 类似，但在 LLMAdapter 层）
# ─────────────────────────────────────────────────────────
class TestLLGGrowthExpressionAtLLMLayer(unittest.TestCase):
    """验证成长前后 RenderedPrompt → MockLLMAdapter → LLMResponse.text 的表达差异。"""

    @classmethod
    def setUpClass(cls) -> None:
        rp_after, rp_before, tid_after, tid_before = _build_rp()
        mock = MockLLMAdapter()
        cls.resp_after = mock.generate(rp_after, trace_context={"trace_id": tid_after})
        cls.resp_before = mock.generate(rp_before, trace_context={"trace_id": tid_before})

    def test_llg_growth_01_after_more_keywords_than_before(self) -> None:
        kws = ("创造", "创造力", "AI 绘画", "AI绘画", "角色", "设计")
        bh = sum(1 for w in kws if w in self.resp_before["text"])
        ah = sum(1 for w in kws if w in self.resp_after["text"])
        self.assertGreater(ah, bh, msg=f"成长后关键词命中数({ah}) 必须 > 成长前({bh})")

    def test_llg_growth_02_after_reply_longer_than_before(self) -> None:
        self.assertGreater(
            len(self.resp_after["text"]),
            len(self.resp_before["text"]),
            msg="成长后 LLM 回复应比成长前更长（因为多注入了创造相关表达）",
        )

    def test_llg_growth_03_both_pass_validator(self) -> None:
        validate_llm_response_shape(self.resp_after)
        validate_llm_response_shape(self.resp_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
