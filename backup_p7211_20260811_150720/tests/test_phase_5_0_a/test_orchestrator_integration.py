# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_a/test_orchestrator_integration.py

Phase 5.0-A: Orchestrator + SelfModelOrchestrator 集成测试。

核心要求:
- 默认行为不变(不注入 SelfModel 时,Orchestrator 行为完全一致)
- 注入 SelfModel 后,process() 完成后会自动触发
- SelfModel 编排器异常不会影响 reply

注意:
Orchestrator.__init__ 真实实例化需要 ResponseEngine 加载 OpenAI client,
在测试环境下会因为缺 API key 失败,因此本测试用 MockOrc 模拟 Orchestrator
的关键表面(process/clear_history/configure_self_model)。
"""
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 src 可导入
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from src.orchestrator.self_model_orchestrator import SelfModelOrchestrator  # noqa: E402
from src.orchestrator.long_loop import LongLoop  # noqa: E402


# ============================================================
# MockOrc: 模拟 Orchestrator 的关键接口
# ============================================================
class MockOrc:
    """测试用 Orchestrator 替身,模拟 process / clear_history / configure_self_model。"""

    def __init__(self, replies: Optional[List[str]] = None) -> None:
        self._replies = list(replies or ["mock reply"])
        self._idx = 0
        self.history: List[Any] = []
        self.clear_count = 0
        self.self_model_orchestrator: Optional[Any] = None
        # 用于验证注入触发次数
        self.smo_call_count = 0
        self.smo_call_contexts: List[Dict[str, Any]] = []

    def process(self, user_input: str) -> str:
        self.history.append(user_input)
        # 模拟 Orchestrator.process() 末尾的 SelfModel 调用
        if self.self_model_orchestrator is not None:
            self.smo_call_count += 1
            try:
                ctx = {
                    "trait_states": {"warmth": 0.5},
                    "current_state": {"mood": "neutral"},
                    "identity_overrides": None,
                }
                result = self.self_model_orchestrator.run_after_event(ctx)
                self.smo_call_contexts.append(ctx)
            except Exception:
                # Orchestrator 内部 try/except 隔离,这里不传播
                pass
        # 返回 reply
        if self._idx < len(self._replies):
            reply = self._replies[self._idx]
            self._idx += 1
        else:
            reply = "mock reply"
        return reply

    def clear_history(self) -> None:
        self.clear_count += 1

    def configure_self_model(self, smo: Optional[Any]) -> None:
        self.self_model_orchestrator = smo

    def is_self_model_configured(self) -> bool:
        return self.self_model_orchestrator is not None


# ============================================================
# TestDefaultBehavior: 默认行为不变
# ============================================================
class TestDefaultBehavior(unittest.TestCase):
    def test_no_smo_configured_by_default(self):
        orch = MockOrc()
        self.assertFalse(orch.is_self_model_configured())
        self.assertIsNone(orch.self_model_orchestrator)

    def test_process_without_smo_works_normally(self):
        orch = MockOrc(replies=["hello", "world"])
        r1 = orch.process("hi")
        r2 = orch.process("again")
        self.assertEqual(r1, "hello")
        self.assertEqual(r2, "world")
        self.assertEqual(orch.smo_call_count, 0)

    def test_configure_with_none_disables(self):
        orch = MockOrc()
        orch.configure_self_model("fake")
        self.assertTrue(orch.is_self_model_configured())
        orch.configure_self_model(None)
        self.assertFalse(orch.is_self_model_configured())
        # 关闭后再 process,不应触发
        orch.process("hi")
        self.assertEqual(orch.smo_call_count, 0)


# ============================================================
# TestInjectionTriggers: 注入后自动触发
# ============================================================
class TestInjectionTriggers(unittest.TestCase):
    def test_smo_called_after_each_process(self):
        orch = MockOrc()
        foundation = _FakeFoundation()
        smo = SelfModelOrchestrator(foundation=foundation)
        orch.configure_self_model(smo)

        orch.process("a")
        orch.process("b")
        orch.process("c")

        self.assertEqual(orch.smo_call_count, 3)
        self.assertEqual(smo.run_count, 3)
        self.assertEqual(foundation.build_count, 3)

    def test_smo_receives_context(self):
        orch = MockOrc()
        smo = SelfModelOrchestrator(foundation=_FakeFoundation())
        orch.configure_self_model(smo)
        orch.process("hi")
        self.assertEqual(len(orch.smo_call_contexts), 1)
        ctx = orch.smo_call_contexts[0]
        self.assertIn("trait_states", ctx)
        self.assertIn("current_state", ctx)

    def test_smo_with_full_subsystem_chain(self):
        orch = MockOrc()
        foundation = _FakeFoundation()
        evolution = _FakeEvolution()
        reflection = _FakeReflection()
        checker = _FakeChecker()
        persistence = _FakePersistence()
        smo = SelfModelOrchestrator(
            foundation=foundation,
            evolution_engine=evolution,
            reflection_engine=reflection,
            consistency_checker=checker,
            persistence_runtime=persistence,
        )
        orch.configure_self_model(smo)
        orch.process("hi")
        self.assertEqual(orch.smo_call_count, 1)
        self.assertEqual(foundation.build_count, 1)
        self.assertEqual(evolution.evolve_count, 1)
        self.assertEqual(reflection.reflect_count, 1)
        self.assertEqual(checker.check_count, 1)
        self.assertEqual(persistence.persist_count, 1)


# ============================================================
# TestExceptionIsolation: SMO 异常不影响 reply
# ============================================================
class TestExceptionIsolation(unittest.TestCase):
    def test_smo_crash_does_not_propagate(self):
        orch = MockOrc(replies=["reply"])
        # 用一个会在 run_after_event 阶段直接 raise 的 SMO
        orch.configure_self_model(_CrashySmo())
        # 不应抛异常
        reply = orch.process("hi")
        self.assertEqual(reply, "reply")
        self.assertEqual(orch.history, ["hi"])

    def test_smo_with_failing_subsystem_still_returns(self):
        orch = MockOrc(replies=["reply"])
        smo = SelfModelOrchestrator(
            foundation=_CrashyFoundation(),
            evolution_engine=_FakeEvolution(),
            reflection_engine=_FakeReflection(),
            consistency_checker=_FakeChecker(),
            persistence_runtime=_FakePersistence(),
        )
        orch.configure_self_model(smo)
        # foundation 抛错,但 process 仍正常返回
        reply = orch.process("hi")
        self.assertEqual(reply, "reply")


# ============================================================
# TestConfigureSelfModel: configure_self_model 接口
# ============================================================
class TestConfigureSelfModel(unittest.TestCase):
    def test_configure_returns_smo(self):
        orch = MockOrc()
        smo = SelfModelOrchestrator()
        orch.configure_self_model(smo)
        self.assertIs(orch.self_model_orchestrator, smo)
        self.assertTrue(orch.is_self_model_configured())

    def test_reconfigure_replaces(self):
        orch = MockOrc()
        smo1 = SelfModelOrchestrator()
        smo2 = SelfModelOrchestrator()
        orch.configure_self_model(smo1)
        orch.configure_self_model(smo2)
        self.assertIs(orch.self_model_orchestrator, smo2)


# ============================================================
# Fakes
# ============================================================
class _FakeFoundation:
    def __init__(self) -> None:
        self.build_count = 0

    def build(self, inputs=None):
        self.build_count += 1
        return {"snapshot": "fake", "from": inputs or {}}

    def current(self):
        return None

    def snapshot(self):
        return None


class _FakeEvolution:
    def __init__(self) -> None:
        self.evolve_count = 0

    def evolve(self, snapshot, proposals=None, reflections=None, manual_changes=None):
        self.evolve_count += 1
        return _FakeEvolutionResult(snapshot)


class _FakeEvolutionResult:
    def __init__(self, new_snapshot) -> None:
        self.new_snapshot = new_snapshot


class _FakeReflection:
    def __init__(self) -> None:
        self.reflect_count = 0

    def reflect(self, old_snapshot, new_snapshot, evolution_history=None):
        self.reflect_count += 1
        return {"reflection": "fake"}


class _FakeChecker:
    def __init__(self) -> None:
        self.check_count = 0

    def check(self, snapshot, evolution_history=None):
        self.check_count += 1
        return {"valid": True}


class _FakePersistence:
    def __init__(self) -> None:
        self.persist_count = 0

    def persist_evolution(self, evolution_result):
        self.persist_count += 1
        return True


class _CrashyFoundation:
    def build(self, inputs=None):
        raise RuntimeError("foundation 故意失败")


class _CrashySmo:
    """在 run_after_event 直接抛错(模拟最坏情况)。"""

    def run_after_event(self, context):
        raise RuntimeError("SMO 直接崩溃")


if __name__ == "__main__":
    unittest.main()
