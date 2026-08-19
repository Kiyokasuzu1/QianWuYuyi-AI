"""
Phase 3 Step 3 单元测试：Self Reflection System

测试覆盖：
1. SelfModel 自我模型
2. ReflectionReport 反思报告
3. ReflectionSystem 反思系统
4. 反思执行流程
5. 事件触发反思
"""

import pytest
import time
from unittest.mock import Mock, patch

from src.reflection_system import (
    BehaviorEvaluation,
    ReflectionFocusArea,
    ReflectionReport,
    ReflectionSystem,
    ReflectionType,
    SelfModel,
    get_reflection_system,
)

from src.core.yuyi_cognitive_core import YuyiCognitiveCore
from src.core.event_bus import EventBus
from src.contracts.cognitive_event_types import CognitiveEvent


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


@pytest.fixture
def reflection_system():
    """创建反思系统实例"""
    system = ReflectionSystem()
    system.start()
    yield system
    system.stop()


# ==========================================
# Test: SelfModel
# ==========================================

class TestSelfModel:
    """测试自我模型"""

    def test_self_model_creation(self):
        """测试自我模型创建"""
        model = SelfModel(
            identity="测试AI",
            strengths=["记忆好"],
            weaknesses=["表达不足"],
        )

        assert model.identity == "测试AI"
        assert "记忆好" in model.strengths
        assert "表达不足" in model.weaknesses
        assert model.last_updated > 0

    def test_self_model_update_identity(self):
        """测试更新身份"""
        model = SelfModel()

        model.update_identity("羽依 - 成长中的伙伴")

        assert model.identity == "羽依 - 成长中的伙伴"

    def test_self_model_add_strength(self):
        """测试添加优势"""
        model = SelfModel()

        model.add_strength("理解能力强")
        model.add_strength("记忆准确")
        model.add_strength("理解能力强")  # 重复

        assert len(model.strengths) == 2

    def test_self_model_add_weakness(self):
        """测试添加不足"""
        model = SelfModel()

        model.add_weakness("主动表达不够")
        model.add_weakness("有时过于谨慎")

        assert len(model.weaknesses) == 2

    def test_self_model_add_growth_direction(self):
        """测试添加成长方向"""
        model = SelfModel()

        model.add_growth_direction("成为更懂用户的伙伴")
        model.add_growth_direction("提高主动关怀能力")

        assert len(model.growth_direction) == 2

    def test_self_model_to_dict(self):
        """测试自我模型序列化"""
        model = SelfModel(
            identity="羽依",
            strengths=["记忆好"],
            weaknesses=["表达不足"],
            growth_direction=["成为更好的伙伴"],
        )

        model_dict = model.to_dict()

        assert model_dict["identity"] == "羽依"
        assert model_dict["strengths"] == ["记忆好"]
        assert model_dict["weaknesses"] == ["表达不足"]


# ==========================================
# Test: BehaviorEvaluation
# ==========================================

class TestBehaviorEvaluation:
    """测试行为评价"""

    def test_evaluation_creation(self):
        """测试行为评价创建"""
        evaluation = BehaviorEvaluation(
            action_type="proactive_message",
            count=10,
            success_count=8,
            failure_count=2,
            success_rate=0.8,
            notable_actions=["问候", "提醒"],
        )

        assert evaluation.action_type == "proactive_message"
        assert evaluation.count == 10
        assert evaluation.success_rate == 0.8


# ==========================================
# Test: ReflectionReport
# ==========================================

class TestReflectionReport:
    """测试反思报告"""

    def test_report_creation(self):
        """测试报告创建"""
        report = ReflectionReport(
            reflection_id="ref_001",
            reflection_type=ReflectionType.DAILY,
            started_at=time.time(),
            focus_areas=[ReflectionFocusArea.BEHAVIOR],
        )

        assert report.reflection_id == "ref_001"
        assert report.reflection_type == ReflectionType.DAILY
        assert report.completed_at == 0.0
        assert len(report.focus_areas) == 1

    def test_report_complete(self):
        """测试报告完成"""
        report = ReflectionReport(
            reflection_id="ref_001",
            reflection_type=ReflectionType.DAILY,
            started_at=time.time(),
            focus_areas=[],
        )

        report.complete()

        assert report.completed_at > 0

    def test_report_to_dict(self):
        """测试报告序列化"""
        report = ReflectionReport(
            reflection_id="ref_001",
            reflection_type=ReflectionType.DAILY,
            started_at=time.time(),
            focus_areas=[ReflectionFocusArea.BEHAVIOR],
            insights=["测试洞察"],
            improvements=["测试改进"],
        )
        report.complete()

        report_dict = report.to_dict()

        assert report_dict["reflection_id"] == "ref_001"
        assert report_dict["reflection_type"] == "daily"
        assert report_dict["insights"] == ["测试洞察"]
        assert report_dict["improvements"] == ["测试改进"]


# ==========================================
# Test: ReflectionSystem
# ==========================================

class TestReflectionSystem:
    """测试反思系统"""

    def test_system_creation(self):
        """测试系统创建"""
        system = ReflectionSystem()

        assert system._running is False
        assert system._self_model is not None
        assert system._reflections == {}

    def test_system_start(self, reflection_system):
        """测试系统启动"""
        assert reflection_system._running is True
        assert reflection_system._cognitive_core is not None
        assert reflection_system._reflection_layer is not None

    def test_system_stop(self, reflection_system):
        """测试系统停止"""
        result = reflection_system.stop()

        assert result is True
        assert reflection_system._running is False

    def test_get_self_model(self, reflection_system):
        """测试获取自我模型"""
        model = reflection_system.get_self_model()

        assert model is not None
        assert model.identity is not None

    def test_update_self_model(self, reflection_system):
        """测试更新自我模型"""
        reflection_system.update_self_model({
            "strengths": ["新优势"],
            "weaknesses": ["新不足"],
        })

        model = reflection_system.get_self_model()
        assert "新优势" in model.strengths
        assert "新不足" in model.weaknesses


# ==========================================
# Test: Reflection Execution
# ==========================================

class TestReflectionExecution:
    """测试反思执行"""

    def test_perform_daily_reflection(self, reflection_system):
        """测试执行每日反思"""
        report = reflection_system.perform_daily_reflection()

        assert report is not None
        assert report.reflection_type == ReflectionType.DAILY
        assert len(report.focus_areas) > 0
        assert report.completed_at > 0
        assert report.reflection_id in reflection_system._reflections

    def test_perform_event_reflection(self, reflection_system):
        """测试执行事件反思"""
        report = reflection_system.perform_event_reflection(
            trigger_event_id="evt_001",
            focus_areas=[ReflectionFocusArea.GOALS],
            reason="目标完成",
        )

        assert report is not None
        assert report.reflection_type == ReflectionType.EVENT_BASED
        assert report.trigger_events == ["evt_001"]
        assert ReflectionFocusArea.GOALS in report.focus_areas

    def test_perform_milestone_reflection(self, reflection_system):
        """测试执行里程碑反思"""
        report = reflection_system.perform_milestone_reflection(
            milestone="用户关系升级为伙伴",
            focus_areas=[ReflectionFocusArea.RELATIONSHIP],
        )

        assert report is not None
        assert report.reflection_type == ReflectionType.MILESTONE

    def test_reflection_generates_insights(self, reflection_system):
        """测试反思生成洞察"""
        report = reflection_system.perform_daily_reflection()

        assert len(report.insights) > 0

    def test_reflection_generates_improvements(self, reflection_system):
        """测试反思生成改进建议"""
        report = reflection_system.perform_daily_reflection()

        assert len(report.improvements) > 0

    def test_reflection_generates_growth_proposals(self, reflection_system):
        """测试反思生成成长提案"""
        report = reflection_system.perform_daily_reflection()

        assert len(report.growth_proposals) > 0


# ==========================================
# Test: Query Methods
# ==========================================

class TestReflectionQuery:
    """测试反思查询"""

    def test_get_reflection(self, reflection_system):
        """测试获取反思报告"""
        report = reflection_system.perform_daily_reflection()

        retrieved = reflection_system.get_reflection(report.reflection_id)

        assert retrieved is report

    def test_get_recent_reflections(self, reflection_system):
        """测试获取最近反思"""
        reflection_system.perform_daily_reflection()
        reflection_system.perform_event_reflection(
            "evt_001",
            [ReflectionFocusArea.GOALS],
        )

        recent = reflection_system.get_recent_reflections(limit=10)

        assert len(recent) == 2

    def test_get_reflections_by_type(self, reflection_system):
        """测试按类型获取反思"""
        reflection_system.perform_daily_reflection()
        reflection_system.perform_event_reflection(
            "evt_001",
            [ReflectionFocusArea.GOALS],
        )

        daily = reflection_system.get_reflections_by_type(ReflectionType.DAILY)
        event_based = reflection_system.get_reflections_by_type(ReflectionType.EVENT_BASED)

        assert len(daily) == 1
        assert len(event_based) == 1

    def test_get_reflection_stats(self, reflection_system):
        """测试获取反思统计"""
        reflection_system.perform_daily_reflection()

        stats = reflection_system.get_reflection_stats()

        assert stats["total_reflections"] == 1
        assert stats["daily_reflections"] == 1

    def test_should_perform_daily_reflection(self, reflection_system):
        """测试是否应该执行每日反思"""
        # 从未执行过
        assert reflection_system.should_perform_daily_reflection() is True

        # 刚执行过
        reflection_system.perform_daily_reflection()
        assert reflection_system.should_perform_daily_reflection() is False


# ==========================================
# Test: ReflectionLayer Event Handling
# ==========================================

class TestReflectionLayerEventHandling:
    """测试反思层事件处理"""

    def test_on_goal_completed_triggers_reflection(self, reflection_system):
        """测试目标完成触发反思"""
        event = CognitiveEvent(
            type="goal.completed",
            data={"outcome": "achieved", "goal_id": "goal_001"},
        )

        reflection_system._reflection_layer.on_goal_completed(event)

        # 检查是否创建了反思报告
        event_reflections = reflection_system.get_reflections_by_type(ReflectionType.EVENT_BASED)
        assert len(event_reflections) == 1

    def test_on_goal_failed_triggers_reflection(self, reflection_system):
        """测试目标失败触发反思"""
        event = CognitiveEvent(
            type="goal.completed",
            data={"outcome": "failed", "goal_id": "goal_001"},
        )

        reflection_system._reflection_layer.on_goal_completed(event)

        event_reflections = reflection_system.get_reflections_by_type(ReflectionType.EVENT_BASED)
        assert len(event_reflections) == 1

    def test_on_growth_applied_triggers_reflection(self, reflection_system):
        """测试成长应用触发反思"""
        event = CognitiveEvent(
            type="growth.applied",
            data={"proposal_id": "prop_001"},
        )

        reflection_system._reflection_layer.on_growth_applied(event)

        event_reflections = reflection_system.get_reflections_by_type(ReflectionType.EVENT_BASED)
        assert len(event_reflections) == 1


# ==========================================
# Test: Convenience Functions
# ==========================================

class TestConvenienceFunctions:
    """测试便捷函数"""

    def test_get_reflection_system(self):
        """测试获取反思系统"""
        system1 = get_reflection_system()
        system2 = get_reflection_system()

        assert system1 is system2


# ==========================================
# 运行测试
# ==========================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])