from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
import uuid


@dataclass
class YuyiEvent:
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    event_type: str = "unknown"
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    source: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "source": self.source,
            "data": self.data,
            "metadata": self.metadata,
        }


class EventType:
    MESSAGE_RECEIVED = "message.received"
    MESSAGE_RESPONDED = "message.responded"

    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"

    PERSONALITY_CHANGED = "personality.changed"
    TRAIT_UPDATED = "trait.updated"

    EMOTION_CHANGED = "emotion.changed"

    RELATIONSHIP_CHANGED = "relationship.changed"
    RELATIONSHIP_LEVEL_UPDATED = "relationship.level_updated"

    GROWTH_EVENT_DETECTED = "growth.event_detected"
    GROWTH_PROPOSAL_CREATED = "growth.proposal_created"
    GROWTH_PROPOSAL_APPROVED = "growth.proposal_approved"
    GROWTH_PROPOSAL_REJECTED = "growth.proposal_rejected"
    GROWTH_APPLIED = "growth.applied"

    AUDIT_LOG_RECORDED = "audit.log_recorded"

    # Phase C.10.6: Runtime Control Plane 事件
    RUNTIME_CONTROL_CHANGED = "runtime.control_changed"
    RUNTIME_CONTROL_SKIPPED = "runtime.control_skipped"

    # Phase C.10.7: Runtime Policy Engine 事件
    RUNTIME_POLICY_DECISION = "runtime.policy_decision"
    RUNTIME_THROTTLE_DECISION = "runtime.throttle_decision"

    # Phase C.10.9: Runtime Policy Feedback 事件
    RUNTIME_POLICY_FEEDBACK = "runtime.policy_feedback"

    # Phase C.10.10: Policy Lifecycle 事件
    RUNTIME_POLICY_PROPOSAL_APPROVED = "runtime.policy_proposal_approved"
    RUNTIME_POLICY_APPLIED = "runtime.policy_applied"
    RUNTIME_POLICY_ROLLBACK = "runtime.policy_rollback"


@dataclass
class MessageReceivedEvent(YuyiEvent):
    event_type: str = EventType.MESSAGE_RECEIVED
    user_id: str = ""
    content: str = ""


@dataclass
class MessageRespondedEvent(YuyiEvent):
    event_type: str = EventType.MESSAGE_RESPONDED
    user_id: str = ""
    content: str = ""
    response_time_ms: int = 0


@dataclass
class MemoryCreatedEvent(YuyiEvent):
    event_type: str = EventType.MEMORY_CREATED
    memory_id: str = ""
    user_id: str = ""
    content: str = ""


@dataclass
class PersonalityChangedEvent(YuyiEvent):
    event_type: str = EventType.PERSONALITY_CHANGED
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class RelationshipChangedEvent(YuyiEvent):
    event_type: str = EventType.RELATIONSHIP_CHANGED
    user_id: str = ""
    dimension: str = ""
    old_value: float = 0.0
    new_value: float = 0.0
    reason: str = ""


@dataclass
class EmotionChangedEvent(YuyiEvent):
    event_type: str = EventType.EMOTION_CHANGED
    user_id: str = ""


@dataclass
class GrowthProposalEvent(YuyiEvent):
    event_type: str = EventType.GROWTH_PROPOSAL_CREATED
    proposal_id: str = ""
    proposal_type: str = ""
    affected_dimensions: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""


@dataclass
class RuntimeControlChangedEvent(YuyiEvent):
    """Phase C.10.6 — Runtime Control 状态变化事件。

    当 RuntimeControlProvider 检测到 ControlState 字段发生
    实际变化(true -> false / false -> true)时,会发出此事件。

    字段:
    - module: 发生变化的模块名(runtime / memory / emotion / ...)
    - old_value: 旧值(True/False/None 表示未知)
    - new_value: 新值(True/False)
    - source: 变化来源(runtime / desktop / manager / system)
    """
    event_type: str = EventType.RUNTIME_CONTROL_CHANGED
    module: str = ""
    old_value: Optional[bool] = None
    new_value: bool = False
    source: str = "runtime"


@dataclass
class RuntimeControlSkippedEvent(YuyiEvent):
    """Phase C.10.6 — Runtime Control 跳过事件。

    当 Runtime 在 cycle 中因为 ControlState 跳过某个模块时,发出此事件。
    """
    event_type: str = EventType.RUNTIME_CONTROL_SKIPPED
    module: str = ""
    cycle_id: str = ""
    reason: str = ""


@dataclass
class RuntimePolicyDecisionEvent(YuyiEvent):
    """Phase C.10.7 — Runtime Policy Engine 决策事件。

    当 PolicyEngine.evaluate() 对某个模块产出决策时,Runtime 会发出此事件。
    事件包含最关键的决策字段,完整结构可从 metadata 或 audit 中读取。

    字段:
    - module:    被评估的模块名(memory/emotion/growth/...)
    - allowed:   是否允许执行
    - reason:    决策原因(简短文本)
    - mode:      运行时模式(normal/safe/maintenance/unknown)
    - cycle_id:  Runtime cycle ID(用于审计关联)
    - readonly:  是否只读
    - throttle:  节流倍率
    - rule:      命中规则的名称(若能取到)
    """
    event_type: str = EventType.RUNTIME_POLICY_DECISION
    module: str = ""
    allowed: bool = True
    reason: str = ""
    mode: str = "normal"
    cycle_id: str = ""
    readonly: bool = False
    throttle: float = 1.0
    rule: str = ""


@dataclass
class RuntimeThrottleDecisionEvent(YuyiEvent):
    """Phase C.10.8 — Runtime Throttle / Budget 决策事件。

    当 AdaptivePolicyLayer 对某个模块产出 throttle/budget 相关决策时,Runtime 会发出此事件。
    与 RuntimePolicyDecisionEvent 的区别:本事件专注于"频率/预算"层面。

    字段:
    - module:    被评估的模块名
    - throttle:  节流倍率(0.0 ~ 1.0)
    - reason:    决策原因(in_cooldown / interval_not_reached / budget_exceeded / throttle_ok / ...)
    - cycle_id:  Runtime cycle ID
    - allowed:   是否仍允许执行(可被 cooldown/interval 设为 False)
    - cooldown:  冷却剩余秒数
    - interval:  执行间隔(cycle 数)
    - cost:      本次执行预计成本
    - rule:      命中的节流/预算规则名称
    """
    event_type: str = EventType.RUNTIME_THROTTLE_DECISION
    module: str = ""
    throttle: float = 1.0
    reason: str = ""
    cycle_id: str = ""
    allowed: bool = True
    cooldown: float = 0.0
    interval: int = 0
    cost: float = 0.0
    rule: str = ""


@dataclass
class RuntimePolicyFeedbackEvent(YuyiEvent):
    """Phase C.10.9 — Runtime Policy Feedback 事件。

    当 PolicyFeedbackEngine 生成 PolicyAdjustmentProposal 时,Runtime 会发出此事件。
    与 RuntimePolicyDecisionEvent / RuntimeThrottleDecisionEvent 的区别:
    本事件专注于"自适应反馈"层面,代表 feedback loop 的输出。

    字段:
    - module:     被评估的模块名
    - metric:     指标 / 参数名(throttle.interval / budget.module_cost / ...)
    - value:      建议值(字符串形式)
    - proposal:   完整 PolicyAdjustmentProposal 实例
    - confidence: 置信度 0.0 ~ 1.0
    - cycle_id:   触发反馈的 cycle
    - old_value:  旧值
    - reason:     反馈理由
    - direction:  increase / decrease / no_change
    - evidence:   决策依据(metrics snapshot)
    """
    event_type: str = EventType.RUNTIME_POLICY_FEEDBACK
    module: str = ""
    metric: str = ""
    value: str = ""
    proposal: Any = None
    confidence: float = 0.0
    cycle_id: str = ""
    old_value: Any = None
    reason: str = ""
    direction: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimePolicyProposalApprovedEvent(YuyiEvent):
    """Phase C.10.10 — Proposal 批准事件。

    当 PolicyLifecycleManager.approve() 成功时发布。
    字段:
    - proposal_id:  proposal 唯一 ID
    - module:       模块名
    - parameter:    参数名
    - reviewer:     审核人/系统标识
    - record_id:    对应的 approval record_id
    """
    event_type: str = EventType.RUNTIME_POLICY_PROPOSAL_APPROVED
    proposal_id: str = ""
    module: str = ""
    parameter: str = ""
    reviewer: str = ""
    record_id: str = ""


@dataclass
class RuntimePolicyAppliedEvent(YuyiEvent):
    """Phase C.10.10 — Proposal apply 事件。

    当 PolicyLifecycleManager.apply() 成功(并写入运行时 Policy)时发布。
    字段:
    - proposal_id:  proposal 唯一 ID
    - module:       模块名
    - old_value:    旧值
    - new_value:    新值
    - success:      apply 是否成功
    - snapshot_id:  对应 snapshot(用于回滚)
    """
    event_type: str = EventType.RUNTIME_POLICY_APPLIED
    proposal_id: str = ""
    module: str = ""
    old_value: Any = None
    new_value: Any = None
    success: bool = True
    snapshot_id: str = ""


@dataclass
class RuntimePolicyRollbackEvent(YuyiEvent):
    """Phase C.10.10 — Proposal rollback 事件。

    当 PolicyLifecycleManager.rollback() 成功时发布。
    字段:
    - proposal_id:  proposal 唯一 ID
    - reason:       回滚原因
    - snapshot_id:  回滚依据的 snapshot
    - success:      rollback 是否成功
    """
    event_type: str = EventType.RUNTIME_POLICY_ROLLBACK
    proposal_id: str = ""
    reason: str = ""
    snapshot_id: str = ""
    success: bool = True