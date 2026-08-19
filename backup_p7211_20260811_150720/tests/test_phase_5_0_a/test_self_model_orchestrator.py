# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_a/test_self_model_orchestrator.py

Phase 5.0-A: SelfModelOrchestrator 单元测试

覆盖:
- 5 阶段全部执行
- 单阶段失败隔离
- 空依赖运行
- health_check
- 基础行为
"""
from __future__ import annotations

import os
import sys
import unittest
from typing import Any, Dict, List, Optional


# 确保 src 可导入
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."),
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from src.orchestrator.self_model_orchestrator import (
    SelfModelOrchestrator,
    SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION,
)


# ============================================================
# 测试辅助
# ============================================================
class FakeSnapshot:
    def __init__(self, identity_id: str = "yuyi_default", version: int = 1):
        self.identity_id = identity_id
        self.version = version
        self.entries: List[Any] = []
        self.stable_traits: List[Any] = []
        self.core_values: List[Any] = []
        self.preferences: List[Any] = []
        self.current_state: Dict[str, Any] = {}
        self.health: Dict[str, Any] = {"complete": True}

    def compute_health(self) -> Dict[str, Any]:
        return {"complete": True, "fields": 5}


class FakeReflectionRecord:
    def __init__(self):
        self.reflection_type = "consistent"
        self.severity = 0.0
        self.evidence_ids: List[str] = []


class FakeConsistencyReport:
    def __init__(self, score: float = 1.0, is_consistent: bool = True):
        self.score = score
        self.is_consistent = is_consistent
        self.issues: List[Any] = []
        self.warnings: List[str] = []


class FakeEvolutionResult:
    def __init__(self, new_snapshot: Optional[FakeSnapshot] = None, is_noop: bool = False):
        self.is_noop = is_noop
        self.new_snapshot = new_snapshot or FakeSnapshot()
        self.original_snapshot = FakeSnapshot()
        self.accepted_changes: List[Any] = []
        self.rejected_changes: List[Any] = []
        self.evolution_records: List[Any] = []
        self.summary = "fake evolution"


# ============================================================
# Fake 子系统
# ============================================================
class FakeFoundation:
    def __init__(self, fail: bool = False, snapshot: Optional[FakeSnapshot] = None):
        self._fail = fail
        self._snapshot = snapshot
        self._current: Optional[FakeSnapshot] = snapshot
        self.build_count = 0
        self.snapshot_count = 0

    @property
    def current(self) -> Optional[FakeSnapshot]:
        return self._current

    def build(self, inputs: Optional[Dict[str, Any]] = None) -> Optional[FakeSnapshot]:
        self.build_count += 1
        if self._fail:
            raise RuntimeError("fake foundation build failure")
        snap = FakeSnapshot(version=self.build_count)
        self._current = snap
        return snap

    def snapshot(self) -> FakeSnapshot:
        self.snapshot_count += 1
        if self._current is not None:
            return self._current
        return FakeSnapshot()

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "configured": True, "has_health_check": True}


class FakeEvolutionEngine:
    def __init__(self, fail: bool = False, noop: bool = False):
        self._fail = fail
        self._noop = noop
        self.evolve_count = 0

    def evolve(self, snapshot=None, proposals=None, reflections=None, manual_changes=None):
        self.evolve_count += 1
        if self._fail:
            raise RuntimeError("fake evolution failure")
        if self._noop:
            return FakeEvolutionResult(is_noop=True)
        return FakeEvolutionResult(new_snapshot=FakeSnapshot(version=99))

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "configured": True, "has_health_check": True}


class FakeReflectionEngine:
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.reflect_count = 0

    def reflect(self, old_snapshot=None, new_snapshot=None, evolution_history=None, source="runtime"):
        self.reflect_count += 1
        if self._fail:
            raise RuntimeError("fake reflection failure")
        return FakeReflectionRecord()

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "configured": True, "has_health_check": True}


class FakeConsistencyChecker:
    def __init__(self, fail: bool = False):
        self._fail = fail
        self.check_count = 0

    def check(self, snapshot=None, evolution_history=None):
        self.check_count += 1
        if self._fail:
            raise RuntimeError("fake validation failure")
        return FakeConsistencyReport()

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "configured": True, "has_health_check": True}


class FakePersistenceRuntime:
    def __init__(self, fail: bool = False, history: Optional[List[Any]] = None):
        self._fail = fail
        self._history = history or []
        self.persist_count = 0
        self.current_identity_id = "yuyi_default"

    def persist_evolution(self, evolution_result, identity_id=None) -> bool:
        self.persist_count += 1
        if self._fail:
            return False
        return True

    def save_snapshot(self, snapshot) -> bool:
        return True

    def get_evolution_history(self, identity_id=None, limit=None):
        return list(self._history)

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "configured": True, "has_health_check": True}


# ============================================================
# 测试
# ============================================================
class TestSelfModelOrchestratorConstruction(unittest.TestCase):
    def test_default_construction(self):
        smo = SelfModelOrchestrator()
        self.assertIsNone(smo.foundation)
        self.assertIsNone(smo.evolution_engine)
        self.assertIsNone(smo.reflection_engine)
        self.assertIsNone(smo.consistency_checker)
        self.assertIsNone(smo.persistence_runtime)
        self.assertEqual(smo.identity_id, "yuyi_default")

    def test_construction_with_identity_id(self):
        smo = SelfModelOrchestrator(identity_id="yuyi_test")
        self.assertEqual(smo.identity_id, "yuyi_test")

    def test_construction_with_all_subsystems(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        self.assertIsNotNone(smo.foundation)
        self.assertIsNotNone(smo.evolution_engine)
        self.assertIsNotNone(smo.reflection_engine)
        self.assertIsNotNone(smo.consistency_checker)
        self.assertIsNotNone(smo.persistence_runtime)

    def test_schema_version(self):
        self.assertEqual(
            SelfModelOrchestrator.schema_version,
            SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION,
        )


class TestNoOpBehavior(unittest.TestCase):
    """全部子系统 None 时必须 no-op,不能崩溃。"""

    def test_all_none_runs_without_error(self):
        smo = SelfModelOrchestrator()
        result = smo.run_after_event({})
        self.assertIsNone(result["build"])
        self.assertIsNone(result["evolution"])
        self.assertIsNone(result["reflection"])
        self.assertIsNone(result["validation"])
        self.assertIsNone(result["persistence"])
        self.assertEqual(result["errors"], [])

    def test_all_none_runs_with_none_context(self):
        smo = SelfModelOrchestrator()
        result = smo.run_after_event(None)
        self.assertIsNone(result["build"])
        self.assertEqual(result["errors"], [])

    def test_all_none_increments_run_count(self):
        smo = SelfModelOrchestrator()
        smo.run_after_event({})
        smo.run_after_event({})
        self.assertEqual(smo.run_count, 2)


class TestFiveStagesExecuted(unittest.TestCase):
    """5 阶段全部配置时,必须全部触发。"""

    def setUp(self):
        self.foundation = FakeFoundation()
        self.evolution = FakeEvolutionEngine()
        self.reflection = FakeReflectionEngine()
        self.checker = FakeConsistencyChecker()
        self.persistence = FakePersistenceRuntime()
        self.smo = SelfModelOrchestrator(
            foundation=self.foundation,
            evolution_engine=self.evolution,
            reflection_engine=self.reflection,
            consistency_checker=self.checker,
            persistence_runtime=self.persistence,
        )

    def test_build_executes(self):
        result = self.smo.run_after_event({})
        self.assertIsNotNone(result["build"])
        self.assertEqual(self.foundation.build_count, 1)

    def test_evolution_executes(self):
        result = self.smo.run_after_event({})
        self.assertIsNotNone(result["evolution"])
        self.assertEqual(self.evolution.evolve_count, 1)

    def test_reflection_executes(self):
        result = self.smo.run_after_event({})
        self.assertIsNotNone(result["reflection"])
        self.assertEqual(self.reflection.reflect_count, 1)

    def test_validation_executes(self):
        result = self.smo.run_after_event({})
        self.assertIsNotNone(result["validation"])
        self.assertEqual(self.checker.check_count, 1)

    def test_persistence_executes(self):
        result = self.smo.run_after_event({})
        self.assertTrue(result["persistence"])
        self.assertEqual(self.persistence.persist_count, 1)

    def test_all_errors_empty_on_success(self):
        result = self.smo.run_after_event({})
        self.assertEqual(result["errors"], [])

    def test_run_count_increments(self):
        self.smo.run_after_event({})
        self.smo.run_after_event({})
        self.assertEqual(self.smo.run_count, 2)


class TestStageFailureIsolation(unittest.TestCase):
    """任一阶段失败不影响其他阶段。"""

    def test_foundation_failure_does_not_break_other_stages(self):
        persistence = FakePersistenceRuntime()
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(fail=True),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=persistence,
        )
        result = smo.run_after_event({})
        # foundation 失败 → build 应该是 None
        self.assertIsNone(result["build"])
        # errors 应该有 build 错误
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("build", result["errors"][0])
        self.assertIsNone(result["persistence"])

    def test_evolution_failure_isolated(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(fail=True),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        result = smo.run_after_event({})
        self.assertIsNotNone(result["build"])
        self.assertIsNone(result["evolution"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("evolution", result["errors"][0])

    def test_reflection_failure_isolated(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(fail=True),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        result = smo.run_after_event({})
        self.assertIsNotNone(result["build"])
        self.assertIsNotNone(result["evolution"])
        self.assertIsNone(result["reflection"])
        self.assertIsNotNone(result["validation"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("reflection", result["errors"][0])

    def test_validation_failure_isolated(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(fail=True),
            persistence_runtime=FakePersistenceRuntime(),
        )
        result = smo.run_after_event({})
        self.assertIsNotNone(result["build"])
        self.assertIsNotNone(result["reflection"])
        self.assertIsNone(result["validation"])
        self.assertEqual(len(result["errors"]), 1)
        self.assertIn("validation", result["errors"][0])

    def test_persistence_returning_false_not_error(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(fail=True),
        )
        result = smo.run_after_event({})
        self.assertFalse(result["persistence"])
        # 业务返回 False 不算 error(是正常返回值)
        self.assertEqual(result["errors"], [])

    def test_multiple_failures_all_recorded(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(fail=True),
            evolution_engine=FakeEvolutionEngine(fail=True),
            reflection_engine=FakeReflectionEngine(fail=True),
            consistency_checker=FakeConsistencyChecker(fail=True),
            persistence_runtime=FakePersistenceRuntime(),
        )
        result = smo.run_after_event({})
        self.assertGreaterEqual(len(result["errors"]), 1)


class TestHealthCheck(unittest.TestCase):
    def test_health_check_all_unconfigured(self):
        smo = SelfModelOrchestrator()
        health = smo.health_check()
        self.assertEqual(health["name"], "self_model_orchestrator")
        self.assertEqual(health["schema_version"], SELF_MODEL_ORCHESTRATOR_SCHEMA_VERSION)
        self.assertEqual(health["run_count"], 0)
        for sub_name, sub_status in health["subsystems"].items():
            self.assertFalse(sub_status["configured"])

    def test_health_check_all_configured(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        health = smo.health_check()
        for sub_name, sub_status in health["subsystems"].items():
            self.assertTrue(sub_status["configured"])
            self.assertTrue(sub_status["healthy"])

    def test_health_check_records_last_run(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        smo.run_after_event({})
        health = smo.health_check()
        self.assertEqual(health["run_count"], 1)
        self.assertIsNotNone(health["last_run_at"])
        self.assertEqual(health["last_errors"], [])

    def test_health_check_records_errors(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(fail=True),
        )
        smo.run_after_event({})
        health = smo.health_check()
        self.assertGreater(len(health["last_errors"]), 0)


class TestSnapshotTracking(unittest.TestCase):
    def test_old_snapshot_tracked(self):
        foundation = FakeFoundation(snapshot=FakeSnapshot(version=1))
        smo = SelfModelOrchestrator(foundation=foundation)
        smo.run_after_event({})
        self.assertIsNotNone(smo.last_old_snapshot)

    def test_new_snapshot_tracked(self):
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        smo.run_after_event({})
        self.assertIsNotNone(smo.last_new_snapshot)


class TestContextExtraction(unittest.TestCase):
    def test_context_keys_passed_to_foundation(self):
        foundation = FakeFoundation()
        smo = SelfModelOrchestrator(foundation=foundation)
        ctx = {
            "trait_states": {"warmth": {"current_value": 0.8}},
            "current_state": {"mood": "calm"},
            "identity_overrides": {"name": "yuyi"},
            "random_garbage": "should_be_ignored",
            "proposals": [],
        }
        smo.run_after_event(ctx)
        self.assertEqual(foundation.build_count, 1)

    def test_proposals_passed_to_evolution(self):
        evolution = FakeEvolutionEngine()
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=evolution,
        )
        ctx = {"proposals": ["p1", "p2"]}
        smo.run_after_event(ctx)
        self.assertEqual(evolution.evolve_count, 1)


class TestResetStats(unittest.TestCase):
    def test_reset_clears_counters(self):
        smo = SelfModelOrchestrator(foundation=FakeFoundation())
        smo.run_after_event({})
        smo.run_after_event({})
        self.assertEqual(smo.run_count, 2)
        smo.reset_stats()
        self.assertEqual(smo.run_count, 0)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_run_after_event(self):
        import threading
        smo = SelfModelOrchestrator(
            foundation=FakeFoundation(),
            evolution_engine=FakeEvolutionEngine(),
            reflection_engine=FakeReflectionEngine(),
            consistency_checker=FakeConsistencyChecker(),
            persistence_runtime=FakePersistenceRuntime(),
        )
        errors: List[str] = []

        def worker():
            try:
                for _ in range(10):
                    smo.run_after_event({})
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(smo.run_count, 50)


class TestRealFoundationIntegration(unittest.TestCase):
    """用真实的 SelfModelFoundation 验证(轻量集成)。"""

    def test_real_foundation_builds_snapshot(self):
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        foundation = SelfModelFoundation(identity_id="yuyi_test")
        smo = SelfModelOrchestrator(foundation=foundation)
        result = smo.run_after_event({})
        self.assertIsNotNone(result["build"])
        self.assertEqual(result["errors"], [])

    def test_real_foundation_with_traits(self):
        from src.runtime.self_model.self_model_foundation import (
            SelfModelFoundation,
        )
        foundation = SelfModelFoundation(identity_id="yuyi_test")
        smo = SelfModelOrchestrator(foundation=foundation)
        result = smo.run_after_event({
            "trait_states": {
                "warmth": {"current_value": 0.9, "direction": "up"},
                "shyness": {"current_value": 0.4, "direction": "down"},
            },
        })
        self.assertIsNotNone(result["build"])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
