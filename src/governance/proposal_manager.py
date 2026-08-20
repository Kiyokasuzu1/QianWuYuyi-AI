# -*- coding: utf-8 -*-
"""
P2.3-B.11 Phase 2 — GovernanceProposalManager（提案生命周期消费链）

设计依据：docs/governance/p2_3_b11_proposal_lifecycle_design.md（Phase 1 冻结）

职责（任务书 Phase 2）：
    1. 读取 GovernanceProposalStore（只读消费方）
    2. 状态迁移（六态状态机，表驱动，非法迁移 fail-closed）
    3. 审批记录（approve/reject 事件即审批记录，reviewer/reason 全留痕）
    4. 防重复 apply（APPROVED→APPLIED 一次性守卫 + mutation_id 已应用集合）
    5. 保留 audit trace（迁移事件流本身即审计轨迹）

状态机（§3.1 冻结）：
    CREATED → PENDING_REVIEW → APPROVED → APPLIED → ARCHIVED
                        │          │         │
                        └→ REJECTED ←┘         │
                             │                 │
                             └→ ARCHIVED ←─────┘

存储（§3.3 冻结）：
    迁移事件流 <governance_data_dir>/proposal_lifecycle.jsonl（append-only，
    与 proposals.jsonl 同目录守卫）；当前状态 = 重放事件流的投影。
    proposals.jsonl 永不被改写。

禁止（任务书 Phase 2 冻结）：
    - 自动 approve：approve() 的 reviewer 为必填显式参数，代码层面不存在
      无 reviewer 的 approve 路径；无任何定时器/回调/内部逻辑触发审批。
    - 自动修改 personality/emotion/relationship：mark_applied() 只记录
      "外部执行件已应用"的事实，本 Manager 不持有任何域状态写句柄。
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.governance.proposal_store import GovernanceProposalStore

logger = logging.getLogger(__name__)

# ------------------------------------------------------------
# 状态机常量（Phase 1 设计 §3.1/§3.2 冻结）
# ------------------------------------------------------------
PROPOSAL_STATUSES: tuple = (
    "CREATED",
    "PENDING_REVIEW",
    "APPROVED",
    "REJECTED",
    "APPLIED",
    "ARCHIVED",
)

# 合法迁移表：{(from, event): to}
VALID_TRANSITIONS: Dict[Tuple[str, str], str] = {
    ("CREATED", "register"): "PENDING_REVIEW",
    ("PENDING_REVIEW", "approve"): "APPROVED",
    ("PENDING_REVIEW", "reject"): "REJECTED",
    ("APPROVED", "mark_applied"): "APPLIED",
    ("APPROVED", "archive"): "ARCHIVED",
    ("REJECTED", "archive"): "ARCHIVED",
    ("APPLIED", "archive"): "ARCHIVED",
}

# 生命周期文件名（append-only 迁移事件流）
LIFECYCLE_FILE_NAME: str = "proposal_lifecycle.jsonl"

MANAGED_EVENTS: tuple = ("register", "approve", "reject", "mark_applied", "archive")


class ProposalNotFoundError(LookupError):
    """proposal_id 不在 GovernanceProposalStore 落账中。"""


class InvalidTransition(ValueError):
    """非法状态迁移（fail-closed，不产生任何写入）。"""


class DuplicateApplyError(RuntimeError):
    """重复 apply 防护：该 proposal / mutation 已进入 APPLIED。"""


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class GovernanceProposalManager:
    """提案生命周期管理器（NEED_REVIEW 消费链核心）。"""

    def __init__(
        self,
        store: Optional[GovernanceProposalStore] = None,
        data_dir: Optional[Any] = None,
    ) -> None:
        if store is not None:
            self._store = store
        elif data_dir is not None:
            self._store = GovernanceProposalStore(Path(data_dir))
        else:
            self._store = GovernanceProposalStore()
        # 迁移事件流与 proposals.jsonl 同目录（同一套目录守卫生效）
        self._lifecycle_path = self._store.data_dir / LIFECYCLE_FILE_NAME
        self._lock = threading.Lock()

    # ============================================================
    # 读取（proposals.jsonl 只读消费方）
    # ============================================================
    def _load_proposals(self) -> Dict[str, Dict[str, Any]]:
        return {
            str(rec.get("proposal_id")): rec
            for rec in self._store.load_records()
            if rec.get("proposal_id")
        }

    def _require_proposal(
        self, proposal_id: str,
    ) -> Dict[str, Any]:
        proposals = self._load_proposals()
        record = proposals.get(str(proposal_id))
        if record is None:
            raise ProposalNotFoundError(
                f"proposal {proposal_id!r} 不在 GovernanceProposalStore 落账中"
            )
        return record

    # ============================================================
    # 事件流读写（append-only）
    # ============================================================
    def _read_events(self) -> List[Dict[str, Any]]:
        if not self._lifecycle_path.exists():
            return []
        events: List[Dict[str, Any]] = []
        with open(self._lifecycle_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        events.append(item)
                except json.JSONDecodeError:
                    logger.warning(
                        "[proposal_manager] 跳过损坏事件行: %r", line[:80],
                    )
        return events

    def _append_event(self, event: Dict[str, Any]) -> None:
        self._lifecycle_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lifecycle_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _current_status(self, proposal_id: str) -> str:
        """重放迁移事件流得到当前状态；无事件 = CREATED（落账即创建）。"""
        for event in reversed(self._read_events()):
            if str(event.get("proposal_id")) == str(proposal_id):
                status = event.get("to_status")
                if status in PROPOSAL_STATUSES:
                    return str(status)
        return "CREATED"

    def _applied_mutation_ids(self) -> set:
        """已进入 APPLIED/ARCHIVED 的 proposal 对应 mutation_id 集合。"""
        applied_proposals = {
            str(e.get("proposal_id"))
            for e in self._read_events()
            if e.get("to_status") in ("APPLIED",)
        }
        ids = set()
        for record in self._store.load_records():
            pid = str(record.get("proposal_id"))
            mid = record.get("mutation_id")
            if pid in applied_proposals and mid:
                ids.add(str(mid))
        return ids

    def _trace_ref(self, record: Dict[str, Any]) -> Dict[str, str]:
        return {
            "mutation_id": str(record.get("mutation_id") or ""),
            "request_id": str(record.get("request_id") or ""),
            "trace_id": str(record.get("trace_id") or ""),
        }

    # ============================================================
    # 状态迁移（表驱动 + fail-closed）
    # ============================================================
    def _transition(
        self,
        proposal_id: str,
        event: str,
        *,
        reviewer: str = "",
        reason: str = "",
    ) -> Dict[str, Any]:
        if event not in MANAGED_EVENTS:
            raise InvalidTransition(f"未知事件 {event!r}")
        record = self._require_proposal(proposal_id)

        with self._lock:
            current = self._current_status(proposal_id)
            key = (current, event)
            if key not in VALID_TRANSITIONS:
                raise InvalidTransition(
                    f"proposal {proposal_id} 非法迁移：{current} --{event}--> 无此路径"
                    f"（合法：{sorted(set(k[1] for k in VALID_TRANSITIONS))}）"
                )
            # 防重复 apply：mutation 维度守卫（跨 proposal 同 mutation 也阻断）
            if event == "mark_applied":
                applied = self._applied_mutation_ids()
                mid = str(record.get("mutation_id") or "")
                if mid and mid in applied:
                    raise DuplicateApplyError(
                        f"mutation {mid} 已被应用（proposal {proposal_id} 禁止重复 apply）"
                    )

            to_status = VALID_TRANSITIONS[key]
            event_record: Dict[str, Any] = {
                "event": event,
                "proposal_id": str(proposal_id),
                "from_status": current,
                "to_status": to_status,
                "reviewer": reviewer,
                "reason": reason,
                "actor": "proposal_manager",
                "timestamp": _now_iso(),
                "trace_ref": self._trace_ref(record),
            }
            self._append_event(event_record)
        return event_record

    # ============================================================
    # 公开 API（任务书 Phase 2）
    # ============================================================
    def register(self, proposal_id: str) -> Dict[str, Any]:
        """CREATED → PENDING_REVIEW（进入待审队列；幂等场景由状态机拒绝重复注册）。"""
        return self._transition(proposal_id, "register")

    def approve(
        self,
        proposal_id: str,
        *,
        reviewer: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """PENDING_REVIEW → APPROVED（审批通过）。

        reviewer 必填（人工审批接口预留："human:<uid>" / "system:<subsystem>"）。
        本方法不存在默认 reviewer——自动 approve 在 API 形状上被排除。
        """
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("approve 必须显式提供 reviewer（禁止自动 approve）")
        return self._transition(
            proposal_id, "approve", reviewer=reviewer.strip(), reason=reason,
        )

    def reject(
        self,
        proposal_id: str,
        *,
        reviewer: str,
        reason: str,
    ) -> Dict[str, Any]:
        """PENDING_REVIEW → REJECTED（审批否决，终态；不可翻案）。"""
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("reject 必须显式提供 reviewer")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reject 必须提供 reason")
        return self._transition(
            proposal_id, "reject", reviewer=reviewer.strip(), reason=reason,
        )

    def mark_applied(self, proposal_id: str) -> Dict[str, Any]:
        """APPROVED → APPLIED（外部执行件回告"已应用"）。

        只记录事实，不执行任何应用动作；重复调用 → DuplicateApplyError。
        """
        return self._transition(proposal_id, "mark_applied")

    def archive(self, proposal_id: str) -> Dict[str, Any]:
        """→ ARCHIVED（终态收纳；REJECTED/APPROVED/APPLIED 均可归档）。"""
        return self._transition(proposal_id, "archive")

    # ============================================================
    # 查询（只读）
    # ============================================================
    def get_status(self, proposal_id: str) -> str:
        """当前生命周期状态（重放投影；未注册的落账 proposal = CREATED）。"""
        self._require_proposal(proposal_id)
        return self._current_status(proposal_id)

    def list_by_status(self, status: str) -> List[Dict[str, Any]]:
        """按当前状态过滤落账 proposal（返回 proposals.jsonl 原始记录）。"""
        if status not in PROPOSAL_STATUSES:
            raise ValueError(f"未知状态 {status!r}，允许 {PROPOSAL_STATUSES}")
        records = self._store.load_records()
        status_map = {
            str(rec.get("proposal_id")): self._current_status(
                str(rec.get("proposal_id")),
            )
            for rec in records
            if rec.get("proposal_id")
        }
        return [
            rec for rec in records
            if status_map.get(str(rec.get("proposal_id"))) == status
        ]

    def audit_trace(self, proposal_id: str) -> List[Dict[str, Any]]:
        """该 proposal 的全部迁移事件（按发生顺序）——审计轨迹留痕。"""
        return [
            e for e in self._read_events()
            if str(e.get("proposal_id")) == str(proposal_id)
        ]


__all__ = [
    "GovernanceProposalManager",
    "PROPOSAL_STATUSES",
    "VALID_TRANSITIONS",
    "LIFECYCLE_FILE_NAME",
    "ProposalNotFoundError",
    "InvalidTransition",
    "DuplicateApplyError",
]
