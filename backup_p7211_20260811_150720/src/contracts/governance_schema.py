"""
Phase 5.3: Admin Governance Schema —— Admin 治理层数据契约

设计原则：
- 不引入新的核心 Authority，仅描述 Admin 治理层的请求/响应结构
- 复用已有 GrowthProposal（src.growth.proposal.proposal.GrowthProposal）作为治理载体
- 所有 Admin 治理操作（人格变更建议 / 记忆操作建议 / 成长方案审查）
  均通过 GrowthProposal 形式表达，并持久化到 ProposalStorage

数据流：
    Admin
      ↓
    GovernanceProvider
      ↓
    GrowthProposal (本模块)
      ↓
    ProposalStorage
      ↓
    Growth System / PersonalityAdapter（应用时）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# 治理动作常量
# ============================================================

# MemoryActionProposal 的 action 字段
MEMORY_ACTION_MARK_INCORRECT = "mark_incorrect"   # 标记错误（不删除）
MEMORY_ACTION_DELETE = "delete"                   # 申请删除
MEMORY_ACTION_MERGE = "merge"                     # 申请合并

ALL_MEMORY_ACTIONS = frozenset({
    MEMORY_ACTION_MARK_INCORRECT,
    MEMORY_ACTION_DELETE,
    MEMORY_ACTION_MERGE,
})

# GrowthProposal 审查动作（仅标记状态，不直接 apply）
REVIEW_ACTION_APPROVE = "approve"
REVIEW_ACTION_REJECT = "reject"
REVIEW_ACTION_MODIFY = "modify"

ALL_REVIEW_ACTIONS = frozenset({
    REVIEW_ACTION_APPROVE,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_MODIFY,
})

# 用于在 GrowthProposal.metadata 中标记治理来源
METADATA_GOVERNANCE_SOURCE = "admin_governance"
METADATA_GOVERNANCE_VERSION = "phase5.3"


# ============================================================
# 数据契约
# ============================================================


@dataclass
class GovernanceRequestMeta:
    """Admin 治理请求的元信息（用于审计和追踪）。"""
    actor: str = "admin"
    operator_type: str = "human"   # human / agent / system
    request_id: str = ""
    client_info: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PersonalityChangeRequest:
    """
    Admin 提交的人格变更建议。

    通过 GovernanceProvider 转换为 GrowthProposal 写入 ProposalStorage。
    不直接修改 PersonalityResolver / TraitState / GrowthState。
    """
    trait: str                                # 特质名（如 开放性 / initiative / warmth）
    delta: float                              # 建议调整量（建议值的小幅缩放）
    before_value: Optional[float] = None      # 当前值（由 Provider 读取后填入）
    after_value: Optional[float] = None       # 目标值
    confidence: float = 0.5                   # 置信度（0-1）
    reason: str = ""                          # 变更原因
    priority: str = "medium"                  # low / medium / high
    evidence: List[str] = field(default_factory=list)  # 证据（来源事件、记忆 ID 等）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryActionRequest:
    """
    Admin 提交的记忆处理申请。

    action:
        - "mark_incorrect": 标记为错误（不删除，仅打标）
        - "delete":         申请删除
        - "merge":          申请合并到另一条记忆
    """
    memory_id: str                            # 目标记忆 ID
    action: str                               # 见 ALL_MEMORY_ACTIONS
    reason: str = ""                          # 申请原因
    target_memory_id: Optional[str] = None    # 合并时指定的目标记忆
    priority: str = "medium"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GrowthProposalReviewRequest:
    """
    Admin 提交的生长方案审查动作。

    仅做状态标记（approved / rejected / modified），不直接 apply。
    真正的应用走 PersonalityAdapter / GrowthAccumulator 路径。
    """
    proposal_id: str
    action: str                               # approve / reject / modify
    reason: str = ""
    modified_changes: Optional[List[Dict[str, Any]]] = None  # modify 时使用

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GovernanceSnapshot:
    """治理快照（只读视图）的容器。"""
    section: str                              # personality / memory / growth
    available: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
