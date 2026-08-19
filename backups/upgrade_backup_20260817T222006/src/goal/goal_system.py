"""
Autonomous Goal System

羽依自主目标系统

设计目标：
- 羽依可以自主创建、追踪、完成目标
- 目标有优先级、进度、依赖关系
- 目标完成后形成经验，用于优化未来目标
- 支持短期目标和长期目标

目标生命周期：
Created → Active → InProgress → Completed/Abandoned

Goal Memory：
目标 → 尝试 → 结果 → 经验 → 下次优化

使用方式：
    from src.goal.goal_system import GoalSystem

    goal_system = GoalSystem()
    goal_system.start()

    # 创建目标
    goal_system.create_goal(
        goal_type="relationship",
        description="提升用户满意度",
        priority=8,
    )

    # 更新进度
    goal_system.update_progress("goal_001", 0.5, milestone="完成初次主动关怀")

    # 完成目标
    goal_system.complete_goal("goal_001", outcome="achieved", lessons=["避免过度主动"])
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from src.core.yuyi_cognitive_core import (
    CognitiveLayerBase,
    get_cognitive_core,
)
from src.contracts.cognitive_event_types import (
    GoalCreatedEvent,
    GoalProgressEvent,
    GoalCompletedEvent,
    CognitiveEvent,
)

logger = logging.getLogger(__name__)


class GoalStatus(Enum):
    """目标状态"""
    CREATED = "created"           # 新创建
    ACTIVE = "active"             # 激活中
    IN_PROGRESS = "in_progress"   # 执行中
    PAUSED = "paused"             # 暂停
    COMPLETED = "completed"       # 已完成
    ABANDONED = "abandoned"       # 已放弃
    FAILED = "failed"             # 失败


class GoalType(Enum):
    """目标类型"""
    RELATIONSHIP = "relationship"  # 关系提升
    KNOWLEDGE = "knowledge"        # 知识增长
    BEHAVIOR = "behavior"          # 行为优化
    MEMORY = "memory"              # 记忆积累
    SELF_IMPROVEMENT = "self_improvement"  # 自我提升
    USER_SATISFACTION = "user_satisfaction"  # 用户满意度
    PROACTIVE_CARE = "proactive_care"  # 主动关怀


class GoalOutcome(Enum):
    """目标结果"""
    ACHIEVED = "achieved"      # 成功达成
    ABANDONED = "abandoned"    # 主动放弃
    FAILED = "failed"          # 执行失败
    SUPERSEDED = "superseded"  # 被新目标取代


@dataclass
class GoalAttempt:
    """目标尝试记录"""
    attempt_id: str
    goal_id: str
    started_at: float
    ended_at: float = 0.0
    actions_taken: List[str] = field(default_factory=list)
    result: Optional[str] = None
    success: bool = False
    notes: str = ""


@dataclass
class GoalMemory:
    """目标记忆 - 经验累积"""
    goal_type: str
    goal_description: str

    # 尝试历史
    attempts: List[GoalAttempt] = field(default_factory=list)

    # 经验总结
    lessons_learned: List[str] = field(default_factory=list)
    best_practices: List[str] = field(default_factory=list)
    pitfalls: List[str] = field(default_factory=list)

    # 成功率
    success_rate: float = 0.0
    total_attempts: int = 0
    successful_attempts: int = 0

    # 时间统计
    avg_completion_time: float = 0.0

    def record_attempt(self, attempt: GoalAttempt):
        """记录一次尝试"""
        self.attempts.append(attempt)
        self.total_attempts += 1
        if attempt.success:
            self.successful_attempts += 1
        self._update_stats()

    def _update_stats(self):
        """更新统计信息"""
        if self.total_attempts > 0:
            self.success_rate = self.successful_attempts / self.total_attempts

        # 计算平均完成时间
        completed = [a for a in self.attempts if a.success and a.ended_at > 0]
        if completed:
            times = [a.ended_at - a.started_at for a in completed]
            self.avg_completion_time = sum(times) / len(times)

    def add_lesson(self, lesson: str):
        """添加经验教训"""
        if lesson not in self.lessons_learned:
            self.lessons_learned.append(lesson)

    def add_best_practice(self, practice: str):
        """添加最佳实践"""
        if practice not in self.best_practices:
            self.best_practices.append(practice)

    def add_pitfall(self, pitfall: str):
        """添加陷阱警告"""
        if pitfall not in self.pitfalls:
            self.pitfalls.append(pitfall)


@dataclass
class Goal:
    """自主目标"""
    goal_id: str
    goal_type: GoalType
    description: str

    # 状态
    status: GoalStatus = GoalStatus.CREATED
    priority: int = 5  # 1-10, 10 最高
    progress: float = 0.0  # 0.0 - 1.0

    # 时间
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    deadline: Optional[float] = None

    # 用户关联
    user_id: Optional[str] = None

    # 依赖关系
    depends_on: List[str] = field(default_factory=list)
    blocks: List[str] = field(default_factory=list)

    # 进度里程碑
    milestones: List[Dict[str, Any]] = field(default_factory=list)

    # 尝试历史
    attempts: List[GoalAttempt] = field(default_factory=list)
    current_attempt: Optional[GoalAttempt] = None

    # 结果
    outcome: Optional[GoalOutcome] = None
    lessons_learned: List[str] = field(default_factory=list)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.goal_id:
            self.goal_id = f"goal_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()

    def start(self):
        """开始执行目标"""
        self.status = GoalStatus.IN_PROGRESS
        self.started_at = time.time()
        self.current_attempt = GoalAttempt(
            attempt_id=f"att_{uuid.uuid4().hex[:8]}",
            goal_id=self.goal_id,
            started_at=time.time(),
        )

    def update_progress(self, progress: float, milestone: Optional[str] = None):
        """更新进度"""
        self.progress = max(0.0, min(1.0, progress))
        if milestone:
            self.milestones.append({
                "description": milestone,
                "progress": progress,
                "timestamp": time.time(),
            })

    def complete(self, outcome: GoalOutcome, lessons: List[str] = None):
        """完成目标"""
        self.status = GoalStatus.COMPLETED
        self.outcome = outcome
        self.completed_at = time.time()
        self.progress = 1.0 if outcome == GoalOutcome.ACHIEVED else self.progress
        self.lessons_learned = lessons or []

        # 结束当前尝试
        if self.current_attempt:
            self.current_attempt.ended_at = time.time()
            self.current_attempt.success = (outcome == GoalOutcome.ACHIEVED)
            self.current_attempt.result = outcome.value
            self.attempts.append(self.current_attempt)

    def abandon(self, reason: str = ""):
        """放弃目标"""
        self.status = GoalStatus.ABANDONED
        self.outcome = GoalOutcome.ABANDONED
        self.completed_at = time.time()

        if self.current_attempt:
            self.current_attempt.ended_at = time.time()
            self.current_attempt.success = False
            self.current_attempt.result = "abandoned"
            self.current_attempt.notes = reason
            self.attempts.append(self.current_attempt)

    def to_dict(self) -> Dict[str, Any]:
        """序列化"""
        return {
            "goal_id": self.goal_id,
            "goal_type": self.goal_type.value,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority,
            "progress": self.progress,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "deadline": self.deadline,
            "user_id": self.user_id,
            "depends_on": self.depends_on,
            "blocks": self.blocks,
            "milestones": self.milestones,
            "outcome": self.outcome.value if self.outcome else None,
            "lessons_learned": self.lessons_learned,
            "metadata": self.metadata,
        }


class GoalLayer(CognitiveLayerBase):
    """
    目标认知层

    集成到 YuyiCognitiveCore，处理目标相关事件
    """

    def __init__(self, goal_system: "GoalSystem"):
        super().__init__("goal")
        self._goal_system = goal_system

    def on_user_input(self, event: CognitiveEvent):
        """用户输入可能触发新目标"""
        # 分析是否需要创建新目标
        content = event.data.get("content", "").lower()

        # 示例：用户表达不满时，创建满意度提升目标
        if any(kw in content for kw in ["不满意", "失望", "不好"]):
            self._goal_system.suggest_goal(
                goal_type=GoalType.USER_SATISFACTION,
                description="用户表达不满，需要改善服务质量",
                priority=8,
                user_id=event.user_id,
                trigger_event=event.id,
            )

    def on_user_emotion_detected(self, event: CognitiveEvent):
        """用户情绪可能影响目标优先级"""
        emotion = event.data.get("emotion", "")

        # 用户情绪低落时，提升关怀目标优先级
        if emotion in ["tired", "sad", "stressed"]:
            self._goal_system.adjust_priority_by_type(
                goal_type=GoalType.PROACTIVE_CARE,
                boost=2,
            )

    def on_emotion_state_changed(self, event: CognitiveEvent):
        """羽依情绪变化可能影响目标执行"""
        pass  # 可根据情绪调整目标执行策略

    def on_relationship_evolved(self, event: CognitiveEvent):
        """关系进化可能触发新目标"""
        current_level = event.data.get("current_level", "")

        # 关系升级时，创建新目标
        if current_level == "partner":
            self._goal_system.suggest_goal(
                goal_type=GoalType.RELATIONSHIP,
                description="建立长期伙伴关系",
                priority=7,
                user_id=event.user_id,
            )

    def on_growth_applied(self, event: CognitiveEvent):
        """成长应用可能影响目标"""
        pass  # 可根据成长调整目标策略


class GoalSystem:
    """
    自主目标系统

    管理羽依的自主目标生命周期
    """

    def __init__(self):
        # 目标存储
        self._goals: Dict[str, Goal] = {}
        self._active_goals: List[str] = []

        # Goal Memory 存储
        self._goal_memories: Dict[str, GoalMemory] = {}

        # 认知核心
        self._cognitive_core = None

        # 目标层
        self._goal_layer: Optional[GoalLayer] = None

        # 状态
        self._running = False
        self._lock = threading.Lock()

        logger.info("GoalSystem initialized")

    def start(self) -> bool:
        """启动目标系统"""
        if self._running:
            return True

        logger.info("Starting GoalSystem")

        try:
            # 获取认知核心
            self._cognitive_core = get_cognitive_core()
            if not self._cognitive_core.is_running():
                self._cognitive_core.start()

            # 创建并注册目标层
            self._goal_layer = GoalLayer(self)
            self._cognitive_core.register_layer("goal", self._goal_layer)

            # 标记层状态
            self._goal_layer._status = "running"
            self._goal_layer.update_digest("active_count", 0)

            self._running = True
            logger.info("GoalSystem started successfully")
            return True

        except Exception as e:
            logger.error(f"GoalSystem start failed: {e}")
            return False

    def stop(self) -> bool:
        """停止目标系统"""
        if not self._running:
            return True

        self._running = False
        logger.info("GoalSystem stopped")
        return True

    # ==========================================
    # 目标创建
    # ==========================================

    def create_goal(
        self,
        goal_type: GoalType,
        description: str,
        priority: int = 5,
        user_id: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        deadline: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ) -> Goal:
        """
        创建新目标

        Args:
            goal_type: 目标类型
            description: 目标描述
            priority: 优先级（1-10）
            user_id: 关联用户
            depends_on: 依赖的目标 ID
            deadline: 截止时间戳
            metadata: 元数据

        Returns:
            创建的目标对象
        """
        with self._lock:
            goal = Goal(
                goal_id=f"goal_{uuid.uuid4().hex[:12]}",
                goal_type=goal_type,
                description=description,
                priority=max(1, min(10, priority)),
                user_id=user_id,
                depends_on=depends_on or [],
                deadline=deadline,
                metadata=metadata or {},
            )

            self._goals[goal.goal_id] = goal

            # 发布事件
            if self._cognitive_core:
                self._cognitive_core.emit_event(
                    event_type="goal.created",
                    data={
                        "goal_id": goal.goal_id,
                        "goal_type": goal.goal_type.value,
                        "description": description,
                        "priority": goal.priority,
                    },
                    user_id=user_id,
                    source="goal_system",
                )

            logger.info(f"Goal created: {goal.goal_id} - {description}")
            return goal

    def suggest_goal(
        self,
        goal_type: GoalType,
        description: str,
        priority: int,
        user_id: Optional[str] = None,
        trigger_event: Optional[str] = None,
    ) -> Optional[Goal]:
        """
        建议目标（由系统自动触发）

        Args:
            goal_type: 目标类型
            description: 描述
            priority: 优先级
            user_id: 用户 ID
            trigger_event: 触发事件 ID

        Returns:
            创建的目标（如果通过检查）
        """
        # 检查是否已有类似目标
        similar = self._find_similar_active_goal(goal_type, user_id)
        if similar:
            logger.debug(f"Similar goal exists: {similar.goal_id}, skipping suggestion")
            return None

        return self.create_goal(
            goal_type=goal_type,
            description=description,
            priority=priority,
            user_id=user_id,
            metadata={"trigger_event": trigger_event} if trigger_event else None,
        )

    # ==========================================
    # 目标执行
    # ==========================================

    def activate_goal(self, goal_id: str) -> bool:
        """激活目标"""
        goal = self._goals.get(goal_id)
        if not goal:
            logger.warning(f"Goal not found: {goal_id}")
            return False

        # 检查依赖
        for dep_id in goal.depends_on:
            dep = self._goals.get(dep_id)
            if dep and dep.status != GoalStatus.COMPLETED:
                logger.debug(f"Goal {goal_id} depends on incomplete goal {dep_id}")
                return False

        goal.status = GoalStatus.ACTIVE
        self._active_goals.append(goal_id)

        logger.info(f"Goal activated: {goal_id}")
        return True

    def start_goal(self, goal_id: str) -> bool:
        """开始执行目标"""
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        goal.start()

        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="goal.progress",
                data={
                    "goal_id": goal_id,
                    "progress": 0.0,
                    "milestone": "开始执行",
                },
                user_id=goal.user_id,
                source="goal_system",
            )

        logger.info(f"Goal started: {goal_id}")
        return True

    def update_progress(
        self,
        goal_id: str,
        progress: float,
        milestone: Optional[str] = None,
    ) -> bool:
        """更新目标进度"""
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        goal.update_progress(progress, milestone)

        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="goal.progress",
                data={
                    "goal_id": goal_id,
                    "progress": progress,
                    "milestone": milestone,
                },
                user_id=goal.user_id,
                source="goal_system",
            )

        # 更新层摘要
        if self._goal_layer:
            self._goal_layer.update_digest("last_progress_update", time.time())

        return True

    def complete_goal(
        self,
        goal_id: str,
        outcome: GoalOutcome,
        lessons: Optional[List[str]] = None,
    ) -> bool:
        """
        完成目标

        Args:
            goal_id: 目标 ID
            outcome: 结果
            lessons: 经验教训

        Returns:
            是否成功
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        goal.complete(outcome, lessons)

        # 从活动列表移除
        if goal_id in self._active_goals:
            self._active_goals.remove(goal_id)

        # 更新 Goal Memory
        self._update_goal_memory(goal)

        # 发布事件
        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="goal.completed",
                data={
                    "goal_id": goal_id,
                    "outcome": outcome.value,
                    "lessons_learned": lessons or [],
                },
                user_id=goal.user_id,
                source="goal_system",
            )

        logger.info(f"Goal completed: {goal_id} ({outcome.value})")
        return True

    def abandon_goal(self, goal_id: str, reason: str = "") -> bool:
        """放弃目标"""
        goal = self._goals.get(goal_id)
        if not goal:
            return False

        goal.abandon(reason)

        if goal_id in self._active_goals:
            self._active_goals.remove(goal_id)

        # 更新 Goal Memory
        self._update_goal_memory(goal)

        logger.info(f"Goal abandoned: {goal_id} - {reason}")
        return True

    # ==========================================
    # Goal Memory
    # ==========================================

    def _update_goal_memory(self, goal: Goal):
        """更新目标记忆"""
        memory_key = goal.goal_type.value

        if memory_key not in self._goal_memories:
            self._goal_memories[memory_key] = GoalMemory(
                goal_type=goal.goal_type.value,
                goal_description=goal.description,
            )

        memory = self._goal_memories[memory_key]

        # 记录尝试
        for attempt in goal.attempts:
            memory.record_attempt(attempt)

        # 添加经验
        for lesson in goal.lessons_learned:
            memory.add_lesson(lesson)

        # 根据结果添加最佳实践或陷阱
        if goal.outcome == GoalOutcome.ACHIEVED:
            memory.add_best_practice(f"{goal.description}: {', '.join(goal.lessons_learned)}")
        else:
            memory.add_pitfall(f"{goal.description}: {', '.join(goal.lessons_learned)}")

        logger.debug(f"Updated goal memory for {memory_key}: success_rate={memory.success_rate:.2f}")

    def get_goal_memory(self, goal_type: GoalType) -> Optional[GoalMemory]:
        """获取目标记忆"""
        return self._goal_memories.get(goal_type.value)

    def get_lessons_for_type(self, goal_type: GoalType) -> List[str]:
        """获取某类型目标的经验教训"""
        memory = self._goal_memories.get(goal_type.value)
        return memory.lessons_learned if memory else []

    # ==========================================
    # 查询
    # ==========================================

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        """获取目标"""
        return self._goals.get(goal_id)

    def get_active_goals(self) -> List[Goal]:
        """获取所有活动目标"""
        return [self._goals[gid] for gid in self._active_goals if gid in self._goals]

    def get_goals_by_type(self, goal_type: GoalType) -> List[Goal]:
        """按类型获取目标"""
        return [g for g in self._goals.values() if g.goal_type == goal_type]

    def get_goals_by_user(self, user_id: str) -> List[Goal]:
        """按用户获取目标"""
        return [g for g in self._goals.values() if g.user_id == user_id]

    def get_goal_stats(self) -> Dict[str, Any]:
        """获取目标统计"""
        total = len(self._goals)
        active = len(self._active_goals)
        completed = sum(1 for g in self._goals.values() if g.status == GoalStatus.COMPLETED)
        abandoned = sum(1 for g in self._goals.values() if g.status == GoalStatus.ABANDONED)

        return {
            "total_goals": total,
            "active_goals": active,
            "completed_goals": completed,
            "abandoned_goals": abandoned,
            "completion_rate": completed / total if total > 0 else 0,
        }

    # ==========================================
    # 辅助方法
    # ==========================================

    def _find_similar_active_goal(
        self,
        goal_type: GoalType,
        user_id: Optional[str] = None,
    ) -> Optional[Goal]:
        """查找相似的活动目标"""
        for goal in self.get_active_goals():
            if goal.goal_type == goal_type:
                if user_id is None or goal.user_id == user_id:
                    return goal
        return None

    def adjust_priority_by_type(self, goal_type: GoalType, boost: int):
        """调整某类型目标的优先级"""
        for goal in self.get_active_goals():
            if goal.goal_type == goal_type:
                goal.priority = max(1, min(10, goal.priority + boost))
                logger.debug(f"Adjusted priority for {goal.goal_id}: {goal.priority}")


# ==========================================
# 便捷函数
# ==========================================

_goal_system_instance: Optional[GoalSystem] = None


def get_goal_system() -> GoalSystem:
    """获取目标系统单例"""
    global _goal_system_instance
    if _goal_system_instance is None:
        _goal_system_instance = GoalSystem()
    return _goal_system_instance