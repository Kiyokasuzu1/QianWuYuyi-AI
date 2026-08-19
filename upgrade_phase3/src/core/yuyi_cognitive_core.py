"""
Yuyi Unified Cognitive Core

Phase 3 统一认知核心

设计目标：
- 整合 Memory、Emotion、Personality、Goal、Relationship 等模块
- 通过 CognitiveEventBus 实现模块间松耦合通信
- 提供统一的认知状态查询和管理接口

架构：
                    YuyiCognitiveCore
                            │
    ┌───────────┬───────────┼───────────┬───────────┐
    │           │           │           │           │
MemoryLayer EmotionLayer PersonalityLayer GoalLayer RelationshipLayer
    │           │           │           │           │
    └───────────┴───────────┼───────────┴───────────┘
                            │
                    CognitiveEventBus
                            │
                    Event Handlers

使用方式：
    from src.core.yuyi_cognitive_core import YuyiCognitiveCore

    core = YuyiCognitiveCore()
    core.start()

    # 发布事件
    core.emit_event("user.input", {"content": "你好"}, user_id="user_001")

    # 查询状态
    state = core.get_unified_state()
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from src.core.event_bus import EventBus, Event
from src.contracts.cognitive_event_types import (
    CognitiveEvent,
    EventPriority,
    create_event_from_dict,
    UserInputEvent,
    UserEmotionDetectedEvent,
    MemoryRecordedEvent,
    EmotionStateChangedEvent,
    GoalCreatedEvent,
    GoalProgressEvent,
    GoalCompletedEvent,
    RelationshipEvolvedEvent,
    ProactiveActionProposedEvent,
    ReflectionTriggeredEvent,
    ReflectionCompletedEvent,
    GrowthProposalCreatedEvent,
    GrowthAppliedEvent,
    SystemTickEvent,
)

if TYPE_CHECKING:
    from src.memory import MemorySystem
    from src.emotion import EmotionManager
    from src.personality import PersonalityController
    from src.relationship import RelationshipManager
    from src.growth import GrowthEngine

logger = logging.getLogger(__name__)


@dataclass
class CognitiveLayerState:
    """认知层状态摘要"""
    layer_name: str
    status: str = "uninitialized"  # uninitialized | running | error
    last_update: float = 0.0
    digest: Dict[str, Any] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UnifiedCognitiveState:
    """统一认知状态"""
    timestamp: float
    emotion: CognitiveLayerState
    memory: CognitiveLayerState
    personality: CognitiveLayerState
    goals: CognitiveLayerState
    relationship: CognitiveLayerState
    growth: CognitiveLayerState

    # 综合指标
    overall_health: float = 1.0  # 0.0 - 1.0
    active_goals: int = 0
    pending_reflections: int = 0
    growth_proposals: int = 0

    # Phase 3: 自主智能层
    proactive: Optional[CognitiveLayerState] = None
    dream: Optional[CognitiveLayerState] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "emotion": {
                "layer_name": self.emotion.layer_name,
                "status": self.emotion.status,
                "last_update": self.emotion.last_update,
                "digest": self.emotion.digest,
            },
            "memory": {
                "layer_name": self.memory.layer_name,
                "status": self.memory.status,
                "last_update": self.memory.last_update,
                "digest": self.memory.digest,
            },
            "personality": {
                "layer_name": self.personality.layer_name,
                "status": self.personality.status,
                "last_update": self.personality.last_update,
                "digest": self.personality.digest,
            },
            "goals": {
                "layer_name": self.goals.layer_name,
                "status": self.goals.status,
                "last_update": self.goals.last_update,
                "digest": self.goals.digest,
            },
            "relationship": {
                "layer_name": self.relationship.layer_name,
                "status": self.relationship.status,
                "last_update": self.relationship.last_update,
                "digest": self.relationship.digest,
            },
            "growth": {
                "layer_name": self.growth.layer_name,
                "status": self.growth.status,
                "last_update": self.growth.last_update,
                "digest": self.growth.digest,
            },
            "proactive": {
                "layer_name": self.proactive.layer_name,
                "status": self.proactive.status,
                "last_update": self.proactive.last_update,
                "digest": self.proactive.digest,
            } if self.proactive else None,
            "dream": {
                "layer_name": self.dream.layer_name,
                "status": self.dream.status,
                "last_update": self.dream.last_update,
                "digest": self.dream.digest,
            } if self.dream else None,
            "overall_health": self.overall_health,
            "active_goals": self.active_goals,
            "pending_reflections": self.pending_reflections,
            "growth_proposals": self.growth_proposals,
        }


class CognitiveLayerBase:
    """
    认知层基类

    所有认知层继承此基类，提供统一的事件处理接口
    """

    def __init__(self, layer_name: str):
        self.layer_name = layer_name
        self._status = "uninitialized"
        self._last_update = time.time()
        self._digest: Dict[str, Any] = {}
        self._metrics: Dict[str, Any] = {}

    @property
    def status(self) -> str:
        return self._status

    @property
    def last_update(self) -> float:
        return self._last_update

    @property
    def digest(self) -> Dict[str, Any]:
        return self._digest

    def get_state(self) -> CognitiveLayerState:
        return CognitiveLayerState(
            layer_name=self.layer_name,
            status=self._status,
            last_update=self._last_update,
            digest=self._digest,
            metrics=self._metrics,
        )

    def update_digest(self, key: str, value: Any):
        """更新摘要信息"""
        self._digest[key] = value
        self._last_update = time.time()

    def update_metric(self, key: str, value: Any):
        """更新指标"""
        self._metrics[key] = value
        self._last_update = time.time()

    # ==========================================
    # 事件处理接口（子类重写）
    # ==========================================

    def on_user_input(self, event: CognitiveEvent):
        """处理用户输入事件"""
        pass

    def on_user_emotion_detected(self, event: CognitiveEvent):
        """处理用户情绪检测事件"""
        pass

    def on_memory_recorded(self, event: CognitiveEvent):
        """处理记忆记录事件"""
        pass

    def on_emotion_state_changed(self, event: CognitiveEvent):
        """处理情绪状态变化事件"""
        pass

    def on_goal_created(self, event: CognitiveEvent):
        """处理目标创建事件"""
        pass

    def on_goal_progress(self, event: CognitiveEvent):
        """处理目标进度事件"""
        pass

    def on_goal_completed(self, event: CognitiveEvent):
        """处理目标完成事件"""
        pass

    def on_relationship_evolved(self, event: CognitiveEvent):
        """处理关系进化事件"""
        pass

    def on_reflection_triggered(self, event: CognitiveEvent):
        """处理反思触发事件"""
        pass

    def on_reflection_completed(self, event: CognitiveEvent):
        """处理反思完成事件"""
        pass

    def on_growth_proposal_created(self, event: CognitiveEvent):
        """处理成长提案创建事件"""
        pass

    def on_growth_applied(self, event: CognitiveEvent):
        """处理成长应用事件"""
        pass

    def on_system_tick(self, event: CognitiveEvent):
        """处理系统时钟事件"""
        pass


class YuyiCognitiveCore:
    """
    Yuyi 统一认知核心

    整合各认知模块，通过事件总线协调通信

    使用方式：
        core = YuyiCognitiveCore()
        core.start()

        # 发布事件
        core.emit_event("user.input", {...})

        # 获取统一状态
        state = core.get_unified_state()
    """

    _instance: Optional[YuyiCognitiveCore] = None
    _lock = threading.Lock()

    def __init__(self):
        # 认知层实例
        self._emotion_layer: Optional[CognitiveLayerBase] = None
        self._memory_layer: Optional[CognitiveLayerBase] = None
        self._personality_layer: Optional[CognitiveLayerBase] = None
        self._goal_layer: Optional[CognitiveLayerBase] = None
        self._relationship_layer: Optional[CognitiveLayerBase] = None
        self._growth_layer: Optional[CognitiveLayerBase] = None
        self._reflection_layer: Optional[CognitiveLayerBase] = None
        self._proactive_layer: Optional[CognitiveLayerBase] = None
        self._dream_layer: Optional[CognitiveLayerBase] = None

        # 事件总线
        self._event_bus = EventBus.get_instance()

        # 状态
        self._running = False
        self._start_time = 0.0

        # 事件处理器映射
        self._event_handlers: Dict[str, List[Callable[[CognitiveEvent], None]]] = {}

        # 事件历史（用于回溯）
        self._event_history: List[CognitiveEvent] = []
        self._max_history = 1000

        logger.info("YuyiCognitiveCore initialized")

    @classmethod
    def get_instance(cls) -> "YuyiCognitiveCore":
        """获取全局单例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    @classmethod
    def reset_instance(cls):
        """重置单例（仅测试用）"""
        with cls._lock:
            cls._instance = None

    # ==========================================
    # 生命周期管理
    # ==========================================

    def start(self) -> bool:
        """
        启动认知核心

        Returns:
            True 表示启动成功
        """
        if self._running:
            logger.warning("YuyiCognitiveCore already running")
            return True

        logger.info("Starting YuyiCognitiveCore")

        try:
            # 初始化认知层
            self._init_layers()

            # 注册事件处理器
            self._register_event_handlers()

            self._running = True
            self._start_time = time.time()
            logger.info("YuyiCognitiveCore started successfully")
            return True

        except Exception as e:
            logger.error(f"YuyiCognitiveCore start failed: {e}")
            return False

    def stop(self) -> bool:
        """
        停止认知核心

        Returns:
            True 表示停止成功
        """
        if not self._running:
            return True

        logger.info("Stopping YuyiCognitiveCore")

        self._running = False
        self._unregister_event_handlers()

        logger.info("YuyiCognitiveCore stopped")
        return True

    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running

    # ==========================================
    # 认知层初始化
    # ==========================================

    def _init_layers(self):
        """初始化各认知层"""
        # 创建基础认知层实例
        self._emotion_layer = CognitiveLayerBase("emotion")
        self._memory_layer = CognitiveLayerBase("memory")
        self._personality_layer = CognitiveLayerBase("personality")
        self._goal_layer = CognitiveLayerBase("goal")
        self._relationship_layer = CognitiveLayerBase("relationship")
        self._growth_layer = CognitiveLayerBase("growth")
        self._reflection_layer = CognitiveLayerBase("reflection")
        self._proactive_layer = CognitiveLayerBase("proactive")
        self._dream_layer = CognitiveLayerBase("dream")

        # 标记为运行状态
        for layer in [
            self._emotion_layer,
            self._memory_layer,
            self._personality_layer,
            self._goal_layer,
            self._relationship_layer,
            self._growth_layer,
            self._reflection_layer,
            self._proactive_layer,
            self._dream_layer,
        ]:
            layer._status = "running"

        logger.debug("Cognitive layers initialized")

    def register_layer(self, layer_type: str, layer: CognitiveLayerBase):
        """
        注册认知层

        Args:
            layer_type: 层类型（emotion/memory/personality/goal/relationship/growth/reflection）
            layer: 认知层实例
        """
        layer_map = {
            "emotion": "_emotion_layer",
            "memory": "_memory_layer",
            "personality": "_personality_layer",
            "goal": "_goal_layer",
            "relationship": "_relationship_layer",
            "growth": "_growth_layer",
            "reflection": "_reflection_layer",
            "proactive": "_proactive_layer",
            "dream": "_dream_layer",
        }

        if layer_type not in layer_map:
            raise ValueError(f"Unknown layer type: {layer_type}")

        setattr(self, layer_map[layer_type], layer)
        logger.info(f"Registered cognitive layer: {layer_type}")

    # ==========================================
    # 事件处理
    # ==========================================

    def _register_event_handlers(self):
        """注册事件处理器"""
        # 用户域事件
        self._subscribe("user.input", self._handle_user_input)
        self._subscribe("user.emotion_detected", self._handle_user_emotion_detected)

        # 记忆域事件
        self._subscribe("memory.recorded", self._handle_memory_recorded)

        # 情绪域事件
        self._subscribe("emotion.state_changed", self._handle_emotion_state_changed)

        # 目标域事件
        self._subscribe("goal.created", self._handle_goal_created)
        self._subscribe("goal.progress", self._handle_goal_progress)
        self._subscribe("goal.completed", self._handle_goal_completed)

        # 关系域事件
        self._subscribe("relationship.evolved", self._handle_relationship_evolved)

        # 反思域事件
        self._subscribe("reflection.triggered", self._handle_reflection_triggered)
        self._subscribe("reflection.completed", self._handle_reflection_completed)

        # 成长域事件
        self._subscribe("growth.proposal_created", self._handle_growth_proposal_created)
        self._subscribe("growth.applied", self._handle_growth_applied)

        # 系统域事件
        self._subscribe("system.tick", self._handle_system_tick)

        logger.debug("Event handlers registered")

    def _unregister_event_handlers(self):
        """注销事件处理器"""
        # EventBus 会自动清理订阅
        logger.debug("Event handlers unregistered")

    def _subscribe(self, event_type: str, handler: Callable[[CognitiveEvent], None]):
        """订阅事件"""
        def wrapper(event: Event):
            # 将 EventBus.Event 转换为 CognitiveEvent
            cognitive_event = create_event_from_dict({
                "type": event.event_type,
                "id": event.request_id,
                "timestamp": event.timestamp,
                "source": event.source,
                "data": event.payload,
            })
            handler(cognitive_event)

        self._event_bus.subscribe(event_type, wrapper)

    # ==========================================
    # 事件发布
    # ==========================================

    def emit_event(
        self,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        source: str = "",
        priority: EventPriority = EventPriority.NORMAL,
        related_event_ids: Optional[List[str]] = None,
    ) -> CognitiveEvent:
        """
        发布认知事件

        Args:
            event_type: 事件类型
            data: 事件数据
            user_id: 用户 ID
            session_id: 会话 ID
            source: 事件来源
            priority: 事件优先级
            related_event_ids: 关联事件 ID 列表

        Returns:
            发布的事件对象
        """
        event = CognitiveEvent(
            type=event_type,
            source=source,
            priority=priority,
            data=data,
            user_id=user_id,
            session_id=session_id,
            related_event_ids=related_event_ids or [],
        )

        # 记录历史
        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

        # 发布到 EventBus
        self._event_bus.emit(
            event_type=event_type,
            payload=data,
            source=source,
            request_id=event.id,
        )

        logger.debug(f"Event emitted: {event_type} (id={event.id})")
        return event

    # ==========================================
    # 事件处理器实现
    # ==========================================

    def _handle_user_input(self, event: CognitiveEvent):
        """处理用户输入事件"""
        logger.debug(f"Handling user input: {event.data.get('content', '')[:50]}")

        # 通知各层
        if self._emotion_layer:
            self._emotion_layer.on_user_input(event)
        if self._memory_layer:
            self._memory_layer.on_user_input(event)
        if self._goal_layer:
            self._goal_layer.on_user_input(event)
        if self._relationship_layer:
            self._relationship_layer.on_user_input(event)

    def _handle_user_emotion_detected(self, event: CognitiveEvent):
        """处理用户情绪检测事件"""
        logger.debug(f"User emotion detected: {event.data.get('emotion')}")

        if self._emotion_layer:
            self._emotion_layer.on_user_emotion_detected(event)
        if self._relationship_layer:
            self._relationship_layer.on_user_emotion_detected(event)
        if self._goal_layer:
            self._goal_layer.on_user_emotion_detected(event)

    def _handle_memory_recorded(self, event: CognitiveEvent):
        """处理记忆记录事件"""
        logger.debug(f"Memory recorded: {event.data.get('memory_id')}")

        if self._memory_layer:
            self._memory_layer.on_memory_recorded(event)
        if self._relationship_layer:
            self._relationship_layer.on_memory_recorded(event)
        if self._growth_layer:
            self._growth_layer.on_memory_recorded(event)

    def _handle_emotion_state_changed(self, event: CognitiveEvent):
        """处理情绪状态变化事件"""
        logger.debug(f"Emotion changed: {event.data.get('previous_emotion')} -> {event.data.get('current_emotion')}")

        if self._emotion_layer:
            self._emotion_layer.on_emotion_state_changed(event)
        if self._personality_layer:
            self._personality_layer.on_emotion_state_changed(event)
        if self._goal_layer:
            self._goal_layer.on_emotion_state_changed(event)

    def _handle_goal_created(self, event: CognitiveEvent):
        """处理目标创建事件"""
        logger.debug(f"Goal created: {event.data.get('goal_id')}")

        if self._goal_layer:
            self._goal_layer.on_goal_created(event)

    def _handle_goal_progress(self, event: CognitiveEvent):
        """处理目标进度事件"""
        if self._goal_layer:
            self._goal_layer.on_goal_progress(event)
        if self._growth_layer:
            self._growth_layer.on_goal_progress(event)

    def _handle_goal_completed(self, event: CognitiveEvent):
        """处理目标完成事件"""
        logger.debug(f"Goal completed: {event.data.get('goal_id')} ({event.data.get('outcome')})")

        if self._goal_layer:
            self._goal_layer.on_goal_completed(event)
        if self._growth_layer:
            self._growth_layer.on_goal_completed(event)

    def _handle_relationship_evolved(self, event: CognitiveEvent):
        """处理关系进化事件"""
        logger.debug(f"Relationship evolved: {event.data.get('previous_level')} -> {event.data.get('current_level')}")

        if self._relationship_layer:
            self._relationship_layer.on_relationship_evolved(event)
        if self._personality_layer:
            self._personality_layer.on_relationship_evolved(event)
        if self._goal_layer:
            self._goal_layer.on_relationship_evolved(event)

    def _handle_reflection_triggered(self, event: CognitiveEvent):
        """处理反思触发事件"""
        logger.debug(f"Reflection triggered: {event.data.get('reflection_type')}")

        if self._growth_layer:
            self._growth_layer.on_reflection_triggered(event)

    def _handle_reflection_completed(self, event: CognitiveEvent):
        """处理反思完成事件"""
        logger.debug(f"Reflection completed: {event.data.get('reflection_id')}")

        if self._growth_layer:
            self._growth_layer.on_reflection_completed(event)
        if self._personality_layer:
            self._personality_layer.on_reflection_completed(event)

    def _handle_growth_proposal_created(self, event: CognitiveEvent):
        """处理成长提案创建事件"""
        logger.debug(f"Growth proposal created: {event.data.get('proposal_id')}")

        if self._growth_layer:
            self._growth_layer.on_growth_proposal_created(event)

    def _handle_growth_applied(self, event: CognitiveEvent):
        """处理成长应用事件"""
        logger.debug(f"Growth applied: {event.data.get('proposal_id')}")

        if self._growth_layer:
            self._growth_layer.on_growth_applied(event)
        if self._personality_layer:
            self._personality_layer.on_growth_applied(event)
        if self._goal_layer:
            self._goal_layer.on_growth_applied(event)

    def _handle_system_tick(self, event: CognitiveEvent):
        """处理系统时钟事件"""
        # 定期更新各层状态
        for layer in [
            self._emotion_layer,
            self._memory_layer,
            self._personality_layer,
            self._goal_layer,
            self._relationship_layer,
            self._growth_layer,
        ]:
            if layer:
                layer.on_system_tick(event)

    # ==========================================
    # 状态查询
    # ==========================================

    def get_unified_state(self) -> UnifiedCognitiveState:
        """
        获取统一认知状态

        Returns:
            UnifiedCognitiveState 对象
        """
        now = time.time()

        return UnifiedCognitiveState(
            timestamp=now,
            emotion=self._emotion_layer.get_state() if self._emotion_layer else CognitiveLayerState("emotion"),
            memory=self._memory_layer.get_state() if self._memory_layer else CognitiveLayerState("memory"),
            personality=self._personality_layer.get_state() if self._personality_layer else CognitiveLayerState("personality"),
            goals=self._goal_layer.get_state() if self._goal_layer else CognitiveLayerState("goals"),
            relationship=self._relationship_layer.get_state() if self._relationship_layer else CognitiveLayerState("relationship"),
            growth=self._growth_layer.get_state() if self._growth_layer else CognitiveLayerState("growth"),
            overall_health=self._calculate_health(),
            active_goals=self._goal_layer._metrics.get("active_count", 0) if self._goal_layer else 0,
            pending_reflections=self._growth_layer._metrics.get("pending_reflections", 0) if self._growth_layer else 0,
            growth_proposals=self._growth_layer._metrics.get("pending_proposals", 0) if self._growth_layer else 0,
            proactive=self._proactive_layer.get_state() if self._proactive_layer else None,
            dream=self._dream_layer.get_state() if self._dream_layer else None,
        )

    def _calculate_health(self) -> float:
        """计算整体健康度"""
        layers = [
            self._emotion_layer,
            self._memory_layer,
            self._personality_layer,
            self._goal_layer,
            self._relationship_layer,
            self._growth_layer,
            self._reflection_layer,
            self._proactive_layer,
            self._dream_layer,
        ]

        if not layers:
            return 1.0

        healthy_count = sum(1 for layer in layers if layer and layer.status == "running")
        return healthy_count / len(layers)

    def get_event_history(
        self,
        event_type: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[CognitiveEvent]:
        """
        获取事件历史

        Args:
            event_type: 按事件类型过滤
            user_id: 按用户 ID 过滤
            limit: 最大返回数量

        Returns:
            事件列表（按时间倒序）
        """
        history = list(reversed(self._event_history))

        if event_type:
            history = [e for e in history if e.type == event_type]

        if user_id:
            history = [e for e in history if e.user_id == user_id]

        return history[:limit]

    # ==========================================
    # 快捷方法
    # ==========================================

    def record_user_input(
        self,
        content: str,
        user_id: str,
        session_id: Optional[str] = None,
    ) -> CognitiveEvent:
        """
        记录用户输入

        Args:
            content: 用户输入内容
            user_id: 用户 ID
            session_id: 会话 ID

        Returns:
            发布的事件对象
        """
        return self.emit_event(
            event_type="user.input",
            data={"content": content},
            user_id=user_id,
            session_id=session_id,
            source="conversation",
        )

    def record_memory(
        self,
        memory_id: str,
        memory_type: str,
        content_summary: str,
        user_id: str,
        importance: float = 0.5,
    ) -> CognitiveEvent:
        """
        记录新记忆

        Args:
            memory_id: 记忆 ID
            memory_type: 记忆类型
            content_summary: 内容摘要
            user_id: 用户 ID
            importance: 重要性

        Returns:
            发布的事件对象
        """
        return self.emit_event(
            event_type="memory.recorded",
            data={
                "memory_id": memory_id,
                "memory_type": memory_type,
                "content_summary": content_summary,
                "importance": importance,
            },
            user_id=user_id,
            source="memory_system",
        )

    def trigger_reflection(
        self,
        reflection_type: str,
        focus_areas: List[str],
        trigger_event_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> CognitiveEvent:
        """
        触发反思

        Args:
            reflection_type: 反思类型（daily/event_based/milestone）
            focus_areas: 关注领域
            trigger_event_ids: 触发事件 ID 列表
            user_id: 用户 ID

        Returns:
            发布的事件对象
        """
        return self.emit_event(
            event_type="reflection.triggered",
            data={
                "reflection_type": reflection_type,
                "focus_areas": focus_areas,
            },
            user_id=user_id,
            related_event_ids=trigger_event_ids,
            source="reflection_engine",
        )


# ==========================================
# 便捷函数
# ==========================================

def get_cognitive_core() -> YuyiCognitiveCore:
    """获取认知核心单例"""
    return YuyiCognitiveCore.get_instance()


def emit_cognitive_event(
    event_type: str,
    data: Dict[str, Any],
    user_id: Optional[str] = None,
) -> CognitiveEvent:
    """便捷函数：发布认知事件"""
    core = get_cognitive_core()
    return core.emit_event(event_type, data, user_id=user_id)