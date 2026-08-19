# -*- coding: utf-8 -*-
"""
tests/test_phase_5_0_d3_b/test_integration.py

Phase 5.0-D3-B: 端到端集成测试。
覆盖:
- IntegrationEvent → Reflection → Suggestion → SelfModel 流程
- 确定性 / 证据链完整 / 异常隔离
- 事件类型常量完整性
- SelfModel 不被直接修改
"""
import unittest
from typing import List

from src.runtime.integration.integration_event import (
    ALL_INTEGRATION_EVENT_TYPES,
    INTEGRATION_REFLECTION_COMPLETED,
    INTEGRATION_REFLECTION_DAILY_COMPLETED,
    INTEGRATION_REFLECTION_EVENT_COMPLETED,
    INTEGRATION_REFLECTION_GROWTH_COMPLETED,
    IntegrationEvent,
    make_integration_event,
)
from src.runtime.integration.integration_event_log import IntegrationEventLog
from src.runtime.reflection.adapter.reflection_adapter import ReflectionAdapter
from src.runtime.reflection.adapter.reflection_event_emitter import (
    EVENT_TYPE_REFLECTION_COMPLETED,
)
from src.runtime.reflection.daily_reflection import DailyReflection
from src.runtime.reflection.event_reflection import EventReflection
from src.runtime.reflection.growth_reflection import GrowthReflection
from src.runtime.reflection.reflection_engine import ReflectionEngine
from src.runtime.reflection.reflection_history import ReflectionHistory
from src.runtime.reflection.reflection_result import (
    ALL_REFLECTION_TYPES,
    INSIGHT_CATEGORY_PATTERN,
    REFLECTION_TYPE_DAILY,
    REFLECTION_TYPE_EVENT,
    Insight,
    ReflectionResult,
    StateChangeSuggestion,
)
from src.runtime.self_model.self_model_adapter import SelfModelAdapter
from src.runtime.self_model.self_model_manager import SelfModelManager
from src.runtime.self_model.self_state import (
    InterestState,
    SelfState,
    TraitState,
)


def _ev(et: str, ts: float, topic: str = "", trait: str = "", severity: str = "info") -> IntegrationEvent:
    p = {}
    if topic:
        p["topic"] = topic
    if trait:
        p["trait"] = trait
    return IntegrationEvent(
        event_type=et,
        source="test",
        severity=severity,
        timestamp=ts,
        payload=p,
    )


class TestEventTypeConstants(unittest.TestCase):
    def test_all_event_types_include_reflection(self):
        for et in [
            INTEGRATION_REFLECTION_COMPLETED,
            INTEGRATION_REFLECTION_DAILY_COMPLETED,
            INTEGRATION_REFLECTION_EVENT_COMPLETED,
            INTEGRATION_REFLECTION_GROWTH_COMPLETED,
        ]:
            self.assertIn(et, ALL_INTEGRATION_EVENT_TYPES)


class TestEventLogIntegration(unittest.TestCase):
    def test_log_and_engine(self):
        log = IntegrationEventLog(capacity=64)
        engine = ReflectionEngine()
        # 注入 5 个相同事件
        for i in range(5):
            log.append(_ev("user.input", 100.0 + i, topic="ai_art"))
        # 用 log 的事件跑反思
        events = log.events()
        results = engine.reflect(events)
        self.assertGreater(len(results), 0)
        # 至少 event strategy
        types = [r.reflection_type for r in results]
        self.assertIn(REFLECTION_TYPE_EVENT, types)


class TestEndToEndFlow(unittest.TestCase):
    """Event → Reflection → Suggestion → SelfModel。"""

    def test_event_to_reflection_to_self_model_via_adapter(self):
        # 1) 创建 SelfModel + Adapter
        manager = SelfModelManager()
        manager.start()
        sm_adapter = SelfModelAdapter(manager=manager)
        # 2) 创建 Reflection Adapter
        ref_adapter = ReflectionAdapter()
        # 3) 收集事件
        events = [_ev("user.input", 100.0 + i, topic="ai_art") for i in range(5)]
        # 4) Reflection 产出 Suggestion
        results = ref_adapter.reflect(events)
        self.assertGreater(len(results), 0)
        # 5) 假设有 interest suggestion
        suggestions: List[StateChangeSuggestion] = []
        for r in results:
            suggestions.extend(r.suggested_changes)
        self.assertGreater(len(suggestions), 0)
        # 6) 手动通过 SelfModelAdapter 更新(模拟下游消费)
        for s in suggestions:
            if s.target_field.startswith("interest_state."):
                topic = s.target_field[len("interest_state."):]
                # 构造 SELF_MODEL_INTEREST_REINFORCED 事件
                ev = make_integration_event(
                    event_type="self_model.interest.reinforced",
                    source="test",
                    payload={
                        "interest": topic,
                        "boost": s.delta,
                        "reason": s.reason,
                        "evidence_event_ids": s.evidence_event_ids,
                    },
                )
                sm_adapter.handle_event(ev)
        # 7) 验证 SelfModel 已更新
        state = manager.get_state()
        self.assertGreater(state.interest_count, 0)

    def test_reflection_does_not_directly_modify_self_model(self):
        """Reflection 本身不应修改 SelfModel。
        验证方式: 创建一个 SelfModelManager,无任何 trigger,
        只运行 Reflection,然后检查 SelfModel 不变。
        """
        manager = SelfModelManager()
        manager.start()
        initial_state = manager.get_state()
        ref_adapter = ReflectionAdapter()
        events = [_ev("a", 100.0 + i) for i in range(10)]
        ref_adapter.reflect(events)
        # SelfModel 应保持不变
        final_state = manager.get_state()
        self.assertEqual(final_state.interest_count, initial_state.interest_count)
        self.assertEqual(final_state.trait_count, initial_state.trait_count)
        self.assertEqual(final_state.capability_count, initial_state.capability_count)

    def test_all_evidence_preserved_through_flow(self):
        """所有 Insight / Suggestion 必含 evidence_event_ids。"""
        ref_adapter = ReflectionAdapter()
        events = [_ev("a", 100.0 + i, topic="t") for i in range(5)]
        results = ref_adapter.reflect(events)
        for r in results:
            for insight in r.insights:
                self.assertGreater(len(insight.supporting_event_ids), 0)
            for s in r.suggested_changes:
                self.assertGreater(len(s.evidence_event_ids), 0)


class TestDeterminism(unittest.TestCase):
    def test_same_events_same_results(self):
        e1 = ReflectionEngine()
        e2 = ReflectionEngine()
        events = [_ev(f"a{i}", 100.0 + i, topic="t") for i in range(10)]
        r1 = e1.reflect(events)
        r2 = e2.reflect(events)
        # 同样数量
        self.assertEqual(len(r1), len(r2))
        # 同样 confidence / insight_count
        for a, b in zip(r1, r2):
            self.assertEqual(a.confidence, b.confidence)
            self.assertEqual(a.insight_count, b.insight_count)
            self.assertEqual(a.suggestion_count, b.suggestion_count)


class TestExceptionIsolation(unittest.TestCase):
    def test_bad_event_in_list_doesnt_crash(self):
        e = ReflectionEngine()
        events = [_ev("a", 100.0), "bad event", _ev("b", 101.0), None, _ev("c", 102.0)]
        # 不抛错
        results = e.reflect(events)  # type: ignore[arg-type]
        self.assertIsInstance(results, list)

    def test_engine_continues_after_strategy_error(self):
        from src.runtime.reflection.reflection_strategy import BaseReflectionStrategy
        from src.runtime.reflection.reflection_result import ReflectionResult

        class ErrorStrategy(BaseReflectionStrategy):
            def __init__(self):
                super().__init__(name="err", reflection_type="event")

            def should_run(self, ctx):
                return ctx.event_count > 0

            def reflect(self, ctx):
                raise RuntimeError("intentional")

        e = ReflectionEngine(
            strategies=[ErrorStrategy(), DailyReflection(preference_threshold=2)],
        )
        events = [_ev("a", 100.0 + i, topic="t") for i in range(3)]
        # 不抛错
        results = e.reflect(events)
        self.assertIsInstance(results, list)


class TestArchitectureConstraints(unittest.TestCase):
    """架构约束验证: Reflection 不依赖业务模块。"""

    def test_reflection_does_not_import_business_modules(self):
        """验证: 反射模块不应 import 业务模块。"""
        import importlib
        import inspect
        # 直接 import 所有反射相关模块
        modules_to_check = [
            "src.runtime.reflection",
            "src.runtime.reflection.reflection_result",
            "src.runtime.reflection.reflection_history",
            "src.runtime.reflection.reflection_strategy",
            "src.runtime.reflection.daily_reflection",
            "src.runtime.reflection.event_reflection",
            "src.runtime.reflection.growth_reflection",
            "src.runtime.reflection.reflection_engine",
            "src.runtime.reflection.adapter.reflection_adapter",
            "src.runtime.reflection.adapter.reflection_event_emitter",
            "src.runtime.integration.tasks.reflection_lifecycle_task",
        ]
        for mod_name in ["memory", "growth", "personality", "emotion", "relationship"]:
            for path in modules_to_check:
                try:
                    mod = importlib.import_module(path)
                except Exception:
                    continue
                src = inspect.getsource(mod)
                # 检查不应 import 业务模块
                self.assertNotIn(
                    f"src.{mod_name}.", src,
                    f"{path} should not import src.{mod_name}.*",
                )
                # 也检查 from src.xxx import 形式(不光是 import src.xxx)
                self.assertNotIn(
                    f"from src.{mod_name}", src,
                    f"{path} should not import from src.{mod_name}",
                )

    def test_no_third_party_imports_in_reflection(self):
        import importlib
        import inspect
        modules_to_check = [
            "src.runtime.reflection.reflection_result",
            "src.runtime.reflection.reflection_history",
            "src.runtime.reflection.reflection_strategy",
            "src.runtime.reflection.daily_reflection",
            "src.runtime.reflection.event_reflection",
            "src.runtime.reflection.growth_reflection",
            "src.runtime.reflection.reflection_engine",
            "src.runtime.reflection.adapter.reflection_adapter",
            "src.runtime.reflection.adapter.reflection_event_emitter",
            "src.runtime.integration.tasks.reflection_lifecycle_task",
        ]
        forbidden = ["openai", "anthropic", "requests", "urllib3", "httpx",
                     "aiohttp", "pymongo", "sqlalchemy", "redis", "pydantic"]
        for path in modules_to_check:
            try:
                mod = importlib.import_module(path)
            except Exception:
                continue
            src = inspect.getsource(mod)
            for line in src.splitlines():
                stripped = line.strip()
                if not (stripped.startswith("import ") or stripped.startswith("from ")):
                    continue
                for f in forbidden:
                    self.assertNotIn(f, stripped, f"{path} imports third-party: {stripped}")


class TestHistoryIntegration(unittest.TestCase):
    def test_reflection_writes_to_history(self):
        h = ReflectionHistory(capacity=10)
        e = ReflectionEngine(history=h)
        events = [_ev("a", 100.0 + i) for i in range(3)]
        e.reflect(events)
        self.assertGreater(h.size, 0)
        # 可通过 type 检索
        self.assertGreater(h.count_by_type(REFLECTION_TYPE_EVENT), 0)


if __name__ == "__main__":
    unittest.main()
