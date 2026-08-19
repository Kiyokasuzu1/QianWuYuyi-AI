"""
Phase 4.0 — R2.7.1-A Offline Cognitive Loop Gates

运行 run_offline_loop() 并强制要求通过 3 个 Gate：
  RL-1 End-to-End Lifecycle Gate
      一次运行必须产出：
        personality_state_after (PersonalityState)
        evolution_record (contracts 合法结构：has before/after/reasons)
        self_model_snapshot (SelfModelSnapshot)
        identity_continuity_report (is_continuous + warnings/version 结构)
        self_reflection_snapshot (SelfReflectionSnapshot)
        self_context (由 SCB 产出：validate_self_context_shape 通过)
        prompt_context (由 PCB 产出：validate_prompt_context_shape 通过)
        runtime_trace (validate_runtime_trace_shape 通过，且 step_snapshots ≥ 11，
                      所有 11 个认知步骤 phase_step 白名单存在)
      缺一个失败。

  RL-2 Information Boundary Gate
      最终 PromptContext：
        允许存在：identity_summary, growth_summary, continuity_status, reflection_summary（字段层面）
        禁止：
          - 出现字典键名：proposal_id / approval_reason / internal_score / evaluator_meta / confidence
          - 出现字符串值：proposal_id / approval_reason / internal_score / evaluator_meta / confidence
                         以及 trait_delta（独立 token 语义；用正则 \\btrait_delta\\b 匹配）

  RL-3 Growth Expression Gate
      同一份 simulator 前后对比：
      成长前：
        personality_summary.top_traits 中 creativity.level = medium
        growth_summary.recent_change_bullets 为空
      成长后：
        creativity.level = high（相对于 before 发生了 level 跃迁或明确 value ≥ 0.67）
        growth_summary.recent_change_bullets 中至少 2 条包含 ("创造" / "设计" / "AI 绘画") 相关词
        reflection_summary.observed_change_bullets 中包含 ("创造力" / "creativity")
        continuity_status = continuous_safe（self_context.inject_policy 与之前一致：summary_only）
        prompt_context 中 personality_block / growth_block 的字符串表达体现出上述差异
"""

from __future__ import annotations

import json
import re
import unittest
from typing import Any, List, Tuple

from tests.support.offline_cognitive_loop_sim import run_offline_loop

from src.personality.personality_state import PersonalityState
from src.self_model.self_model_snapshot import SelfModelSnapshot
from src.self_reflection.self_reflection_snapshot import SelfReflectionSnapshot
from src.context.self_context_schema import validate_self_context_shape
from src.context.prompt_context_schema import validate_prompt_context_shape
from src.runtime.runtime_trace_schema import validate_runtime_trace_shape


# 必须产出的 11 个 phase_step（顺序无关，只要存在；允许比这更多，但不能缺）
REQUIRED_COGNITIVE_STEPS = (
    "memory_recall",
    "experience_bridge",
    "growth_candidate_eval",
    "growth_proposal_approval",
    "evolution_pipeline_apply",
    "personality_state_snapshot",
    "self_model_build",
    "identity_continuity_check",
    "self_reflection_build",
    "self_context_build",
    "prompt_context_assembly",
)


def _flatten_forbidden_hits(obj: Any) -> List[Tuple[str, str, str]]:
    """RL-2 forbidden 扫描：返回 (forbidden_word, path, snippet)。

    规则：
      - 键名 ∈ KEY_FORBIDDEN 直接命中
      - 字符串值中：KEY_FORBIDDEN 任何一个做 case-insensitive 子串命中即算；
        "trait_delta" 按独立 token 语义（正则 \\btrait_delta\\b，case-insensitive）命中。
      - 其他类型：str(obj) 后走上述规则。
    """
    KEY_FORBIDDEN = frozenset(
        ["proposal_id", "approval_reason", "internal_score", "evaluator_meta", "confidence"]
    )
    STR_FORBIDDEN_SUBSTR = KEY_FORBIDDEN
    TRAIT_DELTA_RE = re.compile(r"\btrait_delta\b", re.IGNORECASE)

    hits: List[Tuple[str, str, str]] = []

    def walk(o: Any, path: str) -> None:
        # Dict：先检查键名，再检查值
        if isinstance(o, dict):
            for k, v in o.items():
                ks = str(k)
                if ks.lower() in KEY_FORBIDDEN:
                    hits.append((ks.lower(), path + f".KEY:{ks}", f"KEY={ks}"))
                walk(v, path + "." + ks)
            return
        # List / tuple
        if isinstance(o, (list, tuple)):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")
            return
        # 字符串：按字符串规则
        if isinstance(o, str):
            s_low = o.lower()
            for w in STR_FORBIDDEN_SUBSTR:
                if w in s_low:
                    hits.append((w, path, o[:200]))
            if TRAIT_DELTA_RE.search(o):
                hits.append(("trait_delta", path, o[:200]))
            return
        # 其他类型：转 str 再查
        if o is None or isinstance(o, (int, float, bool)):
            return
        s = str(o)
        s_low = s.lower()
        for w in STR_FORBIDDEN_SUBSTR:
            if w in s_low:
                hits.append((w, path + ".__str__", s[:200]))
        if TRAIT_DELTA_RE.search(s):
            hits.append(("trait_delta", path + ".__str__", s[:200]))

    walk(obj, "root")
    return hits


def _extract_level_for_trait(self_context_or_block: Any, trait_name: str) -> str:
    """从 self_context.personality_summary 里找某个 trait 的 level。找不到返回 ''。"""
    if not isinstance(self_context_or_block, dict):
        return ""
    ps = self_context_or_block.get("personality_summary") or {}
    for t in ps.get("top_traits", []) or []:
        if str(t.get("trait")) == trait_name:
            return str(t.get("level") or "")
    return ""


def _contains_any(text: str, keywords) -> bool:
    t = str(text)
    for k in keywords:
        if k in t:
            return True
    return False


class TestRL1LifecycleGate(unittest.TestCase):
    """RL-1 End-to-End Lifecycle Gate."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_offline_loop(
            return_before=True, trace_id="t_rl_demo_001", run_ts_ms=1700000000000
        )
        cls.after = cls.result["after"]

    def test_rl1_01_all_eight_products_present_and_types(self) -> None:
        after = self.after
        # 7 份核心产物 + 1 份 trace → 共 8
        self.assertIsInstance(after.get("personality_state_after"), PersonalityState, msg="缺少 PersonalityState")
        evo = after.get("evolution_record")
        self.assertIsInstance(evo, dict, msg="缺少 evolution_record（dict 形式）")
        self.assertTrue(evo.get("before") and evo.get("after"), msg="evo_record 没有 before/after")
        self.assertIsInstance(after.get("self_model_snapshot"), SelfModelSnapshot, msg="缺少 SelfModelSnapshot")
        icr = after.get("identity_continuity_report")
        self.assertIsInstance(icr, dict, msg="缺少 ICR dict")
        self.assertIn("is_continuous", icr, msg="ICR 缺 is_continuous")
        self.assertIn("version", icr, msg="ICR 缺 version")
        self.assertIsInstance(after.get("self_reflection_snapshot"), SelfReflectionSnapshot, msg="缺少 SelfReflectionSnapshot")
        sc = after.get("self_context")
        self.assertIsInstance(sc, dict, msg="缺少 self_context dict")
        pc = after.get("prompt_context")
        self.assertIsInstance(pc, dict, msg="缺少 prompt_context dict")
        trace = after.get("runtime_trace")
        self.assertIsInstance(trace, dict, msg="缺少 runtime_trace dict")

    def test_rl1_02_self_prompt_trace_shape_validators_pass(self) -> None:
        sc = self.after["self_context"]
        pc = self.after["prompt_context"]
        trace = self.after["runtime_trace"]
        # 这 3 个 validator 都不抛异常算通过；并记录各自版本号
        validate_self_context_shape(sc)
        validate_prompt_context_shape(pc)
        validate_runtime_trace_shape(trace)

    def test_rl1_03_trace_has_11_core_cognitive_steps(self) -> None:
        steps = [s.get("phase_step") for s in self.after["runtime_trace"]["step_snapshots"]]
        self.assertGreaterEqual(
            len(steps),
            len(REQUIRED_COGNITIVE_STEPS),
            msg=f"step_snapshots 数量不足（需要≥{len(REQUIRED_COGNITIVE_STEPS)}）: {steps}",
        )
        for req in REQUIRED_COGNITIVE_STEPS:
            self.assertIn(req, steps, msg=f"缺少 required phase_step={req}。现有 steps={steps}")

    def test_rl1_04_trace_overall_status_ok_and_growth_happened_true(self) -> None:
        s = self.after["runtime_trace"]["summary"]
        self.assertEqual(s["overall_status"], "ok")
        self.assertTrue(s["growth_happened"] is True, msg="growth_happened 必须为 True（因为本次 Demo 发生了成长）")
        self.assertEqual(s["continuity_status_final"], "continuous_safe", msg="continuity_status_final 必须是 continuous_safe")
        self.assertEqual(s["injection_mode_used"], "summary_only", msg="R2.7.1-A Demo 必须用 summary_only 模式")
        self.assertGreater(s["counters"]["evolutions_applied"], 0)
        # 4 个版本号都应该 ≥ 1（builders 都真实跑过）
        for k in (
            "self_model_version",
            "self_reflection_version",
            "self_context_version",
            "prompt_context_version",
        ):
            self.assertGreaterEqual(s[k], 1, msg=f"{k} 版本号应 ≥ 1，实际={s[k]}")

    def test_rl1_05_self_model_snapshot_contains_evolving_trait_creativity(self) -> None:
        sm: SelfModelSnapshot = self.after["self_model_snapshot"]
        pv = sm.personality_view if isinstance(sm.personality_view, dict) else {}
        et = pv.get("evolving_traits") or {}
        # evolving_traits 可以是 dict[str,float] / list[dict]；统一取 key 或 trait 字段
        trait_names: set = set()
        if isinstance(et, dict):
            for k in et.keys():
                trait_names.add(str(k).replace("trait.", ""))
        elif isinstance(et, list):
            for item in et:
                if isinstance(item, dict):
                    if "trait" in item:
                        trait_names.add(str(item["trait"]).replace("trait.", ""))
        self.assertIn("creativity", trait_names, msg=f"SelfModel.evolving_traits 中必须出现 creativity；当前={trait_names}")

    def test_rl1_06_self_reflection_contains_creativity_increase_with_evidence_driven_cause(self) -> None:
        sr: SelfReflectionSnapshot = self.after["self_reflection_snapshot"]
        observed = sr.observed_changes if isinstance(sr.observed_changes, list) else []
        traits_in_observed = set()
        found_creativity_up = False
        for o in observed:
            t = str(o.get("trait") if isinstance(o, dict) else getattr(o, "trait", "") or "")
            traits_in_observed.add(t)
            delta = float(o.get("delta") if isinstance(o, dict) else getattr(o, "delta", 0.0) or 0.0)
            if t == "creativity" and delta > 0.05:
                found_creativity_up = True
        self.assertIn("creativity", traits_in_observed, msg=f"SelfReflection.observed_changes 未出现 creativity；实际={traits_in_observed}")
        self.assertTrue(found_creativity_up, msg="SelfReflection.observed_changes 必须包含 creativity ↑ 且 delta≥0.05")
        # cause_tag=evidence_driven 必须出现在 interpreted_causes 中至少一条
        causes = sr.interpreted_causes if isinstance(sr.interpreted_causes, list) else []
        self.assertTrue(
            any(str(c.get("cause_tag", "")).lower() == "evidence_driven" for c in causes if isinstance(c, dict)),
            msg=f"interpreted_causes 中必须至少一条 cause_tag=evidence_driven；实际={causes}",
        )


class TestRL2InformationBoundaryGate(unittest.TestCase):
    """RL-2 Information Boundary Gate — 内部机制不得污染对外人格表达。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_offline_loop(
            return_before=True, trace_id="t_rl2_demo_002", run_ts_ms=1700000001000
        )
        cls.pc = cls.result["after"]["prompt_context"]
        cls.sc = cls.result["after"]["self_context"]

    def test_rl2_01_prompt_context_no_forbidden(self) -> None:
        hits = _flatten_forbidden_hits(self.pc)
        self.assertEqual(
            hits,
            [],
            msg="PromptContext 中命中 forbidden：\n" + "\n".join(
                f"  [{w}] {p} :: {snippet}" for (w, p, snippet) in hits
            ),
        )

    def test_rl2_02_self_context_no_forbidden(self) -> None:
        hits = _flatten_forbidden_hits(self.sc)
        self.assertEqual(
            hits,
            [],
            msg="SelfContext 中命中 forbidden：\n" + "\n".join(
                f"  [{w}] {p} :: {snippet}" for (w, p, snippet) in hits
            ),
        )

    def test_rl2_03_prompt_context_contains_expected_outer_sections(self) -> None:
        # PromptContext 必须“允许”的对外区块（RL-2 允许）
        pc = self.pc
        sc = pc.get("self_context") or {}
        self.assertIn("identity_summary", sc, msg="PromptContext.self_context 必须包含 identity_summary")
        self.assertIn("growth_summary", sc, msg="PromptContext.self_context 必须包含 growth_summary")
        self.assertIn("continuity_status", sc, msg="PromptContext.self_context 必须包含 continuity_status")
        self.assertIn("reflection_summary", sc, msg="PromptContext.self_context 必须包含 reflection_summary")


class TestRL3GrowthExpressionGate(unittest.TestCase):
    """RL-3 Growth Expression Gate — 成长必须真正影响表达，而不是只存在 DB。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_offline_loop(
            return_before=True, trace_id="t_rl3_demo_003", run_ts_ms=1700000002000
        )
        cls.before_sc = cls.result["before"]["self_context_before"]
        cls.after_sc = cls.result["after"]["self_context"]
        cls.before_pc = cls.result["before"]["prompt_context_before"]
        cls.after_pc = cls.result["after"]["prompt_context"]
        cls.growth_keywords = ("创造", "创造力", "设计", "角色", "AI 绘画", "AI绘画", "绘画", "创造相关")

    # ---------- creativity level 跃迁 ----------
    def test_rl3_01_creativity_level_medium_before_and_high_after(self) -> None:
        lv_before = _extract_level_for_trait(self.before_sc, "creativity")
        lv_after = _extract_level_for_trait(self.after_sc, "creativity")
        self.assertEqual(lv_before, "medium", msg=f"成长前 creativity 必须是 medium，实际={lv_before}")
        self.assertEqual(lv_after, "high", msg=f"成长后 creativity 必须是 high，实际={lv_after}")

    # ---------- growth bullets 空 → 非空，出现 创造/设计/AI 绘画 ----------
    def test_rl3_02_growth_bullets_empty_before_nonempty_after(self) -> None:
        before_bullets = list((self.before_sc["growth_summary"] or {}).get("recent_change_bullets", []) or [])
        after_bullets = list((self.after_sc["growth_summary"] or {}).get("recent_change_bullets", []) or [])
        self.assertEqual(len(before_bullets), 0, msg="baseline 下 growth bullets 必须为空（还没成长）")
        self.assertGreaterEqual(len(after_bullets), 2, msg="成长后 growth bullets 至少 2 条（trait 自然语言 + 至少一条 evidence 中文）")

    def test_rl3_03_growth_bullets_contain_creative_design_ai_art_words(self) -> None:
        after_bullets = list((self.after_sc["growth_summary"] or {}).get("recent_change_bullets", []) or [])
        matched = [b for b in after_bullets if _contains_any(b, self.growth_keywords)]
        self.assertGreaterEqual(
            len(matched),
            2,
            msg=f"growth_summary.recent_change_bullets 中至少 2 条命中创造/设计/AI绘画相关词；命中={matched}；全部={after_bullets}",
        )

    # ---------- reflection observed 含 creativity（中文“创造力”） ----------
    def test_rl3_04_reflection_observed_contains_creativity(self) -> None:
        obs = list((self.after_sc["reflection_summary"] or {}).get("observed_change_bullets", []) or [])
        self.assertTrue(
            any(_contains_any(b, ("创造力", "creativity", "创造")) for b in obs),
            msg=f"reflection_summary.observed_change_bullets 必须包含创造力/creativity 字样；实际={obs}",
        )
        # baseline 下 reflection observed 应该空（没成长过）
        obs_before = list((self.before_sc["reflection_summary"] or {}).get("observed_change_bullets", []) or [])
        self.assertEqual(
            obs_before,
            [],
            msg=f"baseline 下 reflection observed bullets 必须空；实际={obs_before}",
        )

    # ---------- continuity / injection policy 保持稳定 ----------
    def test_rl3_05_continuity_stable_and_injection_mode_unchanged(self) -> None:
        self.assertEqual(self.before_sc["continuity_status"], "continuous_safe")
        self.assertEqual(self.after_sc["continuity_status"], "continuous_safe")
        self.assertEqual(
            self.before_sc["injection_policy"]["mode"],
            self.after_sc["injection_policy"]["mode"],
            msg="成长前后 injection_policy.mode 必须一致（summary_only，避免表达因策略而变）",
        )
        self.assertEqual(self.before_sc["injection_policy"]["mode"], "summary_only")

    # ---------- PromptContext 对外 blocks 也要体现表达差异 ----------
    def test_rl3_06_prompt_context_blocks_reflect_growth_difference(self) -> None:
        """把 before / after prompt_context 转成字符串（允许 JSON 序列化后的字符串），
        检查：
          - after JSON 中包含 growth bullets 中 ≥2 条的关键词
          - before JSON 中这些关键词出现显著更少（count 差距要明显）
        """
        before_s = json.dumps(self.before_pc, ensure_ascii=False)
        after_s = json.dumps(self.after_pc, ensure_ascii=False)
        after_hits = sum(1 for k in self.growth_keywords if k in after_s)
        before_hits = sum(1 for k in self.growth_keywords if k in before_s)
        # 成长后关键词出现次数必须 > 成长前
        self.assertGreater(
            after_hits,
            before_hits,
            msg=(
                f"成长前后 PromptContext 关键词命中数必须 after > before；"
                f"after_hits={after_hits}, before_hits={before_hits}"
            ),
        )
        # after 至少命中 3 个关键词
        self.assertGreaterEqual(
            after_hits,
            3,
            msg=f"成长后 PromptContext 中必须至少命中 3 个创造/设计/AI绘画相关关键词；实际命中数={after_hits}，关键词={self.growth_keywords}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
