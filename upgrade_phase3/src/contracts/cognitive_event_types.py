"""
Cognitive Event Types

Phase 3 事件类型定义

设计原则:
- 所有事件使用语义化命名 (user.*, yuyi.*, system.*)
- 事件携带完整上下文，支持回溯
- 支持事件溯源 (Event Sourcing)

事件流:
User Input -> Memory Record -> Emotion Update -> Goal Adjust -> Action -> Reflection -> Growth

使用方式:
    from src.contracts.cognitive_event_types import (
        UserEmotionDetectedEvent,
        MemoryRecordedEvent,
        ...
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class EventPriority(Enum):
    """事件优先级"""
    LOW = 1       # 日志类事件
    NORMAL = 5    # 普通事件
    HIGH = 8      # 状态变化事件
    CRITICAL = 10 # 系统关键事件


@dataclass
class CognitiveEvent:
    """认知事件基类"""
    id: str = field(default_factory=lambda: f"ce_{uuid.uuid4().hex[:12]}")
    type: str = "cognitive.base"
    timestamp: str = field(default_factory=now_iso)
    source: str = ""  # 事件来源模块
    priority: EventPriority = EventPriority.NORMAL

    # 事件数据
    data: Dict[str, Any] = field(default_factory=dict)

    # 关联信息
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    related_event_ids: List[str] = field(default_factory=list)

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "timestamp": self.timestamp,
            "source": self.source,
            "priority": self.priority.value,
            "data": self.data,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "related_event_ids": self.related_event_ids,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CognitiveEvent":
        return cls(
            id=data.get("id", f"ce_{uuid.uuid4().hex[:12]}"),
            type=data.get("type", "cognitive.base"),
            timestamp=data.get("timestamp", now_iso()),
            source=data.get("source", ""),
            priority=EventPriority(data.get("priority", 5)),
            data=data.get("data", {}),
            user_id=data.get("user_id"),
            session_id=data.get("session_id"),
            related_event_ids=data.get("related_event_ids", []),
            metadata=data.get("metadata", {}),
        )


# ==========================================
# User Domain Events (用户域事件)
# ==========================================

@dataclass
class UserInputEvent(CognitiveEvent):
    """
    用户输入事件

    触发时机: 用户发送消息
    影响: Memory 记录 -> Emotion 处理 -> Goal 更新
    """
    type: str = "user.input"

    @classmethod
    def create(
        cls,
        content: str,
        user_id: str,
        session_id: Optional[str] = None,
        source: str = "conversation",
        metadata: Optional[Dict] = None,
    ) -> "UserInputEvent":
        return cls(
            type="user.input",
            source=source,
            user_id=user_id,
            session_id=session_id,
            data={"content": content},
            metadata=metadata or {},
        )


@dataclass
class UserEmotionDetectedEvent(CognitiveEvent):
    """
    用户情绪检测事件

    触发时机: 从用户输入中检测到情绪特征
    影响: Emotion 调整 -> Relationship 更新 -> Goal 调整
    """
    type: str = "user.emotion_detected"

    @classmethod
    def create(
        cls,
        emotion: str,
        confidence: float,
        user_id: str,
        source_event_id: str,
        indicators: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> "UserEmotionDetectedEvent":
        return cls(
            type="user.emotion_detected",
            source="emotion_detector",
            user_id=user_id,
            related_event_ids=[source_event_id],
            data={
                "emotion": emotion,
                "confidence": confidence,
                "indicators": indicators or [],
            },
            metadata=metadata or {},
        )


@dataclass
class UserPatternDetectedEvent(CognitiveEvent):
    """
    用户行为模式检测事件

    触发时机: 检测到用户的行为模式（如交流频率、话题偏好）
    影响: Relationship 策略调整 -> Proactive 行为决策
    """
    type: str = "user.pattern_detected"

    @classmethod
    def create(
        cls,
        pattern_type: str,
        pattern_data: Dict[str, Any],
        user_id: str,
        confidence: float,
        metadata: Optional[Dict] = None,
    ) -> "UserPatternDetectedEvent":
        return cls(
            type="user.pattern_detected",
            source="pattern_analyzer",
            user_id=user_id,
            data={
                "pattern_type": pattern_type,
                "pattern_data": pattern_data,
                "confidence": confidence,
            },
            metadata=metadata or {},
        )


# ==========================================
# Memory Domain Events (记忆域事件)
# ==========================================

@dataclass
class MemoryRecordedEvent(CognitiveEvent):
    """
    记忆记录事件

    触发时机: 新记忆被创建
    影响: Relationship 更新 -> Growth 累积
    """
    type: str = "memory.recorded"

    @classmethod
    def create(
        cls,
        memory_id: str,
        memory_type: str,
        content_summary: str,
        user_id: str,
        importance: float = 0.5,
        metadata: Optional[Dict] = None,
    ) -> "MemoryRecordedEvent":
        return cls(
            type="memory.recorded",
            source="memory_system",
            user_id=user_id,
            data={
                "memory_id": memory_id,
                "memory_type": memory_type,
                "content_summary": content_summary,
                "importance": importance,
            },
            metadata=metadata or {},
        )


@dataclass
class MemoryAccessedEvent(CognitiveEvent):
    """
    记忆访问事件

    触发时机: 记忆被检索使用
    影响: Memory 重要性调整
    """
    type: str = "memory.accessed"

    @classmethod
    def create(
        cls,
        memory_id: str,
        access_context: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "MemoryAccessedEvent":
        return cls(
            type="memory.accessed",
            source="memory_system",
            user_id=user_id,
            data={
                "memory_id": memory_id,
                "access_context": access_context,
            },
            metadata=metadata or {},
        )


# ==========================================
# Emotion Domain Events (情绪域事件)
# ==========================================

@dataclass
class EmotionStateChangedEvent(CognitiveEvent):
    """
    情绪状态变化事件

    触发时机: 羽依情绪状态发生显著变化
    影响: Personality 调整 -> Action 选择 -> UI 反馈
    """
    type: str = "emotion.state_changed"

    @classmethod
    def create(
        cls,
        previous_emotion: str,
        current_emotion: str,
        intensity: float,
        trigger_reason: str,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "EmotionStateChangedEvent":
        return cls(
            type="emotion.state_changed",
            source="emotion_engine",
            user_id=user_id,
            data={
                "previous_emotion": previous_emotion,
                "current_emotion": current_emotion,
                "intensity": intensity,
                "trigger_reason": trigger_reason,
            },
            priority=EventPriority.HIGH,
            metadata=metadata or {},
        )


# ==========================================
# Goal Domain Events (目标域事件)
# ==========================================

@dataclass
class GoalCreatedEvent(CognitiveEvent):
    """
    目标创建事件

    触发时机: 羽依创建新的自主目标
    影响: Action 规划 -> Resource 分配
    """
    type: str = "goal.created"

    @classmethod
    def create(
        cls,
        goal_id: str,
        goal_type: str,
        description: str,
        priority: int,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "GoalCreatedEvent":
        return cls(
            type="goal.created",
            source="goal_system",
            user_id=user_id,
            data={
                "goal_id": goal_id,
                "goal_type": goal_type,
                "description": description,
                "priority": priority,
            },
            metadata=metadata or {},
        )


@dataclass
class GoalProgressEvent(CognitiveEvent):
    """
    目标进度事件

    触发时机: 目标进度更新
    影响: Reflection 素材 -> Growth 评估
    """
    type: str = "goal.progress"

    @classmethod
    def create(
        cls,
        goal_id: str,
        progress: float,
        milestone: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "GoalProgressEvent":
        return cls(
            type="goal.progress",
            source="goal_system",
            user_id=user_id,
            data={
                "goal_id": goal_id,
                "progress": progress,
                "milestone": milestone,
            },
            metadata=metadata or {},
        )


@dataclass
class GoalCompletedEvent(CognitiveEvent):
    """
    目标完成事件

    触发时机: 目标达成或放弃
    影响: Reflection 触发 -> Growth 累积 -> 新 Goal 生成
    """
    type: str = "goal.completed"

    @classmethod
    def create(
        cls,
        goal_id: str,
        outcome: str,  # "achieved" | "abandoned" | "failed"
        lessons_learned: List[str],
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "GoalCompletedEvent":
        return cls(
            type="goal.completed",
            source="goal_system",
            user_id=user_id,
            data={
                "goal_id": goal_id,
                "outcome": outcome,
                "lessons_learned": lessons_learned,
            },
            priority=EventPriority.HIGH,
            metadata=metadata or {},
        )


# ==========================================
# Relationship Domain Events (关系域事件)
# ==========================================

@dataclass
class RelationshipEvolvedEvent(CognitiveEvent):
    """
    关系进化事件

    触发时机: 关系等级或质量发生变化
    影响: Personality 调整 -> Goal 重新优先级
    """
    type: str = "relationship.evolved"

    @classmethod
    def create(
        cls,
        user_id: str,
        previous_level: str,
        current_level: str,
        trust_delta: float,
        reason: str,
        metadata: Optional[Dict] = None,
    ) -> "RelationshipEvolvedEvent":
        return cls(
            type="relationship.evolved",
            source="relationship_manager",
            user_id=user_id,
            data={
                "previous_level": previous_level,
                "current_level": current_level,
                "trust_delta": trust_delta,
                "reason": reason,
            },
            priority=EventPriority.HIGH,
            metadata=metadata or {},
        )


# ==========================================
# Action Domain Events (行动域事件)
# ==========================================

@dataclass
class ProactiveActionProposedEvent(CognitiveEvent):
    """
    主动行为提议事件

    触发时机: Proactive Engine 提出行为候选
    影响: Safety Check -> Confidence Gate -> 执行决策
    """
    type: str = "action.proactive_proposed"

    @classmethod
    def create(
        cls,
        action_id: str,
        action_type: str,
        action_content: str,
        trigger_reason: str,
        user_id: Optional[str] = None,
        confidence: float = 0.5,
        metadata: Optional[Dict] = None,
    ) -> "ProactiveActionProposedEvent":
        return cls(
            type="action.proactive_proposed",
            source="proactive_engine",
            user_id=user_id,
            data={
                "action_id": action_id,
                "action_type": action_type,
                "action_content": action_content,
                "trigger_reason": trigger_reason,
                "confidence": confidence,
            },
            metadata=metadata or {},
        )


@dataclass
class ProactiveActionExecutedEvent(CognitiveEvent):
    """
    主动行为执行事件

    触发时机: 主动行为被执行
    影响: Memory 记录 -> Emotion 反馈 -> Reflection 素材
    """
    type: str = "action.proactive_executed"

    @classmethod
    def create(
        cls,
        action_id: str,
        action_type: str,
        action_content: str,
        user_id: Optional[str] = None,
        user_response: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "ProactiveActionExecutedEvent":
        return cls(
            type="action.proactive_executed",
            source="proactive_engine",
            user_id=user_id,
            data={
                "action_id": action_id,
                "action_type": action_type,
                "action_content": action_content,
                "user_response": user_response,
            },
            metadata=metadata or {},
        )


# ==========================================
# Reflection Domain Events (反思域事件)
# ==========================================

@dataclass
class ReflectionTriggeredEvent(CognitiveEvent):
    """
    反思触发事件

    触发时机: 定期反思或事件触发的反思
    影响: Self Model 更新 -> Growth 提案
    """
    type: str = "reflection.triggered"

    @classmethod
    def create(
        cls,
        reflection_type: str,  # "daily" | "event_based" | "milestone"
        trigger_event_ids: List[str],
        focus_areas: List[str],
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "ReflectionTriggeredEvent":
        return cls(
            type="reflection.triggered",
            source="reflection_engine",
            user_id=user_id,
            related_event_ids=trigger_event_ids,
            data={
                "reflection_type": reflection_type,
                "focus_areas": focus_areas,
            },
            metadata=metadata or {},
        )


@dataclass
class ReflectionCompletedEvent(CognitiveEvent):
    """
    反思完成事件

    触发时机: 反思过程完成
    影响: Growth 提案 -> Personality 微调
    """
    type: str = "reflection.completed"

    @classmethod
    def create(
        cls,
        reflection_id: str,
        insights: List[str],
        improvements: List[str],
        self_model_updates: Dict[str, Any],
        user_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "ReflectionCompletedEvent":
        return cls(
            type="reflection.completed",
            source="reflection_engine",
            user_id=user_id,
            data={
                "reflection_id": reflection_id,
                "insights": insights,
                "improvements": improvements,
                "self_model_updates": self_model_updates,
            },
            priority=EventPriority.HIGH,
            metadata=metadata or {},
        )


# ==========================================
# Growth Domain Events (成长域事件)
# ==========================================

@dataclass
class GrowthProposalCreatedEvent(CognitiveEvent):
    """
    成长提案创建事件

    触发时机: 系统生成新的成长建议
    影响: 用户审核队列 -> Growth Loop
    """
    type: str = "growth.proposal_created"

    @classmethod
    def create(
        cls,
        proposal_id: str,
        proposal_type: str,
        description: str,
        impact_preview: Dict[str, Any],
        user_id: Optional[str] = None,
        requires_approval: bool = True,
        metadata: Optional[Dict] = None,
    ) -> "GrowthProposalCreatedEvent":
        return cls(
            type="growth.proposal_created",
            source="growth_engine",
            user_id=user_id,
            data={
                "proposal_id": proposal_id,
                "proposal_type": proposal_type,
                "description": description,
                "impact_preview": impact_preview,
                "requires_approval": requires_approval,
            },
            priority=EventPriority.HIGH,
            metadata=metadata or {},
        )


@dataclass
class GrowthAppliedEvent(CognitiveEvent):
    """
    成长应用事件

    触发时机: 成长提案被批准并应用
    影响: Personality 更新 -> Goal 调整 -> Self Model 更新
    """
    type: str = "growth.applied"

    @classmethod
    def create(
        cls,
        proposal_id: str,
        changes_applied: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        approved_by: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> "GrowthAppliedEvent":
        return cls(
            type="growth.applied",
            source="growth_engine",
            user_id=user_id,
            data={
                "proposal_id": proposal_id,
                "changes_applied": changes_applied,
                "approved_by": approved_by,
            },
            priority=EventPriority.HIGH,
            metadata=metadata or {},
        )


# ==========================================
# System Domain Events (系统域事件)
# ==========================================

@dataclass
class SystemTickEvent(CognitiveEvent):
    """
    系统时钟事件

    触发时机: 定期触发（如每分钟）
    影响: Reflection 检查 -> Goal 检查 -> Proactive 检查
    """
    type: str = "system.tick"

    @classmethod
    def create(
        cls,
        tick_id: str,
        tick_type: str,  # "minute" | "hour" | "day"
        metadata: Optional[Dict] = None,
    ) -> "SystemTickEvent":
        return cls(
            type="system.tick",
            source="system_scheduler",
            data={
                "tick_id": tick_id,
                "tick_type": tick_type,
            },
            priority=EventPriority.LOW,
            metadata=metadata or {},
        )


# ==========================================
# Event Type Registry
# ==========================================

EVENT_TYPE_REGISTRY = {
    # User Domain
    "user.input": UserInputEvent,
    "user.emotion_detected": UserEmotionDetectedEvent,
    "user.pattern_detected": UserPatternDetectedEvent,

    # Memory Domain
    "memory.recorded": MemoryRecordedEvent,
    "memory.accessed": MemoryAccessedEvent,

    # Emotion Domain
    "emotion.state_changed": EmotionStateChangedEvent,

    # Goal Domain
    "goal.created": GoalCreatedEvent,
    "goal.progress": GoalProgressEvent,
    "goal.completed": GoalCompletedEvent,

    # Relationship Domain
    "relationship.evolved": RelationshipEvolvedEvent,

    # Action Domain
    "action.proactive_proposed": ProactiveActionProposedEvent,
    "action.proactive_executed": ProactiveActionExecutedEvent,

    # Reflection Domain
    "reflection.triggered": ReflectionTriggeredEvent,
    "reflection.completed": ReflectionCompletedEvent,

    # Growth Domain
    "growth.proposal_created": GrowthProposalCreatedEvent,
    "growth.applied": GrowthAppliedEvent,

    # System Domain
    "system.tick": SystemTickEvent,
}


def create_event_from_dict(data: Dict[str, Any]) -> CognitiveEvent:
    """
    从字典创建具体事件类型

    Args:
        data: 事件字典

    Returns:
        具体事件类型实例
    """
    event_type = data.get("type", "cognitive.base")
    event_class = EVENT_TYPE_REGISTRY.get(event_type, CognitiveEvent)
    return event_class.from_dict(data)