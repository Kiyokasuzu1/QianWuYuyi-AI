"""
Phase 3.5.17: Identity Stability Engine 测试

覆盖：
- 稳定性报告生成（continuity + anchor + memory pollution）
- memory pollution 检测
- RuntimeCore 接入
"""

from __future__ import annotations

import os
import tempfile
import unittest

from src.personality.identity_stability_engine import IdentityStabilityEngine


class TestIdentityStabilityEngine(unittest.TestCase):
    def test_01_stable_report_no_issues(self):
        engine = IdentityStabilityEngine()
        report = engine.generate_report(
            identity_id="si_test",
            continuity_report={
                "continuity_score": 0.92,
                "is_continuous": True,
                "before_snapshot_id": "a",
                "after_snapshot_id": "b",
                "conflicts": [],
            },
            anchor_integrity_report={
                "is_intact": True,
                "deviations": [],
                "constraint_violations": [],
            },
            memories=[],
        )
        d = report.to_dict()
        self.assertTrue(d["is_stable"])
        self.assertGreaterEqual(d["stability_score"], 0.75)
        self.assertEqual(len(d["issues"]), 0)

    def test_02_memory_pollution_detected(self):
        engine = IdentityStabilityEngine()
        report = engine.generate_report(
            identity_id="si_test",
            continuity_report={"continuity_score": 0.90, "is_continuous": True, "conflicts": []},
            anchor_integrity_report={"is_intact": True},
            memories=[
                {"id": "m1", "role": "assistant", "truth": 0.2, "memory_class": "assistant_output", "usage": []},
                {"id": "m2", "role": "user", "truth": 0.6, "memory_class": "user_statement", "usage": ["growth"]},
            ],
        )
        self.assertFalse(report.is_stable)
        self.assertGreaterEqual(report.memory_pollution.suspicious_count, 1)
        self.assertTrue(any(i.issue_type == "memory_pollution" for i in report.issues))

    def test_03_anchor_deviation_blocks(self):
        engine = IdentityStabilityEngine()
        report = engine.generate_report(
            identity_id="si_test",
            continuity_report={"continuity_score": 0.88, "is_continuous": True, "conflicts": []},
            anchor_integrity_report={
                "is_intact": False,
                "risk_level": "high",
                "deviations": [{"deviation_type": "weight_drift"}],
                "constraint_violations": [{"anchor_id": "anchor_authenticity"}],
            },
            memories=[],
        )
        self.assertFalse(report.is_stable)
        self.assertTrue(any(i.issue_type == "anchor_deviation" for i in report.issues))


class TestRuntimeIdentityStabilityIntegration(unittest.TestCase):
    def test_01_runtime_refresh_identity_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            from src.runtime.runtime_core import RuntimeCore
            from src.personality.trait_state import create_trait_state

            config = {
                "adapters_enabled": True,
                "experience_enabled": True,
                "identity_anchor_enabled": True,
                "identity_continuity_enabled": True,
                "identity_stability_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem_stability.json"),
                "growth_proposals_path": os.path.join(tmp, "gp_stability.json"),
                "state_file": os.path.join(tmp, "rt_stability.json"),
                "tick_interval_seconds": 1,
            }
            rc = RuntimeCore(config=config)
            self.assertIsNotNone(rc.identity_stability_engine)

            # 1) SelfModel 刷新
            rc.refresh_self_model(trait_states={
                "warmth": create_trait_state("warmth", 0.7),
                "honesty": create_trait_state("honesty", 0.8),
                "autonomy": create_trait_state("autonomy", 0.75),
            })

            # 2) 写入一条疑似污染记忆（模拟外部系统错误）
            store = rc.memory_adapter.get_memory_store()
            store.add({
                "id": "mem_pollute_001",
                "user_id": "yuyi",
                "role": "assistant",
                "content": "过去回复被错误当成事实",
                "memory_class": "assistant_output",
                "truth": 0.2,
                "usage": ["growth"],
            })

            # 3) 第一次刷新 continuity（建立 baseline）
            rc.refresh_identity_continuity(force=True)

            # 4) 刷新稳定性报告
            report = rc.refresh_identity_stability(force=True)
            self.assertIsNotNone(report)
            self.assertIn("issues", report)
            self.assertTrue(any(i["issue_type"] == "memory_pollution" for i in report["issues"]))
            snap = rc.get_identity_stability_snapshot()
            self.assertIsNotNone(snap)
            self.assertGreaterEqual(snap["total_reports"], 1)


if __name__ == "__main__":
    unittest.main()

