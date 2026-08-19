"""
Goal System Package

羽依自主目标系统

使用方式：
    from src.goal import GoalSystem, GoalType, GoalOutcome

    goal_system = GoalSystem()
    goal_system.start()

    # 创建目标
    goal = goal_system.create_goal(
        goal_type=GoalType.RELATIONSHIP,
        description="提升用户满意度",
        priority=8,
    )

    # 更新进度
    goal_system.update_progress(goal.goal_id, 0.5)

    # 完成目标
    goal_system.complete_goal(
        goal.goal_id,
        outcome=GoalOutcome.ACHIEVED,
        lessons=["避免过度主动"],
    )
"""

from src.goal.goal_system import (
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

__all__ = [
    "Goal",
    "GoalAttempt",
    "GoalMemory",
    "GoalOutcome",
    "GoalStatus",
    "GoalSystem",
    "GoalType",
    "GoalLayer",
    "get_goal_system",
]