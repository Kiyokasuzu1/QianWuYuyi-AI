# -*- coding: utf-8 -*-
"""
src/runtime/policy/lifecycle/audit.py

Phase C.10.10 — Policy Lifecycle Audit

本文件实现:
- PolicyAuditRecord  单条审计记录
- PolicyAuditStore   审计存储(append-only)

覆盖事件:
- proposal_created    (新 proposal 写入 store)
- proposal_approved   (人工 / 自动批准)
- proposal_rejected   (人工拒绝)
- proposal_snapshot   (apply 前 snapshot)
- proposal_applied    (apply 成功)
- proposal_verified   (verify 成功)
- proposal_failed     (apply 失败)
- proposal_rollback   (rollback 执行)

设计原则:
- append-only:任何写入不可修改 / 删除
- Audit 异常 → 静默,不影响 lifecycle
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
# 审计事件常量
# ============================================================
AUDIT_EVENT_CREATED = "proposal_created"
AUDIT_EVENT_APPROVED = "proposal_approved"
AUDIT_EVENT_REJECTED = "proposal_rejected"
AUDIT_EVENT_SNAPSHOT = "proposal_snapshot"
AUDIT_EVENT_APPLIED = "proposal_applied"
AUDIT_EVENT_VERIFIED = "proposal_verified"
AUDIT_EVENT_FAILED = "proposal_failed"
AUDIT_EVENT_ROLLBACK = "proposal_rollback"

ALL_AUDIT_EVENTS = frozenset({
    AUDIT_EVENT_CREATED,
    AUDIT_EVENT_APPROVED,
    AUDIT_EVENT_REJECTED,
    AUDIT_EVENT_SNAPSHOT,
    AUDIT_EVENT_APPLIED,
    AUDIT_EVENT_VERIFIED,
    AUDIT_EVENT_FAILED,
    AUDIT_EVENT_ROLLBACK,
})


# ============================================================
# PolicyAuditRecord
# ============================================================
@dataclass
class PolicyAuditRecord:
    """单条 Policy 审计记录(append-only)。

    字段:
    - record_id:    唯一 ID
    - event:        事件类型
    - proposal_id:  关联的 proposal_id(可能为空,如 created)
    - module:       模块名
    - parameter:    参数名
    - actor:        触发者(reviewer / system / runtime)
    - timestamp:    事件时间戳
    - metadata:     附加元数据
    - success:      操作是否成功
    - error:        错误信息
    """

    record_id: str = ""
    event: str = ""
    proposal_id: str = ""
    module: str = ""
    parameter: str = ""
    actor: str = ""
    timestamp: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str = ""

    def __post_init__(self) -> None:
        if not self.record_id:
            self.record_id = f"aud_{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = float(time.time())
        self.event = str(self.event or "")
        self.proposal_id = str(self.proposal_id or "")
        self.module = str(self.module or "").strip().lower()
        self.parameter = str(self.parameter or "")
        self.actor = str(self.actor or "")
        self.error = str(self.error or "")

    def to_dict(self) -> Dict[str, Any]:
        try:
            return {
                "record_id": str(self.record_id),
                "event": str(self.event),
                "proposal_id": str(self.proposal_id),
                "module": str(self.module),
                "parameter": str(self.parameter),
                "actor": str(self.actor),
                "timestamp": float(self.timestamp),
                "metadata": dict(self.metadata or {}),
                "success": bool(self.success),
                "error": str(self.error),
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyAuditRecord.to_dict 异常(已隔离): %s", exc)
            return {
                "record_id": str(self.record_id),
                "event": "",
                "proposal_id": "",
                "module": "",
                "parameter": "",
                "actor": "",
                "timestamp": 0.0,
                "metadata": {},
                "success": False,
                "error": "to_dict_failed",
            }


# ============================================================
# PolicyAuditStore
# ============================================================
class PolicyAuditStore:
    """审计记录 append-only 存储。

    行为:
    - 写入接口仅 record
    - 提供按 proposal_id / event / module 过滤
    - 任何 audit 异常静默隔离
    """

    def __init__(self, max_records: int = 50000) -> None:
        self._lock = threading.RLock()
        self._records: List[PolicyAuditRecord] = []
        self._max_records = max(0, int(max_records))

    def record(
        self,
        event: str,
        proposal_id: str = "",
        module: str = "",
        parameter: str = "",
        actor: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: str = "",
    ) -> bool:
        try:
            rec = PolicyAuditRecord(
                event=str(event or ""),
                proposal_id=str(proposal_id or ""),
                module=str(module or "").strip().lower(),
                parameter=str(parameter or ""),
                actor=str(actor or ""),
                metadata=dict(metadata or {}),
                success=bool(success),
                error=str(error or ""),
            )
            with self._lock:
                self._records.append(rec)
                if self._max_records > 0 and len(self._records) > self._max_records:
                    excess = len(self._records) - self._max_records
                    self._records = self._records[excess:]
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyAuditStore.record 异常(已隔离): %s", exc)
            return False

    def list(
        self,
        proposal_id: Optional[str] = None,
        event: Optional[str] = None,
        module: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[PolicyAuditRecord]:
        try:
            with self._lock:
                results: List[PolicyAuditRecord] = []
                for rec in self._records:
                    if proposal_id and rec.proposal_id != str(proposal_id):
                        continue
                    if event and rec.event != str(event):
                        continue
                    if module and rec.module != str(module).strip().lower():
                        continue
                    results.append(rec)
                if limit is not None and limit > 0:
                    results = results[-int(limit):]
                return results
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyAuditStore.list 异常(已隔离): %s", exc)
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
                    "by_event": self._count_by_event(),
                    "latest": [r.to_dict() for r in self._records[-10:]],
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyAuditStore.snapshot 异常(已隔离): %s", exc)
            return {"total": 0, "max_records": 0, "by_event": {}, "latest": []}

    def stats(self) -> Dict[str, int]:
        try:
            with self._lock:
                return {
                    "total": len(self._records),
                    "success": sum(1 for r in self._records if r.success),
                    "failed": sum(1 for r in self._records if not r.success),
                    "by_event_count": len(self._count_by_event()),
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("PolicyAuditStore.stats 异常(已隔离): %s", exc)
            return {"total": 0, "success": 0, "failed": 0, "by_event_count": 0}

    def _count_by_event(self) -> Dict[str, int]:
        try:
            out: Dict[str, int] = {}
            for r in self._records:
                out[r.event] = out.get(r.event, 0) + 1
            return out
        except Exception:  # noqa: BLE001
            return {}

    def clear(self) -> None:
        with self._lock:
            self._records = []


# ============================================================
# 工厂
# ============================================================
def build_audit_record(
    event: str,
    proposal_id: str = "",
    module: str = "",
    parameter: str = "",
    actor: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    success: bool = True,
    error: str = "",
) -> PolicyAuditRecord:
    """构造一条 audit 记录(不写入 store)。"""
    return PolicyAuditRecord(
        event=str(event or ""),
        proposal_id=str(proposal_id or ""),
        module=str(module or "").strip().lower(),
        parameter=str(parameter or ""),
        actor=str(actor or ""),
        metadata=dict(metadata or {}),
        success=bool(success),
        error=str(error or ""),
    )


def build_default_audit_store(max_records: int = 50000) -> PolicyAuditStore:
    return PolicyAuditStore(max_records=max_records)
