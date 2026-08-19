"""
Proactive Behavior Engine

羽依主动行为引擎

设计目标：
- 让羽依不只是等待输入，而是主动行动
- 主动提醒、主动交流、主动关怀
- Action Confidence Gate（克制层）：防止过度主动

主动行为类型：
- greeting: 问候（长时间未交流）
- reminder: 提醒（用户设定的待办）
- care: 关怀（检测到用户情绪低落）
- share: 分享（发现有趣内容）
- suggestion: 建议（基于用户习惯）

克制层流程：
发现机会 -> 生成行为 -> 评估必要性 -> 评估打扰风险 -> 决定执行

使用方式：
    from src.proactive.proactive_engine import ProactiveEngine

    engine = ProactiveEngine()
    engine.start()

    # 检查并执行主动行为
    actions = engine.check_and_act(user_id="user_001")
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from src.core.yuyi_cognitive_core import (
    CognitiveLayerBase,
    get_cognitive_core,
)
from src.contracts.cognitive_event_types import (
    ProactiveActionProposedEvent,
    ProactiveActionExecutedEvent,
    CognitiveEvent,
)

logger = logging.getLogger(__name__)


class ActionType(Enum):
    """主动行为类型"""
    GREETING = "greeting"       # 问候
    REMINDER = "reminder"       # 提醒
    CARE = "care"               # 关怀
    SHARE = "share"             # 分享
    SUGGESTION = "suggestion"   # 建议
    CHECKIN = "checkin"         # 状态询问


class ActionOutcome(Enum):
    """行为执行结果"""
    EXECUTED = "executed"       # 已执行
    REJECTED = "rejected"       # 被克制层拒绝
    DEFERRED = "deferred"       # 推迟
    SKIPPED = "skipped"         # 跳过


@dataclass
class ProposedAction:
    """提议的主动行为"""
    action_id: str
    action_type: ActionType
    content: str
    trigger_reason: str

    # 目标用户
    user_id: Optional[str] = None

    # 评估分数
    necessity_score: float = 0.0    # 必要性 0-1
    disturbance_risk: float = 0.0   # 打扰风险 0-1
    confidence: float = 0.0          # 综合信心 0-1

    # 上下文
    context: Dict[str, Any] = field(default_factory=dict)

    # 时间
    created_at: float = 0.0
    executed_at: float = 0.0

    # 结果
    outcome: Optional[ActionOutcome] = None
    outcome_reason: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "content": self.content,
            "trigger_reason": self.trigger_reason,
            "user_id": self.user_id,
            "necessity_score": round(self.necessity_score, 2),
            "disturbance_risk": round(self.disturbance_risk, 2),
            "confidence": round(self.confidence, 2),
            "context": self.context,
            "created_at": self.created_at,
            "executed_at": self.executed_at,
            "outcome": self.outcome.value if self.outcome else None,
            "outcome_reason": self.outcome_reason,
        }


class ActionConfidenceGate:
    """
    行为信心克制层

    防止羽依过度主动，评估每个行为的必要性和打扰风险
    """

    # 用户交流频率偏好（默认假设）
    DEFAULT_USER_PREFERENCE = {
        "frequency": "medium",   # low / medium / high
        "active_hours": [(9, 23)],  # 允许主动交流的时间段
        "do_not_disturb": False,
    }

    def __init__(self):
        # 用户偏好缓存
        self._user_preferences: Dict[str, Dict[str, Any]] = {}
        # 最近主动行为记录（用于控制频率）
        self._recent_actions: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def set_user_preference(self, user_id: str, preference: Dict[str, Any]):
        """设置用户偏好"""
        with self._lock:
            self._user_preferences[user_id] = {**self.DEFAULT_USER_PREFERENCE, **preference}

    def evaluate(self, action: ProposedAction) -> tuple[ActionOutcome, str]:
        """
        评估行为是否应该执行

        Returns:
            (outcome, reason)
        """
        user_id = action.user_id or "default"

        with self._lock:
            pref = self._user_preferences.get(user_id, self.DEFAULT_USER_PREFERENCE.copy())
            recent = self._recent_actions.get(user_id, [])

        # 1. 检查免打扰
        if pref.get("do_not_disturb", False):
            return ActionOutcome.REJECTED, "用户处于免打扰模式"

        # 2. 检查活跃时间段
        now = time.localtime()
        current_hour = now.tm_hour
        active_hours = pref.get("active_hours", [(0, 24)])
        in_active_hours = any(start <= current_hour < end for start, end in active_hours)
        if not in_active_hours:
            return ActionOutcome.DEFERRED, "当前不在用户活跃时间段"

        # 3. 检查频率限制
        frequency = pref.get("frequency", "medium")
        freq_limits = {
            "low": (1, 86400),      # 每天最多1次
            "medium": (3, 86400),   # 每天最多3次
            "high": (10, 86400),    # 每天最多10次
        }
        max_count, window = freq_limits.get(frequency, (3, 86400))

        now_ts = time.time()
        recent_in_window = [t for t in recent if now_ts - t < window]
        if len(recent_in_window) >= max_count:
            return ActionOutcome.REJECTED, f"已达到用户频率限制 ({frequency})"

        # 4. 评估必要性
        if action.necessity_score < 0.3:
            return ActionOutcome.SKIPPED, "必要性不足"

        # 5. 评估打扰风险
        if action.disturbance_risk > 0.7:
            return ActionOutcome.REJECTED, "打扰风险过高"

        # 6. 综合信心
        if action.confidence < 0.5:
            return ActionOutcome.DEFERRED, "信心不足，等待更好时机"

        # 记录执行
        with self._lock:
            if user_id not in self._recent_actions:
                self._recent_actions[user_id] = []
            self._recent_actions[user_id].append(now_ts)
            # 清理过期记录
            self._recent_actions[user_id] = [
                t for t in self._recent_actions[user_id] if now_ts - t < window
            ]

        return ActionOutcome.EXECUTED, "通过克制层评估"

    def get_user_stats(self, user_id: str) -> Dict[str, Any]:
        """获取用户主动行为统计"""
        with self._lock:
            recent = self._recent_actions.get(user_id, [])
        now_ts = time.time()
        day_ago = now_ts - 86400
        return {
            "today_actions": len([t for t in recent if t > day_ago]),
            "total_recent": len(recent),
            "preference": self._user_preferences.get(user_id, self.DEFAULT_USER_PREFERENCE),
        }


class ProactiveLayer(CognitiveLayerBase):
    """
    主动行为认知层

    集成到 YuyiCognitiveCore
    """

    def __init__(self, proactive_engine: "ProactiveEngine"):
        super().__init__("proactive")
        self._proactive_engine = proactive_engine

    def on_system_tick(self, event: CognitiveEvent):
        """系统时钟触发主动行为检查"""
        tick_type = event.data.get("tick_type", "")
        if tick_type in ("minute", "hour"):
            # 定期扫描所有用户
            self._proactive_engine.scan_all_users()

    def on_user_input(self, event: CognitiveEvent):
        """用户输入更新用户活跃时间"""
        user_id = event.user_id
        if user_id:
            self._proactive_engine.record_user_activity(user_id)

    def on_user_emotion_detected(self, event: CognitiveEvent):
        """用户情绪检测可能触发关怀行为"""
        emotion = event.data.get("emotion", "")
        user_id = event.user_id

        if emotion in ("tired", "sad", "stressed", "upset"):
            self._proactive_engine.propose_action(
                action_type=ActionType.CARE,
                content=f"检测到用户情绪{emotion}，发送关怀消息",
                trigger_reason=f"用户情绪检测: {emotion}",
                user_id=user_id,
                necessity_score=0.8,
                context={"detected_emotion": emotion},
            )


class ProactiveEngine:
    """
    主动行为引擎

    管理羽依的主动行为生命周期
    """

    def __init__(self):
        # 行为存储
        self._actions: Dict[str, ProposedAction] = {}
        self._action_history: List[str] = []
        self._max_history = 100

        # 克制层
        self._confidence_gate = ActionConfidenceGate()

        # 用户活跃时间（用于判断用户是否在线）
        self._user_last_active: Dict[str, float] = {}

        # 认知核心
        self._cognitive_core = None

        # 主动行为层
        self._proactive_layer: Optional[ProactiveLayer] = None

        # 状态
        self._running = False
        self._lock = threading.Lock()

        logger.info("ProactiveEngine initialized")

    def start(self) -> bool:
        """启动主动行为引擎"""
        if self._running:
            return True

        logger.info("Starting ProactiveEngine")

        try:
            self._cognitive_core = get_cognitive_core()
            if not self._cognitive_core.is_running():
                self._cognitive_core.start()

            self._proactive_layer = ProactiveLayer(self)
            self._cognitive_core.register_layer("proactive", self._proactive_layer)

            self._proactive_layer._status = "running"
            self._proactive_layer.update_digest("total_actions", 0)

            self._running = True
            logger.info("ProactiveEngine started successfully")
            return True

        except Exception as e:
            logger.error(f"ProactiveEngine start failed: {e}")
            return False

    def stop(self) -> bool:
        """停止主动行为引擎"""
        if not self._running:
            return True

        self._running = False
        logger.info("ProactiveEngine stopped")
        return True

    # ==========================================
    # 行为创建
    # ==========================================

    def propose_action(
        self,
        action_type: ActionType,
        content: str,
        trigger_reason: str,
        user_id: Optional[str] = None,
        necessity_score: float = 0.5,
        disturbance_risk: float = 0.3,
        context: Optional[Dict[str, Any]] = None,
    ) -> ProposedAction:
        """
        提议一个主动行为

        Args:
            action_type: 行为类型
            content: 行为内容
            trigger_reason: 触发原因
            user_id: 目标用户
            necessity_score: 必要性评分 0-1
            disturbance_risk: 打扰风险 0-1
            context: 上下文

        Returns:
            提议的行为
        """
        # 计算综合信心
        confidence = necessity_score * (1 - disturbance_risk)

        action = ProposedAction(
            action_id=f"act_{uuid.uuid4().hex[:12]}",
            action_type=action_type,
            content=content,
            trigger_reason=trigger_reason,
            user_id=user_id,
            necessity_score=necessity_score,
            disturbance_risk=disturbance_risk,
            confidence=confidence,
            context=context or {},
        )

        with self._lock:
            self._actions[action.action_id] = action

        # 发布事件
        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="action.proactive_proposed",
                data={
                    "action_id": action.action_id,
                    "action_type": action.action_type.value,
                    "action_content": content,
                    "trigger_reason": trigger_reason,
                    "confidence": confidence,
                },
                user_id=user_id,
                source="proactive_engine",
            )

        logger.info(f"Action proposed: {action.action_id} ({action_type.value})")
        return action

    def execute_action(self, action_id: str) -> bool:
        """
        执行主动行为（通过克制层评估）

        Args:
            action_id: 行为 ID

        Returns:
            是否执行
        """
        action = self._actions.get(action_id)
        if not action:
            logger.warning(f"Action not found: {action_id}")
            return False

        # 通过克制层
        outcome, reason = self._confidence_gate.evaluate(action)
        action.outcome = outcome
        action.outcome_reason = reason

        if outcome == ActionOutcome.EXECUTED:
            action.executed_at = time.time()

            # 发布执行事件
            if self._cognitive_core:
                self._cognitive_core.emit_event(
                    event_type="action.proactive_executed",
                    data={
                        "action_id": action.action_id,
                        "action_type": action.action_type.value,
                        "action_content": action.content,
                    },
                    user_id=action.user_id,
                    source="proactive_engine",
                )

            logger.info(f"Action executed: {action_id} - {action.content}")

            # 更新层摘要
            if self._proactive_layer:
                self._proactive_layer.update_digest("total_actions", len(self._action_history))

            return True
        else:
            logger.info(f"Action {action_id} {outcome.value}: {reason}")
            return False

    # ==========================================
    # 场景检测
    # ==========================================

    def scan_all_users(self):
        """扫描所有用户，检测主动行为机会"""
        # 获取所有已知用户
        user_ids = list(self._user_last_active.keys())

        for user_id in user_ids:
            self._check_greeting_opportunity(user_id)
            self._check_care_opportunity(user_id)

    def record_user_activity(self, user_id: str):
        """记录用户活跃时间"""
        self._user_last_active[user_id] = time.time()

    def _check_greeting_opportunity(self, user_id: str):
        """检查问候机会（长时间未交流）"""
        last_active = self._user_last_active.get(user_id, 0)
        hours_inactive = (time.time() - last_active) / 3600

        # 超过24小时未交流，发送问候
        if hours_inactive > 24:
            self.propose_action(
                action_type=ActionType.GREETING,
                content="好久不见啦，最近过得怎么样？",
                trigger_reason=f"用户已离线 {hours_inactive:.1f} 小时",
                user_id=user_id,
                necessity_score=min(0.9, hours_inactive / 48),
                disturbance_risk=0.4,
            )

    def _check_care_opportunity(self, user_id: str):
        """检查关怀机会"""
        # 简化版：随机检测（实际应从情绪系统获取数据）
        pass

    # ==========================================
    # 查询
    # ==========================================

    def get_action(self, action_id: str) -> Optional[ProposedAction]:
        """获取行为"""
        return self._actions.get(action_id)

    def get_recent_actions(self, user_id: Optional[str] = None, limit: int = 20) -> List[ProposedAction]:
        """获取最近的行为"""
        actions = list(self._actions.values())
        if user_id:
            actions = [a for a in actions if a.user_id == user_id]
        actions.sort(key=lambda a: a.created_at, reverse=True)
        return actions[:limit]

    def get_pending_actions(self) -> List[ProposedAction]:
        """获取待执行的行为"""
        return [a for a in self._actions.values() if a.outcome is None]

    def get_action_stats(self) -> Dict[str, Any]:
        """获取行为统计"""
        total = len(self._actions)
        executed = sum(1 for a in self._actions.values() if a.outcome == ActionOutcome.EXECUTED)
        rejected = sum(1 for a in self._actions.values() if a.outcome == ActionOutcome.REJECTED)
        deferred = sum(1 for a in self._actions.values() if a.outcome == ActionOutcome.DEFERRED)

        by_type: Dict[str, int] = {}
        for a in self._actions.values():
            t = a.action_type.value
            by_type[t] = by_type.get(t, 0) + 1

        return {
            "total_proposed": total,
            "executed": executed,
            "rejected": rejected,
            "deferred": deferred,
            "execution_rate": executed / total if total > 0 else 0,
            "by_type": by_type,
        }

    def get_gate_stats(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """获取克制层统计"""
        if user_id:
            return self._confidence_gate.get_user_stats(user_id)

        all_stats = {}
        for uid in self._user_last_active:
            all_stats[uid] = self._confidence_gate.get_user_stats(uid)
        return all_stats


# ==========================================
# 便捷函数
# ==========================================

_proactive_engine_instance: Optional[ProactiveEngine] = None


def get_proactive_engine() -> ProactiveEngine:
    """获取主动行为引擎单例"""
    global _proactive_engine_instance
    if _proactive_engine_instance is None:
        _proactive_engine_instance = ProactiveEngine()
    return _proactive_engine_instance
