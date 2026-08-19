"""
Phase 3.5.13: Growth Approval Layer Schema

定义 GrowthProposal 人工审批生命周期的契约数据结构。

职责：
- 记录每一次审批操作（approve / reject / modify）
- 保留完整审计记录（who / when / what / why / before / after）
- 不自动接受 Proposal（所有状态流转需显式人工触发）

约束：
- 不修改 Personality System
- 不修改 GrowthProposal 的核心字段定义
- 所有审批行为可审计、可追溯

核心结构：
- ApprovalAction: 审批动作类型枚举
- ApprovalRecord: 单次审批记录
- ApprovalHistory: 审批历史集合
- ApprovalSnapshot: 审批管理器快照
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. ApprovalAction: 审批动作类型
# ============================================================

ACTION_APPROVE = "approve"   # 批准提案
ACTION_REJECT = "reject"     # 拒绝提案
ACTION_MODIFY = "modify"     # 修改提案内容后批准

ALL_ACTIONS: List[str] = [ACTION_APPROVE, ACTION_REJECT, ACTION_MODIFY]


# ============================================================
# 2. ChangeDiff: 修改前后的差异记录
# ============================================================

@dataclass
class ChangeDiff:
    """
    单个 ChangeItem 的修改前后差异。

    用于 modify 操作时记录具体改动了什么。
    """
    path: str = ""            # 变更路径（如 "self_state.initiative"）
    before: Any = None        # 修改前的值
    after: Any = None         # 修改后的值
    reason: str = ""          # 修改原因

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 3. ApprovalRecord: 单次审批记录
# ============================================================

@dataclass
class ApprovalRecord:
    """
    审批记录（审计单元）。

    每一次 approve / reject / modify 都生成一条记录，
    保留完整的操作上下文，确保可审计、可追溯。

    设计原则：
    - 不修改 GrowthProposal 的核心字段，仅记录审批动作
    - modify 操作记录 before/after 差异
    - 所有时间戳使用 UTC ISO 格式
    """
    record_id: str = field(default_factory=lambda: f"apr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # --- 目标提案 ---
    proposal_id: str = ""         # 被审批的 GrowthProposal ID
    proposal_snapshot: Dict[str, Any] = field(default_factory=dict)  # 操作时的提案快照

    # --- 审批动作 ---
    action: str = ""              # approve / reject / modify
    actor: str = "human"          # 操作者（默认人工）
    reason: str = ""              # 审批理由

    # --- 状态流转 ---
    before_status: str = ""       # 操作前的提案状态
    after_status: str = ""        # 操作后的提案状态

    # --- 修改差异（仅 modify 操作有值） ---
    changes: List[ChangeDiff] = field(default_factory=list)

    # --- 元数据 ---
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "proposal_id": self.proposal_id,
            "proposal_snapshot": dict(self.proposal_snapshot),
            "action": self.action,
            "actor": self.actor,
            "reason": self.reason,
            "before_status": self.before_status,
            "after_status": self.after_status,
            "changes": [c.to_dict() for c in self.changes],
            "metadata": dict(self.metadata),
        }

    def summary(self) -> str:
        n_changes = len(self.changes)
        return (
            f"[{self.action.upper()}] proposal={self.proposal_id} "
            f"{self.before_status}->{self.after_status} "
            f"changes={n_changes} actor={self.actor}"
        )


# ============================================================
# 4. ApprovalHistory: 审批历史集合
# ============================================================

@dataclass
class ApprovalHistory:
    """
    审批历史集合。

    存储所有审批记录，提供统计与查询能力。
    """
    records: List[ApprovalRecord] = field(default_factory=list)

    # --- 统计 ---
    total_approvals: int = 0
    total_rejections: int = 0
    total_modifications: int = 0

    def add(self, record: ApprovalRecord) -> None:
        """添加一条审批记录"""
        self.records.append(record)
        if record.action == ACTION_APPROVE:
            self.total_approvals += 1
        elif record.action == ACTION_REJECT:
            self.total_rejections += 1
        elif record.action == ACTION_MODIFY:
            self.total_modifications += 1

    def get_by_proposal(self, proposal_id: str) -> List[ApprovalRecord]:
        """获取指定提案的审批历史"""
        return [r for r in self.records if r.proposal_id == proposal_id]

    def get_recent(self, limit: int = 50) -> List[ApprovalRecord]:
        """获取最近的审批记录（最新在前）"""
        sorted_records = sorted(self.records, key=lambda r: r.timestamp, reverse=True)
        return sorted_records[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "records": [r.to_dict() for r in self.records],
            "total_approvals": self.total_approvals,
            "total_rejections": self.total_rejections,
            "total_modifications": self.total_modifications,
            "total_records": len(self.records),
        }


# ============================================================
# 5. ApprovalSnapshot: 审批管理器快照
# ============================================================

@dataclass
class ApprovalSnapshot:
    """
    审批管理器状态快照（用于审计和调试）。
    """
    snapshot_id: str = field(default_factory=lambda: f"apsnap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    enabled: bool = False
    total_records: int = 0
    total_approvals: int = 0
    total_rejections: int = 0
    total_modifications: int = 0
    pending_count: int = 0

    last_record_id: str = ""
    last_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
