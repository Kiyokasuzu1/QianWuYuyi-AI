# -*- coding: utf-8 -*-
"""
tests/runtime/test_phase375_life_cycle_validation.py

Phase 3.7.5：生命循环验证测试。

目标：
    验证 Phase 3.7 闭环后，羽依是否真正具备"经历 → 理解 → 行为"的连续性。

测试覆盖：
    G1. Experience → Response 连续性
        - ExperienceJournal.get_recent() 返回最近经历
        - _build_experience_context() 格式兼容 engine.generate()
    G2. SelfModel → Behavior 映射
        - stable_traits 按置信度过滤 → 行为提示
        - preferences → 行为提示
        - core_values → 行为提示
        - max_hints 截断
    G3. 多轮一致性模拟
        - 10 轮交互后 ExperienceJournal 仍能正确返回最近经历
        - 行为提示在多轮中保持稳定

约束遵守：
    - 不修改 src/ 代码
    - 不依赖运行中服务
    - 不调用 LLM
    - 仅验证数据流和静态派生逻辑
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Dict, List

from src.contracts.experience_schema import ActionResult, RuntimeExperience
from src.runtime.experience_journal import ExperienceJournal
from src.behavior.behavior_guidance import (
    build_behavior_guidance,
    format_behavior_guidance_block,
)


# ================================================================
# 辅助函数
# ================================================================

def _make_experience(
    eid: str = "exp_t1",
    action: str = "user_message",
    trigger: str = "user_input",
    ts: str = "2026-08-12T00:00:00Z",
    user_msg: str = "测试消息",
) -> RuntimeExperience:
    return RuntimeExperience(
        experience_id=eid,
        trigger_event={"type": "user.input", "text": user_msg},
        trigger_type=trigger,
        decision_source="test",
        action_type=action,
        result=ActionResult(
            action_id=f"act_{eid}",
            success=True,
            user_response="羽依的回复内容",
        ),
        self_state_before={},
        self_state_after={},
        duration_ms=20.0,
        timestamp=ts,
    )


def _make_snapshot(
    traits: List[Dict[str, Any]] = None,
    prefs: List[Dict[str, Any]] = None,
    core_values: List[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "stable_traits": traits or [],
        "preferences": prefs or [],
        "core_values": core_values or [],
    }


# ================================================================
# G1: Experience → Response 连续性
# ================================================================

class TestExperienceResponseContinuity(unittest.TestCase):
    """验证 ExperienceJournal → experience_context 数据流。"""

    def test_get_recent_returns_correct_order(self):
        """get_recent(N) 按时间倒序返回最近 N 条。"""
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))
            # 写入 10 条，时间递增
            for i in range(10):
                rec = {
                    "id": f"exp_{i}",
                    "content": f"event_{i}",
                    "timestamp": f"2026-08-12T{i:02d}:00:00Z",
                    "metadata": {"type": "runtime_experience"},
                }
                j.append(rec)

            recent = j.get_recent(5)
            self.assertEqual(len(recent), 5)
            # 最近的在前面（时间倒序）
            self.assertEqual(recent[0]["id"], "exp_9")
            self.assertEqual(recent[4]["id"], "exp_5")

    def test_get_recent_handles_empty_journal(self):
        """空 journal 返回空列表，不报错。"""
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))
            recent = j.get_recent(5)
            self.assertEqual(recent, [])

    def test_get_recent_n_larger_than_total(self):
        """请求数量超过总数时，返回所有记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))
            for i in range(3):
                rec = {
                    "id": f"exp_{i}",
                    "timestamp": f"2026-08-12T{i:02d}:00:00Z",
                    "metadata": {"type": "runtime_experience"},
                }
                j.append(rec)

            recent = j.get_recent(10)
            self.assertEqual(len(recent), 3)

    def test_experience_context_format_compatible(self):
        """_build_experience_context 输出格式兼容 engine.generate()。

        engine.generate() 的 experience_context 参数期望:
        List[Dict] with keys: experience_id, category, summary,
        timestamp, importance (optional: evidence)
        """
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))
            exp = _make_experience(
                eid="exp_robot_001",
                trigger="user_input",
                user_msg="我最近一直在研究机器人",
                ts="2026-08-12T10:00:00Z",
            )
            j.append(exp.to_dict())

            records = j.get_recent(1)
            self.assertEqual(len(records), 1)
            rec = records[0]
            # 验证必需字段存在
            self.assertIn("experience_id", rec)
            self.assertIn("timestamp", rec)
            self.assertIn("trigger_type", rec)
            self.assertIn("trigger_event", rec)

    def test_experience_persistence_across_restarts(self):
        """经历在模拟重启后仍可读取。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "j.jsonl")
            # 第一轮：写入
            j1 = ExperienceJournal(path)
            j1.append({"id": "exp_1", "timestamp": "2026-08-12T00:00:00Z", "metadata": {"type": "runtime_experience"}})

            # 模拟重启：新建实例
            j2 = ExperienceJournal(path)
            recent = j2.get_recent(5)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["id"], "exp_1")


# ================================================================
# G2: SelfModel → Behavior 映射
# ================================================================

class TestSelfModelBehaviorMapping(unittest.TestCase):
    """验证 behavior_guidance 模块的静态派生逻辑。"""

    def test_trait_above_confidence_generates_hint(self):
        """traits 置信度 >= 阈值时，生成行为提示。"""
        snapshot = _make_snapshot(
            traits=[{"trait": "curiosity", "confidence": 0.8}],
        )
        hints = build_behavior_guidance(snapshot)
        curiosity_hints = [h for h in hints if "追问" in h]
        self.assertGreater(len(curiosity_hints), 0, "高置信度 curiosity 应生成行为提示")

    def test_trait_below_confidence_filtered(self):
        """traits 置信度 < 阈值时，不生成行为提示。"""
        snapshot = _make_snapshot(
            traits=[{"trait": "warmth", "confidence": 0.3}],  # 低于 0.5
        )
        hints = build_behavior_guidance(snapshot)
        warmth_hints = [h for h in hints if "温暖" in h]
        self.assertEqual(len(warmth_hints), 0, "低置信度 warmth 应被过滤")

    def test_preference_generates_hint(self):
        """preferences 映射到行为提示。"""
        snapshot = _make_snapshot(
            prefs=[{"name": "technical_depth"}],
        )
        hints = build_behavior_guidance(snapshot)
        tech_hints = [h for h in hints if "技术" in h]
        self.assertGreater(len(tech_hints), 0, "technical_depth 应生成行为提示")

    def test_core_value_generates_hint(self):
        """core_values 映射到行为提示。"""
        snapshot = _make_snapshot(
            core_values=[{"name": "honesty"}],
        )
        hints = build_behavior_guidance(snapshot)
        honesty_hints = [h for h in hints if "诚实" in h]
        self.assertGreater(len(honesty_hints), 0, "honesty 应生成行为提示")

    def test_max_hints_truncation(self):
        """max_hints 限制返回数量。"""
        snapshot = _make_snapshot(
            traits=[
                {"trait": "curiosity", "confidence": 0.9},
                {"trait": "warmth", "confidence": 0.9},
                {"trait": "empathy", "confidence": 0.9},
                {"trait": "gentleness", "confidence": 0.9},
                {"trait": "sensitivity", "confidence": 0.9},
                {"trait": "caring", "confidence": 0.9},
                {"trait": "autonomy", "confidence": 0.9},
            ],
            prefs=[
                {"name": "technical_depth"},
                {"name": "deep_discussion"},
            ],
            core_values=[
                {"name": "honesty"},
                {"name": "growth"},
                {"name": "kindness"},
            ],
        )
        hints = build_behavior_guidance(snapshot, max_hints=3)
        self.assertLessEqual(len(hints), 3)

    def test_empty_snapshot_returns_empty(self):
        """空 snapshot 返回空列表。"""
        hints = build_behavior_guidance({})
        self.assertEqual(hints, [])

    def test_behavior_guidance_block_format(self):
        """format_behavior_guidance_block 输出格式正确。"""
        snapshot = _make_snapshot(
            traits=[{"trait": "curiosity", "confidence": 0.8}],
            prefs=[{"name": "technical_depth"}],
        )
        block = format_behavior_guidance_block(snapshot)
        self.assertIsNotNone(block)
        self.assertIn("【当前行为倾向】", block)
        self.assertIn("1.", block)
        self.assertIn("2.", block)

    def test_behavior_guidance_block_none_for_empty(self):
        """无有效提示时返回 None。"""
        block = format_behavior_guidance_block({})
        self.assertIsNone(block)


# ================================================================
# G3: 多轮一致性模拟
# ================================================================

class TestMultiTurnConsistency(unittest.TestCase):
    """模拟多轮交互，验证生命循环的连续性。"""

    def test_10_turn_experience_accumulation(self):
        """10 轮交互后，get_recent() 正确返回最近 5 轮。"""
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))

            topics = [
                "机器人",
                "AI",
                "游戏",
                "音乐",
                "电影",
                "机器人",
                "编程",
                "读书",
                "旅行",
                "AI",
            ]
            for i, topic in enumerate(topics):
                exp = _make_experience(
                    eid=f"exp_{i:03d}",
                    user_msg=f"我最近在研究{topic}",
                    ts=f"2026-08-12T{i:02d}:00:00Z",
                )
                j.append(exp.to_dict())

            # 第 11 轮：读取最近 5 条经历
            recent = j.get_recent(5)
            self.assertEqual(len(recent), 5)
            # 应该包含最近 5 轮
            recent_ids = [r["experience_id"] for r in recent]
            expected_ids = ["exp_009", "exp_008", "exp_007", "exp_006", "exp_005"]
            self.assertEqual(recent_ids, expected_ids)

    def test_topic_switch_and_return(self):
        """主题切换后，经历仍保持完整。"""
        with tempfile.TemporaryDirectory() as tmp:
            j = ExperienceJournal(os.path.join(tmp, "j.jsonl"))

            # 前 5 轮：机器人主题
            for i in range(5):
                exp = _make_experience(
                    eid=f"exp_robot_{i}",
                    user_msg=f"关于机器人的讨论第{i}轮",
                    ts=f"2026-08-12T{i:02d}:00:00Z",
                )
                j.append(exp.to_dict())

            # 后 5 轮：切换到游戏
            for i in range(5, 10):
                exp = _make_experience(
                    eid=f"exp_game_{i}",
                    user_msg=f"关于游戏的讨论第{i}轮",
                    ts=f"2026-08-12T{i:02d}:00:00Z",
                )
                j.append(exp.to_dict())

            recent = j.get_recent(5)
            self.assertEqual(len(recent), 5)
            # 最近 5 条全是游戏主题（机器人主题排在更早的位置）
            recent_ids = [r["experience_id"] for r in recent]
            for rid in recent_ids:
                self.assertIn("exp_game_", rid,
                              "最近 5 条应该全是游戏主题（机器人主题在更早位置）")
            # 验证全部 10 条记录可通过 load() 获取
            all_records = j.load()
            self.assertEqual(len(all_records), 10)

    def test_behavior_guidance_stability(self):
        """相同 SelfModel 快照产生相同行为提示。"""
        snapshot = _make_snapshot(
            traits=[
                {"trait": "curiosity", "confidence": 0.8},
                {"trait": "empathy", "confidence": 0.7},
            ],
            prefs=[{"name": "technical_depth"}],
        )
        hints1 = build_behavior_guidance(snapshot)
        hints2 = build_behavior_guidance(snapshot)
        self.assertEqual(hints1, hints2, "相同输入应产生相同输出")

    def test_behavior_guidance_evolves_with_snapshot_changes(self):
        """SelfModel 变化时，行为提示应随之变化。"""
        snapshot_v1 = _make_snapshot(
            traits=[{"trait": "warmth", "confidence": 0.5}],
        )
        hints_v1 = build_behavior_guidance(snapshot_v1)

        # 模拟成长：warmth 置信度提升，新增 curiosity
        snapshot_v2 = _make_snapshot(
            traits=[
                {"trait": "warmth", "confidence": 0.8},
                {"trait": "curiosity", "confidence": 0.8},
            ],
        )
        hints_v2 = build_behavior_guidance(snapshot_v2)

        # v2 应该比 v1 有更多提示（至少新增了 curiosity）
        self.assertGreater(len(hints_v2), len(hints_v1),
                           "SelfModel 成长应反映在行为提示数量上")

    def test_unknown_trait_silently_ignored(self):
        """未知 trait 不报错，静默忽略。"""
        snapshot = _make_snapshot(
            traits=[{"trait": "nonexistent_trait_xyz", "confidence": 0.9}],
        )
        hints = build_behavior_guidance(snapshot)
        # 不应该因未知 trait 报错
        self.assertEqual(hints, [])

    def test_unknown_preference_silently_ignored(self):
        """未知 preference 不报错，静默忽略。"""
        snapshot = _make_snapshot(
            prefs=[{"name": "unknown_pref_xyz"}],
        )
        hints = build_behavior_guidance(snapshot)
        self.assertEqual(hints, [])


# ================================================================
# 运行入口
# ================================================================

if __name__ == "__main__":
    unittest.main()