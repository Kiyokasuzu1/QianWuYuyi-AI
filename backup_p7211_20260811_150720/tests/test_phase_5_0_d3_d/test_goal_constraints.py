# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_d/test_goal_constraints.py

Phase 5.0-D3-D: 约束与架构验收测试。

覆盖:
1. 无业务模块依赖 (memory / growth / personality / emotion / relationship)
2. 无第三方依赖 (LLM / DB / Network / Browser)
3. 所有 Goal 必须有 evidence
4. Goal 不执行任何动作 (无 execute / run 方法)
5. GoalPlanner 只生成计划,不执行
6. Clock 注入
7. enable_goal = False 短路
8. 所有新文件路径仅在 src/runtime/goal/ 与 src/runtime/integration/tasks/goal_lifecycle_task.py
9. IntegrationEvent 事件类型已扩展
"""
import ast
import os
import unittest
from typing import List, Tuple

from src.runtime.goal.adapter.goal_adapter import GoalAdapter
from src.runtime.goal.desire import build_desire
from src.runtime.goal.goal_generator import build_default_goal_generator
from src.runtime.goal.goal_manager import (
    DEFAULT_MAX_GOALS,
    GoalManager,
    build_default_goal_manager,
)
from src.runtime.goal.goal_planner import (
    DEFAULT_MAX_MILESTONES,
    GoalPlanner,
    build_default_goal_planner,
)
from src.runtime.goal.goal_state import (
    GOAL_STATE_SCHEMA_VERSION,
    GoalState,
    build_candidate_goal,
)
from src.runtime.integration.integration_event import (
    INTEGRATION_DESIRE_CREATED,
    INTEGRATION_GOAL_CREATED,
    INTEGRATION_GOAL_PLAN_CREATED,
    INTEGRATION_GOAL_UPDATED,
)
from src.runtime.lifecycle.internal.clock import Clock, SystemClock


# ============================================================
# 工具
# ============================================================
def _gather_imports(py_path: str) -> List[Tuple[str, int]]:
    """收集 .py 文件中的所有 import 信息 (name, lineno)。"""
    out: List[Tuple[str, int]] = []
    try:
        with open(py_path, "r", encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src, filename=py_path)
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append((node.module, node.lineno))
    return out


# ============================================================
# 测试
# ============================================================
class TestGoalModuleNoBusinessImports(unittest.TestCase):
    """约束:Goal 模块不得直接 import 业务模块。"""

    BASE = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "src", "runtime", "goal"
        )
    )
    FORBIDDEN = (
        "src.memory",
        "src.growth",
        "src.personality",
        "src.emotion",
        "src.relationship",
    )

    def _walk(self):
        out = []
        for root, _, files in os.walk(self.BASE):
            for f in files:
                if f.endswith(".py"):
                    out.append(os.path.join(root, f))
        return out

    def test_no_forbidden_imports(self):
        files = self._walk()
        self.assertGreater(len(files), 0)
        bad = []
        for fp in files:
            for name, lineno in _gather_imports(fp):
                for f in self.FORBIDDEN:
                    if name == f or name.startswith(f + "."):
                        bad.append(f"{fp}:{lineno} -> {name}")
        self.assertEqual(bad, [], f"发现禁止 import: {bad}")

    def test_only_internal_modules(self):
        """Goal 模块只能依赖 src.runtime.* 内部模块 + 标准库。"""
        files = self._walk()
        bad = []
        for fp in files:
            for name, lineno in _gather_imports(fp):
                if name.startswith("src.") and not name.startswith("src.runtime."):
                    bad.append(f"{fp}:{lineno} -> {name}")
        self.assertEqual(bad, [], f"发现外部 src 模块依赖: {bad}")


class TestGoalModuleNoThirdParty(unittest.TestCase):
    """约束:Goal 模块不得引入第三方依赖 (LLM/DB/Network/Browser/ML)。"""

    BASE = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__), "..", "..", "src", "runtime", "goal"
        )
    )
    FORBIDDEN = (
        "requests",
        "urllib",
        "urllib3",
        "httpx",
        "aiohttp",
        "sqlalchemy",
        "pymysql",
        "psycopg",
        "openai",
        "anthropic",
        "langchain",
        "transformers",
        "torch",
        "tensorflow",
        "sklearn",
        "numpy",
        "pandas",
        "selenium",
        "playwright",
    )

    def test_no_third_party(self):
        bad = []
        for root, _, files in os.walk(self.BASE):
            for f in files:
                if not f.endswith(".py"):
                    continue
                fp = os.path.join(root, f)
                for name, lineno in _gather_imports(fp):
                    top = name.split(".")[0]
                    if top in self.FORBIDDEN:
                        bad.append(f"{fp}:{lineno} -> {name}")
        self.assertEqual(bad, [], f"发现第三方依赖: {bad}")


class TestGoalFilePathConstraint(unittest.TestCase):
    """约束:所有新增代码应限制在以下路径:
    - src/runtime/goal/**
    - src/runtime/integration/tasks/goal_lifecycle_task.py
    - (可能) 必要 integration_event.py 扩展
    """

    def test_goal_lifecycle_task_path(self):
        p = "src/runtime/integration/tasks/goal_lifecycle_task.py"
        self.assertTrue(
            os.path.exists(
                os.path.normpath(
                    os.path.join(
                        os.path.dirname(__file__), "..", "..", p.replace("/", os.sep)
                    )
                )
            ),
            f"必须存在: {p}",
        )


class TestAllGoalsHaveEvidence(unittest.TestCase):
    """约束:任何通过 GoalManager.create_goal 注册的 Goal 必须有 evidence。"""

    def test_create_rejects_no_evidence(self):
        m = GoalManager()
        bad = GoalState(title="x")
        m.create_goal(bad)
        self.assertEqual(m.goal_count, 0)

    def test_create_rejects_invalid(self):
        m = GoalManager()
        bad = GoalState(title="")  # 无标题也无 evidence
        m.create_goal(bad)
        self.assertEqual(m.goal_count, 0)

    def test_create_accepts_with_event_evidence(self):
        m = GoalManager()
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        m.create_goal(g)
        self.assertEqual(m.goal_count, 1)
        self.assertTrue(m.get_goal(g.goal_id).has_evidence())

    def test_create_accepts_with_desire_evidence(self):
        m = GoalManager()
        g = build_candidate_goal(title="x", source_desire_ids=["d1"])
        m.create_goal(g)
        self.assertEqual(m.goal_count, 1)
        self.assertTrue(m.get_goal(g.goal_id).has_evidence())

    def test_all_generated_goals_have_evidence(self):
        m = GoalManager()
        g = build_default_goal_generator()
        desires = [
            build_desire(topic=f"t{i}", strength=0.9, confidence=0.9, supporting_signal_ids=[f"e{i}"])
            for i in range(5)
        ]
        out = g.generate(desires=desires)
        for cand in out.candidates:
            m.create_goal(cand, plan=False)
        for goal in m.list_goals():
            self.assertTrue(goal.has_evidence(), f"goal {goal.goal_id} missing evidence")


class TestGoalIsNotExecutable(unittest.TestCase):
    """约束:Goal / GoalPlan 不可执行(没有 execute / run 方法)。"""

    def test_goal_state_no_execute(self):
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        self.assertFalse(hasattr(g, "execute"))
        self.assertFalse(hasattr(g, "run"))

    def test_goal_plan_no_execute(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        self.assertFalse(hasattr(plan, "execute"))
        self.assertFalse(hasattr(plan, "run"))

    def test_goal_planner_does_not_run(self):
        p = GoalPlanner()
        # 验证没有"执行"语义的方法
        self.assertFalse(hasattr(p, "execute"))
        self.assertFalse(hasattr(p, "run_action"))


class TestGoalPlannerOnlyGenerates(unittest.TestCase):
    """约束:GoalPlanner 只生成 Plan,不执行任何动作。"""

    def test_plan_only_returns_data(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        # 必有 goal_id
        self.assertEqual(plan.goal_id, g.goal_id)
        # 必有 steps(模板生成)
        self.assertGreater(len(plan.steps), 0)
        # 必无可执行方法
        self.assertFalse(hasattr(plan, "execute"))

    def test_plan_progress_default_zero(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        self.assertEqual(plan.progress, 0.0)

    def test_plan_max_milestones_respected(self):
        p = GoalPlanner(max_milestones=3)
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        self.assertLessEqual(len(plan.steps), 3)


class TestClockInjection(unittest.TestCase):
    """约束:Goal 子系统应当支持 Clock 注入。"""

    def test_goal_manager_clock_injectable(self):
        """Clock 注入后,GoalManager._safe_now() 应返回 Clock 提供的时间。"""
        class _FixedClock(Clock):
            def now(self) -> float:
                return 1234.5

        m = GoalManager(clock=_FixedClock())
        # 验证 _safe_now 返回 Clock 提供的时间
        self.assertEqual(m._safe_now(), 1234.5)
        # 验证使用 Clock 后, history 中的 change record 的 timestamp 也来自 Clock
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        m.create_goal(g, plan=False)
        hist = m.history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].timestamp, 1234.5)

    def test_goal_generator_clock_injectable(self):
        class _FixedClock(Clock):
            def now(self) -> float:
                return 999.0

        gen = build_default_goal_generator()
        gen._clock = _FixedClock()
        # 通过 _safe_now 间接使用
        self.assertEqual(gen._safe_now(), 999.0)

    def test_goal_planner_clock_injectable(self):
        class _FixedClock(Clock):
            def now(self) -> float:
                return 50.0

        p = GoalPlanner(clock=_FixedClock())
        self.assertEqual(p._safe_now(), 50.0)

    def test_default_clock_is_system(self):
        m = build_default_goal_manager()
        self.assertIsInstance(m._clock, SystemClock)


class TestEnableGoalFalse(unittest.TestCase):
    """约束:enable_goal=False 时应短路,不入业务路径。"""

    def test_manager_disabled_rejects_create(self):
        m = GoalManager(enabled=False)
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        m.create_goal(g, plan=False)
        self.assertEqual(m.goal_count, 0)

    def test_adapter_disabled_evaluate_false(self):
        a = GoalAdapter(enabled=False)
        self.assertFalse(a.evaluate())

    def test_adapter_set_enabled_toggle(self):
        a = GoalAdapter()
        a.set_enabled(False)
        self.assertFalse(a.enabled)
        a.set_enabled(True)
        self.assertTrue(a.enabled)

    def test_generator_disabled_skips(self):
        g = build_default_goal_generator()
        g.set_enabled(False)
        desires = [build_desire(topic="t", strength=0.9, supporting_signal_ids=["s1"])]
        out = g.generate(desires=desires)
        self.assertEqual(len(out.candidates), 0)
        self.assertEqual(out.skipped_reason.get("global"), "disabled")


class TestIntegrationEventTypes(unittest.TestCase):
    """约束:IntegrationEvent 必须包含目标相关事件。"""

    def test_desire_event_constant(self):
        self.assertIsInstance(INTEGRATION_DESIRE_CREATED, str)
        self.assertGreater(len(INTEGRATION_DESIRE_CREATED), 0)

    def test_goal_created_event_constant(self):
        self.assertIsInstance(INTEGRATION_GOAL_CREATED, str)
        self.assertGreater(len(INTEGRATION_GOAL_CREATED), 0)

    def test_goal_updated_event_constant(self):
        self.assertIsInstance(INTEGRATION_GOAL_UPDATED, str)
        self.assertGreater(len(INTEGRATION_GOAL_UPDATED), 0)

    def test_goal_plan_event_constant(self):
        self.assertIsInstance(INTEGRATION_GOAL_PLAN_CREATED, str)
        self.assertGreater(len(INTEGRATION_GOAL_PLAN_CREATED), 0)


class TestSchemaVersion(unittest.TestCase):
    """约束:所有 schema 应当有版本号。"""

    def test_goal_state_schema(self):
        g = GoalState(title="x", source_event_ids=["e1"])
        self.assertEqual(g.version, GOAL_STATE_SCHEMA_VERSION)
        self.assertIsInstance(g.version, str)
        self.assertGreater(len(g.version), 0)


class TestGoalListGoals(unittest.TestCase):
    """测试 list_goals 各种过滤。"""

    def _make(self, m, t, eid):
        g = build_candidate_goal(title=f"t{t}", goal_type=t, source_event_ids=[eid])
        m.create_goal(g, plan=False)
        return g

    def test_list_by_type(self):
        from src.runtime.goal.goal_state import GOAL_TYPE_CREATIVE, GOAL_TYPE_LEARNING

        m = GoalManager()
        self._make(m, GOAL_TYPE_LEARNING, "e1")
        self._make(m, GOAL_TYPE_LEARNING, "e2")
        self._make(m, GOAL_TYPE_CREATIVE, "e3")
        self.assertEqual(len(m.list_goals(goal_type=GOAL_TYPE_LEARNING)), 2)
        self.assertEqual(len(m.list_goals(goal_type=GOAL_TYPE_CREATIVE)), 1)

    def test_list_by_status(self):
        m = GoalManager()
        g1 = self._make(m, "personal_growth", "e1")
        g2 = self._make(m, "personal_growth", "e2")
        m.activate_goal(g1.goal_id)
        self.assertEqual(len(m.list_goals(status="candidate")), 1)
        self.assertEqual(len(m.list_goals(status="active")), 1)

    def test_list_invalid_status(self):
        m = GoalManager()
        self.assertEqual(len(m.list_goals(status="bogus")), 0)


class TestGoalPlanMilestones(unittest.TestCase):
    """测试 GoalPlan 的 Milestones 行为。"""

    def test_learning_template(self):
        p = GoalPlanner()
        from src.runtime.goal.goal_state import GOAL_TYPE_LEARNING

        g = build_candidate_goal(
            title="learn X",
            goal_type=GOAL_TYPE_LEARNING,
            source_event_ids=["e1"],
        )
        plan = p.plan(g, now=10.0)
        self.assertGreater(len(plan.steps), 0)
        # 验证 progress 默认 0
        self.assertEqual(plan.progress, 0.0)

    def test_creative_template(self):
        p = GoalPlanner()
        from src.runtime.goal.goal_state import GOAL_TYPE_CREATIVE

        g = build_candidate_goal(
            title="create X",
            goal_type=GOAL_TYPE_CREATIVE,
            source_event_ids=["e1"],
        )
        plan = p.plan(g, now=10.0)
        self.assertGreater(len(plan.steps), 0)

    def test_recompute_progress(self):
        p = GoalPlanner()
        g = build_candidate_goal(title="x", source_event_ids=["e1"])
        plan = p.plan(g, now=10.0)
        if plan.steps:
            plan.steps[0].mark_completed(now=11.0)
        plan.recompute_progress()
        self.assertGreater(plan.progress, 0.0)


if __name__ == "__main__":
    unittest.main()
