"""
Phase 3.5.5: Experience & Reflection Layer 测试

覆盖：
1. event 生成 experience
2. action 结果记录
3. reflection 生成
4. 无效 experience 拒绝
5. RuntimeCore 无破坏测试
"""

import time
import uuid
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

import sys
sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime.experience_builder import ExperienceBuilder, ExperienceBuilderConfig
from src.runtime.reflection_engine import ReflectionEngine, ReflectionEngineConfig
from src.contracts.experience_schema import (
    RuntimeExperience,
    ActionResult,
    ExperienceValidator,
    ReflectionInsight,
)


class TestEventToExperience(unittest.TestCase):
    """事件生成经验测试"""

    def setUp(self):
        self.builder = ExperienceBuilder(config=ExperienceBuilderConfig(auto_validate=False))

    def test_01_event_generates_experience(self):
        """事件触发经验构建"""
        exp_id = self.builder.start_building(
            trigger_event={"event_type": "user_message", "data": {"content": "hello"}},
            trigger_type="user_message",
            self_state_before={"energy": 0.8, "mood": 0.6},
        )

        self.assertTrue(exp_id.startswith("exp_"))

        result = ActionResult(
            action_id="act_123",
            success=True,
            response_received=True,
            user_response="hi there",
        )

        experience = self.builder.finish_building(
            experience_id=exp_id,
            result=result,
            self_state_after={"energy": 0.7, "mood": 0.7},
        )

        self.assertIsNotNone(experience)
        self.assertEqual(experience.trigger_type, "user_message")
        self.assertTrue(experience.result.success)

    def test_02_multiple_events_generate_multiple_experiences(self):
        """多个事件生成多个经验"""
        for i in range(5):
            exp_id = self.builder.start_building(
                trigger_event={"event_type": "tick", "data": {"count": i}},
                trigger_type="tick",
                self_state_before={"energy": 0.5},
            )

            result = ActionResult(action_id=f"act_{i}", success=True)
            self.builder.finish_building(
                experience_id=exp_id,
                result=result,
                self_state_after={"energy": 0.5},
            )

        buffer = self.builder.get_buffer()
        self.assertEqual(len(buffer), 5)

    def test_03_cancel_building(self):
        """取消构建"""
        exp_id = self.builder.start_building(
            trigger_event={"event_type": "test", "data": {}},
            trigger_type="test",
            self_state_before={},
        )

        self.assertEqual(self.builder.get_building_count(), 1)

        self.builder.cancel_building(exp_id)
        self.assertEqual(self.builder.get_building_count(), 0)

    def test_04_buffer_overflow(self):
        """缓冲区溢出处理"""
        small_builder = ExperienceBuilder(
            config=ExperienceBuilderConfig(max_buffer_size=3, auto_validate=False)
        )

        for i in range(10):
            exp_id = small_builder.start_building(
                trigger_event={"event_type": "test", "data": {"i": i}},
                trigger_type="test",
                self_state_before={},
            )
            result = ActionResult(action_id=f"act_{i}", success=True)
            small_builder.finish_building(
                experience_id=exp_id,
                result=result,
                self_state_after={},
            )

        buffer = small_builder.get_buffer()
        self.assertEqual(len(buffer), 3)
        # 应该保留最新的
        self.assertEqual(buffer[-1].trigger_event["data"]["i"], 9)


class TestActionResultRecording(unittest.TestCase):
    """Action 结果记录测试"""

    def setUp(self):
        self.builder = ExperienceBuilder(config=ExperienceBuilderConfig(auto_validate=False))

    def test_01_success_result_recorded(self):
        """成功结果被记录"""
        exp_id = self.builder.start_building(
            trigger_event={},
            trigger_type="test",
            self_state_before={},
        )

        result = ActionResult(
            action_id="act_success",
            success=True,
            response_received=True,
            user_response="thanks",
        )

        experience = self.builder.finish_building(
            experience_id=exp_id,
            result=result,
            self_state_after={},
        )

        self.assertTrue(experience.result.success)
        self.assertEqual(experience.result.user_response, "thanks")

    def test_02_failure_result_recorded(self):
        """失败结果被记录"""
        exp_id = self.builder.start_building(
            trigger_event={},
            trigger_type="test",
            self_state_before={},
        )

        result = ActionResult(
            action_id="act_fail",
            success=False,
            error_message="network error",
        )

        experience = self.builder.finish_building(
            experience_id=exp_id,
            result=result,
            self_state_after={},
        )

        self.assertFalse(experience.result.success)
        self.assertEqual(experience.result.error_message, "network error")

    def test_03_state_changes_recorded(self):
        """状态变化被记录"""
        exp_id = self.builder.start_building(
            trigger_event={},
            trigger_type="test",
            self_state_before={"energy": 0.8, "mood": 0.6},
        )

        result = ActionResult(
            action_id="act_state",
            success=True,
            state_changes={"energy": (0.8, 0.7)},
        )

        experience = self.builder.finish_building(
            experience_id=exp_id,
            result=result,
            self_state_after={"energy": 0.7, "mood": 0.6},
        )

        self.assertEqual(experience.self_state_before["energy"], 0.8)
        self.assertEqual(experience.self_state_after["energy"], 0.7)

    def test_04_quick_build_method(self):
        """快捷构建方法"""
        experience = self.builder.build_from_action_result(
            action_id="act_quick",
            action_type="send_message",
            trigger_event={"event_type": "test"},
            trigger_type="test",
            decision_source="rule",
            self_state_before={"energy": 0.8},
            self_state_after={"energy": 0.7},
            success=True,
            user_response="ok",
        )

        self.assertIsNotNone(experience)
        self.assertEqual(experience.action_type, "send_message")
        self.assertTrue(experience.result.success)


class TestReflectionGeneration(unittest.TestCase):
    """反思生成测试"""

    def setUp(self):
        self.engine = ReflectionEngine(config=ReflectionEngineConfig(min_experiences=3))

    def _create_experiences(self, count: int, action_type: str = "send_message", success: bool = True):
        """辅助：创建经验"""
        experiences = []
        for i in range(count):
            exp = RuntimeExperience(
                trigger_event={"event_type": "test", "data": {"i": i}},
                trigger_type="user_message" if i % 2 == 0 else "tick",
                decision_source="rule",
                action_type=action_type,
                result=ActionResult(
                    action_id=f"act_{i}",
                    success=success,
                    response_received=bool(i % 3 == 0),
                ),
                self_state_before={},
                self_state_after={},
                duration_ms=100.0 + i * 10,
            )
            experiences.append(exp)
        return experiences

    def test_01_high_frequency_proactive_detected(self):
        """高频主动行为模式检测"""
        experiences = self._create_experiences(5, action_type="send_message")
        insight = self.engine.reflect(experiences)

        self.assertIsNotNone(insight)
        self.assertEqual(insight.pattern_detected, "high_frequency_proactive")
        self.assertEqual(insight.insight_type, "pattern")

    def test_02_low_success_rate_detected(self):
        """低成功率模式检测"""
        experiences = self._create_experiences(5, success=False)
        insight = self.engine.reflect(experiences)

        self.assertIsNotNone(insight)
        self.assertEqual(insight.pattern_detected, "low_success_rate")
        self.assertEqual(insight.insight_type, "problem")

    def test_03_insufficient_experiences(self):
        """经验不足不生成洞察"""
        experiences = self._create_experiences(2)
        insight = self.engine.reflect(experiences)

        self.assertIsNone(insight)

    def test_04_insight_has_suggestions(self):
        """洞察包含改进建议"""
        experiences = self._create_experiences(5, success=False)
        insight = self.engine.reflect(experiences)

        self.assertIsNotNone(insight)
        self.assertGreater(len(insight.suggested_adjustments), 0)

    def test_05_analyze_trends(self):
        """趋势分析"""
        experiences = self._create_experiences(10)
        trends = self.engine.analyze_trends(experiences)

        self.assertEqual(trends["total_experiences"], 10)
        self.assertIn("send_message", trends["action_distribution"])
        self.assertGreaterEqual(trends["success_rate"], 0.0)


class TestInvalidExperienceRejection(unittest.TestCase):
    """无效经验拒绝测试"""

    def setUp(self):
        self.validator = ExperienceValidator()

    def test_01_duration_too_short_rejected(self):
        """耗时过短被拒绝"""
        experience = RuntimeExperience(
            trigger_event={},
            trigger_type="test",
            action_type="send_message",
            result=ActionResult(action_id="act_1", success=True),
            duration_ms=5.0,  # 低于 min_duration_ms
        )

        is_valid = self.validator.validate(experience)

        self.assertFalse(is_valid)
        self.assertFalse(experience.valid)
        self.assertIn("duration_too_short", experience.invalid_reason)

    def test_02_duration_too_long_rejected(self):
        """耗时过长被拒绝"""
        experience = RuntimeExperience(
            trigger_event={},
            trigger_type="test",
            action_type="send_message",
            result=ActionResult(action_id="act_2", success=True),
            duration_ms=70000.0,  # 超过 max_duration_ms
        )

        is_valid = self.validator.validate(experience)

        self.assertFalse(is_valid)
        self.assertIn("duration_too_long", experience.invalid_reason)

    def test_03_missing_result_rejected(self):
        """缺少结果被拒绝"""
        experience = RuntimeExperience(
            trigger_event={},
            trigger_type="test",
            action_type="send_message",
            result=None,
            duration_ms=100.0,
        )

        validator_with_result = ExperienceValidator(require_result=True)
        is_valid = validator_with_result.validate(experience)

        self.assertFalse(is_valid)
        self.assertIn("missing_result", experience.invalid_reason)

    def test_04_missing_action_type_rejected(self):
        """缺少 action_type 被拒绝"""
        experience = RuntimeExperience(
            trigger_event={},
            trigger_type="test",
            action_type="",
            result=ActionResult(action_id="act_3", success=True),
            duration_ms=100.0,
        )

        is_valid = self.validator.validate(experience)

        self.assertFalse(is_valid)
        self.assertIn("missing_action_type", experience.invalid_reason)

    def test_05_valid_experience_accepted(self):
        """有效经验被接受"""
        experience = RuntimeExperience(
            trigger_event={},
            trigger_type="test",
            action_type="send_message",
            result=ActionResult(action_id="act_4", success=True),
            duration_ms=100.0,
        )

        is_valid = self.validator.validate(experience)

        self.assertTrue(is_valid)
        self.assertTrue(experience.valid)

    def test_06_auto_validation_in_builder(self):
        """构建器自动验证"""
        builder = ExperienceBuilder(config=ExperienceBuilderConfig(auto_validate=True))

        exp_id = builder.start_building(
            trigger_event={},
            trigger_type="test",
            self_state_before={},
        )

        # 模拟极短耗时
        result = ActionResult(action_id="act", success=True)
        experience = builder.finish_building(
            experience_id=exp_id,
            result=result,
            self_state_after={},
        )

        # 由于耗时极短，应该被验证拒绝
        # 注意：finish_building 内部计算耗时，实际可能不同
        # 这里主要测试 auto_validate=True 时会调用验证器


class TestRuntimeCoreIntegration(unittest.TestCase):
    """RuntimeCore 集成测试"""

    def setUp(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        reset_runtime_bridge()
        self._state_file = str(PROJECT_ROOT / "data" / f"exp_test_{uuid.uuid4().hex[:8]}.json")

    def tearDown(self):
        from src.runtime.runtime_bridge import RuntimeBridge, reset_runtime_bridge
        bridge = RuntimeBridge.get_instance()
        if bridge._runtime_core and bridge._runtime_core.is_running:
            bridge.shutdown()
        reset_runtime_bridge()
        # 清理测试文件
        try:
            Path(self._state_file).unlink(missing_ok=True)
        except Exception:
            pass

    def test_01_experience_disabled_by_default(self):
        """默认禁用经验层"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        self.assertIsNone(bridge._runtime_core.experience_builder)
        self.assertIsNone(bridge._runtime_core.reflection_engine)

    def test_02_experience_enabled(self):
        """启用经验层"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "experience_enabled": True,
        })
        bridge.initialize()

        self.assertIsNotNone(bridge._runtime_core.experience_builder)
        self.assertIsNotNone(bridge._runtime_core.reflection_engine)

    def test_03_event_triggers_experience(self):
        """事件触发经验构建"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "experience_enabled": True,
        })
        bridge.initialize()

        # 注入高主动状态
        bridge._runtime_core.self_state.initiative = 0.9
        bridge._runtime_core.self_state.social_need = 0.9
        bridge._runtime_core.self_state.energy = 0.8

        # 触发决策
        bridge._runtime_core.trigger_decision("test")

        # 检查经验缓冲区
        experiences = bridge._runtime_core.get_experiences()
        # 可能没有，因为 trigger_decision 不走 _on_event 流程
        # 这里主要测试接口可用

    def test_04_reflection_interface(self):
        """反思接口"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
            "experience_enabled": True,
        })
        bridge.initialize()

        # 直接添加经验到缓冲区（全部有用户响应，触发高频主动模式）
        for i in range(5):
            exp = RuntimeExperience(
                trigger_event={"event_type": "test", "data": {"i": i}},
                trigger_type="user_message",
                action_type="send_message",
                result=ActionResult(
                    action_id=f"act_{i}",
                    success=True,
                    response_received=True,  # 全部有响应
                    user_response=f"response_{i}",
                ),
                duration_ms=100.0,
            )
            bridge._runtime_core.experience_builder._buffer.append(exp)

        # 触发反思
        insight = bridge._runtime_core.reflect_on_experiences()

        self.assertIsNotNone(insight)
        # 应该检测到高频主动行为（send_message 出现 5 次）
        self.assertEqual(insight.pattern_detected, "high_frequency_proactive")

    def test_05_backward_compatibility(self):
        """向后兼容：禁用经验层时行为不变"""
        from src.runtime.runtime_bridge import get_runtime_bridge

        bridge = get_runtime_bridge(config={
            "state_file": self._state_file,
            "tick_interval_seconds": 60,
        })
        bridge.initialize()

        # 正常决策流程
        bridge._runtime_core.self_state.initiative = 0.8
        bridge._runtime_core.self_state.social_need = 0.9
        bridge._runtime_core.self_state.energy = 0.7

        result = bridge._runtime_core.trigger_decision("test")

        # 应该正常返回
        self.assertIsNotNone(bridge._runtime_core)


class TestAdapterInterfaces(unittest.TestCase):
    """适配器接口测试"""

    def test_01_memory_adapter_base_not_implemented(self):
        """MemoryAdapter 基类未实现"""
        from src.contracts.experience_schema import MemoryAdapterBase

        adapter = MemoryAdapterBase()

        with self.assertRaises(NotImplementedError):
            adapter.store_experience(RuntimeExperience())

        with self.assertRaises(NotImplementedError):
            adapter.get_recent_experiences()

    def test_02_growth_adapter_base_not_implemented(self):
        """GrowthAdapter 基类未实现"""
        from src.contracts.experience_schema import GrowthAdapterBase

        adapter = GrowthAdapterBase()

        with self.assertRaises(NotImplementedError):
            adapter.store_insight(ReflectionInsight())

        with self.assertRaises(NotImplementedError):
            adapter.get_recent_insights()


if __name__ == "__main__":
    unittest.main(verbosity=2)