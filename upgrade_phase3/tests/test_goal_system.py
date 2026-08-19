"""
Phase 3 Step 2 单元测试：Autonomous Goal System

测试覆盖：
1. Goal 目标数据结构
2. GoalMemory 目标记忆
3. GoalSystem 目标系统
4. GoalLayer 目标认知层
5. 目标生命周期管理
6. Goal Memory 经验累积
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch

from src.goal import (
    Goal,
    GoalAttempt,
    GoalMemory,
    GoalOutcome,
    GoalStatus,
    GoalSystem,
    GoalType,
    GoalLayer,
    get_goal_system,
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
def goal_system():
    """创建目标系统实例"""
    system = GoalSystem()
    system.start()
    yield system
    system.stop()


# ==========================================
# Test: Goal 数据结构
# ==========================================

class TestGoal:
    """测试目标数据结构"""

    def test_goal_creation(self):
        """测试目标创建"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.RELATIONSHIP,
            description="提升用户满意度",
            priority=8,
        )

        assert goal.goal_id == "goal_001"
        assert goal.goal_type == GoalType.RELATIONSHIP
        assert goal.description == "提升用户满意度"
        assert goal.priority == 8
        assert goal.status == GoalStatus.CREATED
        assert goal.progress == 0.0
        assert goal.created_at > 0

    def test_goal_auto_id(self):
        """测试自动生成目标 ID"""
        goal = Goal(
            goal_id="",
            goal_type=GoalType.KNOWLEDGE,
            description="测试目标",
        )

        assert goal.goal_id.startswith("goal_")
        assert len(goal.goal_id) > 5

    def test_goal_start(self):
        """测试开始执行目标"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.BEHAVIOR,
            description="测试目标",
        )

        goal.start()

        assert goal.status == GoalStatus.IN_PROGRESS
        assert goal.started_at > 0
        assert goal.current_attempt is not None
        assert goal.current_attempt.goal_id == "goal_001"

    def test_goal_update_progress(self):
        """测试更新进度"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.MEMORY,
            description="测试目标",
        )

        goal.update_progress(0.5, milestone="完成一半")

        assert goal.progress == 0.5
        assert len(goal.milestones) == 1
        assert goal.milestones[0]["description"] == "完成一半"

    def test_goal_progress_bounds(self):
        """测试进度边界"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.SELF_IMPROVEMENT,
            description="测试目标",
        )

        goal.update_progress(-0.5)
        assert goal.progress == 0.0

        goal.update_progress(1.5)
        assert goal.progress == 1.0

    def test_goal_complete_achieved(self):
        """测试成功完成目标"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.USER_SATISFACTION,
            description="测试目标",
        )
        goal.start()

        goal.complete(GoalOutcome.ACHIEVED, lessons=["保持良好"])

        assert goal.status == GoalStatus.COMPLETED
        assert goal.outcome == GoalOutcome.ACHIEVED
        assert goal.progress == 1.0
        assert goal.lessons_learned == ["保持良好"]
        assert goal.completed_at > 0
        assert goal.current_attempt.success is True

    def test_goal_complete_failed(self):
        """测试失败完成目标"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.PROACTIVE_CARE,
            description="测试目标",
        )
        goal.start()
        goal.update_progress(0.3)

        goal.complete(GoalOutcome.FAILED, lessons=["需要调整策略"])

        assert goal.status == GoalStatus.COMPLETED
        assert goal.outcome == GoalOutcome.FAILED
        assert goal.progress == 0.3  # 进度保持不变
        assert goal.current_attempt.success is False

    def test_goal_abandon(self):
        """测试放弃目标"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.RELATIONSHIP,
            description="测试目标",
        )
        goal.start()

        goal.abandon(reason="不再需要")

        assert goal.status == GoalStatus.ABANDONED
        assert goal.outcome == GoalOutcome.ABANDONED
        assert goal.completed_at > 0
        assert goal.current_attempt.success is False
        assert goal.current_attempt.notes == "不再需要"

    def test_goal_to_dict(self):
        """测试目标序列化"""
        goal = Goal(
            goal_id="goal_001",
            goal_type=GoalType.RELATIONSHIP,
            description="测试目标",
            priority=7,
            user_id="user_001",
        )

        goal_dict = goal.to_dict()

        assert goal_dict["goal_id"] == "goal_001"
        assert goal_dict["goal_type"] == "relationship"
        assert goal_dict["description"] == "测试目标"
        assert goal_dict["priority"] == 7
        assert goal_dict["user_id"] == "user_001"
        assert goal_dict["status"] == "created"


# ==========================================
# Test: GoalAttempt
# ==========================================

class TestGoalAttempt:
    """测试目标尝试记录"""

    def test_attempt_creation(self):
        """测试尝试创建"""
        attempt = GoalAttempt(
            attempt_id="att_001",
            goal_id="goal_001",
            started_at=time.time(),
        )

        assert attempt.attempt_id == "att_001"
        assert attempt.goal_id == "goal_001"
        assert attempt.started_at > 0
        assert attempt.ended_at == 0.0
        assert attempt.actions_taken == []
        assert attempt.success is False

    def test_attempt_end(self):
        """测试结束尝试"""
        attempt = GoalAttempt(
            attempt_id="att_001",
            goal_id="goal_001",
            started_at=time.time() - 100,
        )

        attempt.ended_at = time.time()
        attempt.success = True
        attempt.result = "achieved"

        assert attempt.ended_at > attempt.started_at
        assert attempt.success is True


# ==========================================
# Test: GoalMemory
# ==========================================

class TestGoalMemory:
    """测试目标记忆"""

    def test_memory_creation(self):
        """测试记忆创建"""
        memory = GoalMemory(
            goal_type="relationship",
            goal_description="提升用户满意度",
        )

        assert memory.goal_type == "relationship"
        assert memory.attempts == []
        assert memory.lessons_learned == []
        assert memory.success_rate == 0.0

    def test_memory_record_attempt_success(self):
        """测试记录成功尝试"""
        memory = GoalMemory(
            goal_type="relationship",
            goal_description="测试",
        )

        attempt = GoalAttempt(
            attempt_id="att_001",
            goal_id="goal_001",
            started_at=time.time() - 100,
            ended_at=time.time(),
            success=True,
        )

        memory.record_attempt(attempt)

        assert memory.total_attempts == 1
        assert memory.successful_attempts == 1
        assert memory.success_rate == 1.0
        assert memory.avg_completion_time > 0

    def test_memory_record_attempt_failure(self):
        """测试记录失败尝试"""
        memory = GoalMemory(
            goal_type="knowledge",
            goal_description="测试",
        )

        # 一次成功
        memory.record_attempt(GoalAttempt(
            attempt_id="att_001",
            goal_id="goal_001",
            started_at=time.time() - 100,
            ended_at=time.time(),
            success=True,
        ))

        # 一次失败
        memory.record_attempt(GoalAttempt(
            attempt_id="att_002",
            goal_id="goal_002",
            started_at=time.time() - 50,
            ended_at=time.time(),
            success=False,
        ))

        assert memory.total_attempts == 2
        assert memory.successful_attempts == 1
        assert memory.success_rate == 0.5

    def test_memory_add_lesson(self):
        """测试添加经验教训"""
        memory = GoalMemory(
            goal_type="behavior",
            goal_description="测试",
        )

        memory.add_lesson("避免过度主动")
        memory.add_lesson("定时检查效果更好")
        memory.add_lesson("避免过度主动")  # 重复

        assert len(memory.lessons_learned) == 2

    def test_memory_add_best_practice(self):
        """测试添加最佳实践"""
        memory = GoalMemory(
            goal_type="proactive_care",
            goal_description="测试",
        )

        memory.add_best_practice("晚上8点发送问候")
        memory.add_best_practice("根据用户习惯调整频率")

        assert len(memory.best_practices) == 2

    def test_memory_add_pitfall(self):
        """测试添加陷阱警告"""
        memory = GoalMemory(
            goal_type="user_satisfaction",
            goal_description="测试",
        )

        memory.add_pitfall("不要在用户忙碌时主动打断")
        memory.add_pitfall("避免过度频繁的问候")

        assert len(memory.pitfalls) == 2


# ==========================================
# Test: GoalSystem
# ==========================================

class TestGoalSystem:
    """测试目标系统"""

    def test_system_creation(self):
        """测试系统创建"""
        system = GoalSystem()

        assert system._running is False
        assert system._goals == {}
        assert system._active_goals == []

    def test_system_start(self, goal_system):
        """测试系统启动"""
        assert goal_system._running is True
        assert goal_system._cognitive_core is not None
        assert goal_system._goal_layer is not None

    def test_system_stop(self, goal_system):
        """测试系统停止"""
        result = goal_system.stop()

        assert result is True
        assert goal_system._running is False

    def test_create_goal(self, goal_system):
        """测试创建目标"""
        goal = goal_system.create_goal(
            goal_type=GoalType.RELATIONSHIP,
            description="提升用户满意度",
            priority=8,
            user_id="user_001",
        )

        assert goal is not None
        assert goal.goal_type == GoalType.RELATIONSHIP
        assert goal.description == "提升用户满意度"
        assert goal.priority == 8
        assert goal.user_id == "user_001"
        assert goal.goal_id in goal_system._goals

    def test_create_goal_with_priority_bounds(self, goal_system):
        """测试优先级边界"""
        # 过高
        goal1 = goal_system.create_goal(
            goal_type=GoalType.KNOWLEDGE,
            description="测试",
            priority=15,
        )
        assert goal1.priority == 10

        # 过低
        goal2 = goal_system.create_goal(
            goal_type=GoalType.KNOWLEDGE,
            description="测试",
            priority=0,
        )
        assert goal2.priority == 1

    def test_activate_goal(self, goal_system):
        """测试激活目标"""
        goal = goal_system.create_goal(
            goal_type=GoalType.BEHAVIOR,
            description="测试目标",
        )

        result = goal_system.activate_goal(goal.goal_id)

        assert result is True
        assert goal.status == GoalStatus.ACTIVE
        assert goal.goal_id in goal_system._active_goals

    def test_activate_goal_with_dependency(self, goal_system):
        """测试带依赖的目标激活"""
        goal1 = goal_system.create_goal(
            goal_type=GoalType.MEMORY,
            description="目标1",
        )
        goal2 = goal_system.create_goal(
            goal_type=GoalType.MEMORY,
            description="目标2",
            depends_on=[goal1.goal_id],
        )

        # 目标1未完成，目标2不能激活
        goal_system.activate_goal(goal1.goal_id)
        result = goal_system.activate_goal(goal2.goal_id)
        assert result is False

        # 完成目标1后，目标2可以激活
        goal1.complete(GoalOutcome.ACHIEVED)
        result = goal_system.activate_goal(goal2.goal_id)
        assert result is True

    def test_start_goal(self, goal_system):
        """测试开始执行目标"""
        goal = goal_system.create_goal(
            goal_type=GoalType.SELF_IMPROVEMENT,
            description="测试目标",
        )

        result = goal_system.start_goal(goal.goal_id)

        assert result is True
        assert goal.status == GoalStatus.IN_PROGRESS
        assert goal.started_at > 0

    def test_update_progress(self, goal_system):
        """测试更新进度"""
        goal = goal_system.create_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="测试目标",
        )

        result = goal_system.update_progress(
            goal.goal_id,
            progress=0.6,
            milestone="完成60%",
        )

        assert result is True
        assert goal.progress == 0.6
        assert len(goal.milestones) == 1

    def test_complete_goal(self, goal_system):
        """测试完成目标"""
        goal = goal_system.create_goal(
            goal_type=GoalType.USER_SATISFACTION,
            description="测试目标",
        )
        goal_system.activate_goal(goal.goal_id)
        goal_system.start_goal(goal.goal_id)

        result = goal_system.complete_goal(
            goal.goal_id,
            outcome=GoalOutcome.ACHIEVED,
            lessons=["保持良好"],
        )

        assert result is True
        assert goal.status == GoalStatus.COMPLETED
        assert goal.outcome == GoalOutcome.ACHIEVED
        assert goal.goal_id not in goal_system._active_goals

    def test_abandon_goal(self, goal_system):
        """测试放弃目标"""
        goal = goal_system.create_goal(
            goal_type=GoalType.RELATIONSHIP,
            description="测试目标",
        )
        goal_system.activate_goal(goal.goal_id)

        result = goal_system.abandon_goal(goal.goal_id, reason="不再需要")

        assert result is True
        assert goal.status == GoalStatus.ABANDONED
        assert goal.goal_id not in goal_system._active_goals

    def test_get_goal(self, goal_system):
        """测试获取目标"""
        goal = goal_system.create_goal(
            goal_type=GoalType.KNOWLEDGE,
            description="测试目标",
        )

        retrieved = goal_system.get_goal(goal.goal_id)

        assert retrieved is goal

    def test_get_active_goals(self, goal_system):
        """测试获取活动目标"""
        goal1 = goal_system.create_goal(
            goal_type=GoalType.BEHAVIOR,
            description="目标1",
        )
        goal2 = goal_system.create_goal(
            goal_type=GoalType.BEHAVIOR,
            description="目标2",
        )

        goal_system.activate_goal(goal1.goal_id)

        active = goal_system.get_active_goals()

        assert len(active) == 1
        assert active[0].goal_id == goal1.goal_id

    def test_get_goals_by_type(self, goal_system):
        """测试按类型获取目标"""
        goal_system.create_goal(GoalType.RELATIONSHIP, "关系目标")
        goal_system.create_goal(GoalType.KNOWLEDGE, "知识目标")
        goal_system.create_goal(GoalType.RELATIONSHIP, "关系目标2")

        relationship_goals = goal_system.get_goals_by_type(GoalType.RELATIONSHIP)

        assert len(relationship_goals) == 2

    def test_get_goals_by_user(self, goal_system):
        """测试按用户获取目标"""
        goal_system.create_goal(GoalType.RELATIONSHIP, "目标1", user_id="user_001")
        goal_system.create_goal(GoalType.RELATIONSHIP, "目标2", user_id="user_002")
        goal_system.create_goal(GoalType.RELATIONSHIP, "目标3", user_id="user_001")

        user_goals = goal_system.get_goals_by_user("user_001")

        assert len(user_goals) == 2

    def test_get_goal_stats(self, goal_system):
        """测试获取目标统计"""
        goal1 = goal_system.create_goal(GoalType.RELATIONSHIP, "目标1")
        goal2 = goal_system.create_goal(GoalType.RELATIONSHIP, "目标2")

        goal_system.activate_goal(goal1.goal_id)
        goal_system.complete_goal(goal1.goal_id, GoalOutcome.ACHIEVED)

        stats = goal_system.get_goal_stats()

        assert stats["total_goals"] == 2
        assert stats["active_goals"] == 0
        assert stats["completed_goals"] == 1
        assert stats["completion_rate"] == 0.5


# ==========================================
# Test: Goal Memory Integration
# ==========================================

class TestGoalMemoryIntegration:
    """测试目标记忆集成"""

    def test_goal_memory_updated_on_completion(self, goal_system):
        """测试完成目标时更新记忆"""
        goal = goal_system.create_goal(
            goal_type=GoalType.RELATIONSHIP,
            description="提升用户满意度",
        )
        goal_system.start_goal(goal.goal_id)
        goal_system.complete_goal(
            goal.goal_id,
            outcome=GoalOutcome.ACHIEVED,
            lessons=["定时问候效果更好"],
        )

        memory = goal_system.get_goal_memory(GoalType.RELATIONSHIP)

        assert memory is not None
        assert memory.total_attempts == 1
        assert memory.successful_attempts == 1
        assert "定时问候效果更好" in memory.lessons_learned

    def test_goal_memory_multiple_attempts(self, goal_system):
        """测试多次尝试"""
        # 第一次尝试失败
        goal1 = goal_system.create_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="主动关怀",
        )
        goal_system.start_goal(goal1.goal_id)
        goal_system.complete_goal(goal1.goal_id, GoalOutcome.FAILED, ["过度主动"])

        # 第二次尝试成功
        goal2 = goal_system.create_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="主动关怀",
        )
        goal_system.start_goal(goal2.goal_id)
        goal_system.complete_goal(goal2.goal_id, GoalOutcome.ACHIEVED, ["控制频率"])

        memory = goal_system.get_goal_memory(GoalType.PROACTIVE_CARE)

        assert memory.total_attempts == 2
        assert memory.successful_attempts == 1
        assert memory.success_rate == 0.5
        assert len(memory.lessons_learned) == 2
        assert len(memory.pitfalls) == 1  # 第一次失败
        assert len(memory.best_practices) == 1  # 第二次成功

    def test_get_lessons_for_type(self, goal_system):
        """测试获取经验教训"""
        goal = goal_system.create_goal(
            goal_type=GoalType.USER_SATISFACTION,
            description="提升满意度",
        )
        goal_system.start_goal(goal.goal_id)
        goal_system.complete_goal(
            goal.goal_id,
            outcome=GoalOutcome.ACHIEVED,
            lessons=["及时响应很重要"],
        )

        lessons = goal_system.get_lessons_for_type(GoalType.USER_SATISFACTION)

        assert "及时响应很重要" in lessons


# ==========================================
# Test: Suggest Goal
# ==========================================

class TestSuggestGoal:
    """测试建议目标"""

    def test_suggest_goal_creates_new(self, goal_system):
        """测试建议创建新目标"""
        goal = goal_system.suggest_goal(
            goal_type=GoalType.RELATIONSHIP,
            description="建立长期关系",
            priority=7,
            user_id="user_001",
        )

        assert goal is not None
        assert goal.goal_type == GoalType.RELATIONSHIP

    def test_suggest_goal_skips_similar(self, goal_system):
        """测试跳过相似目标"""
        # 创建一个活动目标
        goal1 = goal_system.create_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="主动关怀",
            user_id="user_001",
        )
        goal_system.activate_goal(goal1.goal_id)

        # 尝试建议相似目标
        goal2 = goal_system.suggest_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="主动关怀",
            priority=8,
            user_id="user_001",
        )

        assert goal2 is None  # 跳过

    def test_adjust_priority_by_type(self, goal_system):
        """测试调整优先级"""
        goal = goal_system.create_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="主动关怀",
            priority=5,
        )
        goal_system.activate_goal(goal.goal_id)

        goal_system.adjust_priority_by_type(GoalType.PROACTIVE_CARE, boost=3)

        assert goal.priority == 8


# ==========================================
# Test: GoalLayer Event Handling
# ==========================================

class TestGoalLayerEventHandling:
    """测试目标层事件处理"""

    def test_on_user_input_creates_goal(self, goal_system):
        """测试用户输入触发目标创建"""
        # 模拟用户表达不满
        event = CognitiveEvent(
            type="user.input",
            data={"content": "我对你的表现不满意"},
            user_id="user_001",
        )

        goal_system._goal_layer.on_user_input(event)

        # 检查是否创建了满意度目标
        goals = goal_system.get_goals_by_type(GoalType.USER_SATISFACTION)
        assert len(goals) == 1

    def test_on_user_emotion_adjusts_priority(self, goal_system):
        """测试用户情绪调整优先级"""
        goal = goal_system.create_goal(
            goal_type=GoalType.PROACTIVE_CARE,
            description="主动关怀",
            priority=5,
        )
        goal_system.activate_goal(goal.goal_id)

        # 模拟用户情绪低落
        event = CognitiveEvent(
            type="user.emotion_detected",
            data={"emotion": "tired"},
        )

        goal_system._goal_layer.on_user_emotion_detected(event)

        assert goal.priority == 7  # +2

    def test_on_relationship_evolved_creates_goal(self, goal_system):
        """测试关系进化创建目标"""
        event = CognitiveEvent(
            type="relationship.evolved",
            data={"current_level": "partner"},
            user_id="user_001",
        )

        goal_system._goal_layer.on_relationship_evolved(event)

        goals = goal_system.get_goals_by_type(GoalType.RELATIONSHIP)
        assert len(goals) == 1


# ==========================================
# Test: Convenience Functions
# ==========================================

class TestConvenienceFunctions:
    """测试便捷函数"""

    def test_get_goal_system(self):
        """测试获取目标系统"""
        system1 = get_goal_system()
        system2 = get_goal_system()

        assert system1 is system2


# ==========================================
# 运行测试
# ==========================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])