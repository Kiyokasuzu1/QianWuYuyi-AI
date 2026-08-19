"""
人格演化记录 (EvolutionRecord) v1.2

职责：
记录一次人格演化的完整信息，包括触发来源、变化详情、
审批决策和追溯原因。作为 TraitState 修改的唯一合法入口。

v1.2 修正：增加 rejected_dimensions 字段
"""

from typing import TypedDict, Dict, List, Optional


class EvolutionRecord(TypedDict, total=False):
    """人格演化记录"""

    # ---- 标识 ----
    record_id: str                          # 唯一标识
    timestamp: str                          # 记录时间

    # ---- 触发来源 ----
    trigger_candidates: List[str]           # 触发此次审批的 trait_candidates
    source_reflection_id: Optional[str]     # 来源反思记录ID
    source_growth_records: List[str]        # 支撑此决策的成长记录ID列表

    # ---- 变化详情 ----
    trait_changes: Dict[str, Dict[str, float]]  # 各维度的变化详情

    # ---- 审批决策 ----
    approved: bool                          # 是否通过审批
    confidence: float                       # 审批置信度
    decision_reason: str                    # 决策原因摘要
    rejection_reasons: Dict[str, str]       # 每个被拒维度的具体原因
    rejected_dimensions: List[str]          # 被拒绝的维度列表

    # ---- 元数据 ----
    evolution_level: str                    # 演化层级
    requires_validation: bool               # 是否需要后续持续验证