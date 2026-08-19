# -*- coding: utf-8 -*-
"""
tests/runtime/test_phase376_observation.py

Phase 3.7.6：观测基础设施测试

验证：
  1. ResponseStyleMonitor 正确记录和分析风格快照
  2. PersonalityDriftDetector 正确检测漂移
  3. Orchestrator 接入点不崩溃
  4. 环境变量开关正常工作
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 测试 1：ResponseStyleMonitor
# ============================================================

class TestResponseStyleMonitor(unittest.TestCase):
    """验证风格快照记录器。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        from src.behavior.response_style_monitor import ResponseStyleMonitor
        self.monitor = ResponseStyleMonitor(data_dir=self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_analyze_warm_reply(self):
        """温暖回复应检测到高 warmth。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        reply = "我能理解你的感受，谢谢你的信任。我会一直在这里陪伴你，关心你。"
        snapshot = analyze_reply_style(reply)

        self.assertGreater(snapshot.warmth, 0.0, "温暖回复应有 warmth")
        self.assertEqual(snapshot.question_count, 0, "无问号")
        self.assertGreater(snapshot.reply_length, 0)

    def test_analyze_curious_reply(self):
        """好奇回复应检测到高 curiosity。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        reply = "你觉得呢？也许可以试试不同的方法？比如从另一个角度看？"
        snapshot = analyze_reply_style(reply)

        self.assertGreater(snapshot.curiosity, 0.0, "好奇回复应有 curiosity")
        self.assertGreater(snapshot.question_count, 0, "应有问号")

    def test_analyze_formal_reply(self):
        """正式回复应检测到高 formality。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        reply = "综上所述，基于当前的系统架构，应当通过模块化设计来实现高内聚低耦合。"
        snapshot = analyze_reply_style(reply)

        self.assertGreater(snapshot.formality, 0.0, "正式回复应有 formality")

    def test_analyze_playful_reply(self):
        """活泼回复应检测到高 playfulness。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        reply = "哈哈，这个挺有趣的！唔，让我想想……诶，好像有点好玩呢！"
        snapshot = analyze_reply_style(reply)

        self.assertGreater(snapshot.playfulness, 0.0, "活泼回复应有 playfulness")

    def test_empty_reply_handled(self):
        """空回复不崩溃。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        snapshot = analyze_reply_style("")
        self.assertEqual(snapshot.warmth, 0.0)
        self.assertEqual(snapshot.curiosity, 0.0)

        snapshot2 = analyze_reply_style(None)
        self.assertEqual(snapshot2.warmth, 0.0)

    def test_record_and_load(self):
        """记录并加载快照，验证数据完整性。"""
        self.monitor.record(
            reply="你好，我是浅雾羽依，很高兴认识你。",
            user_message="你好",
            conversation_id="conv_test_001",
            user_id="366648462",
        )
        self.monitor.record(
            reply="我觉得机器人是一个很有趣的领域，你觉得呢？",
            user_message="机器人怎么样",
            conversation_id="conv_test_002",
            user_id="366648462",
        )

        records = self.monitor.load_all()
        self.assertEqual(len(records), 2, "应有 2 条记录")

        # 验证字段完整性
        for rec in records:
            self.assertIn("snapshot_id", rec)
            self.assertIn("timestamp", rec)
            self.assertIn("warmth", rec)
            self.assertIn("curiosity", rec)
            self.assertIn("initiative", rec)
            self.assertIn("formality", rec)
            self.assertIn("playfulness", rec)
            self.assertIn("reply_length", rec)

        # 验证记录顺序
        self.assertEqual(records[0]["conversation_id"], "conv_test_001")
        self.assertEqual(records[1]["conversation_id"], "conv_test_002")

    def test_get_recent(self):
        """get_recent 返回最近 N 条。"""
        for i in range(10):
            self.monitor.record(
                reply=f"测试回复 {i}",
                user_message=f"测试 {i}",
                conversation_id=f"conv_{i}",
            )
            time.sleep(0.02)  # 确保时间戳不同

        recent = self.monitor.get_recent(n=3)
        self.assertEqual(len(recent), 3)
        # 最近的在前面（按时间倒序）
        self.assertIn("conv_9", recent[0]["conversation_id"])

    def test_record_count(self):
        """record_count 正确统计。"""
        self.assertEqual(self.monitor.record_count, 0)
        self.monitor.record(reply="测试", user_message="测试")
        self.assertEqual(self.monitor.record_count, 1)

    def test_daily_averages(self):
        """get_daily_averages 正确聚合。"""
        self.monitor.record(reply="温暖回复 " * 10, user_message="测试")
        self.monitor.record(reply="好奇回复？" * 5, user_message="测试")

        daily = self.monitor.get_daily_averages(days=1)
        self.assertGreater(len(daily), 0, "应有至少 1 天的数据")

        for date_key, metrics in daily.items():
            for metric in ["warmth", "curiosity", "initiative", "formality", "playfulness"]:
                self.assertIn(metric, metrics)
                self.assertIsInstance(metrics[metric], float)


# ============================================================
# 测试 2：PersonalityDriftDetector
# ============================================================

class TestPersonalityDriftDetector(unittest.TestCase):
    """验证人格漂移检测器。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self.tmp_dir.name)
        from src.behavior.response_style_monitor import ResponseStyleMonitor
        from src.behavior.personality_drift_detector import PersonalityDriftDetector

        self.monitor = ResponseStyleMonitor(data_dir=self.tmp_dir.name)
        self.detector = PersonalityDriftDetector(monitor=self.monitor)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _seed_data(self, warmth: float, curiosity: float, count: int = 10):
        """往 monitor 中写入指定风格的快照（模拟历史数据）。"""
        file_path = self._tmp_path / "response_styles.jsonl"
        for i in range(count):
            # 使用递增时间戳确保顺序可区分
            record = {
                "snapshot_id": f"seed_{i}",
                "conversation_id": f"conv_seed_{i}",
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(time.time() + i),
                ),
                "user_id": "366648462",
                "warmth": warmth,
                "curiosity": curiosity,
                "initiative": 0.5,
                "formality": 0.3,
                "playfulness": 0.4,
                "reply_length": 100,
                "question_count": 2,
                "exclamation_count": 1,
                "user_message_preview": "测试",
                "reply_preview": "测试",
                "phase": "3.7.6",
                "drift_check": False,
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line)

    def test_no_drift_when_stable(self):
        """风格稳定时不应检测到漂移。"""
        self._seed_data(warmth=0.7, curiosity=0.6, count=10)

        report = self.detector.check_drift(
            baseline_days=(7, 1),
            current_days=(1, 0),
        )
        self.assertFalse(report.has_drift, "稳定风格不应有漂移")

    def test_detect_single_metric_drift(self):
        """单个指标大幅变化时应检测到漂移。"""
        # 基线：warmth=0.7
        self._seed_data(warmth=0.7, curiosity=0.6, count=10)

        # 需要往监控文件中写入不同的当前数据
        # 用更早的时间戳模拟基线
        file_path = Path(self.tmp_dir.name) / "response_styles.jsonl"
        # 读取已有数据，修改时间戳为更早
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # 重写基线数据（时间戳设为 5 天前）
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                rec = json.loads(line)
                five_days_ago = time.time() - 5 * 86400
                rec["timestamp"] = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago)
                )
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # 当前数据：warmth=0.3（大幅下降）
        self._seed_data(warmth=0.3, curiosity=0.6, count=10)

        report = self.detector.check_drift(
            baseline_days=(7, 1),
            current_days=(1, 0),
        )
        self.assertTrue(report.has_drift, "warmth 从 0.7 降到 0.3 应检测到漂移")

    def test_role_collapse_detection(self):
        """warmth 骤降 + formality 骤升应检测到角色崩塌。"""
        # 基线：高 warmth，低 formality
        for i in range(10):
            record = {
                "snapshot_id": f"base_{i}",
                "conversation_id": f"conv_base_{i}",
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(time.time() - 5 * 86400),
                ),
                "user_id": "366648462",
                "warmth": 0.8,
                "curiosity": 0.6,
                "initiative": 0.5,
                "formality": 0.2,
                "playfulness": 0.5,
                "reply_length": 100,
                "question_count": 2,
                "exclamation_count": 1,
                "user_message_preview": "基线",
                "reply_preview": "基线",
                "phase": "3.7.6",
                "drift_check": False,
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            file_path = Path(self.tmp_dir.name) / "response_styles.jsonl"
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line)

        # 当前：低 warmth，高 formality（角色崩塌信号）
        # 需要 formality 从 0.2 升到 >= 0.5 才能触发角色崩塌
        self._seed_data(warmth=0.3, curiosity=0.5, count=10)
        # 手动覆写当前数据的 formality（_seed_data 默认 formality=0.3，不够）
        file_path = self._tmp_path / "response_styles.jsonl"
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        with open(file_path, "w", encoding="utf-8") as f:
            for line in lines:
                rec = json.loads(line)
                # 只修改最近的数据（formality=0.3 的）
                if rec.get("formality") == 0.3:
                    rec["formality"] = 0.6  # 从 0.2 升到 0.6，delta=0.4 > 0.30
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        report = self.detector.check_drift(
            baseline_days=(7, 1),
            current_days=(1, 0),
        )
        # 检查是否有 role_collapse 告警
        role_collapse_alerts = [
            a for a in report.alerts if a.alert_type == "role_collapse"
        ]
        self.assertGreater(
            len(role_collapse_alerts), 0,
            "warmth 降 + formality 升应触发角色崩塌告警"
        )

    def test_insufficient_data(self):
        """数据不足时不会误报。"""
        self._seed_data(warmth=0.7, curiosity=0.6, count=3)  # 只有 3 条

        report = self.detector.check_drift(
            baseline_days=(7, 1),
            current_days=(1, 0),
        )
        self.assertFalse(report.has_drift, "数据不足不应误报")
        self.assertIn("不足", report.summary)

    def test_quick_check(self):
        """quick_check 不崩溃。"""
        self._seed_data(warmth=0.7, curiosity=0.6, count=10)
        report = self.detector.quick_check(days=7)
        self.assertIsNotNone(report)
        self.assertIsInstance(report.summary, str)

    def test_get_trend(self):
        """get_trend 返回正确趋势。"""
        self._seed_data(warmth=0.7, curiosity=0.6, count=10)
        trend = self.detector.get_trend("warmth", days=7)
        self.assertIsInstance(trend, list)
        if trend:
            self.assertIsInstance(trend[0], tuple)
            self.assertEqual(len(trend[0]), 2)


# ============================================================
# 测试 3：Orchestrator 接入点
# ============================================================

class TestOrchestratorStyleSnapshotHook(unittest.TestCase):
    """验证 Orchestrator 的 _record_style_snapshot 钩子。"""

    def test_hook_does_not_crash(self):
        """钩子不崩溃，不抛异常。"""
        from src.orchestrator import Orchestrator

        orch = Orchestrator()

        # 测试空回复
        orch._record_style_snapshot("", "", "conv_test")

        # 测试正常回复
        orch._record_style_snapshot(
            "你好，我是浅雾羽依。",
            "你好",
            "conv_test_2",
        )

        # 测试超长回复
        orch._record_style_snapshot(
            "长回复" * 500,
            "测试",
            "conv_test_3",
        )

        # 不抛异常即通过
        self.assertTrue(True)

    def test_env_var_controls_monitor(self):
        """环境变量 YUYI_STYLE_MONITOR 控制开关。"""
        from src.orchestrator import Orchestrator

        # 默认关闭
        old_env = os.environ.get("YUYI_STYLE_MONITOR", "")
        os.environ["YUYI_STYLE_MONITOR"] = "0"

        orch = Orchestrator()
        orch._record_style_snapshot("测试", "测试", "conv_test")
        self.assertFalse(
            hasattr(orch, "_style_monitor"),
            "默认关闭时不应创建 monitor"
        )

        # 恢复
        if old_env:
            os.environ["YUYI_STYLE_MONITOR"] = old_env
        else:
            os.environ.pop("YUYI_STYLE_MONITOR", None)


# ============================================================
# 测试 4：集成测试（启用监控后真实记录）
# ============================================================

class TestStyleMonitorIntegration(unittest.TestCase):
    """启用监控后的集成测试。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        os.environ["YUYI_STYLE_MONITOR"] = "1"
        # 临时覆盖 data 目录
        self._old_data_dir = os.environ.get("YUYI_DATA_DIR", "")
        os.environ["YUYI_DATA_DIR"] = self.tmp_dir.name

    def tearDown(self):
        os.environ["YUYI_STYLE_MONITOR"] = "0"
        if self._old_data_dir:
            os.environ["YUYI_DATA_DIR"] = self._old_data_dir
        else:
            os.environ.pop("YUYI_DATA_DIR", None)
        self.tmp_dir.cleanup()

    def test_orchestrator_records_style_when_enabled(self):
        """启用监控后，Orchestrator 应记录风格快照。"""
        from src.orchestrator import Orchestrator

        orch = Orchestrator()
        orch.target_user_id = "366648462"

        # 手动创建 monitor 使用 temp dir（覆盖默认的 data/ 目录）
        from src.behavior.response_style_monitor import ResponseStyleMonitor
        orch._style_monitor = ResponseStyleMonitor(data_dir=self.tmp_dir.name)

        # 模拟 process() 中的风格记录调用
        orch._record_style_snapshot(
            "谢谢你的信任，我会一直在这里陪伴你、关心你、理解你。",
            "你好",
            "conv_integration_001",
        )

        # 检查 monitor 是否被创建
        self.assertTrue(hasattr(orch, "_style_monitor"), "应创建 monitor")

        # 加载记录
        records = orch._style_monitor.load_all()
        self.assertGreaterEqual(len(records), 1, "应有至少 1 条记录")

        # 验证记录内容
        self.assertEqual(records[0]["conversation_id"], "conv_integration_001")
        self.assertGreater(records[0]["warmth"], 0.0, "温暖回复应有 warmth")


# ============================================================
# 测试 5：StyleComparator 对比工具
# ============================================================

class TestStyleComparator(unittest.TestCase):
    """验证风格对比工具。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self.tmp_dir.name)
        from src.behavior.response_style_monitor import ResponseStyleMonitor
        from src.behavior.style_comparator import StyleComparator
        self.monitor = ResponseStyleMonitor(data_dir=self.tmp_dir.name)
        self.comparator = StyleComparator(monitor=self.monitor)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _seed_records(self, records: List[Dict]):
        """写入模拟快照数据。"""
        file_path = self._tmp_path / "response_styles.jsonl"
        with open(file_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_compare_daily_no_data(self):
        """无数据时不崩溃。"""
        report = self.comparator.compare_daily()
        self.assertEqual(report.period1_count, 0)
        self.assertEqual(report.period2_count, 0)
        self.assertIsInstance(report.summary, str)

    def test_compare_daily_with_data(self):
        """有数据时正常对比。"""
        now = time.time()
        yesterday = now - 1.5 * 86400  # 1.5 天前（昨天）

        records = []
        for i in range(5):
            records.append({
                "snapshot_id": f"yesterday_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(yesterday)),
                "warmth": 0.7, "curiosity": 0.5, "initiative": 0.4,
                "formality": 0.3, "playfulness": 0.5,
            })
        for i in range(5):
            records.append({
                "snapshot_id": f"today_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "warmth": 0.8, "curiosity": 0.6, "initiative": 0.5,
                "formality": 0.2, "playfulness": 0.6,
            })

        self._seed_records(records)
        report = self.comparator.compare_daily()

        self.assertEqual(report.period1_count, 5)
        self.assertEqual(report.period2_count, 5)
        self.assertGreater(len(report.changes), 0)
        self.assertIsInstance(report.summary, str)

    def test_compare_periods_significant_change(self):
        """显著变化应被标记。"""
        now = time.time()
        three_days_ago = now - 3 * 86400

        records = []
        # 基准期（3天前）：低 warmth
        for i in range(10):
            records.append({
                "snapshot_id": f"base_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(three_days_ago)),
                "warmth": 0.3, "curiosity": 0.5, "initiative": 0.4,
                "formality": 0.6, "playfulness": 0.2,
            })
        # 对比期（现在）：高 warmth
        for i in range(10):
            records.append({
                "snapshot_id": f"curr_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "warmth": 0.8, "curiosity": 0.5, "initiative": 0.4,
                "formality": 0.2, "playfulness": 0.2,
            })

        self._seed_records(records)
        report = self.comparator.compare_periods(
            period1_days=(7, 2),
            period2_days=(2, 0),
        )

        warmth_change = next((c for c in report.changes if c.metric == "warmth"), None)
        self.assertIsNotNone(warmth_change)
        self.assertTrue(warmth_change.significant, "warmth 从 0.3 到 0.8 应标记为显著")

    def test_compare_periods_flat(self):
        """无变化时不应标记显著。"""
        now = time.time()
        five_days_ago = now - 5 * 86400

        records = []
        # 基准期（5天前）：风格值相同
        for i in range(10):
            records.append({
                "snapshot_id": f"base_flat_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago)),
                "warmth": 0.7, "curiosity": 0.6, "initiative": 0.5,
                "formality": 0.3, "playfulness": 0.5,
            })
        # 对比期（现在）：风格值相同
        for i in range(10):
            records.append({
                "snapshot_id": f"curr_flat_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "warmth": 0.7, "curiosity": 0.6, "initiative": 0.5,
                "formality": 0.3, "playfulness": 0.5,
            })

        self._seed_records(records)
        report = self.comparator.compare_periods(
            period1_days=(7, 2),
            period2_days=(2, 0),
        )

        significant = [c for c in report.changes if c.significant]
        self.assertEqual(len(significant), 0, "稳定风格不应有显著变化标记")

    def test_get_trend_chart(self):
        """趋势图不崩溃。"""
        now = time.time()
        records = []
        for day_offset in range(7):
            t = now - day_offset * 86400
            for i in range(3):
                records.append({
                    "snapshot_id": f"day{day_offset}_{i}",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                    "warmth": 0.5 + day_offset * 0.05,
                    "curiosity": 0.6,
                    "initiative": 0.4,
                    "formality": 0.3,
                    "playfulness": 0.5,
                })

        self._seed_records(records)
        chart = self.comparator.get_trend_chart(days=7)
        self.assertIsInstance(chart, str)
        self.assertGreater(len(chart), 0)
        # 趋势图应包含日期
        self.assertIn("20", chart, "趋势图应包含年份")


# ============================================================
# 测试 6：Phase 3.7.7 TopicAnalyzer 话题分类器
# ============================================================

class TestTopicAnalyzer(unittest.TestCase):
    """验证话题分类器。"""

    def test_analyze_technology_topic(self):
        """技术话题应被正确分类。"""
        from src.behavior.topic_analyzer import analyze_topic

        ctx = analyze_topic(
            user_message="请解释动态规划和贪心算法的区别",
            reply="动态规划是一种通过将问题分解为子问题...",
        )
        self.assertEqual(ctx.domain, "technology")
        self.assertGreater(ctx.confidence, 0.0)

    def test_analyze_emotional_topic(self):
        """情感话题应被正确分类。"""
        from src.behavior.topic_analyzer import analyze_topic

        ctx = analyze_topic(
            user_message="你觉得AI能真正理解人类的感情吗？",
            reply="感情是一个非常复杂的话题...",
        )
        self.assertEqual(ctx.domain, "emotional")
        self.assertGreater(ctx.confidence, 0.0)

    def test_analyze_philosophy_topic(self):
        """哲学话题应被正确分类。"""
        from src.behavior.topic_analyzer import analyze_topic

        ctx = analyze_topic(
            user_message="如果AI有了自我意识，我们应该怎么对待它？",
            reply="这是一个非常深刻的伦理问题...",
        )
        self.assertEqual(ctx.domain, "philosophy")
        self.assertGreater(ctx.confidence, 0.0)

    def test_analyze_daily_life_topic(self):
        """日常话题应被正确分类。"""
        from src.behavior.topic_analyzer import analyze_topic

        ctx = analyze_topic(
            user_message="今天天气真好，适合出去散步",
            reply="",
        )
        self.assertEqual(ctx.domain, "daily_life")

    def test_analyze_empty_input(self):
        """空输入不崩溃。"""
        from src.behavior.topic_analyzer import analyze_topic

        ctx = analyze_topic(user_message="", reply="")
        self.assertEqual(ctx.domain, "unknown")
        self.assertEqual(ctx.confidence, 0.0)

    def test_normalize_style_by_topic(self):
        """话题归一化应正确修正风格指标。"""
        from src.behavior.topic_analyzer import normalize_style_by_topic

        # 技术话题：原始 warmth 0.3，formality 0.8
        tech_style = {"warmth": 0.3, "formality": 0.8, "curiosity": 0.5}
        normalized = normalize_style_by_topic(tech_style, "technology")

        # 技术话题的 baseline 是 warmth=0.30, formality=0.70
        # 所以归一化后 warmth delta ≈ 0, formality delta ≈ +0.1
        self.assertAlmostEqual(normalized["warmth"], 0.0, places=1)
        self.assertAlmostEqual(normalized["formality"], 0.1, places=1)

        # 情感话题：原始 warmth 0.7，formality 0.3
        emotional_style = {"warmth": 0.7, "formality": 0.3, "curiosity": 0.6}
        normalized2 = normalize_style_by_topic(emotional_style, "emotional")

        # 情感话题的 baseline 是 warmth=0.65, formality=0.30
        # 所以归一化后 warmth delta ≈ +0.05, formality delta ≈ 0
        self.assertAlmostEqual(normalized2["warmth"], 0.05, places=1)
        self.assertAlmostEqual(normalized2["formality"], 0.0, places=1)


# ============================================================
# 测试 7：Phase 3.7.7 StyleSnapshot 升级
# ============================================================

class TestPhase377StyleSnapshot(unittest.TestCase):
    """验证 StyleSnapshot 新增字段。"""

    def test_snapshot_has_topic_context(self):
        """StyleSnapshot 应包含 topic_context。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        snapshot = analyze_reply_style(
            reply="动态规划的核心思想是将大问题分解为子问题。",
            user_message="解释动态规划",
        )

        self.assertIn("topic_context", snapshot.__dataclass_fields__)
        self.assertIsInstance(snapshot.topic_context, dict)
        self.assertIn("domain", snapshot.topic_context)
        self.assertEqual(snapshot.topic_context["domain"], "technology")

    def test_snapshot_has_core_personality_vector(self):
        """StyleSnapshot 应包含 core_personality_vector。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        snapshot = analyze_reply_style(
            reply="作为一个AI，我不确定能否真正理解，但我可以陪你一起探索。",
            user_message="你能理解我吗？",
        )

        self.assertIn("core_personality_vector", snapshot.__dataclass_fields__)
        cpv = snapshot.core_personality_vector
        self.assertIsInstance(cpv, dict)
        self.assertIn("honesty", cpv)
        self.assertIn("long_term_focus", cpv)
        self.assertIn("relationship_orientation", cpv)

        # 包含"作为一个AI"应触发诚实度
        self.assertGreater(cpv["honesty"], 0.0, "应检测到诚实度")

    def test_snapshot_phase_is_377(self):
        """版本号应为 3.7.7。"""
        from src.behavior.response_style_monitor import analyze_reply_style

        snapshot = analyze_reply_style(reply="你好")
        self.assertEqual(snapshot.phase, "3.7.7")


# ============================================================
# 测试 8：Phase 3.7.7 同话题漂移检测
# ============================================================

class TestPhase377DriftDetection(unittest.TestCase):
    """验证同话题漂移检测。"""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self.tmp_dir.name)
        from src.behavior.response_style_monitor import ResponseStyleMonitor
        from src.behavior.personality_drift_detector import PersonalityDriftDetector
        self.monitor = ResponseStyleMonitor(data_dir=self.tmp_dir.name)
        self.detector = PersonalityDriftDetector(monitor=self.monitor)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _seed_topic_records(self, records: List[Dict]):
        """写入含 topic_context 的模拟快照。"""
        file_path = self._tmp_path / "response_styles.jsonl"
        with open(file_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def test_same_topic_no_drift(self):
        """同话题内风格稳定，不应检测到漂移。"""
        now = time.time()
        five_days_ago = now - 5 * 86400

        records = []
        # 基线：5天前 emotional 话题
        for i in range(5):
            records.append({
                "snapshot_id": f"emo_base_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago)),
                "warmth": 0.7, "curiosity": 0.6, "initiative": 0.4,
                "formality": 0.3, "playfulness": 0.3,
                "topic_context": {"domain": "emotional", "sub_topic": "feelings", "confidence": 0.8},
            })
        # 当前：现在 emotional 话题（风格相同）
        for i in range(5):
            records.append({
                "snapshot_id": f"emo_curr_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "warmth": 0.75, "curiosity": 0.55, "initiative": 0.45,
                "formality": 0.28, "playfulness": 0.35,
                "topic_context": {"domain": "emotional", "sub_topic": "feelings", "confidence": 0.8},
            })

        self._seed_topic_records(records)
        report = self.detector.check_same_topic_drift(
            topic_domain="emotional",
            baseline_days=(7, 2),
            current_days=(2, 0),
        )

        self.assertFalse(report.has_drift, f"同话题稳定风格不应检测到漂移: {report.summary}")
        self.assertTrue(report.topic_aware)

    def test_cross_topic_not_mistaken_for_drift(self):
        """跨话题风格变化不应被同话题检测误判为漂移。"""
        now = time.time()
        five_days_ago = now - 5 * 86400

        records = []
        # 基线：5天前 emotional 话题（高 warmth）
        for i in range(5):
            records.append({
                "snapshot_id": f"emo_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago)),
                "warmth": 0.7, "formality": 0.3,
                "topic_context": {"domain": "emotional", "sub_topic": "feelings", "confidence": 0.8},
            })
        # 当前：现在 technology 话题（低 warmth，高 formality）
        # 这些不应计入 emotional 同话题检测
        for i in range(5):
            records.append({
                "snapshot_id": f"tech_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "warmth": 0.3, "formality": 0.8,
                "topic_context": {"domain": "technology", "sub_topic": "programming", "confidence": 0.9},
            })

        self._seed_topic_records(records)
        report = self.detector.check_same_topic_drift(
            topic_domain="emotional",
            baseline_days=(7, 2),
            current_days=(2, 0),
        )

        # 当前时间段应无 emotional 快照，返回"数据不足"
        self.assertIn("不足", report.summary)

    def test_core_personality_drift_detection(self):
        """核心人格变化应被检测。"""
        now = time.time()
        five_days_ago = now - 5 * 86400

        records = []
        # 基线：高 honesty
        for i in range(5):
            records.append({
                "snapshot_id": f"core_base_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago)),
                "core_personality_vector": {
                    "curiosity": 0.6, "honesty": 0.7, "initiative": 0.5,
                    "long_term_focus": 0.4, "relationship_orientation": 0.6,
                },
            })
        # 当前：honesty 大幅下降
        for i in range(5):
            records.append({
                "snapshot_id": f"core_curr_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "core_personality_vector": {
                    "curiosity": 0.55, "honesty": 0.2, "initiative": 0.45,
                    "long_term_focus": 0.35, "relationship_orientation": 0.55,
                },
            })

        self._seed_topic_records(records)
        report = self.detector.check_core_personality_drift(
            baseline_days=(7, 2),
            current_days=(2, 0),
        )

        self.assertTrue(report.has_drift, f"核心人格大幅变化应被检测: {report.summary}")
        # honesty 从 0.7 到 0.2，delta = -0.5，超出阈值 0.20
        self.assertIn("honesty", str(report.summary))

    def test_core_personality_stable(self):
        """核心人格稳定时不应检测到漂移。"""
        now = time.time()
        five_days_ago = now - 5 * 86400

        records = []
        for i in range(5):
            records.append({
                "snapshot_id": f"stable_base_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(five_days_ago)),
                "core_personality_vector": {
                    "curiosity": 0.6, "honesty": 0.65, "initiative": 0.5,
                    "long_term_focus": 0.4, "relationship_orientation": 0.6,
                },
            })
        for i in range(5):
            records.append({
                "snapshot_id": f"stable_curr_{i}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "core_personality_vector": {
                    "curiosity": 0.62, "honesty": 0.63, "initiative": 0.52,
                    "long_term_focus": 0.42, "relationship_orientation": 0.58,
                },
            })

        self._seed_topic_records(records)
        report = self.detector.check_core_personality_drift(
            baseline_days=(7, 2),
            current_days=(2, 0),
        )

        self.assertFalse(report.has_drift, f"核心人格稳定不应检测到漂移: {report.summary}")


if __name__ == "__main__":
    unittest.main(verbosity=2)