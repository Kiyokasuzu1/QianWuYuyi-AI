"""
Phase 3.5.20: RuntimeIntegrationManager 测试
"""

from __future__ import annotations

import os
import tempfile
import unittest


class TestRuntimeIntegrationManager(unittest.TestCase):
    def test_runtime_health_report_contains_required_sections(self):
        from src.runtime.runtime_core import RuntimeCore

        with tempfile.TemporaryDirectory() as tmp:
            rc = RuntimeCore(config={
                "adapters_enabled": True,
                "experience_enabled": True,
                "reflection_evaluator_enabled": True,
                "reflection_growth_bridge_enabled": True,
                "identity_continuity_enabled": True,
                "identity_stability_enabled": True,
                "runtime_integration_enabled": True,
                "memory_store_path": os.path.join(tmp, "mem.json"),
                "growth_proposals_path": os.path.join(tmp, "gp.json"),
                "state_file": os.path.join(tmp, "runtime.json"),
            })
            report = rc.get_runtime_health_report()
            self.assertIsNotNone(report)
            self.assertIn("memory_status", report)
            self.assertIn("reflection_status", report)
            self.assertIn("growth_status", report)
            self.assertIn("identity_status", report)
            self.assertIn("personality_status", report)
            self.assertIn("relationship_status", report)
            self.assertIn("emotion_status", report)
            self.assertIn("registered_modules", report)

