"""
Cognitive Schema —— 认知决策层契约

定义 CognitiveEngine 与 RuntimeCore 之间的数据交换格式。
与 src/contracts/runtime_schema.py 风格一致，使用 dataclass。

设计原则：
- LLM 只生成 Intention，不直接生成 Action
- DecisionValidator 负责安全检查
- 所有字段可序列化，便于日志和审计
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ==================== 认知上下文 ====================

@dataclass
class PersonalitySnapshot:
    """人格快照（只读，不修改核心）"""
    warmth: float = 0.5
    shyness: float = 0.5
    initiative: float = 0.3
    care_level: float = 0.3
    self_expression: float = 0.3
    sensitivity: float = 0.5
    dependence: float = 0.4
    attachment_level: str = "探索"
    interaction_familiarity_level: str = "试探"
    behaviors: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MemorySnapshot:
    """记忆快照（只读，第一阶段为接口）"""
    recent_topics: List[str] = field(default_factory=list)
    user_preferences: Dict[str, Any] = field(default_factory=dict)
    last_interaction_summary: str = ""
    emotional_highlights: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class RelationshipSnapshot:
    """关系快照（只读，第一阶段为接口）"""
    trust_level: float = 0.5
    familiarity: float = 0.3
    intimacy: float = 0.2
    last_contact_hours: float = 0.0
    interaction_pattern: str = "regular"


@dataclass
class DecisionContext:
    """
    决策上下文

    CognitiveEngine 的输入：将 Runtime 状态打包为认知层可用的上下文。
    """
    context_id: str = field(default_factory=lambda: f"ctx_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=_now_iso)

    # 核心状态
    self_state: Dict[str, Any] = field(default_factory=dict)
    world_state: Dict[str, Any] = field(default_factory=dict)

    # 认知模块快照（第一阶段为可选接口）
    personality: Optional[PersonalitySnapshot] = None
    memory: Optional[MemorySnapshot] = None
    relationship: Optional[RelationshipSnapshot] = None

    # 触发原因
    trigger: str = "tick"
    trigger_event: Optional[Dict[str, Any]] = None

    # 历史约束
    recent_intentions: List[Dict[str, Any]] = field(default_factory=list)
    last_action_time: Optional[float] = None


# ==================== 意图与推理 ====================

@dataclass
class ReasoningTrace:
    """推理轨迹 —— 记录 LLM 的思考过程，便于审计和调试"""
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:8]}")
    reasoning: str = ""                          # LLM 的思考文本
    self_assessment: str = ""                    # LLM 对自身状态的理解
    situational_analysis: str = ""               # 对当前情境的分析
    expected_user_reaction: str = ""             # 预期用户反应
    confidence: float = 0.0                      # LLM 自评置信度


@dataclass
class Intention:
    """
    候选意图 —— 由 LLM 生成，尚未验证

    注意：这不是 Action。Action 由 DecisionValidator 在验证后生成。
    """
    intention_id: str = field(default_factory=lambda: f"int_{uuid.uuid4().hex[:8]}")
    action_type: str = ""                        # 建议的行动类型
    content: str = ""                            # 建议的内容/消息
    target: str = "user"                         # 目标对象
    urgency: float = 0.5                         # 紧急程度 0-1
    priority: float = 0.5                        # 优先级 0-1

    # 元信息
    reasoning: Optional[ReasoningTrace] = None
    personality_alignment: float = 0.5           # 与人格的一致性
    safety_score: float = 1.0                    # 安全评分（初始为1，由验证器修改）

    # 约束标记
    violates_constraints: List[str] = field(default_factory=list)
    requires_approval: bool = False


@dataclass
class CognitiveConstraint:
    """认知约束 —— 定义什么可以做，什么不可以"""
    max_daily_initiatives: int = 10              # 每日最大主动行为数
    min_interval_seconds: float = 300.0          # 两次主动行为最小间隔
    forbidden_topics: List[str] = field(default_factory=list)
    required_tone: str = "gentle"                # 必需语气
    max_message_length: int = 500                # 最大消息长度
    allow_proactive: bool = True                 # 是否允许主动行为


@dataclass
class DecisionIntent:
    """
    经过验证的决策意图 —— 可转为 Decision/Action

    由 DecisionValidator 从 Intention 生成。
    """
    intent_id: str = field(default_factory=lambda: f"di_{uuid.uuid4().hex[:8]}")
    source_intention_id: str = ""
    action_type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.0
    validator_notes: List[str] = field(default_factory=list)
    approved: bool = False


@dataclass
class CognitiveResult:
    """CognitiveEngine 的输出结果"""
    result_id: str = field(default_factory=lambda: f"cr_{uuid.uuid4().hex[:8]}")
    context_id: str = ""
    intentions: List[Intention] = field(default_factory=list)
    validated_intents: List[DecisionIntent] = field(default_factory=list)
    rejected_intentions: List[Intention] = field(default_factory=list)
    processing_time_ms: float = 0.0
    used_llm: bool = False
