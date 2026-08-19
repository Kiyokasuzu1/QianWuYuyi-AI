# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/approval.py

Phase C.10.10 — Policy Proposal Approval

本文件实现:
- PolicyApprovalRecord  单条审核记录(append-only)
- PolicyApprovalStore   审核记录存储(append-only)
- 状态机:PENDING → APPROVED / REJECTED / CANCELLED

设计原则:
- 完全 append-only:不提供 update / delete 接口
- 状态不可回退:approved 后不能改为 rejected(只能重新申请)
- duplicate approve 自动 reject(同一 proposal_id 已有 approved 记录)
- 任何异常 → fail-soft,记录失败但不阻塞 lifecycle
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 状态常量
# ============================================================
APPROVAL_STATUS_PENDING = "pending"
APPROVAL_STATUS_APPROVED = "approved"
APPROVAL_STATUS_REJECTED = "rejected"
APPROVAL_STATUS_CANCELLED = "cancelled"

ALL_APPROVAL_STATUSES = frozenset({
    APPROVAL_STATUS_PENDING,
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_STATUS_CANCELLED,
})

TERMINAL_APPROVAL_STATUSES = frozenset({
    APPROVAL_STATUS_APPROVED,
    APPROVAL_STATUS_REJECTED,
    APPROVAL_STATUS_CANCELLED,
})


# ============================================================
# PolicyApprovalRecord
# ============================================================
@dataclass
class PolicyApprovalRecord:
    """单条 Policy 审核记录(append-only)。

    字段:
    - record_id:       唯一记录 ID
    - proposal_id:     对应的 proposal_id
    - status:          pending / approved / rejected / cancelled
    - reviewer:        审核人/系统标识
    - reason:          审核理由
    - created_at:      创建时间戳
    - updated_at:      最近更新时间
    - metadata:        附加元数据
    - auto_approved:   是否为系统自动批准(safe_auto 模式)
    """

    record_id: str = ""
    proposal_id: str = ""
    status: str = APPROVAL_STATUS_PENDING
    reviewer: str = ""
    reason: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    auto_approved: bool = False

    def __post_init__(self) -> None:
        if not self.record_id:
            self.record_id = f"apr_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = float(time.time())
        if not self.updated_at:
            self.updated_at = float(self.created_at)
        self.proposal_id = str(self.proposal_id or "").strip()
        self.status = str(self.status or APPROVAL_STATUS_PENDING)
        self.reviewer = str(self.reviewer or "").strip()
        self.reason = str(self.reason or "")

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "record_id": str(self.record_id),
                "proposal_id": str(self.proposal_id),
                "status": str(self.status),
                "reviewer": str(self.reviewer),
                "reason": str(self.reason),
                "created_at": float(self.created_at),
                "updated_at": float(self.updated_at),
                "metadata": dict(self.metadata or {}),
                "auto_approved": bool(self.auto_approved),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApprovalRecord.to_dict 异常(已隔离): %s", exc)
            return {
                "record_id": str(self.record_id),
                "proposal_id": "",
                "status": APPROVAL_STATUS_PENDING,
                "reviewer": "",
                "reason": "",
                "created_at": 0.0,
                "updated_at": 0.0,
                "metadata": {},
                "auto_approved": False,
            }

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_APPROVAL_STATUSES

    @property
    def is_approved(self) -> bool:
        return self.status == APPROVAL_STATUS_APPROVED

    @property
    def is_rejected(self) -> bool:
        return self.status == APPROVAL_STATUS_REJECTED


# ============================================================
# PolicyApprovalStore
# ============================================================
class PolicyApprovalStore:
    """审核记录 append-only 存储。

    行为:
    - 一旦写入,不可修改 / 删除
    - 同一 proposal_id 只能有一个 APPROVED 记录
    - duplicate approve / reject 自动 short-circuit
    - 线程安全(RLock)
    """

    def __init__(self, max_records: int = 10000) -> None:
        self._lock = threading.RLock()
        self._records: List[PolicyApprovalRecord] = []
        self._max_records = max(0, int(max_records))

    # --------------------------------------------------------
    # 写入(append-only)
    # --------------------------------------------------------
    def append(self, record: PolicyApprovalRecord) -> bool:
        try:
            if not isinstance(record, PolicyApprovalRecord):
                return False
            with self._lock:
                self._records.append(record)
                if self._max_records > 0 and len(self._records) > self._max_records:
                    excess = len(self._records) - self._max_records
                    self._records = self._records[excess:]
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApprovalStore.append 异常(已隔离): %s", exc)
            return False

    # --------------------------------------------------------
    # 只读查询
    # --------------------------------------------------------
    def get_by_proposal(self, proposal_id: str) -> Optional[PolicyApprovalRecord]:
        try:
            with self._lock:
                # 返回最新的匹配记录
                for rec in reversed(self._records):
                    if rec.proposal_id == str(proposal_id or ""):
                        return rec
            return None
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApprovalStore.get_by_proposal 异常(已隔离): %s", exc)
            return None

    def get_approved_for(self, proposal_id: str) -> Optional[PolicyApprovalRecord]:
        try:
            with self._lock:
                for rec in reversed(self._records):
                    if rec.proposal_id == str(proposal_id or "") and rec.is_approved:
                        return rec
            return None
        except Exception:  # noqa: BLE001
            return None

    def list(
        self,
        proposal_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[PolicyApprovalRecord]:
        try:
            with self._lock:
                results: List[PolicyApprovalRecord] = []
                for rec in self._records:
                    if proposal_id and rec.proposal_id != str(proposal_id):
                        continue
                    if status and rec.status != str(status):
                        continue
                    results.append(rec)
                if limit is not None and limit > 0:
                    results = results[-int(limit):]
                return results
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApprovalStore.list 异常(已隔离): %s", exc)
            return []

    def count(self) -> int:
        try:
            with self._lock:
                return len(self._records)
        except Exception:  # noqa: BLE001
            return 0

    def snapshot(self) -> Dict[str, Any]:
        try:
            with self._lock:
                return {
                    "total": len(self._records),
                    "max_records": int(self._max_records),
                    "by_status": self._count_by_status(),
                    "latest": [r.to_dict() for r in self._records[-10:]],
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApprovalStore.snapshot 异常(已隔离): %s", exc)
            return {"total": 0, "max_records": 0, "by_status": {}, "latest": []}

    def stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                return {
                    "total": len(self._records),
                    "approved": sum(
                        1 for r in self._records if r.is_approved
                    ),
                    "rejected": sum(
                        1 for r in self._records if r.is_rejected
                    ),
                    "cancelled": sum(
                        1 for r in self._records
                        if r.status == APPROVAL_STATUS_CANCELLED
                    ),
                    "pending": sum(
                        1 for r in self._records
                        if r.status == APPROVAL_STATUS_PENDING
                    ),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyApprovalStore.stats 异常(已隔离): %s", exc)
            return {"total": 0, "approved": 0, "rejected": 0, "cancelled": 0, "pending": 0}

    def _count_by_status(self) -> Dict[str, int]:
        try:
            out: Dict[str, int] = {}
            for r in self._records:
                out[r.status] = out.get(r.status, 0) + 1
            return out
        except Exception:  # noqa: BLE001
            return {}

    def clear(self) -> None:
        """测试用:清空存储(不暴露给生产代码)。"""
        with self._lock:
            self._records = []


# ============================================================
# 工厂
# ============================================================
def build_approval_record(
    proposal_id: str,
    status: str = APPROVAL_STATUS_PENDING,
    reviewer: str = "",
    reason: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    auto_approved: bool = False,
) -> PolicyApprovalRecord:
    """构造一条 approval 记录。"""
    return PolicyApprovalRecord(
        proposal_id=str(proposal_id or ""),
        status=str(status or APPROVAL_STATUS_PENDING),
        reviewer=str(reviewer or ""),
        reason=str(reason or ""),
        metadata=dict(metadata or {}),
        auto_approved=bool(auto_approved),
    )


def build_default_approval_store(max_records: int = 10000) -> PolicyApprovalStore:
    """构造一个默认 PolicyApprovalStore。"""
    return PolicyApprovalStore(max_records=max_records)
