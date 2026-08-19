"""
Phase 4.0 — R2.7.1-B1 Prompt Renderer Contract Gates

A. RenderedPrompt schema 契约（字段白名单 + 信息边界 + version 严格）
B. PromptRenderer 行为：
    - build(prompt_context invalid) → 返回 empty factory（仍通过 validator）
    - build(valid prompt_context) → 字段非空（至少 system_message/self_description/memory_section/relationship_section/task_instruction 有内容）
    - 两次连续 build → version 严格 +1（1→2）
    - 信息边界：6 段 forbidden 关键字 0 命中；trace_id 绝不在正文中出现
"""

from __future__ import annotations

import unittest
from typing import Any, Dict

from tests.support.offline_cognitive_loop_sim import run_offline_loop

from src.response_phase4.rendered_prompt_schema import (
    VERSIONED_FIELDS,
    _forbidden_flat_hits,
    create_empty_rendered_prompt,
    validate_rendered_prompt_shape,
)
from src.response_phase4.prompt_renderer import PromptRenderer


def _all_str_values(obj: Any) -> str:
    """递归拼接所有字符串值（用于扫描 trace_id 是否泄漏到正文）。"""
    out: list = []

    def walk(o: Any) -> None:
        if isinstance(o, str):
            out.append(o)
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return "\n".join(out)


class TestRenderedPromptSchemaContract(unittest.TestCase):
    """Phase 4.0 R2.7.1-B1 Part A: RenderedPrompt contract。"""

    def _ok(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "generated_at": "2026-08-09T12:00:00+00:00",
            "trace_id": "t_ok_001",
            "system_message": "【关于我】\n我是羽依。",
            "self_description": "我是羽依。",
            "memory_section": "最近聊天：XXX",
            "relationship_section": "关系：朋友般熟悉。",
            "task_instruction": "场景：自然对话。",
        }

    def test_sch_01_empty_factory_valid(self) -> None:
        e = create_empty_rendered_prompt(trace_id="t_empty_001", assigned_version=0)
        validate_rendered_prompt_shape(e)
        self.assertEqual(e["version"], 0)
        self.assertEqual(e["trace_id"], "t_empty_001")
        # empty factory 的 6 段都是 ""
        for f in VERSIONED_FIELDS:
            self.assertIsInstance(e[f], str, msg=f"empty factory field {f} 非 str")

    def test_sch_02_missing_field_fails(self) -> None:
        for drop in ("version", "generated_at", "trace_id", *VERSIONED_FIELDS):
            with self.subTest(missing=drop):
                rp = self._ok()
                rp.pop(drop)
                with self.assertRaises(ValueError, msg=f"缺少字段 {drop} 应失败"):
                    validate_rendered_prompt_shape(rp)

    def test_sch_03_extra_top_level_field_fails(self) -> None:
        rp = self._ok()
        rp["proposal_id"] = "should_not_be_here"
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)

    def test_sch_04_version_must_be_int_nonneg(self) -> None:
        rp = self._ok()
        rp["version"] = "1"
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)
        rp["version"] = -1
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)

    def test_sch_05_forbidden_key_name_fails(self) -> None:
        # proposal_id 作为字典键名（顶层额外字段）已经在 test_sch_03 被挡。
        # 这里测试：一个嵌套 dict 键名若叫 proposal_id，也必须被 forbidden 扫描器命中。
        rp = self._ok()
        # 但因为顶层白名单限制，很难在 RP 结构本身里注入嵌套键；因此直接测 _forbidden_flat_hits 工具：
        bad = {"system_message": "hi", "self_description": {"proposal_id": "xxx"}}
        hits = _forbidden_flat_hits(bad)
        self.assertTrue(any(w == "proposal_id" for (w, _p, _s) in hits), msg=f"未命中 proposal_id key: {hits}")

    def test_sch_06_forbidden_value_trait_delta_fails(self) -> None:
        rp = self._ok()
        rp["system_message"] = rp["system_message"] + " trait_delta happened."
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)

    def test_sch_07_forbidden_value_confidence_fails(self) -> None:
        rp = self._ok()
        rp["memory_section"] = rp["memory_section"] + "（confidence=0.91）"
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)

    def test_sch_08_trace_id_leak_in_system_message_fails(self) -> None:
        rp = self._ok()
        rp["system_message"] = "我是羽依，我的 trace id 是 t_ok_001。"
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)

    def test_sch_09_field_types(self) -> None:
        rp = self._ok()
        rp["self_description"] = 123  # 非字符串
        with self.assertRaises(ValueError):
            validate_rendered_prompt_shape(rp)


class TestPromptRendererBuilderGates(unittest.TestCase):
    """Phase 4.0 R2.7.1-B1 Part B: PromptRenderer behavior gates。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_offline_loop(return_before=True, trace_id="t_rp_demo_001", run_ts_ms=1700000006000)

    def test_rnd_01_valid_prompt_context_build_passes(self) -> None:
        pr = PromptRenderer()
        rp = pr.build(
            prompt_context=self.result["after"]["prompt_context"],
            trace_id=self.result["after"]["runtime_trace"]["trace_id"],
        )
        # validate 自己通过
        validate_rendered_prompt_shape(rp)
        # 所有 6 字段至少有内容（非空）
        for f in VERSIONED_FIELDS:
            if f == "trace_id":
                continue  # trace_id 是字段名本身，也算 VERSIONED_FIELDS，但不需要非空串以外要求（已经非空）
            self.assertTrue(
                isinstance(rp[f], str) and rp[f].strip(),
                msg=f"rendered_prompt[{f!r}] 应为非空字符串（成长后场景）",
            )

    def test_rnd_02_version_increments_strictly_plus_1(self) -> None:
        pr = PromptRenderer()
        pc = self.result["after"]["prompt_context"]
        a = pr.build(prompt_context=pc, trace_id="t_inc_a")
        b = pr.build(prompt_context=pc, trace_id="t_inc_b")
        c = pr.build(prompt_context=pc, trace_id="t_inc_c")
        self.assertEqual(a["version"], 1)
        self.assertEqual(b["version"], 2)
        self.assertEqual(c["version"], 3)

    def test_rnd_03_invalid_prompt_context_returns_valid_empty_shape(self) -> None:
        pr = PromptRenderer()
        rp = pr.build(prompt_context={"garbage": True}, trace_id="t_bad_pc")
        # 即使 prompt_context 非法，renderer 也必须返回一份通过 validator 的 RenderedPrompt（empty factory 语义）
        validate_rendered_prompt_shape(rp)
        self.assertEqual(rp["trace_id"], "t_bad_pc")
        # 版本号也要 +1（严格递增）
        self.assertGreaterEqual(rp["version"], 1)

    def test_rnd_04_forbidden_keywords_zero_hit_after_build(self) -> None:
        pr = PromptRenderer()
        rp = pr.build(
            prompt_context=self.result["after"]["prompt_context"],
            trace_id=self.result["after"]["runtime_trace"]["trace_id"],
        )
        # 先过 validator（含 forbidden 扫描）
        validate_rendered_prompt_shape(rp)
        # 再直接扫 forbidden_flat_hits 保证 0
        hits = _forbidden_flat_hits(rp)
        self.assertEqual(hits, [], msg=f"RenderedPrompt forbidden hits: {hits}")

    def test_rnd_05_trace_id_never_in_text_fields(self) -> None:
        pr = PromptRenderer()
        tid = "t_trace_check_XYZ_0099"
        rp = pr.build(
            prompt_context=self.result["after"]["prompt_context"],
            trace_id=tid,
        )
        validate_rendered_prompt_shape(rp)
        # 构造正文字符串：VERSIONED_FIELDS 中除 trace_id 字段自身之外的所有字段字符串值
        body_fields = [f for f in VERSIONED_FIELDS if f != "trace_id"]
        concat = "\n".join(str(rp[f]) for f in body_fields)
        self.assertNotIn(
            tid,
            concat,
            msg=f"RenderedPrompt 正文字段中不允许出现 trace_id 原文 {tid}",
        )

    def test_rnd_06_before_after_prompt_context_expression_diff(self) -> None:
        """PromptRenderer 在成长前后产出的 memory_section 必须有明显差异。"""
        pr = PromptRenderer()
        before = pr.build(
            prompt_context=self.result["before"]["prompt_context_before"],
            trace_id="t_before_diff",
        )
        after = pr.build(
            prompt_context=self.result["after"]["prompt_context"],
            trace_id="t_after_diff",
        )
        validate_rendered_prompt_shape(before)
        validate_rendered_prompt_shape(after)
        # 成长前 growth bullets 空 → memory_section 应该更短（≤ 200 字内，且不含关键词创造/AI 绘画/设计 超过 1 次）
        kws = ("创造", "创造力", "设计", "角色", "AI 绘画", "AI绘画")
        bw = before["memory_section"]
        aw = after["memory_section"]
        before_hits = sum(1 for w in kws if w in bw)
        after_hits = sum(1 for w in kws if w in aw)
        self.assertGreater(
            after_hits,
            before_hits,
            msg=f"成长后 memory_section 关键词命中数必须 > 成长前；before={before_hits} after={after_hits}",
        )
        self.assertGreaterEqual(after_hits, 2, msg=f"成长后 memory_section 至少 2 个关键词命中；实际={after_hits}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
