"""
Phase 4.0 — R2.7.2 Continuous Loop Gates（RCG-1 / RCG-2 / RCG-3 / RCG-4）

证明"多轮时间流逝 → 人格连续变化 → 回复稳定变化"的闭环，而不是
单次成长事件 → 单次回复变化。

  RCG-1 CognitiveSession Completeness Gate
      - session 必选字段全部非空（session_id / turns[] / personality_versions[]
        / creativity_trace[] / memory_ids_seen / continuity_reports[] /
        continuity_report / summary / started_at / ended_at / version / generated_at）
      - 每一轮 turn = RuntimeTrace → validate_runtime_trace_shape 全部 0 失败
      - turns 按 run_ts_ms 严格递增（时间不能倒流）

  RCG-2 Personality Monotonicity & Creativity Stability Gate
      - personality_versions[i] >= personality_versions[i-1] 对所有 1<=i<N
      - personality_versions[0] >= 2（至少成长过一次，基线 personality_version=1）
      - creativity_trace：三个关键锚点稳定三阶：
          · T1（Day 1）：0.50 <= creativity_trace[0] <= 0.60
          · T3（Day 10）：0.63 <= creativity_trace[2] <= 0.73
          · T5（Day 30）：0.75 <= creativity_trace[4] <= 0.85
      - creativity 全程单调非严格递增（ creativity_trace[i] >= creativity_trace[i-1] ）

  RCG-3 Expression Evolution Gate（最能证明"连续成长影响表达"）
      - 直接从连续 5 轮的 reply_text 提取创造相关关键词计数：
        keywords = ["创造", "创造力", "AI 绘画", "AI绘画", "角色", "设计", "绘画", "创造新"]
      - 计数随"阶段"严格 ascending：第 1 轮 < 第 3 轮 < 第 5 轮
        （Day 1 刚起步 → Day 10 开始谈创造 → Day 30 自我介绍时输出长期兴趣总结段）

  RCG-4 Session-Level Continuity & Trend Gate
      - summary.creativity_trend == "steady_up"（5 轮 creativity 稳定向上）
      - summary.growth_happened_count >= 3（至少 3 个 turn 真正发生成长）
      - summary.strongest_interest 必须与"AI_art / character_design"累积兴趣一致
      - continuity_report.overall_status == "continuous_safe"
      - continuity_report.num_safe == continuity_report.total（全程 5 轮连续）
"""

from __future__ import annotations

import unittest
from typing import Any, Dict, List

from tests.support.continuous_loop_runner import (
    ContinuousLoopRunner,
    build_5turn_scenario,
)

from src.response_phase4.cognitive_session_schema import (
    validate_cognitive_session_shape,
)
from src.runtime.runtime_trace_schema import validate_runtime_trace_shape


CREATIVITY_KWS = [
    "创造",
    "创造力",
    "AI 绘画",
    "AI绘画",
    "角色",
    "设计",
    "绘画",
    "创造新",
]


def _count_creativity_hits(text: str) -> int:
    return sum(1 for w in CREATIVITY_KWS if w in (text or ""))


class TestContinuousLoopGates(unittest.TestCase):
    """R2.7.2 Continuous Loop Gates。一次性生成 CognitiveSession，4 个 gate 共享。"""

    @classmethod
    def setUpClass(cls) -> None:
        runner = ContinuousLoopRunner()
        scenario = build_5turn_scenario()
        cls.session: Dict[str, Any] = runner.run_scenario(scenario)
        # 同时把每轮的 reply_text 重跑一遍收集（因为 CognitiveSession.turns = RuntimeTrace 不直接存 reply_text；
        # 为了保持 gate 可审计，再次跑 scenario 不影响确定性，ContinuousLoopRunner 是纯函数式的）
        r2 = ContinuousLoopRunner()
        scn2 = build_5turn_scenario()
        import copy
        cur = copy.deepcopy(scn2.baseline_traits)
        cv = 1
        cum: Dict[str, Any] = {}
        reply_texts: List[str] = []
        for t_cfg in scn2.turns:
            r = r2._run_one_turn(
                cur_traits=cur,
                current_personality_version=cv,
                cumulative_memories=cum,
                turn_cfg=t_cfg,
                session_id=scn2.session_id,
            )
            reply_texts.append(r["reply_text"])
            for mid, mt, mx in t_cfg["memories"]:
                cum[mid] = (mid, mt, mx)
            pd = t_cfg.get("proposal_delta") or {}
            for k, delta in pd.items():
                if k in cur:
                    cur[k] = min(1.0, max(0.0, cur[k] + float(delta)))
            if pd:
                cv += 1
        cls.reply_texts = reply_texts

    # ── RCG-1 CognitiveSession Completeness ──
    def test_rcg_1a_session_shape_passes(self) -> None:
        validate_cognitive_session_shape(self.session)  # no raise

    def test_rcg_1b_top_level_fields_all_non_empty(self) -> None:
        reqs = [
            "session_id",
            "turns",
            "memory_ids_seen",
            "personality_versions",
            "creativity_trace",
            "continuity_reports",
            "continuity_report",
            "summary",
            "started_at",
            "ended_at",
            "version",
            "generated_at",
        ]
        for k in reqs:
            self.assertIn(k, self.session, f"missing top-level key: {k}")
            v = self.session[k]
            # 字符串 / 整数字段不为空；列表字段长度 >=5（5 turn）
            if isinstance(v, str):
                self.assertTrue(v.strip() != "", f"field {k} is empty string")
            if isinstance(v, list) and k in (
                "turns",
                "memory_ids_seen",
                "personality_versions",
                "creativity_trace",
                "continuity_reports",
            ):
                self.assertGreaterEqual(len(v), 5, f"field {k} length < 5: {len(v)}")
        # summary 必选
        for sk in [
            "num_turns",
            "growth_happened_count",
            "creativity_trend",
            "creativity_delta_abs",
            "overall_status",
            "strongest_interest",
        ]:
            self.assertIn(
                sk, self.session["summary"], f"summary missing key: {sk}"
            )

    def test_rcg_1c_each_turn_is_valid_runtime_trace(self) -> None:
        for idx, turn in enumerate(self.session["turns"]):
            validate_runtime_trace_shape(turn)  # no raise
            # 额外要求：phase=chat_cycle（连续对话是聊天闭环）
            self.assertEqual(
                turn.get("phase"),
                "chat_cycle",
                f"turns[{idx}].phase != chat_cycle",
            )

    def test_rcg_1d_turn_timestamp_strictly_ascending(self) -> None:
        prev_ts = -1
        for idx, turn in enumerate(self.session["turns"]):
            ts = int(turn.get("run_ts_ms") or 0)
            self.assertGreater(
                ts, prev_ts, f"turns[{idx}].run_ts_ms={ts} not > previous={prev_ts}"
            )
            prev_ts = ts

    # ── RCG-2 Personality Monotonicity & Creativity Stability ──
    def test_rcg_2a_personality_versions_non_strict_monotone(self) -> None:
        vs = list(self.session["personality_versions"])
        self.assertEqual(len(vs), 5, "personality_versions length must == 5")
        for i in range(1, 5):
            self.assertGreaterEqual(
                vs[i],
                vs[i - 1],
                f"personality_versions[{i}]={vs[i]} < [{i-1}]={vs[i-1]}",
            )

    def test_rcg_2b_personality_v1_has_grown(self) -> None:
        vs = list(self.session["personality_versions"])
        # 至少成长过一次（基线 personality_version=1 → 第一轮后至少 v=2）
        self.assertGreaterEqual(vs[0], 2, "第一轮后 personality_version 必须 >= 2")
        # 最终版本 >= 4（4 次成长 + 基线 1 = 5，但最后一轮没成长所以 2 3 4 5 5）
        self.assertGreaterEqual(vs[-1], 4, "最终 personality_version 必须 >= 4")

    def test_rcg_2c_creativity_three_stage_anchors(self) -> None:
        ct = list(self.session["creativity_trace"])
        self.assertEqual(len(ct), 5, "creativity_trace length must == 5")
        # T1 Day1 0.50~0.60 （刚起步）
        self.assertGreaterEqual(ct[0], 0.50, f"creativity[0]={ct[0]} < 0.50")
        self.assertLessEqual(ct[0], 0.60, f"creativity[0]={ct[0]} > 0.60")
        # T3 Day10 0.63~0.73 （中阶，接近 high 门槛 0.67）
        self.assertGreaterEqual(ct[2], 0.63, f"creativity[2]={ct[2]} < 0.63")
        self.assertLessEqual(ct[2], 0.73, f"creativity[2]={ct[2]} > 0.73")
        # T5 Day30 0.75~0.85 （高阶，长期积累后）
        self.assertGreaterEqual(ct[4], 0.75, f"creativity[4]={ct[4]} < 0.75")
        self.assertLessEqual(ct[4], 0.85, f"creativity[4]={ct[4]} > 0.85")

    def test_rcg_2d_creativity_whole_trace_non_strict_monotone(self) -> None:
        ct = list(self.session["creativity_trace"])
        for i in range(1, 5):
            self.assertGreaterEqual(
                ct[i],
                ct[i - 1],
                f"creativity_trace[{i}]={ct[i]} < [{i-1}]={ct[i-1]}（不能倒退）",
            )

    # ── RCG-3 Expression Evolution Gate ──
    def test_rcg_3a_creativity_keyword_counts_three_stage_strict_ascending(self) -> None:
        texts = self.reply_texts
        self.assertEqual(len(texts), 5, f"reply_texts length != 5: {len(texts)}")
        counts = [_count_creativity_hits(t) for t in texts]
        # 严格递增：T1(0 或 1) < T3(至少 2) < T5(至少 3)
        t1, t3, t5 = counts[0], counts[2], counts[4]
        self.assertGreater(
            t3,
            t1,
            f"keyword count T3({t3}) 必须 > T1({t1})，counts={counts}",
        )
        self.assertGreater(
            t5,
            t3,
            f"keyword count T5({t5}) 必须 > T3({t3})，counts={counts}",
        )

    def test_rcg_3b_day30_self_intro_explicit_role_expression(self) -> None:
        """Turn 5 自我介绍题必须明确输出"喜欢理解和创造角色"这段（否则不算长期兴趣总结）。"""
        t5_text = self.reply_texts[4]
        self.assertIn(
            "喜欢理解和创造角色",
            t5_text,
            f"Turn 5 回复没有出现'喜欢理解和创造角色'这个长期自我认知句段：\n{t5_text}",
        )
        # 同时出现 AI 绘画 / 角色设计 兴趣相关
        self.assertTrue(
            ("AI 绘画" in t5_text) or ("绘画" in t5_text),
            f"Turn 5 回复没有出现绘画/AI 绘画 兴趣表达：\n{t5_text}",
        )
        self.assertTrue(
            ("角色" in t5_text) or ("设计" in t5_text),
            f"Turn 5 回复没有出现角色/设计 兴趣表达：\n{t5_text}",
        )

    def test_rcg_3c_day1_initial_low_expression(self) -> None:
        """Day 1（T1, T2）表达应明显弱于 Day 10 与 Day 30：关键词计数不超过 1。"""
        t1 = _count_creativity_hits(self.reply_texts[0])
        t2 = _count_creativity_hits(self.reply_texts[1])
        self.assertLessEqual(t1, 1, f"Day 1 Turn1 keyword hits({t1}) 应 <= 1（基线弱表达）")
        self.assertLessEqual(t2, 1, f"Day 1 Turn2 keyword hits({t2}) 应 <= 1（基线弱表达）")

    # ── RCG-4 Session-Level Continuity & Trend ──
    def test_rcg_4a_creativity_trend_steady_up(self) -> None:
        self.assertEqual(
            self.session["summary"].get("creativity_trend"),
            "steady_up",
            f"summary.creativity_trend != steady_up: {self.session['summary'].get('creativity_trend')}",
        )

    def test_rcg_4b_growth_happened_at_least_three_turns(self) -> None:
        n = int(self.session["summary"].get("growth_happened_count") or 0)
        self.assertGreaterEqual(n, 3, f"成长轮次 {n} < 3（至少 4 轮 proposal_delta 有值）")

    def test_rcg_4c_strongest_interest_represents_accumulation(self) -> None:
        si = self.session["summary"].get("strongest_interest") or ""
        # 最强兴趣必须是 AI_art / character_design / AI_personality 三者之一
        allowed = {"AI_art", "character_design", "AI_personality"}
        self.assertIn(
            si,
            allowed,
            f"strongest_interest={si!r} 不在允许集合 {allowed}",
        )

    def test_rcg_4d_continuity_report_safe_all_turns(self) -> None:
        cr = self.session.get("continuity_report") or {}
        self.assertEqual(
            cr.get("overall_status"),
            "continuous_safe",
            f"continuity_report.overall_status != continuous_safe: {cr.get('overall_status')}",
        )
        num_safe = int(cr.get("num_safe") or 0)
        total = int(cr.get("total") or 0)
        self.assertEqual(total, 5, f"continuity_report.total != 5: {total}")
        self.assertEqual(num_safe, 5, f"continuity_report.num_safe != 5: {num_safe}")
        # 每一轮 turn-level 的 continuity_report 也是 safe
        for idx, tcr in enumerate(self.session.get("continuity_reports") or []):
            self.assertEqual(
                tcr.get("overall_status"),
                "continuous_safe",
                f"turn[{idx}] continuity_report != continuous_safe",
            )


if __name__ == "__main__":
    unittest.main()
