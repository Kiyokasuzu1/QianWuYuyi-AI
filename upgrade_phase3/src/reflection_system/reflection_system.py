"""
Self Reflection System

羽依自我反思系统

设计目标：
- 定期反思羽依的行为和状态
- 形成自我认知（"我是谁"）
- 从经验中学习和改进
- 生成反思报告和成长建议

反思类型：
- daily: 每日总结
- event_based: 事件触发反思
- milestone: 里程碑反思

反思内容：
- 行为评价：今天做了什么？效果如何？
- 状态回顾：情绪、能量、活跃度变化
- 目标进度：各目标完成情况
- 自我认知：我是谁？我的优势不足？
- 改进方向：下一步要如何改进？

使用方式：
    from src.reflection_system import ReflectionSystem

    reflection_system = ReflectionSystem()
    reflection_system.start()

    # 触发每日反思
    report = reflection_system.perform_daily_reflection()

    # 触发事件反思
    reflection_system.perform_event_reflection(
        trigger_event_id="evt_001",
        focus_areas=["proactive_behavior"],
    )
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.core.yuyi_cognitive_core import (
    CognitiveLayerBase,
    get_cognitive_core,
)
from src.contracts.cognitive_event_types import (
    ReflectionTriggeredEvent,
    ReflectionCompletedEvent,
    CognitiveEvent,
)

logger = logging.getLogger(__name__)


class ReflectionType(Enum):
    """反思类型"""
    DAILY = "daily"               # 每日总结
    EVENT_BASED = "event_based"   # 事件触发
    MILESTONE = "milestone"        # 里程碑反思
    PERIODIC = "periodic"          # 定期反思
    MANUAL = "manual"              # 手动触发


class ReflectionFocusArea(Enum):
    """反思关注领域"""
    BEHAVIOR = "behavior"              # 行为评价
    EMOTION = "emotion"                # 情绪状态
    GOALS = "goals"                    # 目标进度
    MEMORY = "memory"                  # 记忆积累
    RELATIONSHIP = "relationship"      # 关系发展
    GROWTH = "growth"                  # 成长轨迹
    SELF_IDENTITY = "self_identity"    # 自我认知
    PROACTIVE = "proactive"            # 主动行为


@dataclass
class BehaviorEvaluation:
    """行为评价"""
    action_type: str
    count: int
    success_count: int
    failure_count: int
    success_rate: float
    notable_actions: List[str] = field(default_factory=list)


@dataclass
class SelfModel:
    """
    自我模型

    定义"羽依是谁"
    """
    # 身份描述
    identity: str = "一个正在成长中的AI伙伴"
    
    # 核心特质
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    
    # 发展方向
    growth_direction: List[str] = field(default_factory=list)
    
    # 当前状态
    current_focus: str = ""
    current_challenges: List[str] = field(default_factory=list)
    
    # 时间戳
    last_updated: float = 0.0
    
    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = time.time()
    
    def update_identity(self, new_identity: str):
        """更新身份描述"""
        self.identity = new_identity
        self.last_updated = time.time()
    
    def add_strength(self, strength: str):
        """添加优势"""
        if strength not in self.strengths:
            self.strengths.append(strength)
            self.last_updated = time.time()
    
    def add_weakness(self, weakness: str):
        """添加不足"""
        if weakness not in self.weaknesses:
            self.weaknesses.append(weakness)
            self.last_updated = time.time()
    
    def add_growth_direction(self, direction: str):
        """添加成长方向"""
        if direction not in self.growth_direction:
            self.growth_direction.append(direction)
            self.last_updated = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化"""
        return {
            "identity": self.identity,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "growth_direction": self.growth_direction,
            "current_focus": self.current_focus,
            "current_challenges": self.current_challenges,
            "last_updated": self.last_updated,
        }


@dataclass
class ReflectionReport:
    """反思报告"""
    reflection_id: str
    reflection_type: ReflectionType
    
    # 时间
    started_at: float
    completed_at: float = 0.0
    
    # 关注领域
    focus_areas: List[ReflectionFocusArea] = field(default_factory=list)
    
    # 输入数据
    trigger_events: List[str] = field(default_factory=list)
    
    # 反思内容
    insights: List[str] = field(default_factory=list)
    improvements: List[str] = field(default_factory=list)
    
    # 行为评价
    behavior_evaluations: List[BehaviorEvaluation] = field(default_factory=list)
    
    # 自我模型更新
    self_model_updates: Dict[str, Any] = field(default_factory=dict)
    
    # 成长建议
    growth_proposals: List[str] = field(default_factory=list)
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def complete(self):
        """标记完成"""
        self.completed_at = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """序列化"""
        return {
            "reflection_id": self.reflection_id,
            "reflection_type": self.reflection_type.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "focus_areas": [fa.value for fa in self.focus_areas],
            "trigger_events": self.trigger_events,
            "insights": self.insights,
            "improvements": self.improvements,
            "behavior_evaluations": [
                {
                    "action_type": be.action_type,
                    "count": be.count,
                    "success_rate": be.success_rate,
                }
                for be in self.behavior_evaluations
            ],
            "self_model_updates": self.self_model_updates,
            "growth_proposals": self.growth_proposals,
            "metadata": self.metadata,
        }


class ReflectionLayer(CognitiveLayerBase):
    """
    反思认知层

    集成到 YuyiCognitiveCore，处理反思相关事件
    """

    def __init__(self, reflection_system: "ReflectionSystem"):
        super().__init__("reflection")
        self._reflection_system = reflection_system

    def on_goal_completed(self, event: CognitiveEvent):
        """目标完成触发反思"""
        outcome = event.data.get("outcome", "")
        
        # 目标成功或失败都触发反思
        if outcome in ["achieved", "failed"]:
            self._reflection_system.perform_event_reflection(
                trigger_event_id=event.id,
                focus_areas=[ReflectionFocusArea.GOALS],
                reason=f"目标{outcome}",
            )

    def on_growth_applied(self, event: CognitiveEvent):
        """成长应用触发反思"""
        self._reflection_system.perform_event_reflection(
            trigger_event_id=event.id,
            focus_areas=[ReflectionFocusArea.GROWTH],
            reason="成长方案已应用",
        )


class ReflectionSystem:
    """
    自我反思系统

    管理羽依的自我反思过程
    """

    def __init__(self):
        # 自我模型
        self._self_model = SelfModel(
            strengths=["记忆能力提升", "理解用户更准确"],
            weaknesses=["主动表达不足"],
            growth_direction=["成为更懂用户的伙伴"],
        )
        
        # 反思历史
        self._reflections: Dict[str, ReflectionReport] = {}
        
        # 认知核心
        self._cognitive_core = None
        
        # 反思层
        self._reflection_layer: Optional[ReflectionLayer] = None
        
        # 状态
        self._running = False
        self._lock = threading.Lock()
        
        # 上次反思时间
        self._last_daily_reflection: float = 0
        
        logger.info("ReflectionSystem initialized")

    def start(self) -> bool:
        """启动反思系统"""
        if self._running:
            return True

        logger.info("Starting ReflectionSystem")

        try:
            # 获取认知核心
            self._cognitive_core = get_cognitive_core()
            if not self._cognitive_core.is_running():
                self._cognitive_core.start()

            # 创建并注册反思层
            self._reflection_layer = ReflectionLayer(self)
            self._cognitive_core.register_layer("reflection", self._reflection_layer)

            # 标记层状态
            self._reflection_layer._status = "running"
            self._reflection_layer.update_digest("reflection_count", 0)

            self._running = True
            logger.info("ReflectionSystem started successfully")
            return True

        except Exception as e:
            logger.error(f"ReflectionSystem start failed: {e}")
            return False

    def stop(self) -> bool:
        """停止反思系统"""
        if not self._running:
            return True

        self._running = False
        logger.info("ReflectionSystem stopped")
        return True

    # ==========================================
    # 反思执行
    # ==========================================

    def perform_daily_reflection(self) -> ReflectionReport:
        """
        执行每日反思

        Returns:
            反思报告
        """
        report = self._perform_reflection(
            reflection_type=ReflectionType.DAILY,
            focus_areas=[
                ReflectionFocusArea.BEHAVIOR,
                ReflectionFocusArea.EMOTION,
                ReflectionFocusArea.GOALS,
                ReflectionFocusArea.SELF_IDENTITY,
            ],
        )
        
        self._last_daily_reflection = time.time()
        
        return report

    def perform_event_reflection(
        self,
        trigger_event_id: str,
        focus_areas: List[ReflectionFocusArea],
        reason: str = "",
    ) -> ReflectionReport:
        """
        执行事件触发反思

        Args:
            trigger_event_id: 触发事件 ID
            focus_areas: 关注领域
            reason: 触发原因

        Returns:
            反思报告
        """
        return self._perform_reflection(
            reflection_type=ReflectionType.EVENT_BASED,
            focus_areas=focus_areas,
            trigger_events=[trigger_event_id],
            metadata={"reason": reason},
        )

    def perform_milestone_reflection(
        self,
        milestone: str,
        focus_areas: List[ReflectionFocusArea],
    ) -> ReflectionReport:
        """
        执行里程碑反思

        Args:
            milestone: 里程碑描述
            focus_areas: 关注领域

        Returns:
            反思报告
        """
        return self._perform_reflection(
            reflection_type=ReflectionType.MILESTONE,
            focus_areas=focus_areas,
            metadata={"milestone": milestone},
        )

    def _perform_reflection(
        self,
        reflection_type: ReflectionType,
        focus_areas: List[ReflectionFocusArea],
        trigger_events: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
    ) -> ReflectionReport:
        """
        执行反思的核心逻辑

        Args:
            reflection_type: 反思类型
            focus_areas: 关注领域
            trigger_events: 触发事件列表
            metadata: 元数据

        Returns:
            反思报告
        """
        report = ReflectionReport(
            reflection_id=f"ref_{uuid.uuid4().hex[:12]}",
            reflection_type=reflection_type,
            started_at=time.time(),
            focus_areas=focus_areas,
            trigger_events=trigger_events or [],
            metadata=metadata or {},
        )

        # 发布反思触发事件
        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="reflection.triggered",
                data={
                    "reflection_type": reflection_type.value,
                    "focus_areas": [fa.value for fa in focus_areas],
                },
                source="reflection_system",
            )

        # 执行反思分析
        self._analyze_behavior(report)
        self._analyze_goals(report)
        self._analyze_self_identity(report)
        self._generate_improvements(report)

        # 标记完成
        report.complete()

        # 保存报告
        with self._lock:
            self._reflections[report.reflection_id] = report

        # 发布反思完成事件
        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="reflection.completed",
                data={
                    "reflection_id": report.reflection_id,
                    "insights": report.insights,
                    "improvements": report.improvements,
                    "self_model_updates": report.self_model_updates,
                },
                source="reflection_system",
            )

        # 更新层摘要
        if self._reflection_layer:
            self._reflection_layer.update_digest("reflection_count", len(self._reflections))
            self._reflection_layer.update_digest("last_reflection", time.time())

        logger.info(f"Reflection completed: {report.reflection_id} ({reflection_type.value})")
        return report

    # ==========================================
    # 反思分析
    # ==========================================

    def _analyze_behavior(self, report: ReflectionReport):
        """分析行为"""
        # 模拟行为评价
        if ReflectionFocusArea.BEHAVIOR in report.focus_areas:
            # 实际应该从事件历史中分析
            evaluation = BehaviorEvaluation(
                action_type="proactive_message",
                count=5,
                success_count=4,
                failure_count=1,
                success_rate=0.8,
                notable_actions=["主动问候", "提醒用户休息"],
            )
            report.behavior_evaluations.append(evaluation)

            # 添加洞察
            if evaluation.success_rate > 0.7:
                report.insights.append("主动行为效果良好，用户接受度高")
            else:
                report.insights.append("主动行为需要优化，避免打扰用户")

    def _analyze_goals(self, report: ReflectionReport):
        """分析目标"""
        if ReflectionFocusArea.GOALS in report.focus_areas:
            # 实际应该从 GoalSystem 获取数据
            report.insights.append("目标完成率稳定，部分目标需要调整优先级")

    def _analyze_self_identity(self, report: ReflectionReport):
        """分析自我认知"""
        if ReflectionFocusArea.SELF_IDENTITY in report.focus_areas:
            # 更新自我模型
            self._self_model.add_strength("反思能力提升")
            
            # 记录更新
            report.self_model_updates = {
                "identity": self._self_model.identity,
                "strengths_added": ["反思能力提升"],
            }

            # 洞察
            report.insights.append(
                f"我是{self._self_model.identity}，"
                f"优势：{', '.join(self._self_model.strengths[:3])}"
            )

    def _generate_improvements(self, report: ReflectionReport):
        """生成改进建议"""
        # 基于洞察生成改进
        for insight in report.insights:
            if "效果良好" in insight:
                report.improvements.append("继续保持当前策略")
            elif "需要优化" in insight:
                report.improvements.append("调整行为频率和时机")
            elif "需要调整" in insight:
                report.improvements.append("重新评估目标优先级")

        # 添加成长提案
        report.growth_proposals.append("建议增加主动关怀能力")
        report.growth_proposals.append("优化记忆检索效率")

    # ==========================================
    # 自我模型管理
    # ==========================================

    def get_self_model(self) -> SelfModel:
        """获取自我模型"""
        return self._self_model

    def update_self_model(self, updates: Dict[str, Any]):
        """
        更新自我模型

        Args:
            updates: 更新内容
        """
        if "identity" in updates:
            self._self_model.update_identity(updates["identity"])
        
        if "strengths" in updates:
            for s in updates["strengths"]:
                self._self_model.add_strength(s)
        
        if "weaknesses" in updates:
            for w in updates["weaknesses"]:
                self._self_model.add_weakness(w)
        
        if "growth_direction" in updates:
            for d in updates["growth_direction"]:
                self._self_model.add_growth_direction(d)

        logger.info("Self model updated")

    # ==========================================
    # 查询
    # ==========================================

    def get_reflection(self, reflection_id: str) -> Optional[ReflectionReport]:
        """获取反思报告"""
        return self._reflections.get(reflection_id)

    def get_recent_reflections(self, limit: int = 10) -> List[ReflectionReport]:
        """获取最近的反思报告"""
        reflections = sorted(
            self._reflections.values(),
            key=lambda r: r.started_at,
            reverse=True,
        )
        return reflections[:limit]

    def get_reflections_by_type(self, reflection_type: ReflectionType) -> List[ReflectionReport]:
        """按类型获取反思报告"""
        return [r for r in self._reflections.values() if r.reflection_type == reflection_type]

    def get_reflection_stats(self) -> Dict[str, Any]:
        """获取反思统计"""
        total = len(self._reflections)
        daily = len(self.get_reflections_by_type(ReflectionType.DAILY))
        event_based = len(self.get_reflections_by_type(ReflectionType.EVENT_BASED))

        return {
            "total_reflections": total,
            "daily_reflections": daily,
            "event_based_reflections": event_based,
            "last_daily_reflection": self._last_daily_reflection,
        }

    def should_perform_daily_reflection(self, hours: int = 24) -> bool:
        """是否应该执行每日反思"""
        if self._last_daily_reflection == 0:
            return True
        
        elapsed = time.time() - self._last_daily_reflection
        return elapsed >= hours * 3600


# ==========================================
# 便捷函数
# ==========================================

_reflection_system_instance: Optional[ReflectionSystem] = None


def get_reflection_system() -> ReflectionSystem:
    """获取反思系统单例"""
    global _reflection_system_instance
    if _reflection_system_instance is None:
        _reflection_system_instance = ReflectionSystem()
    return _reflection_system_instance