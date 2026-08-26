# -*- coding: utf-8 -*-
"""Phase 2.5-D: 关系核心激活 —— ActivationDraft / RelationshipActivationRecord / Service。

补全 2.5-C 缺失的最后一环(accepted → activated → 长期存在):

SYSTEM_C_RELATIONSHIP_PROPOSALS = LEGACY_FROZEN（2026-08-27 P0 冻结）
  - 历史数据保留（111 条 append-only，不删除、不迁移、不清空）
  - 不再新增（handlers.py 已停止候选桥订阅）
  - 不自动激活（routes.py activate 端点 410；canonical 唯一写路径 = gov confirm）
  - 不作为 canonical source（RelationshipCore 7 条 confirmed 为唯一真源）
  - 重新启用需重新经过治理架构设计


    RelationshipProposal (accepted)
        ↓ 人工定义 ActivationDraft(可只认可提案中的部分记忆)
        ↓ 人工调用 activate(独立治理步骤,绝不自动执行)
    RelationshipActivationService —— 事务日志式写入:
        1. 校验全部前置条件(accepted / 审核人 / 理由 / 草案合法 / 无重复核心)
        2. 生成 RelationshipActivationRecord(status=PREPARED,内存)
        3. 写 ActivationRecord(PREPARED) —— 日志先行
        4. 写 RelationshipCore(append-only,重复 relationship_id 被拒)
        5. 更新 ActivationRecord → COMPLETED
        6. proposal.activate()(审计含 before/after)
        7. AnchorRegistry 刷新 CORE_RELATIONSHIP_MEMORY_IDS
        8. 返回 ok

崩溃语义(可扫描,可恢复):
- activation_records.jsonl 中 status=PREPARED 且核心不存在 = 该次激活未完成,
  重新发起激活即可(旧记录保留作为失败审计);
- 核心已落库但尾部(COMPLETED 标记 / proposal.activate / 锚点刷新)未走完 =
  finalize(record_id) 补齐剩余步骤,绝不重复写核心;
- 任何一步失败都不触碰旧核心:RelationshipCoreStore 拒绝重复 relationship_id,
  所以回滚保证由存储层结构保证,而非业务层假设。

治理边界(AGENTS.md Rule 1/2/3):
- 本模块只负责「激活落库」,不修改 personality / emotion_state /
  relationship_state / identity_core;
- 禁止 LLM 或系统侧调用 activate:该能力只暴露给人工治理端点。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.relationship.relationship_core import (
    DEFAULT_VISIBILITY,
    RELATIONSHIP_TYPES,
    VALID_VISIBILITIES,
    RelationshipCore,
)
from src.relationship.relationship_core_store import RelationshipCoreStore
from src.relationship.relationship_proposal import PROPOSAL_STATUS, RelationshipProposal
from src.relationship.relationship_proposal_store import RelationshipProposalStore

logger = logging.getLogger(__name__)

ACTIVATION_STATUS_PREPARED = "prepared"
ACTIVATION_STATUS_COMPLETED = "completed"
ACTIVATION_STATUSES = (ACTIVATION_STATUS_PREPARED, ACTIVATION_STATUS_COMPLETED)


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value)
    except Exception:  # noqa: BLE001
        return default


def _safe_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


# ===================================================
# ActivationDraft —— 人工定义的激活草案
# ===================================================

@dataclass
class ActivationDraft:
    """人工审核后对「激活什么」的最终定义(与提案分离)。

    提案是 AI/评估器提出的候选证据;草案是人工认可的结果:
    - approved_memory_ids 可只认可提案 source_memory_ids 的子集;
    - agreements / boundaries / visibility / relationship_type 由人工敲定,
      不直接复制提案(提案本身不携带这些字段)。
    """

    proposal_id: str = ""
    approved_memory_ids: List[str] = field(default_factory=list)
    relationship_type: str = "other"
    agreements: List[str] = field(default_factory=list)
    boundaries: List[str] = field(default_factory=list)
    visibility: str = DEFAULT_VISIBILITY
    activated_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "approved_memory_ids": list(self.approved_memory_ids),
            "relationship_type": self.relationship_type,
            "agreements": list(self.agreements),
            "boundaries": list(self.boundaries),
            "visibility": self.visibility,
            "activated_by": self.activated_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActivationDraft":
        data = data if isinstance(data, dict) else {}
        relationship_type = _safe_str(data.get("relationship_type"), "other")
        if relationship_type not in RELATIONSHIP_TYPES:
            relationship_type = "other"
        visibility = _safe_str(data.get("visibility"), DEFAULT_VISIBILITY)
        if visibility not in VALID_VISIBILITIES:
            visibility = DEFAULT_VISIBILITY
        return cls(
            proposal_id=_safe_str(data.get("proposal_id")),
            approved_memory_ids=_safe_str_list(data.get("approved_memory_ids")),
            relationship_type=relationship_type,
            agreements=_safe_str_list(data.get("agreements")),
            boundaries=_safe_str_list(data.get("boundaries")),
            visibility=visibility,
            activated_by=_safe_str(data.get("activated_by")),
        )


# ===================================================
# RelationshipActivationRecord —— 激活事务日志
# ===================================================

@dataclass
class RelationshipActivationRecord:
    """一次激活的完整审计记录(append-only,latest-wins by record_id)。

    status 是事务进度:
    - prepared :日志已写,核心未落库(或落库失败)—— 未完成激活;
    - completed:核心已落库 —— 已完成激活。
    """

    record_id: str = ""
    proposal_id: str = ""
    relationship_id: str = ""
    activated_by: str = ""
    activation_reason: str = ""
    source_memories: List[str] = field(default_factory=list)
    relationship_type: str = "other"
    agreements: List[str] = field(default_factory=list)
    boundaries: List[str] = field(default_factory=list)
    visibility: str = DEFAULT_VISIBILITY
    status: str = ACTIVATION_STATUS_PREPARED
    timestamp: str = ""
    previous_state: str = "no_core"
    new_state: str = "activated"

    def __post_init__(self) -> None:
        self.record_id = _safe_str(self.record_id) or f"rela_{uuid4().hex[:12]}"
        self.proposal_id = _safe_str(self.proposal_id)
        self.relationship_id = _safe_str(self.relationship_id)
        self.activated_by = _safe_str(self.activated_by)
        self.activation_reason = _safe_str(self.activation_reason)
        self.source_memories = _safe_str_list(self.source_memories)
        self.agreements = _safe_str_list(self.agreements)
        self.boundaries = _safe_str_list(self.boundaries)
        self.status = (
            _safe_str(self.status, ACTIVATION_STATUS_PREPARED)
            if self.status in ACTIVATION_STATUSES
            else ACTIVATION_STATUS_PREPARED
        )
        self.timestamp = _safe_str(self.timestamp) or _now()
        self.previous_state = _safe_str(self.previous_state, "no_core")
        self.new_state = _safe_str(self.new_state, "activated")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "proposal_id": self.proposal_id,
            "relationship_id": self.relationship_id,
            "activated_by": self.activated_by,
            "activation_reason": self.activation_reason,
            "source_memories": list(self.source_memories),
            "relationship_type": self.relationship_type,
            "agreements": list(self.agreements),
            "boundaries": list(self.boundaries),
            "visibility": self.visibility,
            "status": self.status,
            "timestamp": self.timestamp,
            "previous_state": self.previous_state,
            "new_state": self.new_state,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipActivationRecord":
        data = data if isinstance(data, dict) else {}
        return cls(
            record_id=_safe_str(data.get("record_id")),
            proposal_id=_safe_str(data.get("proposal_id")),
            relationship_id=_safe_str(data.get("relationship_id")),
            activated_by=_safe_str(data.get("activated_by")),
            activation_reason=_safe_str(data.get("activation_reason")),
            source_memories=data.get("source_memories"),
            relationship_type=_safe_str(data.get("relationship_type"), "other"),
            agreements=data.get("agreements"),
            boundaries=data.get("boundaries"),
            visibility=_safe_str(data.get("visibility"), DEFAULT_VISIBILITY),
            status=_safe_str(data.get("status"), ACTIVATION_STATUS_PREPARED),
            timestamp=_safe_str(data.get("timestamp")),
            previous_state=_safe_str(data.get("previous_state"), "no_core"),
            new_state=_safe_str(data.get("new_state"), "activated"),
        )


# ===================================================
# RelationshipActivationRecordStore —— 事务日志持久化
# ===================================================

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
    return lock


def _backup_corrupt(path: str) -> None:
    try:
        if os.path.exists(path):
            backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            shutil.copy2(path, backup)
            logger.warning("RelationshipActivationRecordStore: 损坏文件已备份为 %s", backup)
    except Exception:  # noqa: BLE001
        pass


class RelationshipActivationRecordStore:
    """激活事务记录的 append-only JSONL 存储(latest-wins by record_id)。

    路径约定:data/relationship_core/activation_records.jsonl
    崩溃恢复依赖本文件可被扫描:PREPARED = 未完成,COMPLETED = 已完成。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or "data/relationship_core/activation_records.jsonl"
        self._lock = _path_lock(self.path)
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load_index()

    def _load_index(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception:  # noqa: BLE001
            _backup_corrupt(self.path)
            self._index = {}
            return
        valid = 0
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(parsed, dict) and parsed.get("record_id"):
                self._index[str(parsed["record_id"])] = parsed
                valid += 1
        if raw_lines and valid == 0:
            _backup_corrupt(self.path)

    def save(self, record: Any) -> bool:
        """追加保存(latest-wins by record_id)。"""
        if isinstance(record, RelationshipActivationRecord):
            data = record.to_dict()
        elif isinstance(record, dict):
            data = dict(record)
        else:
            return False
        record_id = str(data.get("record_id") or "")
        if not record_id:
            return False
        with self._lock:
            try:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:  # noqa: BLE001
                        pass
                self._index[record_id] = data
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("RelationshipActivationRecordStore.save 失败(已隔离): %s", exc)
                return False

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            data = self._index.get(str(record_id))
        return dict(data) if data else None

    def list(
        self,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._index.values())
        if status is not None:
            items = [d for d in items if d.get("status") == status]
        items.sort(key=lambda d: str(d.get("timestamp") or ""), reverse=True)
        return [dict(d) for d in items[offset:offset + limit]]


# ===================================================
# RelationshipActivationService —— 唯一激活执行者
# ===================================================

class RelationshipActivationService:
    """执行「accepted 提案 → 已审核关系核心」的唯一入口。

    激活写入顺序(事务日志思想,无数据库事务):
    1. 校验全部前置条件(失败 = 零写入)
    2. 生成记录(内存) → 3. 写 PREPARED 日志
    4. 写核心 → 5. 更新 COMPLETED → 6. proposal.activate() → 7. 锚点刷新
    """

    def __init__(
        self,
        proposal_store: Optional[RelationshipProposalStore] = None,
        core_store: Optional[RelationshipCoreStore] = None,
        record_store: Optional[RelationshipActivationRecordStore] = None,
        user_prefix: str = "yuyi",
    ):
        self.proposal_store = proposal_store or RelationshipProposalStore()
        self.core_store = core_store or RelationshipCoreStore()
        self.record_store = record_store or RelationshipActivationRecordStore()
        self.user_prefix = str(user_prefix or "yuyi")

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------

    def _relationship_id(self, proposal: RelationshipProposal) -> str:
        return f"{self.user_prefix}:{proposal.source_user_id}"

    def _build_core(
        self,
        proposal: RelationshipProposal,
        draft: ActivationDraft,
        relationship_id: str,
    ) -> RelationshipCore:
        """从「提案(证据) + 草案(人工定义)」构建关系核心。"""
        importance = 0.5
        try:
            if proposal.score and proposal.score > 0:
                importance = round(max(0.0, min(1.0, proposal.score)), 4)
        except Exception:  # noqa: BLE001
            pass
        return RelationshipCore(
            relationship_id=relationship_id,
            source_user_id=proposal.source_user_id,
            relationship_type=draft.relationship_type,
            importance=importance,
            events=[
                {"type": "approved_memory", "memory_id": mid}
                for mid in draft.approved_memory_ids
            ],
            agreements=list(draft.agreements),
            boundaries=list(draft.boundaries),
            anchor_memory_ids=list(draft.approved_memory_ids),
            visibility=draft.visibility,
        )

    def _refresh_anchors(self) -> int:
        """把已审核核心的锚点播种进内存白名单(fail-soft,失败返回 0)。"""
        try:
            from src.identity.yui_core_profile import CORE_RELATIONSHIP_MEMORY_IDS
            from src.relationship.anchor_registry import AnchorRegistry
            return AnchorRegistry(store=self.core_store).seed_into(
                CORE_RELATIONSHIP_MEMORY_IDS
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("锚点刷新失败(已降级): %s", exc)
            return 0

    def _validate(self, draft: ActivationDraft, reviewer: str, reason: str):
        """返回 (error, proposal, relationship_id)。error 非空 = 拒绝,零写入。"""
        if not reviewer:
            return "reviewer(审核人)必填", None, ""
        if not reason:
            return "activation_reason(激活理由)必填", None, ""
        if not draft.proposal_id:
            return "draft.proposal_id 必填", None, ""
        if not draft.approved_memory_ids:
            return "approved_memory_ids 不能为空(人工需明确认可哪些记忆)", None, ""
        if draft.visibility not in VALID_VISIBILITIES:
            return f"visibility 非法: {draft.visibility}", None, ""
        if draft.relationship_type not in RELATIONSHIP_TYPES:
            return f"relationship_type 非法: {draft.relationship_type}", None, ""
        record = self.proposal_store.get(draft.proposal_id)
        if record is None:
            return f"proposal {draft.proposal_id} 不存在", None, ""
        proposal = RelationshipProposal.from_dict(record)
        if proposal.status != PROPOSAL_STATUS["ACCEPTED"]:
            return (
                f"仅 accepted 提案可激活,当前状态: {proposal.status}",
                None, "",
            )
        relationship_id = self._relationship_id(proposal)
        if self.core_store.get(relationship_id) is not None:
            return f"关系核心 {relationship_id} 已存在,append-only 禁止覆盖", None, ""
        return None, proposal, relationship_id

    # ------------------------------------------------------------
    # 激活 / 恢复
    # ------------------------------------------------------------

    def activate(self, draft: Any, reviewer: str = "", reason: str = "") -> Dict[str, Any]:
        """执行事务日志式激活。返回 dict,ok=False 时附 stage 供恢复判断。"""
        try:
            draft = draft if isinstance(draft, ActivationDraft) else ActivationDraft.from_dict(draft or {})
            reviewer = str(reviewer or draft.activated_by or "").strip()
            reason = str(reason or "")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "stage": "validation", "error": f"草案解析失败: {exc}"}

        error, proposal, relationship_id = self._validate(draft, reviewer, reason)
        if error:
            return {"ok": False, "stage": "validation", "error": error}

        record = RelationshipActivationRecord(
            proposal_id=proposal.proposal_id,
            relationship_id=relationship_id,
            activated_by=reviewer,
            activation_reason=reason,
            source_memories=list(draft.approved_memory_ids),
            relationship_type=draft.relationship_type,
            agreements=list(draft.agreements),
            boundaries=list(draft.boundaries),
            visibility=draft.visibility,
            status=ACTIVATION_STATUS_PREPARED,
            previous_state="no_core",
            new_state="activated",
        )
        if not self.record_store.save(record):
            return {"ok": False, "stage": "record_prepare_failed", "error": "激活记录(PREPARED)写入失败"}

        core = self._build_core(proposal, draft, relationship_id)
        if not self.core_store.save(core):
            return {
                "ok": False, "stage": "core_write_failed",
                "error": "关系核心写入失败(事务未完成,可扫描 PREPARED 记录)",
                "record_id": record.record_id,
            }

        record.status = ACTIVATION_STATUS_COMPLETED
        if not self.record_store.save(record):
            return {
                "ok": False, "stage": "record_finalize_failed",
                "error": "激活记录更新(COMPLETED)失败,可用 finalize 补齐",
                "record_id": record.record_id,
            }

        if not proposal.activate(reviewer, reason):
            return {
                "ok": False, "stage": "proposal_activate_failed",
                "error": "提案状态流转失败(激活标记未写入)",
                "record_id": record.record_id,
            }
        if not self.proposal_store.save(proposal):
            return {
                "ok": False, "stage": "proposal_save_failed",
                "error": "提案激活状态保存失败,可用 finalize 补齐",
                "record_id": record.record_id,
            }

        self._refresh_anchors()
        return {
            "ok": True,
            "record_id": record.record_id,
            "relationship_id": relationship_id,
            "proposal_id": proposal.proposal_id,
            "status": "activated",
        }

    def finalize(self, record_id: str) -> Dict[str, Any]:
        """崩溃恢复:补齐「核心已落库但尾部未走完」的激活事务。

        绝不重复写核心(append-only 结构保证);核心未落库时拒绝完成,
        此时应重新发起激活,旧 PREPARED 记录保留作为失败审计。
        """
        data = self.record_store.get(record_id)
        if data is None:
            return {"ok": False, "error": f"激活记录 {record_id} 不存在"}
        relationship_id = str(data.get("relationship_id") or "")
        if self.core_store.get(relationship_id) is None:
            return {
                "ok": False,
                "error": "核心尚未落库,该次激活未完成;请重新发起激活",
                "record_id": record_id,
            }
        proposal_data = self.proposal_store.get(str(data.get("proposal_id") or ""))
        if proposal_data is None:
            return {
                "ok": False,
                "error": "提案缺失,无法完成激活审计",
                "record_id": record_id,
            }
        proposal = RelationshipProposal.from_dict(proposal_data)

        record = RelationshipActivationRecord.from_dict(data)
        if record.status != ACTIVATION_STATUS_COMPLETED:
            record.status = ACTIVATION_STATUS_COMPLETED
            if not self.record_store.save(record):
                return {"ok": False, "error": "激活记录更新失败", "record_id": record_id}

        if proposal.status == PROPOSAL_STATUS["ACCEPTED"]:
            actor = str(data.get("activated_by") or "recovery")
            if not proposal.activate(actor, str(data.get("activation_reason") or "崩溃恢复补齐")):
                return {"ok": False, "error": "提案状态流转失败", "record_id": record_id}
            if not self.proposal_store.save(proposal):
                return {"ok": False, "error": "提案保存失败", "record_id": record_id}

        self._refresh_anchors()
        return {
            "ok": True,
            "record_id": record_id,
            "relationship_id": relationship_id,
            "proposal_id": proposal.proposal_id,
            "status": "activated",
        }


__all__ = [
    "ACTIVATION_STATUS_PREPARED",
    "ACTIVATION_STATUS_COMPLETED",
    "ACTIVATION_STATUSES",
    "ActivationDraft",
    "RelationshipActivationRecord",
    "RelationshipActivationRecordStore",
    "RelationshipActivationService",
]
