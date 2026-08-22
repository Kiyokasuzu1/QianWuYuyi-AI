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

# v1.3 Agency Phase 1: Goal 治理骨架(提案信封 + 状态存储 + 消费器 + 来源校验)
from src.goal.goal_proposal import (
    GOAL_PAYLOAD_KEY,
    build_goal_proposal,
    extract_goal_payload,
)
from src.goal.goal_state import (
    DEFAULT_GOAL_STATE_PATH,
    GOAL_STATUS,
    GOAL_STATUS_SET,
    GoalStateStore,
)
from src.goal.goal_source_validation import validate_goal_source_refs
from src.goal.goal_approved_drain import (
    DRAIN_ACTOR,
    drain_approved_goal_proposals,
)
# v1.3 Agency Phase 2: GoalResolver（只读 GoalContext）
from src.goal.goal_resolver import (
    DEFAULT_MAX_ACTIVE_GOALS,
    GOAL_CONTEXT_SCHEMA_VERSION,
    resolve_active_goals,
    format_goal_context,
    resolve_goal_context_text,
)
# v1.3 Agency Phase 3: Evidence Pattern Detection（Candidate 生命周期）
from src.goal.goal_pattern_detector import (
    DETECTOR_VERSION,
    DEFAULT_CANDIDATE_PATH,
    MIN_EVIDENCE,
    DEFAULT_SIMILARITY_THRESHOLD,
    FORBIDDEN_SOURCE_TYPES,
    GoalCandidateStore,
    detect_candidates,
)
from src.goal.goal_candidate_bridge import (
    BRIDGE_SOURCE,
    bridge_candidate_to_proposal,
)
# v1.3 Agency Phase 4: GoalProductionRunner（OFF→SHADOW→ACTIVE 生产接线）
from src.goal.goal_production_runner import (
    GOAL_DETECTION_MODES,
    DEFAULT_MODE,
    DEFAULT_METRICS_PATH,
    MAX_CANDIDATES_PER_RUN,
    GoalProductionRunner,
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
    "GOAL_PAYLOAD_KEY",
    "build_goal_proposal",
    "extract_goal_payload",
    "DEFAULT_GOAL_STATE_PATH",
    "GOAL_STATUS",
    "GOAL_STATUS_SET",
    "GoalStateStore",
    "validate_goal_source_refs",
    "DRAIN_ACTOR",
    "drain_approved_goal_proposals",
    "DEFAULT_MAX_ACTIVE_GOALS",
    "GOAL_CONTEXT_SCHEMA_VERSION",
    "resolve_active_goals",
    "format_goal_context",
    "resolve_goal_context_text",
    "DETECTOR_VERSION",
    "DEFAULT_CANDIDATE_PATH",
    "MIN_EVIDENCE",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "FORBIDDEN_SOURCE_TYPES",
    "GoalCandidateStore",
    "detect_candidates",
    "BRIDGE_SOURCE",
    "bridge_candidate_to_proposal",
    "GOAL_DETECTION_MODES",
    "DEFAULT_MODE",
    "DEFAULT_METRICS_PATH",
    "MAX_CANDIDATES_PER_RUN",
    "GoalProductionRunner",
]