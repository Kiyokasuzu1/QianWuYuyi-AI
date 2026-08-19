# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_a/test_e2e_100_events.py

Phase 5.0-A: 端到端 100 事件闭环集成测试。

验证完整闭环:
  输入
   ↓ Orchestrator.process()
   ↓ SelfModelOrchestrator.run_after_event()
       ↓ Build → Evolution → Reflection → Validation → Persistence
   ↓ PersistenceRuntime.save

每条事件都流经完整链路,无遗漏。
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
# Counting Subsystems: 真实数据流,带计数
# ============================================================
class CountingFoundation:
    def __init__(self) -> None:
        self.build_count = 0
        self.last_inputs: Optional[Dict[str, Any]] = None

    def build(self, inputs=None):
        self.build_count += 1
        self.last_inputs = inputs
        return {
            "snapshot_id": f"snap_{self.build_count}",
            "from": inputs or {},
        }

    def current(self):
        return None

    def snapshot(self):
        return None


class CountingEvolution:
    def __init__(self) -> None:
        self.evolve_count = 0

    def evolve(self, snapshot, proposals=None, reflections=None, manual_changes=None):
        self.evolve_count += 1
        # 在原 snapshot 上叠一个 evolution_id
        out = dict(snapshot) if isinstance(snapshot, dict) else {"value": snapshot}
        out["evolution_id"] = f"evo_{self.evolve_count}"
        return _EvolutionResult(snapshot_in=snapshot, snapshot_out=out)


class _EvolutionResult:
    def __init__(self, snapshot_in, snapshot_out) -> None:
        self.old_snapshot = snapshot_in
        self.new_snapshot = snapshot_out


class CountingReflection:
    def __init__(self) -> None:
        self.reflect_count = 0

    def reflect(self, old_snapshot, new_snapshot, evolution_history=None):
        self.reflect_count += 1
        return {
            "reflection_id": f"ref_{self.reflect_count}",
            "delta": (new_snapshot, old_snapshot),
        }


class CountingChecker:
    def __init__(self) -> None:
        self.check_count = 0

    def check(self, snapshot, evolution_history=None):
        self.check_count += 1
        return {"valid": True, "check_id": f"chk_{self.check_count}"}


class CountingPersistence:
    """模拟持久化,记录每条 evolution_result 写入。"""

    def __init__(self) -> None:
        self.persist_count = 0
        self.saved: List[Dict[str, Any]] = []

    def persist_evolution(self, evolution_result):
        self.persist_count += 1
        self.saved.append(
            {
                "persisted_id": self.persist_count,
                "snapshot_id": getattr(evolution_result, "new_snapshot", {}).get("snapshot_id"),
                "evolution_id": getattr(evolution_result, "new_snapshot", {}).get("evolution_id"),
            }
        )
        return True


# ============================================================
# CountingOrchestrator: 真实事件流的 Orchestrator 替身
# ============================================================
class CountingOrchestrator:
    def __init__(self, replies: Optional[List[str]] = None) -> None:
        self._replies = list(replies or ["ok"])
        self._idx = 0
        self.history: List[Any] = []
        self.turn = 0
        self.self_model_orchestrator: Optional[Any] = None
        # 模拟 personality 上下文
        self._personality = {"trait_states": {"warmth": 0.5}}

    def process(self, user_input: str) -> str:
        self.turn += 1
        self.history.append(user_input)
        reply = self._replies[self.turn - 1] if self.turn <= len(self._replies) else "ok"
        # 模拟 Orchestrator.process() 末尾的 SelfModel 触发
        if self.self_model_orchestrator is not None:
            self.self_model_orchestrator.run_after_event({
                "trait_states": self._personality.get("trait_states"),
                "current_state": {"turn": self.turn, "input": user_input},
                "identity_overrides": None,
            })
        return reply

    def clear_history(self) -> None:
        self.history = []


# ============================================================
# TestHundredEventsEnd2End: 100 事件端到端
# ============================================================
class TestHundredEventsEnd2End(unittest.TestCase):
    """100 事件端到端:输入 → Orchestrator → SelfModel → Persistence。"""

    EVENT_COUNT = 100

    def test_full_pipeline_runs_100_times(self):
        # 准备所有子系统
        foundation = CountingFoundation()
        evolution = CountingEvolution()
        reflection = CountingReflection()
        checker = CountingChecker()
        persistence = CountingPersistence()
        smo = SelfModelOrchestrator(
            foundation=foundation,
            evolution_engine=evolution,
            reflection_engine=reflection,
            consistency_checker=checker,
            persistence_runtime=persistence,
        )
        orch = CountingOrchestrator(replies=[f"reply_{i}" for i in range(self.EVENT_COUNT)])
        orch.self_model_orchestrator = smo

        # 100 轮事件
        for i in range(self.EVENT_COUNT):
            reply = orch.process(f"event_{i}")
            self.assertEqual(reply, f"reply_{i}")

        # 全链路计数
        self.assertEqual(orch.turn, self.EVENT_COUNT)
        self.assertEqual(len(orch.history), self.EVENT_COUNT)
        self.assertEqual(smo.run_count, self.EVENT_COUNT)
        self.assertEqual(foundation.build_count, self.EVENT_COUNT)
        self.assertEqual(evolution.evolve_count, self.EVENT_COUNT)
        self.assertEqual(reflection.reflect_count, self.EVENT_COUNT)
        self.assertEqual(checker.check_count, self.EVENT_COUNT)
        self.assertEqual(persistence.persist_count, self.EVENT_COUNT)

        # 持久化记录数 == 100
        self.assertEqual(len(persistence.saved), self.EVENT_COUNT)
        for idx, item in enumerate(persistence.saved, 1):
            self.assertEqual(item["persisted_id"], idx)
            self.assertEqual(item["snapshot_id"], f"snap_{idx}")
            self.assertEqual(item["evolution_id"], f"evo_{idx}")

    def test_full_pipeline_via_long_loop(self):
        """通过 LongLoop 跑 100 事件,验证完整 chat 闭环。"""
        foundation = CountingFoundation()
        evolution = CountingEvolution()
        reflection = CountingReflection()
        checker = CountingChecker()
        persistence = CountingPersistence()
        smo = SelfModelOrchestrator(
            foundation=foundation,
            evolution_engine=evolution,
            reflection_engine=reflection,
            consistency_checker=checker,
            persistence_runtime=persistence,
        )
        orch = CountingOrchestrator(replies=["ack"] * (self.EVENT_COUNT + 1))
        orch.self_model_orchestrator = smo

        # LongLoop 输入流: "msg_0" ... "msg_99" + "exit"
        events = [f"msg_{i}" for i in range(self.EVENT_COUNT)] + ["exit"]
        idx = {"i": 0}

        def input_provider():
            val = events[idx["i"]]
            idx["i"] += 1
            return val

        loop = LongLoop(
            input_provider=input_provider,
            orchestrator=orch,
            on_reply=lambda u, r: None,
        )
        loop.run()

        # 验证状态
        self.assertEqual(loop.state.value, "STOPPED")
        self.assertEqual(loop.turn_count, self.EVENT_COUNT)
        # 全链路触发
        self.assertEqual(smo.run_count, self.EVENT_COUNT)
        self.assertEqual(foundation.build_count, self.EVENT_COUNT)
        self.assertEqual(evolution.evolve_count, self.EVENT_COUNT)
        self.assertEqual(reflection.reflect_count, self.EVENT_COUNT)
        self.assertEqual(checker.check_count, self.EVENT_COUNT)
        self.assertEqual(persistence.persist_count, self.EVENT_COUNT)

    def test_every_event_triggers_full_chain(self):
        """每一事件都触发完整 5 阶段,中间无遗漏。"""
        foundation = CountingFoundation()
        evolution = CountingEvolution()
        reflection = CountingReflection()
        checker = CountingChecker()
        persistence = CountingPersistence()
        smo = SelfModelOrchestrator(
            foundation=foundation,
            evolution_engine=evolution,
            reflection_engine=reflection,
            consistency_checker=checker,
            persistence_runtime=persistence,
        )
        orch = CountingOrchestrator(replies=["r"] * self.EVENT_COUNT)
        orch.self_model_orchestrator = smo

        for i in range(self.EVENT_COUNT):
            orch.process(f"e_{i}")
            # 立即检查:每轮后所有子系统都应恰好执行 i+1 次
            self.assertEqual(smo.run_count, i + 1)
            self.assertEqual(foundation.build_count, i + 1)
            self.assertEqual(evolution.evolve_count, i + 1)
            self.assertEqual(reflection.reflect_count, i + 1)
            self.assertEqual(checker.check_count, i + 1)
            self.assertEqual(persistence.persist_count, i + 1)


# ============================================================
# TestMixedFailureEvents: 部分事件失败,后续仍正常
# ============================================================
class TestMixedFailureEvents(unittest.TestCase):
    """100 事件中部分事件触发子系统失败,验证不影响主流程。"""

    def test_partial_subsystem_failures_still_progress(self):
        foundation = CountingFoundation()
        evolution = CountingEvolution()
        reflection = CountingReflection()
        checker = CountingChecker()
        persistence = CountingPersistence()
        # 故意让 reflection 在奇数轮抛错
        original_reflect = reflection.reflect

        def flaky_reflect(old_snapshot, new_snapshot, evolution_history=None):
            original_reflect(old_snapshot, new_snapshot, evolution_history)
            if (n[0] % 2) == 1:
                n[0] += 1
                raise RuntimeError("reflection flaky")
            n[0] += 1
            return {"reflection_id": f"ref_{n[0]}"}

        n = [1]
        reflection.reflect = flaky_reflect

        smo = SelfModelOrchestrator(
            foundation=foundation,
            evolution_engine=evolution,
            reflection_engine=reflection,
            consistency_checker=checker,
            persistence_runtime=persistence,
        )
        orch = CountingOrchestrator(replies=["r"] * 10)
        orch.self_model_orchestrator = smo

        # 10 轮
        for i in range(10):
            reply = orch.process(f"e_{i}")
            self.assertEqual(reply, "r")  # 全部 reply 正常

        # 100% 的事件 process 成功
        self.assertEqual(orch.turn, 10)
        # Build / Evolution / Validation / Persistence 全部 10 次
        self.assertEqual(foundation.build_count, 10)
        self.assertEqual(evolution.evolve_count, 10)
        self.assertEqual(checker.check_count, 10)
        self.assertEqual(persistence.persist_count, 10)
        # Reflection 失败 5 次(奇数轮),成功 5 次
        # 注: flaky_reflect 在 original_reflect 之后才抛错,所以 reflect_count 实际是 10
        # 关键是 5 次错误被记录到 smo.errors_total
        self.assertEqual(smo.errors_total, 5)


if __name__ == "__main__":
    unittest.main()
