"""
Phase 3 单元测试：Yuyi Unified Cognitive Model + Cognitive Event Bus

测试覆盖：
1. CognitiveEvent 事件类型定义
2. YuyiCognitiveCore 统一认知核心
3. CognitiveLayerBase 认知层基类
4. 事件发布/订阅机制
5. 统一状态查询
"""

import pytest
import time
from unittest.mock import Mock, MagicMock
from dataclasses import asdict

from src.contracts.cognitive_event_types import (
    CognitiveEvent,
    EventPriority,
    UserInputEvent,
    UserEmotionDetectedEvent,
    UserPatternDetectedEvent,
    MemoryRecordedEvent,
    MemoryAccessedEvent,
    EmotionStateChangedEvent,
    GoalCreatedEvent,
    GoalProgressEvent,
    GoalCompletedEvent,
    RelationshipEvolvedEvent,
    ProactiveActionProposedEvent,
    ProactiveActionExecutedEvent,
    ReflectionTriggeredEvent,
    ReflectionCompletedEvent,
    GrowthProposalCreatedEvent,
    GrowthAppliedEvent,
    SystemTickEvent,
    create_event_from_dict,
    EVENT_TYPE_REGISTRY,
)

from src.core.yuyi_cognitive_core import (
    YuyiCognitiveCore,
    CognitiveLayerBase,
    CognitiveLayerState,
    UnifiedCognitiveState,
    get_cognitive_core,
    emit_cognitive_event,
)

from src.core.event_bus import EventBus


# ==========================================
# Fixtures
# ==========================================

@pytest.fixture(autouse=True)
def reset_singletons():
    """每个测试前重置单例"""
    YuyiCognitiveCore.reset_instance()
    EventBus.reset_instance()
    yield
    YuyiCognitiveCore.reset_instance()
    EventBus.reset_instance()


# ==========================================
# Test: CognitiveEvent 基础类
# ==========================================

class TestCognitiveEvent:
    """测试认知事件基类"""

    def test_cognitive_event_creation(self):
        """测试事件创建"""
        event = CognitiveEvent(
            type="test.event",
            source="test_module",
            data={"key": "value"},
            user_id="user_001",
        )

        assert event.type == "test.event"
        assert event.source == "test_module"
        assert event.data == {"key": "value"}
        assert event.user_id == "user_001"
        assert event.priority == EventPriority.NORMAL
        assert event.id.startswith("ce_")
        assert event.timestamp is not None

    def test_cognitive_event_to_dict(self):
        """测试事件序列化"""
        event = CognitiveEvent(
            type="test.event",
            source="test_module",
            data={"key": "value"},
            user_id="user_001",
            related_event_ids=["evt_001", "evt_002"],
        )

        event_dict = event.to_dict()

        assert event_dict["type"] == "test.event"
        assert event_dict["source"] == "test_module"
        assert event_dict["data"] == {"key": "value"}
        assert event_dict["user_id"] == "user_001"
        assert event_dict["priority"] == 5
        assert event_dict["related_event_ids"] == ["evt_001", "evt_002"]

    def test_cognitive_event_from_dict(self):
        """测试事件反序列化"""
        data = {
            "id": "ce_test123",
            "type": "test.event",
            "timestamp": "2026-01-01T00:00:00Z",
            "source": "test_module",
            "priority": 8,
            "data": {"key": "value"},
            "user_id": "user_001",
            "session_id": "session_001",
            "related_event_ids": ["evt_001"],
            "metadata": {"meta_key": "meta_value"},
        }

        event = CognitiveEvent.from_dict(data)

        assert event.id == "ce_test123"
        assert event.type == "test.event"
        assert event.timestamp == "2026-01-01T00:00:00Z"
        assert event.source == "test_module"
        assert event.priority == EventPriority.HIGH
        assert event.data == {"key": "value"}
        assert event.user_id == "user_001"
        assert event.session_id == "session_001"
        assert event.related_event_ids == ["evt_001"]
        assert event.metadata == {"meta_key": "meta_value"}

    def test_event_priority_values(self):
        """测试事件优先级枚举"""
        assert EventPriority.LOW.value == 1
        assert EventPriority.NORMAL.value == 5
        assert EventPriority.HIGH.value == 8
        assert EventPriority.CRITICAL.value == 10


# ==========================================
# Test: 具体事件类型
# ==========================================

class TestConcreteEventTypes:
    """测试具体事件类型"""

    def test_user_input_event(self):
        """测试用户输入事件"""
        event = UserInputEvent.create(
            content="你好，羽依",
            user_id="user_001",
            session_id="session_001",
            source="conversation",
        )

        assert event.type == "user.input"
        assert event.data["content"] == "你好，羽依"
        assert event.user_id == "user_001"
        assert event.session_id == "session_001"
        assert event.source == "conversation"

    def test_user_emotion_detected_event(self):
        """测试用户情绪检测事件"""
        event = UserEmotionDetectedEvent.create(
            emotion="tired",
            confidence=0.82,
            user_id="user_001",
            source_event_id="ce_abc123",
            indicators=["expression", "context"],
        )

        assert event.type == "user.emotion_detected"
        assert event.data["emotion"] == "tired"
        assert event.data["confidence"] == 0.82
        assert event.related_event_ids == ["ce_abc123"]
        assert event.data["indicators"] == ["expression", "context"]

    def test_memory_recorded_event(self):
        """测试记忆记录事件"""
        event = MemoryRecordedEvent.create(
            memory_id="mem_001",
            memory_type="conversation",
            content_summary="用户分享了他们的兴趣爱好",
            user_id="user_001",
            importance=0.8,
        )

        assert event.type == "memory.recorded"
        assert event.data["memory_id"] == "mem_001"
        assert event.data["memory_type"] == "conversation"
        assert event.data["importance"] == 0.8

    def test_emotion_state_changed_event(self):
        """测试情绪状态变化事件"""
        event = EmotionStateChangedEvent.create(
            previous_emotion="neutral",
            current_emotion="happy",
            intensity=0.75,
            trigger_reason="user_positive_feedback",
            user_id="user_001",
        )

        assert event.type == "emotion.state_changed"
        assert event.data["previous_emotion"] == "neutral"
        assert event.data["current_emotion"] == "happy"
        assert event.data["intensity"] == 0.75
        assert event.priority == EventPriority.HIGH

    def test_goal_created_event(self):
        """测试目标创建事件"""
        event = GoalCreatedEvent.create(
            goal_id="goal_001",
            goal_type="relationship",
            description="提升用户满意度",
            priority=8,
            user_id="user_001",
        )

        assert event.type == "goal.created"
        assert event.data["goal_id"] == "goal_001"
        assert event.data["goal_type"] == "relationship"
        assert event.data["description"] == "提升用户满意度"
        assert event.data["priority"] == 8

    def test_goal_completed_event(self):
        """测试目标完成事件"""
        event = GoalCompletedEvent.create(
            goal_id="goal_001",
            outcome="achieved",
            lessons_learned=["定时检查效果更好", "避免过度主动"],
            user_id="user_001",
        )

        assert event.type == "goal.completed"
        assert event.data["outcome"] == "achieved"
        assert len(event.data["lessons_learned"]) == 2
        assert event.priority == EventPriority.HIGH

    def test_relationship_evolved_event(self):
        """测试关系进化事件"""
        event = RelationshipEvolvedEvent.create(
            user_id="user_001",
            previous_level="familiar",
            current_level="partner",
            trust_delta=15.0,
            reason="持续积极互动",
        )

        assert event.type == "relationship.evolved"
        assert event.data["previous_level"] == "familiar"
        assert event.data["current_level"] == "partner"
        assert event.data["trust_delta"] == 15.0
        assert event.priority == EventPriority.HIGH

    def test_reflection_triggered_event(self):
        """测试反思触发事件"""
        event = ReflectionTriggeredEvent.create(
            reflection_type="daily",
            trigger_event_ids=["evt_001", "evt_002"],
            focus_areas=["proactive_behavior", "memory_usage"],
            user_id="user_001",
        )

        assert event.type == "reflection.triggered"
        assert event.data["reflection_type"] == "daily"
        assert event.related_event_ids == ["evt_001", "evt_002"]
        assert event.data["focus_areas"] == ["proactive_behavior", "memory_usage"]

    def test_reflection_completed_event(self):
        """测试反思完成事件"""
        event = ReflectionCompletedEvent.create(
            reflection_id="ref_001",
            insights=["用户偏好夜间交流", "需要增加主动关怀"],
            improvements=["调整交流时间", "优化主动策略"],
            self_model_updates={"communication_style": "more_proactive"},
            user_id="user_001",
        )

        assert event.type == "reflection.completed"
        assert event.data["reflection_id"] == "ref_001"
        assert len(event.data["insights"]) == 2
        assert event.data["self_model_updates"]["communication_style"] == "more_proactive"
        assert event.priority == EventPriority.HIGH

    def test_growth_proposal_created_event(self):
        """测试成长提案创建事件"""
        event = GrowthProposalCreatedEvent.create(
            proposal_id="prop_001",
            proposal_type="personality_adjustment",
            description="增加温暖度特质",
            impact_preview={
                "personality": {"warmth": "+15%"},
                "behavior": {"proactive_care": "increase"},
            },
            user_id="user_001",
            requires_approval=True,
        )

        assert event.type == "growth.proposal_created"
        assert event.data["proposal_id"] == "prop_001"
        assert event.data["requires_approval"] is True
        assert "personality" in event.data["impact_preview"]
        assert event.priority == EventPriority.HIGH

    def test_growth_applied_event(self):
        """测试成长应用事件"""
        event = GrowthAppliedEvent.create(
            proposal_id="prop_001",
            changes_applied=[
                {"path": "personality.warmth", "before": 62, "after": 77},
            ],
            user_id="user_001",
            approved_by="user_001",
        )

        assert event.type == "growth.applied"
        assert event.data["proposal_id"] == "prop_001"
        assert event.data["approved_by"] == "user_001"
        assert event.priority == EventPriority.HIGH

    def test_system_tick_event(self):
        """测试系统时钟事件"""
        event = SystemTickEvent.create(
            tick_id="tick_20260101_0000",
            tick_type="minute",
        )

        assert event.type == "system.tick"
        assert event.data["tick_type"] == "minute"
        assert event.priority == EventPriority.LOW


# ==========================================
# Test: 事件类型注册表
# ==========================================

class TestEventTypeRegistry:
    """测试事件类型注册表"""

    def test_registry_contains_all_types(self):
        """测试注册表包含所有事件类型"""
        expected_types = [
            "user.input",
            "user.emotion_detected",
            "user.pattern_detected",
            "memory.recorded",
            "memory.accessed",
            "emotion.state_changed",
            "goal.created",
            "goal.progress",
            "goal.completed",
            "relationship.evolved",
            "action.proactive_proposed",
            "action.proactive_executed",
            "reflection.triggered",
            "reflection.completed",
            "growth.proposal_created",
            "growth.applied",
            "system.tick",
        ]

        for event_type in expected_types:
            assert event_type in EVENT_TYPE_REGISTRY, f"Missing event type: {event_type}"

    def test_create_event_from_dict_user_input(self):
        """测试从字典创建具体事件类型：用户输入"""
        data = {
            "type": "user.input",
            "data": {"content": "你好"},
            "user_id": "user_001",
        }

        event = create_event_from_dict(data)

        assert isinstance(event, UserInputEvent)
        assert event.type == "user.input"
        assert event.data["content"] == "你好"

    def test_create_event_from_dict_goal_created(self):
        """测试从字典创建具体事件类型：目标创建"""
        data = {
            "type": "goal.created",
            "data": {
                "goal_id": "goal_001",
                "goal_type": "relationship",
                "description": "测试目标",
                "priority": 8,
            },
        }

        event = create_event_from_dict(data)

        assert isinstance(event, GoalCreatedEvent)
        assert event.type == "goal.created"

    def test_create_event_from_dict_unknown_type(self):
        """测试从字典创建未知事件类型"""
        data = {
            "type": "unknown.event",
            "data": {"key": "value"},
        }

        event = create_event_from_dict(data)

        # 未知类型返回基类 CognitiveEvent
        assert isinstance(event, CognitiveEvent)
        assert event.type == "unknown.event"


# ==========================================
# Test: CognitiveLayerBase
# ==========================================

class TestCognitiveLayerBase:
    """测试认知层基类"""

    def test_layer_creation(self):
        """测试认知层创建"""
        layer = CognitiveLayerBase("test_layer")

        assert layer.layer_name == "test_layer"
        assert layer.status == "uninitialized"
        assert layer.last_update > 0
        assert layer.digest == {}
        assert layer._metrics == {}  # 使用私有属性

    def test_layer_get_state(self):
        """测试认知层状态获取"""
        layer = CognitiveLayerBase("emotion")
        layer._status = "running"
        layer.update_digest("current_emotion", "happy")
        layer.update_metric("intensity", 0.75)

        state = layer.get_state()

        assert state.layer_name == "emotion"
        assert state.status == "running"
        assert state.digest["current_emotion"] == "happy"
        assert state.metrics["intensity"] == 0.75

    def test_layer_update_digest(self):
        """测试认知层摘要更新"""
        layer = CognitiveLayerBase("memory")

        old_update = layer.last_update
        time.sleep(0.01)
        layer.update_digest("total_memories", 42)

        assert layer.digest["total_memories"] == 42
        assert layer.last_update > old_update

    def test_layer_update_metric(self):
        """测试认知层指标更新"""
        layer = CognitiveLayerBase("goal")

        old_update = layer.last_update
        time.sleep(0.01)
        layer.update_metric("active_count", 3)

        assert layer._metrics["active_count"] == 3  # 使用私有属性
        assert layer.last_update > old_update


# ==========================================
# Test: YuyiCognitiveCore
# ==========================================

class TestYuyiCognitiveCore:
    """测试统一认知核心"""

    def test_core_creation(self):
        """测试认知核心创建"""
        core = YuyiCognitiveCore()

        assert core._running is False
        assert core._event_bus is not None
        assert core._emotion_layer is None
        assert core._memory_layer is None

    def test_core_singleton(self):
        """测试单例模式"""
        core1 = YuyiCognitiveCore.get_instance()
        core2 = YuyiCognitiveCore.get_instance()

        assert core1 is core2

    def test_core_start(self):
        """测试认知核心启动"""
        core = YuyiCognitiveCore()

        result = core.start()

        assert result is True
        assert core.is_running() is True
        assert core._emotion_layer is not None
        assert core._memory_layer is not None
        assert core._personality_layer is not None
        assert core._goal_layer is not None
        assert core._relationship_layer is not None
        assert core._growth_layer is not None

    def test_core_stop(self):
        """测试认知核心停止"""
        core = YuyiCognitiveCore()
        core.start()

        result = core.stop()

        assert result is True
        assert core.is_running() is False

    def test_core_double_start(self):
        """测试重复启动"""
        core = YuyiCognitiveCore()

        result1 = core.start()
        result2 = core.start()

        assert result1 is True
        assert result2 is True
        assert core.is_running() is True

    def test_core_emit_event(self):
        """测试事件发布"""
        core = YuyiCognitiveCore()
        core.start()

        event = core.emit_event(
            event_type="user.input",
            data={"content": "测试消息"},
            user_id="user_001",
        )

        assert event.type == "user.input"
        assert event.data["content"] == "测试消息"
        assert event.user_id == "user_001"
        assert event.id.startswith("ce_")
        assert len(core._event_history) == 1

    def test_core_record_user_input(self):
        """测试记录用户输入快捷方法"""
        core = YuyiCognitiveCore()
        core.start()

        event = core.record_user_input(
            content="你好，羽依",
            user_id="user_001",
            session_id="session_001",
        )

        assert event.type == "user.input"
        assert event.data["content"] == "你好，羽依"
        assert event.user_id == "user_001"
        assert event.session_id == "session_001"
        assert event.source == "conversation"

    def test_core_record_memory(self):
        """测试记录记忆快捷方法"""
        core = YuyiCognitiveCore()
        core.start()

        event = core.record_memory(
            memory_id="mem_001",
            memory_type="conversation",
            content_summary="测试记忆",
            user_id="user_001",
            importance=0.8,
        )

        assert event.type == "memory.recorded"
        assert event.data["memory_id"] == "mem_001"
        assert event.data["importance"] == 0.8

    def test_core_trigger_reflection(self):
        """测试触发反思快捷方法"""
        core = YuyiCognitiveCore()
        core.start()

        event = core.trigger_reflection(
            reflection_type="daily",
            focus_areas=["behavior", "memory"],
            trigger_event_ids=["evt_001"],
            user_id="user_001",
        )

        assert event.type == "reflection.triggered"
        assert event.data["reflection_type"] == "daily"
        assert event.data["focus_areas"] == ["behavior", "memory"]
        assert event.related_event_ids == ["evt_001"]

    def test_core_get_unified_state(self):
        """测试获取统一状态"""
        core = YuyiCognitiveCore()
        core.start()

        state = core.get_unified_state()

        assert state.timestamp > 0
        assert state.emotion.layer_name == "emotion"
        assert state.memory.layer_name == "memory"
        assert state.personality.layer_name == "personality"
        assert state.goals.layer_name == "goal"
        assert state.relationship.layer_name == "relationship"
        assert state.growth.layer_name == "growth"
        assert state.overall_health == 1.0

    def test_core_unified_state_to_dict(self):
        """测试统一状态序列化"""
        core = YuyiCognitiveCore()
        core.start()

        state = core.get_unified_state()
        state_dict = state.to_dict()

        assert "timestamp" in state_dict
        assert "emotion" in state_dict
        assert "memory" in state_dict
        assert "personality" in state_dict
        assert "goals" in state_dict
        assert "relationship" in state_dict
        assert "growth" in state_dict
        assert "overall_health" in state_dict

    def test_core_get_event_history(self):
        """测试获取事件历史"""
        core = YuyiCognitiveCore()
        core.start()

        # 发布多个事件
        core.emit_event("user.input", {"content": "消息1"}, user_id="user_001")
        core.emit_event("user.input", {"content": "消息2"}, user_id="user_002")
        core.emit_event("memory.recorded", {"memory_id": "mem_001"}, user_id="user_001")

        # 获取全部历史
        history = core.get_event_history()
        assert len(history) == 3

        # 按类型过滤
        user_inputs = core.get_event_history(event_type="user.input")
        assert len(user_inputs) == 2

        # 按用户过滤
        user_001_events = core.get_event_history(user_id="user_001")
        assert len(user_001_events) == 2

    def test_core_register_layer(self):
        """测试注册自定义认知层"""
        core = YuyiCognitiveCore()
        core.start()

        # 创建自定义层
        custom_layer = CognitiveLayerBase("emotion")
        custom_layer._status = "running"
        custom_layer.update_digest("custom_field", "custom_value")

        # 注册
        core.register_layer("emotion", custom_layer)

        assert core._emotion_layer.layer_name == "emotion"
        assert core._emotion_layer.digest["custom_field"] == "custom_value"

    def test_core_register_unknown_layer_type(self):
        """测试注册未知类型认知层"""
        core = YuyiCognitiveCore()

        with pytest.raises(ValueError, match="Unknown layer type"):
            core.register_layer("unknown_type", CognitiveLayerBase("test"))

    def test_core_health_calculation(self):
        """测试健康度计算"""
        core = YuyiCognitiveCore()
        core.start()

        # 所有层都运行（9层）
        health = core._calculate_health()
        assert health == 1.0

        # 模拟一层出错
        core._emotion_layer._status = "error"
        health = core._calculate_health()
        assert health == 8 / 9  # 9 层中有 8 层正常


# ==========================================
# Test: 事件流集成
# ==========================================

class TestEventFlowIntegration:
    """测试事件流集成"""

    def test_user_input_flows_to_layers(self):
        """测试用户输入事件流向各层"""
        core = YuyiCognitiveCore()
        core.start()

        # 监听事件
        received_events = []
        original_handler = core._emotion_layer.on_user_input

        def mock_handler(event):
            received_events.append(event)
            original_handler(event)

        core._emotion_layer.on_user_input = mock_handler

        # 发布用户输入
        core.emit_event(
            event_type="user.input",
            data={"content": "测试消息"},
            user_id="user_001",
        )

        # 验证事件被处理
        assert len(received_events) == 1
        assert received_events[0].data["content"] == "测试消息"

    def test_goal_completion_triggers_reflection(self):
        """测试目标完成触发反思流程"""
        core = YuyiCognitiveCore()
        core.start()

        # 发布目标完成事件
        core.emit_event(
            event_type="goal.completed",
            data={
                "goal_id": "goal_001",
                "outcome": "achieved",
                "lessons_learned": ["测试"],
            },
            user_id="user_001",
        )

        # 验证事件被记录
        history = core.get_event_history(event_type="goal.completed")
        assert len(history) == 1
        assert history[0].data["goal_id"] == "goal_001"

    def test_growth_applied_updates_layers(self):
        """测试成长应用更新各层"""
        core = YuyiCognitiveCore()
        core.start()

        # 发布成长应用事件
        core.emit_event(
            event_type="growth.applied",
            data={
                "proposal_id": "prop_001",
                "changes_applied": [{"path": "warmth", "before": 62, "after": 77}],
            },
            user_id="user_001",
        )

        # 验证事件流向各层
        history = core.get_event_history(event_type="growth.applied")
        assert len(history) == 1


# ==========================================
# Test: 便捷函数
# ==========================================

class TestConvenienceFunctions:
    """测试便捷函数"""

    def test_get_cognitive_core(self):
        """测试获取认知核心"""
        core1 = get_cognitive_core()
        core2 = get_cognitive_core()

        assert core1 is core2
        assert isinstance(core1, YuyiCognitiveCore)

    def test_emit_cognitive_event(self):
        """测试便捷发布事件"""
        core = get_cognitive_core()
        core.start()

        event = emit_cognitive_event(
            event_type="test.event",
            data={"key": "value"},
            user_id="user_001",
        )

        assert event.type == "test.event"
        assert event.data["key"] == "value"
        assert event.user_id == "user_001"


# ==========================================
# 运行测试
# ==========================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])