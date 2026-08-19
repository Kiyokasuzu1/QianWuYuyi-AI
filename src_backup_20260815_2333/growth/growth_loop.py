"""
Autonomous Growth Loop

羽依自主成长循环

设计目标：
- 羽依自主提出成长方案
- 用户审批层（Human Approval Layer）
- 安全边界保护

成长循环流程：
发现问题 -> 提出方案 -> 模拟影响 -> 用户批准 -> 执行 -> 记录

使用方式：
    from src.growth.growth_loop import GrowthLoop

    growth = GrowthLoop()
    growth.start()

    # 用户批准成长方案
    growth.approve_growth(proposal_id, user_id="user_001")
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from src.core.yuyi_cognitive_core import (
    CognitiveLayerBase,
    get_cognitive_core,
)
from src.contracts.cognitive_event_types import (
    GrowthProposalCreatedEvent,
    GrowthAppliedEvent,
    CognitiveEvent,
)

logger = logging.getLogger(__name__)


class GrowthStatus(Enum):
    """成长方案状态"""
    PROPOSED = "proposed"
    SIMULATED = "simulated"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class GrowthCategory(Enum):
    """成长类别"""
    PERSONALITY = "personality"
    BEHAVIOR = "behavior"
    RELATIONSHIP = "relationship"
    KNOWLEDGE = "knowledge"
    EMOTION = "emotion"


@dataclass
class ImpactPreview:
    """影响预览"""
    category: str
    before: Any
    after: Any
    risk_level: str  # low / medium / high
    confidence: float
    description: str


@dataclass
class GrowthProposal:
    """
    [DEPRECATED][Authority Registry v1.0] GrowthProposal（growth_loop.py 内重名定义）。

    DEPRECATED SINCE: Phase 4.0-R1
    REASON:       与 `src/contracts/growth_schema.py::GrowthProposal` **同名不同 schema**，
                  造成提案双轨和数据不一致风险。
    CANONICAL:    `src/contracts/growth_schema.py::GrowthProposal`（schema 冻结）。
    MIGRATION:    Phase 4.0-R3 彻底移除本类；当前仅供历史 growth_loop 流程读取兼容。

    成长方案（Legacy schema，禁止用于新的提案写入）。
    """
    proposal_id: str
    category: GrowthCategory
    description: str
    requires_approval: bool

    # 状态
    status: GrowthStatus = GrowthStatus.PROPOSED

    # 影响预览
    impact_previews: List[ImpactPreview] = field(default_factory=list)

    # 时间
    created_at: float = 0.0
    approved_at: float = 0.0
    applied_at: float = 0.0

    # 审批信息
    approved_by: Optional[str] = None
    approval_reason: str = ""

    # 应用结果
    changes_applied: List[Dict[str, Any]] = field(default_factory=list)
    rollback_possible: bool = True

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.proposal_id:
            self.proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()

    def approve(self, user_id: str, reason: str = ""):
        """批准方案"""
        self.status = GrowthStatus.APPROVED
        self.approved_by = user_id
        self.approval_reason = reason
        self.approved_at = time.time()

    def reject(self, reason: str = ""):
        """拒绝方案"""
        self.status = GrowthStatus.REJECTED

    def apply(self):
        """应用方案"""
        self.status = GrowthStatus.APPLIED
        self.applied_at = time.time()

    def rollback(self):
        """回滚方案"""
        self.status = GrowthStatus.ROLLED_BACK


class GrowthLayer(CognitiveLayerBase):
    """
    成长认知层

    集成到 YuyiCognitiveCore
    """

    def __init__(self, growth_loop: "GrowthLoop"):
        super().__init__("growth")
        self._growth_loop = growth_loop

    def on_reflection_completed(self, event: CognitiveEvent):
        """反思完成可能触发成长提案"""
        growth_proposals = event.data.get("growth_proposals", [])
        for proposal in growth_proposals:
            self._growth_loop.propose_growth(
                category=GrowthCategory.PERSONALITY,
                description=proposal,
                requires_approval=True,
            )


class GrowthLoop:
    """
    自主成长循环

    管理羽依的成长方案生命周期
    """

    def __init__(self):
        # 方案存储
        self._proposals: Dict[str, GrowthProposal] = {}
        self._pending_approvals: List[str] = []

        # 认知核心
        self._cognitive_core = None

        # 成长层
        self._growth_layer: Optional[GrowthLayer] = None

        # 状态
        self._running = False
        self._lock = threading.Lock()

        logger.info("GrowthLoop initialized")

    def start(self) -> bool:
        """启动成长循环"""
        if self._running:
            return True

        logger.info("Starting GrowthLoop")

        try:
            self._cognitive_core = get_cognitive_core()
            if not self._cognitive_core.is_running():
                self._cognitive_core.start()

            self._growth_layer = GrowthLayer(self)
            self._cognitive_core.register_layer("growth", self._growth_layer)

            self._growth_layer._status = "running"
            self._growth_layer.update_digest("pending_proposals", 0)

            self._running = True
            logger.info("GrowthLoop started successfully")
            return True

        except Exception as e:
            logger.error(f"GrowthLoop start failed: {e}")
            return False

    def stop(self) -> bool:
        """停止成长循环"""
        if not self._running:
            return True

        self._running = False
        logger.info("GrowthLoop stopped")
        return True

    # ==========================================
    # 方案创建
    # ==========================================

    def propose_growth(
        self,
        category: GrowthCategory,
        description: str,
        requires_approval: bool = True,
        impact_previews: Optional[List[ImpactPreview]] = None,
        metadata: Optional[Dict] = None,
    ) -> GrowthProposal:
        """
        提出成长方案

        Args:
            category: 成长类别
            description: 描述
            requires_approval: 是否需要用户审批
            impact_previews: 影响预览列表
            metadata: 元数据

        Returns:
            创建的方案
        """
        with self._lock:
            proposal = GrowthProposal(
                proposal_id=f"prop_{uuid.uuid4().hex[:12]}",
                category=category,
                description=description,
                requires_approval=requires_approval,
                impact_previews=impact_previews or [],
                metadata=metadata or {},
            )

            self._proposals[proposal.proposal_id] = proposal

            if requires_approval:
                self._pending_approvals.append(proposal.proposal_id)
                proposal.status = GrowthStatus.PENDING_APPROVAL

            # 发布事件
            if self._cognitive_core:
                self._cognitive_core.emit_event(
                    event_type="growth.proposal_created",
                    data={
                        "proposal_id": proposal.proposal_id,
                        "proposal_type": category.value,
                        "description": description,
                        "impact_preview": {
                            p.category: {
                                "before": p.before,
                                "after": p.after,
                                "risk_level": p.risk_level,
                            }
                            for p in proposal.impact_previews
                        },
                        "requires_approval": requires_approval,
                    },
                    source="growth_loop",
                )

            # 更新层摘要
            if self._growth_layer:
                self._growth_layer.update_digest(
                    "pending_proposals",
                    len(self._pending_approvals),
                )

            logger.info(f"Growth proposed: {proposal.proposal_id} ({category.value})")
            return proposal

    # ==========================================
    # 安全边界
    # ==========================================

    def approve_growth(self, proposal_id: str, user_id: str, reason: str = "") -> bool:
        """
        用户批准成长方案

        Args:
            proposal_id: 方案 ID
            user_id: 用户 ID
            reason: 批准原因

        Returns:
            是否成功
        """
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                logger.warning(f"Proposal not found: {proposal_id}")
                return False

            if proposal.status != GrowthStatus.PENDING_APPROVAL:
                logger.warning(f"Proposal {proposal_id} not pending approval")
                return False

            # 检查高风险方案
            high_risk = any(
                p.risk_level == "high" for p in proposal.impact_previews
            )
            if high_risk:
                logger.info(f"High-risk proposal {proposal_id} approved")

            proposal.approve(user_id, reason)

            if proposal_id in self._pending_approvals:
                self._pending_approvals.remove(proposal_id)

            # 应用方案
            self._apply_proposal(proposal)

            logger.info(f"Growth approved and applied: {proposal_id}")
            return True

    def reject_growth(self, proposal_id: str, reason: str = "") -> bool:
        """
        拒绝成长方案

        Args:
            proposal_id: 方案 ID
            reason: 拒绝原因

        Returns:
            是否成功
        """
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return False

            proposal.reject(reason)

            if proposal_id in self._pending_approvals:
                self._pending_approvals.remove(proposal_id)

            logger.info(f"Growth rejected: {proposal_id} - {reason}")
            return True

    def _apply_proposal(self, proposal: GrowthProposal):
        """应用方案"""
        proposal.apply()

        # 记录变化
        changes = [
            {
                "category": p.category,
                "before": p.before,
                "after": p.after,
            }
            for p in proposal.impact_previews
        ]
        proposal.changes_applied = changes

        # 发布事件
        if self._cognitive_core:
            self._cognitive_core.emit_event(
                event_type="growth.applied",
                data={
                    "proposal_id": proposal.proposal_id,
                    "changes_applied": changes,
                    "approved_by": proposal.approved_by,
                },
                source="growth_loop",
            )

    # ==========================================
    # 查询
    # ==========================================

    def get_proposal(self, proposal_id: str) -> Optional[GrowthProposal]:
        """获取方案"""
        return self._proposals.get(proposal_id)

    def get_pending_proposals(self) -> List[GrowthProposal]:
        """获取待审批方案"""
        return [
            self._proposals[pid]
            for pid in self._pending_approvals
            if pid in self._proposals
        ]

    def get_proposals_by_status(self, status: GrowthStatus) -> List[GrowthProposal]:
        """按状态获取方案"""
        return [p for p in self._proposals.values() if p.status == status]

    def get_growth_stats(self) -> Dict[str, Any]:
        """获取成长统计"""
        total = len(self._proposals)
        pending = len(self._pending_approvals)
        approved = len(self.get_proposals_by_status(GrowthStatus.APPROVED))
        applied = len(self.get_proposals_by_status(GrowthStatus.APPLIED))
        rejected = len(self.get_proposals_by_status(GrowthStatus.REJECTED))

        return {
            "total_proposals": total,
            "pending_approvals": pending,
            "approved": approved,
            "applied": applied,
            "rejected": rejected,
        }


# ==========================================
# 便捷函数
# ==========================================

_growth_loop_instance: Optional[GrowthLoop] = None


def get_growth_loop() -> GrowthLoop:
    """获取成长循环单例"""
    global _growth_loop_instance
    if _growth_loop_instance is None:
        _growth_loop_instance = GrowthLoop()
    return _growth_loop_instance
