# -*- coding: utf-8 -*-
"""
src/growth/proposal/proposal.py

.. deprecated::
    Phase 3.6.4 起，本模块的 ``GrowthProposal`` dataclass 已被标记为 deprecated。

    Deprecated:
        This schema is retained only for compatibility.
        New code should use ``src.contracts.growth_schema.GrowthProposal``.

    Replacement:
        ``src.contracts.growth_schema.GrowthProposal`` (canonical schema)
        + ``src.contracts.proposal_normalizer.GrowthProposalNormalizer``
          (双 Schema 转换层，lossless)
        + ``src.admin.normalized_proposal_translator.NormalizedProposalTranslator``
          (业务层 GrowthProposal 统一入口)

    Migration:
        1. 业务模块（selfmodel_consumer / personality_adapter / approval_manager 等）
           不应再直接 import 本类，应通过 NormalizedProposalTranslator 入口。
        2. 如需构造新 proposal 用于治理：
              from src.contracts.growth_schema import GrowthProposal
              p = GrowthProposal(id=..., proposed_changes=..., ...)
        3. legacy proposal 输入到下游前：
              from src.contracts.proposal_normalizer import normalize_to_canonical
              canonical = normalize_to_canonical(legacy_proposal_dict)

    保留原因：
        - 兼容 Admin GovernanceProvider 的 storage path（输入兼容层）
        - 兼容 ProposalStorage / ProposalReviewer 的历史持久化格式
        - 兼容 Phase 5.5.x 镜像集成路径
        - 提供回滚路径（不影响在线系统）

    后续计划：
        - Phase 3.6.4：标记 deprecated（当前阶段）
        - 后续阶段：在保证 backward compatibility 的前提下逐步迁移到 canonical schema

设计说明：
- 字段保持原状（proposal_id / timestamp / proposal_type / status / source /
  source_event_id / user_id / affected_dimensions / before_state / after_state /
  confidence / reason / evidence / priority / reviewer_id / review_comment /
  reviewed_at / applied_at / applied_by / expires_at / metadata）
- 不修改字段名、字段类型、字段顺序
- 不删除任何方法（is_expired / is_pending / get_total_delta 等）

Import Direction（Phase 3.6.4 起）：
- 允许：storage / reviewer / governance_provider（producer / storage 兼容层）
- 允许：tests（验证向后兼容）
- 禁止：consumer / personality_adapter / approval_manager / runtime 主链路
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional, List
import uuid

from src.growth.proposal.constants import PROPOSAL_STATUS, PROPOSAL_TYPE, PRIORITY_LEVEL


# Phase 3.6.4: 标记类为 deprecated
# 保留所有字段与行为，仅添加 deprecation 元信息，避免破坏现有调用方
_LEGACY_SCHEMA_DEPRECATED = True
_LEGACY_SCHEMA_SINCE = "3.6.4"
_LEGACY_SCHEMA_REPLACEMENT = "src.contracts.growth_schema.GrowthProposal"


@dataclass
class GrowthProposal:  # noqa: F401  # Phase 3.6.4: retained for backward compatibility
    """
    Legacy GrowthProposal（governance / storage schema）。

    .. deprecated::
        Phase 3.6.4 起已标记为 deprecated。
        新代码请使用 :class:`src.contracts.growth_schema.GrowthProposal` (canonical schema)，
        并通过 :class:`src.contracts.proposal_normalizer.GrowthProposalNormalizer` 或
        :class:`src.admin.normalized_proposal_translator.NormalizedProposalTranslator`
        处理 schema 转换。

    Replacement:
        ``src.contracts.growth_schema.GrowthProposal`` (canonical schema)。

    Migration:
        见模块级 docstring。
    """
    proposal_id: str = field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    proposal_type: str = PROPOSAL_TYPE["PERSONALITY"]
    status: str = PROPOSAL_STATUS["PENDING"]

    source: str = ""
    source_event_id: str = ""
    user_id: str = ""

    affected_dimensions: Dict[str, float] = field(default_factory=dict)
    before_state: Dict[str, float] = field(default_factory=dict)
    after_state: Dict[str, float] = field(default_factory=dict)

    confidence: float = 0.0
    reason: str = ""
    evidence: List[str] = field(default_factory=list)

    priority: str = PRIORITY_LEVEL["MEDIUM"]
    reviewer_id: str = ""
    review_comment: str = ""
    reviewed_at: Optional[str] = None

    applied_at: Optional[str] = None
    applied_by: str = ""

    expires_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Phase 3.6.4: deprecation 元信息
    # 保留为类属性 / 字典项，便于运行时检查 + 静态分析
    __deprecated__ = True
    __deprecated_since__ = "3.6.4"
    __deprecated_replacement__ = (
        "src.contracts.growth_schema.GrowthProposal (canonical schema)"
    )
    __deprecated_migration__ = (
        "通过 src.contracts.proposal_normalizer.normalize_to_canonical() "
        "或 src.admin.normalized_proposal_translator.NormalizedProposalTranslator "
        "进行 schema 归一化"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "timestamp": self.timestamp,
            "proposal_type": self.proposal_type,
            "status": self.status,
            "source": self.source,
            "source_event_id": self.source_event_id,
            "user_id": self.user_id,
            "affected_dimensions": self.affected_dimensions,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "confidence": self.confidence,
            "reason": self.reason,
            "evidence": self.evidence,
            "priority": self.priority,
            "reviewer_id": self.reviewer_id,
            "review_comment": self.review_comment,
            "reviewed_at": self.reviewed_at,
            "applied_at": self.applied_at,
            "applied_by": self.applied_by,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GrowthProposal":
        return cls(
            proposal_id=data.get("proposal_id", ""),
            timestamp=data.get("timestamp", ""),
            proposal_type=data.get("proposal_type", PROPOSAL_TYPE["PERSONALITY"]),
            status=data.get("status", PROPOSAL_STATUS["PENDING"]),
            source=data.get("source", ""),
            source_event_id=data.get("source_event_id", ""),
            user_id=data.get("user_id", ""),
            affected_dimensions=data.get("affected_dimensions", {}),
            before_state=data.get("before_state", {}),
            after_state=data.get("after_state", {}),
            confidence=data.get("confidence", 0.0),
            reason=data.get("reason", ""),
            evidence=data.get("evidence", []),
            priority=data.get("priority", PRIORITY_LEVEL["MEDIUM"]),
            reviewer_id=data.get("reviewer_id", ""),
            review_comment=data.get("review_comment", ""),
            reviewed_at=data.get("reviewed_at"),
            applied_at=data.get("applied_at"),
            applied_by=data.get("applied_by", ""),
            expires_at=data.get("expires_at"),
            metadata=data.get("metadata", {}),
        )

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            expire_time = datetime.fromisoformat(self.expires_at)
            return datetime.now() > expire_time
        except Exception:
            return False

    def is_pending(self) -> bool:
        return self.status == PROPOSAL_STATUS["PENDING"] and not self.is_expired()

    def get_total_delta(self) -> float:
        return sum(abs(delta) for delta in self.affected_dimensions.values())