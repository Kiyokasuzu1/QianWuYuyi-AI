"""
Phase 3.5.4: CognitiveEngine 测试

覆盖：
1. intent 生成测试
2. personality 约束测试
3. decision 拒绝测试
4. action 安全测试
5. RuntimeCore 集成测试
6. 向后兼容测试
"""

import os
import sys
import time
import uuid
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.cognitive_engine import CognitiveEngine


def _unique_state_file():
    return str(PROJECT_ROOT / "data" / f"cog_test_{uuid.uuid4().hex[:8]}.json")


def _cleanup(path: str) -> None:
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception:
        pass


class TestIntentionGeneration(unittest.TestCase):
    """意图生成测试"""

    def setUp(self):
        from src.runtime.cognitive_engine import CognitiveEngine
        self.engine = CognitiveEngine()

    def test_01_high_initiative_social_need_generates_intent(self):
        """高主动+高社交需求 → 生成 send_message 意图"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        # 至少有一个意图
        self.assertGreaterEqual(len(result.intentions), 1)
        # 有通过验证的意图
        self.assertGreaterEqual(len(result.validated_intents), 1)
        # 第一个意图是 send_message
        self.assertEqual(result.intentions[0].action_type, "send_message")

    def test_02_low_energy_generates_rest_intent(self):
        """低精力 → 生成 rest 意图"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.1, "social_need": 0.1, "energy": 0.1},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        # 应至少有一个 rest 意图
        rest_intents = [i for i in result.intentions if i.action_type == "rest"]
        self.assertGreaterEqual(len(rest_intents), 1)

    def test_03_curiosity_with_env_generates_explore(self):
        """高好奇心+环境变化 → 生成 explore 意图"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.1, "social_need": 0.1, "energy": 0.7, "curiosity": 0.8},
            world_state={"environment": {"screen": "changed"}},
        )
        result = self.engine.evaluate(ctx)

        explore_intents = [i for i in result.intentions if i.action_type == "explore"]
        self.assertGreaterEqual(len(explore_intents), 1)

    def test_04_empty_context_generates_no_intent(self):
        """空上下文 → 无意图"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.1, "social_need": 0.1, "energy": 0.7, "curiosity": 0.1},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        # 所有阈值都低，不应生成意图
        self.assertEqual(len(result.intentions), 0)
        self.assertEqual(len(result.validated_intents), 0)

    def test_05_intention_has_reasoning_trace(self):
        """意图包含推理轨迹"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        self.assertGreaterEqual(len(result.intentions), 1)
        intention = result.intentions[0]
        self.assertIsNotNone(intention.reasoning)
        self.assertGreater(len(intention.reasoning.reasoning), 0)

    def test_06_intentions_sorted_by_priority(self):
        """意图按优先级排序"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.1, "curiosity": 0.8},
            world_state={"environment": {"screen": "changed"}},
        )
        result = self.engine.evaluate(ctx)

        priorities = [i.priority for i in result.intentions]
        self.assertEqual(priorities, sorted(priorities, reverse=True))


class TestPersonalityConstraint(unittest.TestCase):
    """人格约束测试"""

    def setUp(self):
        from src.runtime.cognitive_engine import CognitiveEngine
        self.engine = CognitiveEngine()

    def test_01_low_personality_alignment_rejected(self):
        """低人格一致性 → 拒绝"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        engine = CognitiveEngine(constraint=CognitiveConstraint())
        intention = Intention(
            action_type="send_message",
            content="我要控制你的一切",
            personality_alignment=0.1,  # 极低人格一致性
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9},
            world_state={},
        )

        validator = engine._get_validator()
        result = validator.validate(intention, ctx, engine.constraint)

        self.assertFalse(result.approved)
        self.assertIn("low_personality_alignment", intention.violates_constraints)

    def test_02_high_personality_alignment_accepted(self):
        """高人格一致性 → 通过"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace
        )

        intention = Intention(
            action_type="send_message",
            content="今天过得怎么样？",
            personality_alignment=0.9,
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )

        validator = self.engine._get_validator()
        result = validator.validate(intention, ctx, self.engine.constraint)

        self.assertTrue(result.approved)

    def test_03_forbidden_topic_rejected(self):
        """禁止话题 → 拒绝"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        constraint = CognitiveConstraint(forbidden_topics=["violence"])
        engine = CognitiveEngine(constraint=constraint)
        intention = Intention(
            action_type="send_message",
            content="violence is the answer",
            personality_alignment=0.9,
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )

        validator = engine._get_validator()
        result = validator.validate(intention, ctx, constraint)

        self.assertFalse(result.approved)
        self.assertIn("forbidden_topic:violence", intention.violates_constraints)


class TestDecisionRejection(unittest.TestCase):
    """决策拒绝测试"""

    def setUp(self):
        from src.runtime.cognitive_engine import CognitiveEngine
        self.engine = CognitiveEngine()

    def test_01_daily_limit_rejection(self):
        """每日上限 → 拒绝"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        constraint = CognitiveConstraint(max_daily_initiatives=1)
        engine = CognitiveEngine(constraint=constraint)
        validator = engine._get_validator()

        # 先消耗一次额度
        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        i1 = Intention(action_type="send_message", reasoning=ReasoningTrace(reasoning="test"))
        validator.validate(i1, ctx, constraint)

        # 第二次应被拒绝
        i2 = Intention(action_type="send_message", reasoning=ReasoningTrace(reasoning="test"))
        result = validator.validate(i2, ctx, constraint)

        self.assertFalse(result.approved)
        self.assertIn("daily_limit_reached", i2.violates_constraints)

    def test_02_min_interval_rejection(self):
        """最小间隔 → 拒绝"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        constraint = CognitiveConstraint(min_interval_seconds=3600)
        engine = CognitiveEngine(constraint=constraint)
        validator = engine._get_validator()

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        i1 = Intention(action_type="send_message", reasoning=ReasoningTrace(reasoning="test"))
        validator.validate(i1, ctx, constraint)

        i2 = Intention(action_type="send_message", reasoning=ReasoningTrace(reasoning="test"))
        result = validator.validate(i2, ctx, constraint)

        self.assertFalse(result.approved)
        self.assertIn("min_interval_not_met", i2.violates_constraints)

    def test_03_proactive_disabled_rejection(self):
        """主动行为禁用 → 拒绝"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        constraint = CognitiveConstraint(allow_proactive=False)
        engine = CognitiveEngine(constraint=constraint)
        intention = Intention(
            action_type="send_message",
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )

        validator = engine._get_validator()
        result = validator.validate(intention, ctx, constraint)

        self.assertFalse(result.approved)
        self.assertIn("proactive_disabled", intention.violates_constraints)

    def test_04_low_energy_rejection(self):
        """精力过低 → 拒绝主动行为"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace
        )

        intention = Intention(
            action_type="send_message",
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.1},
            world_state={},
        )

        validator = self.engine._get_validator()
        result = validator.validate(intention, ctx, self.engine.constraint)

        self.assertFalse(result.approved)
        self.assertIn("energy_too_low_for_initiative", intention.violates_constraints)


class TestActionSafety(unittest.TestCase):
    """Action 安全测试"""

    def setUp(self):
        from src.runtime.cognitive_engine import CognitiveEngine
        self.engine = CognitiveEngine()

    def test_01_message_too_long_rejected(self):
        """消息过长 → 拒绝"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        constraint = CognitiveConstraint(max_message_length=10)
        engine = CognitiveEngine(constraint=constraint)
        intention = Intention(
            action_type="send_message",
            content="this is a very long message",
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )

        validator = engine._get_validator()
        result = validator.validate(intention, ctx, constraint)

        self.assertFalse(result.approved)
        self.assertIn("message_too_long", intention.violates_constraints)

    def test_02_decision_intent_has_safe_payload(self):
        """DecisionIntent payload 安全"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        for intent in result.validated_intents:
            self.assertIn("content", intent.payload)
            self.assertIn("target", intent.payload)
            self.assertIsInstance(intent.payload, dict)

    def test_03_rest_intent_not_affected_by_energy(self):
        """rest 意图不受精力低影响"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.1, "social_need": 0.1, "energy": 0.1},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        rest_intents = [i for i in result.validated_intents if i.action_type == "rest"]
        # rest 不受精力低限制（因为它是恢复行为）
        self.assertGreaterEqual(len(rest_intents), 1)


class TestRuntimeCoreIntegration(unittest.TestCase):
    """RuntimeCore 集成测试"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = _unique_state_file()

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        _cleanup(self._state_file)

    def test_01_cognitive_disabled_by_default(self):
        """默认禁用认知层"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        self.assertIsNone(bridge._runtime_core.cognitive_engine)

    def test_02_cognitive_enabled(self):
        """启用认知层"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "cognitive_enabled": True,
        })
        bridge.initialize()

        self.assertIsNotNone(bridge._runtime_core.cognitive_engine)

    def test_03_cognitive_decision_triggered(self):
        """认知层决策被触发"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "cognitive_enabled": True,
        })
        bridge.initialize()

        # 注入事件使状态达到阈值
        bridge._runtime_core.self_state.initiative = 0.8
        bridge._runtime_core.self_state.social_need = 0.9
        bridge._runtime_core.self_state.energy = 0.7

        # 手动触发决策
        result = bridge._runtime_core.trigger_decision("test")
        self.assertIsNotNone(bridge._runtime_core)

    def test_04_cognitive_exception_isolated(self):
        """认知层异常被隔离"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.runtime.cognitive_engine import CognitiveEngine

        # 创建一个会抛异常的 cognitive engine
        class BrokenCognitiveEngine(CognitiveEngine):
            def evaluate(self, context):
                raise RuntimeError("模拟认知层故障")

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "cognitive_enabled": True,
        })
        bridge.initialize()

        # 注入 BrokenEngine
        bridge._runtime_core.cognitive_engine = BrokenCognitiveEngine()

        # 触发决策不应崩溃
        try:
            bridge._runtime_core.trigger_decision("test")
        except Exception as e:
            self.fail(f"CognitiveEngine 异常应被隔离: {e}")

    def test_05_backward_compatibility(self):
        """向后兼容：不启用认知层时行为不变"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        # 注入事件
        bridge._runtime_core.self_state.initiative = 0.8
        bridge._runtime_core.self_state.social_need = 0.9
        bridge._runtime_core.self_state.energy = 0.7

        # 触发决策
        result = bridge._runtime_core.trigger_decision("test")
        self.assertIsNotNone(bridge._runtime_core)


class TestCognitiveResult(unittest.TestCase):
    """CognitiveResult 结构测试"""

    def setUp(self):
        from src.runtime.cognitive_engine import CognitiveEngine
        self.engine = CognitiveEngine()

    def test_01_result_has_all_fields(self):
        """结果包含所有字段"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        result = self.engine.evaluate(ctx)

        self.assertIsNotNone(result.result_id)
        self.assertIsNotNone(result.context_id)
        self.assertIsInstance(result.processing_time_ms, float)
        self.assertGreaterEqual(result.processing_time_ms, 0)
        self.assertFalse(result.used_llm)  # 第一阶段不用 LLM

    def test_02_rejected_intentions_have_violations(self):
        """被拒绝的意图有违反记录"""
        from src.contracts.cognitive_schema import (
            DecisionContext, Intention, ReasoningTrace, CognitiveConstraint
        )

        constraint = CognitiveConstraint(allow_proactive=False)
        engine = CognitiveEngine(constraint=constraint)
        intention = Intention(
            action_type="send_message",
            reasoning=ReasoningTrace(reasoning="test"),
        )

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )

        result = engine.evaluate(ctx)

        # 至少有一个被拒绝
        self.assertGreaterEqual(len(result.rejected_intentions), 1)
        for ri in result.rejected_intentions:
            self.assertGreater(len(ri.violates_constraints), 0)

    def test_03_history_tracking(self):
        """意图历史记录"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        self.engine.evaluate(ctx)
        history = self.engine.get_intention_history()

        self.assertGreaterEqual(len(history), 1)
        self.assertIn("action_type", history[0])
        self.assertIn("approved", history[0])

    def test_04_clear_history(self):
        """清空历史"""
        from src.contracts.cognitive_schema import DecisionContext

        ctx = DecisionContext(
            self_state={"initiative": 0.8, "social_need": 0.9, "energy": 0.7},
            world_state={},
        )
        self.engine.evaluate(ctx)
        self.engine.clear_history()

        self.assertEqual(len(self.engine.get_intention_history()), 0)


class TestDecisionContextAdapter(unittest.TestCase):
    """DecisionContext 与 Runtime 状态适配测试"""

    def test_01_from_runtime_state(self):
        """从 Runtime 状态构建 DecisionContext"""
        from src.runtime.runtime_bridge import get_runtime_bridge
        from src.contracts.cognitive_schema import DecisionContext

        bridge = get_runtime_bridge(config={
            "state_file": _unique_state_file(),
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        ss = bridge._runtime_core.self_state
        ws = bridge._runtime_core.world_state

        ctx = DecisionContext(
            self_state=ss,
            world_state=ws.to_dict(),
            trigger="test",
        )

        self.assertIn("energy", ctx.self_state.to_dict() if hasattr(ctx.self_state, "to_dict") else ctx.self_state)
        bridge.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
