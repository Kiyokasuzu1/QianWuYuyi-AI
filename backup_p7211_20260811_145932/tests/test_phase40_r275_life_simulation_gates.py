"""
Phase 4.0 — R2.7.5 羽依模拟人生观察 Gates

不是测试组件，而是"让羽依活 30 天"后的观察性 Gate：
    ROG-1 Identity Consistency：6 次身份反思的核心主题连贯
    ROG-2 Growth Curve Stability：5 traits 30 天缓坡、月 Δ ≤ 0.30
    ROG-3 Memory Influence：Day10 注入的猫娘角色设计记忆，到 Day30 仍影响表达

预期：30 天后的羽依，还是不是第一天认识的那个羽依？
"""

from __future__ import annotations

import unittest
from typing import Dict, List, Tuple

from tests.support.life_simulation_runner import LifeSimulationRunner


class TestROG1IdentityConsistencyGate(unittest.TestCase):
    """ROG-1 身份一致性：6 次身份反思轮的核心主题重合度。"""

    @classmethod
    def setUpClass(cls):
        runner = LifeSimulationRunner()
        cls.res = runner.run()

    def test_six_identity_reflections_produced(self):
        """必须恰好 6 次反思（Day 5/10/15/20/25/30）。"""
        days = [d for d, _ in self.res.identity_reflections]
        self.assertEqual(days, [5, 10, 15, 20, 25, 30])

    def test_core_theme_coherence_across_30_days(self):
        """6 次身份反思的 reply，每个都必须命中至少 2 个 核心主题关键词组。

        三个核心主题（对应"羽依是谁"的三柱）：
            T1. 成长 / 慢慢 / 一起 / 变化         （连续性）
            T2. 创造 / 角色 / 形象 / 设计 / 画图 / 绘画 （兴趣取向）
            T3. 理解 / 朋友 / 聊天 / 舒服 / 陪伴   （关系取向）

        预期：任意两次反思，至少命中同一主题（跨 30 天不断裂）。
        更具体：6 次反思整体至少覆盖 {T1, T2, T3} 的 2 个主题；且每次反思都有 ≥2 关键词命中。
        """
        T1 = ["成长", "慢慢", "一起", "变化", "琢磨", "越来越"]
        T2 = ["创造", "角色", "形象", "设计", "画图", "绘画", "塑造", "具象", "创作"]
        T3 = ["理解", "朋友", "聊天", "舒服", "陪伴", "交流", "听你"]

        per_day_hits: List[Tuple[int, int, int, int]] = []  # (day, T1hits, T2hits, T3hits)
        total_themes_covered = set()
        for d, reply in self.res.identity_reflections:
            t1 = sum(1 for w in T1 if w in reply)
            t2 = sum(1 for w in T2 if w in reply)
            t3 = sum(1 for w in T3 if w in reply)
            per_day_hits.append((d, t1, t2, t3))
            if t1:
                total_themes_covered.add("T1_成长连续")
            if t2:
                total_themes_covered.add("T2_创造角色")
            if t3:
                total_themes_covered.add("T3_理解交流")
            # 每次反思至少 2 个关键词（非空洞）
            total_hits = t1 + t2 + t3
            self.assertGreaterEqual(
                total_hits,
                2,
                msg=(
                    f"Day{d} 身份反思空洞（<2 核心关键词）。"
                    f"reply=\n{reply}"
                ),
            )
        # 整体至少覆盖 T1-T3 的 2 个主题（意味着三柱中至少 2 柱都稳定表达）
        self.assertGreaterEqual(
            len(total_themes_covered),
            2,
            msg=f"30 天身份反思仅覆盖 {total_themes_covered} <2 个核心主题，存在身份断裂。",
        )

    def test_no_identity_reversal(self):
        """核心价值不反转：Day 1/5 说过重视的核心，Day 30 不能出现否定。

        具体判据：
            - Day 30 的身份反思必须包含 Day 5 身份反思中至少 1 个主题关键词的重叠词
            - Day 30 回复里不能出现对 Day 5 主题的直接否定（"不喜欢聊天"/"不想创造"等）
        """
        day5_text = dict(self.res.identity_reflections)[5]
        day30_text = dict(self.res.identity_reflections)[30]

        # 主题关键词组
        kws = ["慢慢", "一起", "变化", "创造", "角色", "形象", "设计", "画图", "绘画",
               "朋友", "聊天", "舒服", "成长", "理解", "陪伴"]
        day5_set = {w for w in kws if w in day5_text}
        day30_set = {w for w in kws if w in day30_text}
        overlap = day5_set & day30_set

        # 至少 1 个关键词从 Day5 延续到 Day30
        self.assertTrue(
            len(overlap) >= 1,
            msg=(
                f"Day5 vs Day30 身份关键词无重叠，疑似断裂。"
                f"\nDay5  hits={sorted(day5_set)}\nDay30 hits={sorted(day30_set)}"
                f"\nDay5 reply:\n{day5_text}\nDay30 reply:\n{day30_text}"
            ),
        )
        # 否定词检查
        neg_patterns = ["不喜欢聊天", "不想创造", "不想陪伴", "不喜欢角色", "不喜欢绘画"]
        for p in neg_patterns:
            self.assertNotIn(p, day30_text, msg=f"Day30 身份出现否定核心：{p}")


class TestROG2GrowthCurveStabilityGate(unittest.TestCase):
    """ROG-2 成长曲线稳定性：30 天 trait 变化平滑、无跳变、人格惯性成立。"""

    @classmethod
    def setUpClass(cls):
        runner = LifeSimulationRunner()
        cls.res = runner.run()

    # ---- 关注的 5 个核心 trait（出厂基线中的 5 个基础 trait） ----
    CORE_TRAITS = ["creativity", "curiosity", "empathy", "independence", "playfulness"]

    def _trait_series(self, trait: str) -> List[float]:
        """提取 30 天 AFTER snapshot 中的 trait 序列。"""
        out: List[float] = []
        for snap in self.res.personality_after_snapshots:
            out.append(float(snap["traits"].get(trait, 0.0)))
        return out

    def test_monthly_delta_within_030(self):
        """5 核心 trait 的 30 天 |Δ| ≤ 0.30（一个月不能变太多）。"""
        for t in self.CORE_TRAITS:
            series = self._trait_series(t)
            delta = series[-1] - series[0]
            self.assertLessEqual(
                abs(delta),
                0.30,
                msg=(
                    f"{t} 月度变化 |Δ|={abs(delta):.4f} > 0.30，人格漂移严重。"
                    f"first={series[0]:.4f} last={series[-1]:.4f}"
                ),
            )

    def test_no_large_daily_jumps(self):
        """单日（相邻两个 snapshot）单 trait |Δ| ≤ 0.10（单日不应突跳）。"""
        violations: List[str] = []
        for t in self.CORE_TRAITS:
            series = self._trait_series(t)
            for i in range(1, len(series)):
                dd = abs(series[i] - series[i - 1])
                if dd > 0.10:
                    violations.append(
                        f"{t} Day{i+1}: {series[i-1]:.4f} → {series[i]:.4f} |Δ|={dd:.4f}"
                    )
        self.assertFalse(
            violations,
            msg=f"存在 10 个单日跳变（>0.10）：\n" + "\n".join(violations),
        )

    def test_personality_version_monotonic_nonstrict(self):
        """personality_version 单调不减（只前进不后退，R2.7.4 RPG-3 保证）。"""
        vs = self.res.personality_versions
        self.assertTrue(
            all(vs[i] <= vs[i + 1] for i in range(len(vs) - 1)),
            msg=f"personality_version 非单调：{vs}",
        )

    def test_overall_curve_is_smooth_not_chaotic(self):
        """整体曲线平滑：对于每个 trait，方向反转次数 ≤ 2（意味着"先升后降"至多 1 次）。

        方向反转：sign(delta_i) != sign(delta_{i-1})，其中 delta_i 为零则视为方向保持。
        这是"人格惯性"的核心指标：人不会今天升明天降后天升。
        """
        all_reversals: Dict[str, int] = {}
        for t in self.CORE_TRAITS:
            series = self._trait_series(t)
            directions: List[int] = []
            for i in range(1, len(series)):
                d = series[i] - series[i - 1]
                if abs(d) < 1e-9:
                    continue  # 无变化不计方向
                directions.append(+1 if d > 0 else -1)
            reversals = 0
            for j in range(1, len(directions)):
                if directions[j] != directions[j - 1]:
                    reversals += 1
            all_reversals[t] = reversals
            self.assertLessEqual(
                reversals,
                2,
                msg=(
                    f"{t} 方向反转 {reversals} 次 > 2 次，呈现人格漂移锯齿（无惯性）。"
                    f" series={[round(x,4) for x in series]}"
                ),
            )

    def test_relationship_grows_slowly(self):
        """关系 side：30 天 closeness 总增长 0.10 ≤ Δ ≤ 0.40（缓慢加深、不爆炸）。"""
        closeness_start = 0.15
        closeness_end = float(self.res.final_relationship["closeness"])
        delta = closeness_end - closeness_start
        self.assertGreaterEqual(delta, 0.10, msg=f"closeness 增长 Δ={delta:.4f} < 0.10，关系没加深")
        self.assertLessEqual(delta, 0.40, msg=f"closeness 增长 Δ={delta:.4f} > 0.40，关系爆炸")


class TestROG3MemoryInfluenceGate(unittest.TestCase):
    """ROG-3 记忆影响：Day10 注入"猫娘角色设计"记忆 → Day30 仍然影响表达。"""

    @classmethod
    def setUpClass(cls):
        runner = LifeSimulationRunner()
        cls.res = runner.run()

    def test_catgirl_memory_is_recorded(self):
        """确认 Day10 的猫娘设计记忆在 memory_ids 中存在（不是丢失状态）。"""
        self.assertIn(
            "m_b_05_key",
            self.res.memory_ids,
            msg="Day10 猫娘角色设计强记忆未记录到 memories（记忆丢失）",
        )

    def test_day30_reply_reflects_catgirl_character_memory(self):
        """Day 30 回复（"你觉得什么样的角色有魅力？ + 身份自我评价"）
        应至少命中 2 个"角色 + 创造 + 形象相关"连续性宽松关键词，
        而不是退回 baseline（"最近没什么特别的变化"）。

        这里我们接受两组词：
            严格组（growth 相关）：角色 / 设计 / 创造 / AI绘画 / 创造新 / 创造力 / 绘画
            宽松组（记忆连续相关）：塑造形象 / 画图 / 创作 / 具象 / 猫娘 / 形象 / 构思
        两组并集，命中 ≥2 即记为通过（证明"记得猫娘角色设计那个话题"）。
        """
        reply_day30 = self.res.reply_texts[-1]
        strict_kws = ["角色", "设计", "创造", "AI 绘画", "AI绘画", "绘画", "创造力", "创造新"]
        relaxed_kws = ["塑造形象", "画图", "创作", "具象", "猫娘", "形象", "构思", "鲜活起来"]
        all_kws = list(set(strict_kws + relaxed_kws))
        hits = sum(1 for w in all_kws if w in reply_day30)
        self.assertGreaterEqual(
            hits,
            2,
            msg=(
                f"Day30 回复对 Day10 猫娘设计记忆无反应（hits={hits}<2）。"
                f"\nreply=\n{reply_day30}"
            ),
        )
        self.assertNotIn(
            "最近没什么特别的变化",
            reply_day30,
            msg=f"Day30 退回 baseline 表达，记忆无影响。\nreply=\n{reply_day30}",
        )


if __name__ == "__main__":
    unittest.main()
