"""
成长系统数据模型 (Growth Schema) v0.21

定义羽依成长事件的标准结构和系统常量。
"""

from typing import Dict, List, TypedDict, Optional, Literal


# ============================================================
# GrowthEvent 数据结构
# ============================================================
class GrowthEvent(TypedDict, total=False):
    # ---- 原始事件 ----
    event_id: str
    event: str
    event_type: Literal[
        "identity", "milestone", "creation", "preference", "relationship"
    ]
    canonical_topic: str
    evidence: List[Dict]
    importance: float

    # ---- 评估元数据 ----
    confidence: float            # 综合可信度
    source_reliability: float    # 来源可靠度
    stability: float             # 时间与次数稳定度
    consistency: float           # 语义一致性（当前基于主题，未来可引入embedding）
    impact: float                # 潜在影响等级

    # ---- 成长控制 ----
    growth_allowed: bool
    growth_level: Literal["trace", "context", "preference", "trait"]
    growth_domain: Literal[
        "knowledge", "preference", "expression", "capability", "relationship_context"
    ]
    max_allowed_level: str       # 该领域允许的最高成长层级
    evidence_quality: float      # 证据质量

    # ---- 影响候选 ----
    growth_signal: str
    target_candidates: List[str]

    # ---- 应用结果 ----
    applied_delta: Optional[float]
    resolver_decision: Optional[str]

    # ---- 历史 ----
    first_seen: Optional[str]
    last_seen: Optional[str]
    occurrence_count: int

    # ---- 元数据 ----
    schema_version: str          # 当前 schema 版本
    evaluator_version: str       # 评估器版本


# ============================================================
# 系统常量
# ============================================================

# 事件类型基础可信度（行为 > 声明）
TYPE_WEIGHTS: Dict[str, float] = {
    "creation": 0.9,
    "preference": 0.85,
    "milestone": 0.8,
    "identity": 0.75,
    "relationship": 0.4,
}

# 来源可靠度
SOURCE_RELIABILITY: Dict[str, float] = {
    "user_behavior": 1.0,       # 用户的实际行为记录
    "user_statement": 0.8,      # 用户明确表达
    "llm_inference": 0.4,       # LLM从上下文中推断
    "context_guess": 0.2,       # 上下文猜测
}

# 领域成长上限
DOMAIN_MAX_LEVEL: Dict[str, str] = {
    "knowledge": "trait",
    "preference": "preference",
    "expression": "preference",
    "capability": "trait",
    "relationship_context": "context",  # 硬限制：永远不能进入 preference/trait
}

# 事件类型基础影响（结合行为证据权重）
TYPE_IMPACT_BASE: Dict[str, float] = {
    "preference": 0.1,
    "creation": 0.15,
    "relationship": 0.05,
    "identity": 0.25,
    "milestone": 0.3,
    "growth_support": 0.2,
}

# 人格维度最大变化量
MAX_GROWTH_PER_DIMENSION = 0.15    # 年度最大变化
MAX_SINGLE_EVENT_DELTA = 0.01      # 单次事件最大变化
MAX_EVENT_IMPACT = 0.35            # 单事件最大影响

# 成长层级对应的变化范围
GROWTH_LEVEL_DELTA_RANGE = {
    "trace": (0.0, 0.0),
    "context": (0.001, 0.003),
    "preference": (0.003, 0.005),
    "trait": (0.005, 0.01),
}

# 成长信号到候选人格维度的映射（多候选，由Resolver决定）
GROWTH_SIGNAL_CANDIDATES: Dict[str, List[str]] = {
    "creative_activity_interest": ["creativity", "curiosity", "self_expression"],
    "complex_problem_solving": ["analytical", "confidence", "curiosity"],
    "social_interaction_preference": ["playfulness", "expressiveness"],
    "knowledge_exploration": ["curiosity", "analytical"],
    "self_expression_growth": ["self_expression", "confidence"],
    "emotional_understanding": ["expressiveness"],
    "general_preference": ["curiosity", "preference_learning"],  # 通用偏好
}


# 当前版本
SCHEMA_VERSION = "0.21"
EVALUATOR_VERSION = "1.1"