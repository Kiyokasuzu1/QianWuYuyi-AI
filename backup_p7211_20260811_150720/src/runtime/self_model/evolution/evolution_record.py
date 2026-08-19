# -*- coding: utf-8 -*-
"""
src/runtime/self_model/evolution/evolution_record.py

Phase 4.5: 自我模型演化记录数据层。

数据契约:
- SelfModelChange:        单字段的变更(老值 -> 新值 + 原因 + 置信度 + 证据)
- EvolutionRecord:        一次完整演化的不可变记录
- SelfModelEvolutionResult: 引擎返回的统一结果(accepted + rejected + records + new_snapshot)
- EvolutionSourceType:    演化来源枚举(growth_proposal / reflection / manual)

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.*
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine.generate()
- 字段可序列化(to_dict / from_dict),支持 round_trip
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# 尝试从 Phase 4.2.1 引入合法字段集合(若失败则使用本地兜底)
try:
    from src.runtime.self_model.self_model_data import (
        SelfModelSnapshot as _Snapshot,
    )
    _HAS_SNAPSHOT_IMPORT = True
except Exception:  # noqa: BLE001
    _HAS_SNAPSHOT_IMPORT = False


# ============================================================
# 常量与版本号
# ============================================================

EVOLUTION_RECORD_SCHEMA_VERSION = "1.0"

# 文本截断长度
MAX_REASON_TEXT = 240
MAX_EVIDENCE_TEXT = 80
MAX_FIELD_NAME = 64
MAX_CHANGE_LIST = 64


# ============================================================
# 演化来源
# ============================================================
class EvolutionSourceType(str, Enum):
    """演化来源(Phase 4.5 / v1.0)。"""
    GROWTH_PROPOSAL = "growth_proposal"
    REFLECTION = "reflection"
    MANUAL = "manual"


_VALID_SOURCES = frozenset(s.value for s in EvolutionSourceType)


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clamp_confidence(v: Any) -> float:
    """把 confidence 限制在 [0.0, 1.0]。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


# ============================================================
# SelfModelChange
# ============================================================
@dataclass
class SelfModelChange:
    """单字段的演化变更 —— Phase 4.5 / v1.0。

    字段:
    - field_name:   str                 # 字段名(preferences / stable_traits / current_state.mood / ...)
    - old_value:    Any                 # 旧值(深拷贝,可能为 None)
    - new_value:    Any                 # 新值
    - reason:       str                 # 变更原因
    - confidence:   float               # 置信度 [0.0, 1.0]
    - evidence_ids: List[str]           # 证据 ID 列表(可能为空)

    约束:
    - field_name 必须是 SelfModelSnapshot 允许变化的字段(由 EvolutionPolicy 校验)
    - old_value / new_value 支持任意 JSON-safe 类型(dict / list / str / int / float / bool / None)
    """

    field_name: str
    new_value: Any = None
    old_value: Any = None
    reason: str = ""
    confidence: float = 0.0
    evidence_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.field_name = str(self.field_name or "")
        if len(self.field_name) > MAX_FIELD_NAME:
            self.field_name = self.field_name[:MAX_FIELD_NAME]
        self.confidence = _clamp_confidence(self.confidence)
        # reason 截断
        try:
            self.reason = str(self.reason or "")
        except Exception:  # noqa: BLE001
            self.reason = ""
        if len(self.reason) > MAX_REASON_TEXT:
            self.reason = self.reason[:MAX_REASON_TEXT]
        # evidence_ids 规范化为 list[str]
        if not isinstance(self.evidence_ids, list):
            try:
                self.evidence_ids = list(self.evidence_ids or [])
            except Exception:  # noqa: BLE001
                self.evidence_ids = []
        out: List[str] = []
        for eid in self.evidence_ids:
            if eid is None:
                continue
            try:
                s = str(eid)
            except Exception:  # noqa: BLE001
                continue
            if not s or s == "None":
                continue
            if len(s) > MAX_EVIDENCE_TEXT:
                s = s[:MAX_EVIDENCE_TEXT]
            out.append(s)
        self.evidence_ids = out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "reason": self.reason,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "SelfModelChange":
        d = dict(data or {})
        return cls(
            field_name=str(d.get("field_name", "") or ""),
            old_value=d.get("old_value"),
            new_value=d.get("new_value"),
            reason=str(d.get("reason", "") or ""),
            confidence=_clamp_confidence(d.get("confidence", 0.0)),
            evidence_ids=list(d.get("evidence_ids", []) or []),
        )

    def is_field_change(self) -> bool:
        """是否是"有效变化"(old != new)。"""
        return self.old_value != self.new_value


# ============================================================
# EvolutionRecord
# ============================================================
@dataclass
class EvolutionRecord:
    """一次完整演化的不可变记录 —— Phase 4.5 / v1.0。

    字段:
    - record_id:        str                              # 唯一 id
    - identity_id:      str                              # SelfModel identity_id
    - timestamp:        str                              # ISO 时间
    - source_type:      str                              # EvolutionSourceType
    - source_id:        str                              # 触发本次演化的源 ID(proposal_id / reflection_id)
    - from_version:     int                              # 旧版本号
    - to_version:       int                              # 新版本号(可能与 from_version 相同,表示无变化)
    - changes:          List[SelfModelChange]            # 字段级变更列表
    - rejected_changes: List[SelfModelChange]            # 被策略拒绝的变更(原因 = reject_reason)
    - reject_reasons:   List[str]                        # 与 rejected_changes 一一对应
    - summary:          str                              # 一句话摘要
    - metadata:         Dict[str, Any]                   # 其它元信息
    - schema_version:   str                              # 固定 "1.0"
    """

    identity_id: str = ""
    source_type: str = EvolutionSourceType.MANUAL.value
    source_id: str = ""
    from_version: int = 0
    to_version: int = 0
    changes: List[SelfModelChange] = field(default_factory=list)
    rejected_changes: List[SelfModelChange] = field(default_factory=list)
    reject_reasons: List[str] = field(default_factory=list)
    summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_now_iso)
    record_id: str = field(default_factory=lambda: _new_id("evo"))
    schema_version: str = EVOLUTION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.source_type not in _VALID_SOURCES:
            self.source_type = EvolutionSourceType.MANUAL.value
        # 规范化 changes
        self.changes = _coerce_change_list(self.changes)
        self.rejected_changes = _coerce_change_list(self.rejected_changes)
        if not isinstance(self.reject_reasons, list):
            try:
                self.reject_reasons = list(self.reject_reasons or [])
            except Exception:  # noqa: BLE001
                self.reject_reasons = []
        self.reject_reasons = [str(r) for r in self.reject_reasons]
        if not isinstance(self.metadata, dict):
            self.metadata = {}

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def accepted_count(self) -> int:
        return len(self.changes)

    def rejected_count(self) -> int:
        return len(self.rejected_changes)

    def is_noop(self) -> bool:
        """无任何变化(没有 accepted changes)。"""
        return len(self.changes) == 0

    def confidence_avg(self) -> float:
        if not self.changes:
            return 0.0
        total = sum(c.confidence for c in self.changes)
        return round(total / len(self.changes), 4)

    def field_names(self) -> List[str]:
        return [c.field_name for c in self.changes]

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "identity_id": self.identity_id,
            "timestamp": self.timestamp,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "changes": [c.to_dict() for c in self.changes],
            "rejected_changes": [c.to_dict() for c in self.rejected_changes],
            "reject_reasons": list(self.reject_reasons),
            "summary": self.summary,
            "metadata": dict(self.metadata),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "EvolutionRecord":
        d = dict(data or {})
        changes_raw = d.get("changes", []) or []
        rejected_raw = d.get("rejected_changes", []) or []
        return cls(
            identity_id=str(d.get("identity_id", "") or ""),
            source_type=str(
                d.get("source_type", EvolutionSourceType.MANUAL.value)
            ),
            source_id=str(d.get("source_id", "") or ""),
            from_version=int(d.get("from_version", 0) or 0),
            to_version=int(d.get("to_version", 0) or 0),
            changes=[SelfModelChange.from_dict(c) for c in changes_raw],
            rejected_changes=[
                SelfModelChange.from_dict(c) for c in rejected_raw
            ],
            reject_reasons=list(d.get("reject_reasons", []) or []),
            summary=str(d.get("summary", "") or ""),
            metadata=dict(d.get("metadata", {}) or {}),
            timestamp=str(d.get("timestamp", "") or _now_iso()),
            record_id=str(d.get("record_id", "") or _new_id("evo")),
            schema_version=str(
                d.get("schema_version", EVOLUTION_RECORD_SCHEMA_VERSION)
            ),
        )


# ============================================================
# SelfModelEvolutionResult
# ============================================================
@dataclass
class SelfModelEvolutionResult:
    """SelfModelEvolutionEngine.evolve() 的统一返回结果。

    字段:
    - accepted_changes:  List[SelfModelChange]   # 通过策略、可应用到 snapshot 的变化
    - rejected_changes:  List[SelfModelChange]   # 被策略拒绝的变化
    - reject_reasons:    List[str]               # 与 rejected_changes 一一对应
    - evolution_records: List[EvolutionRecord]   # 每次演化的不可变记录(可能多条)
    - new_snapshot:      Optional[Any]           # 应用 accepted_changes 后的新 SelfModelSnapshot(不可变 copy)
    - original_snapshot: Optional[Any]           # 原始 snapshot(便于对比)
    - is_noop:           bool                    # 无任何变化
    - summary:           str                     # 一句话摘要
    - schema_version:    str                     # 固定 "1.0"

    约束:
    - new_snapshot 永不为 None 时,必须是 original_snapshot 的深拷贝(应用 accepted_changes)
    - evolution_records 至少包含一条主记录(source_type 决定)
    """

    accepted_changes: List[SelfModelChange] = field(default_factory=list)
    rejected_changes: List[SelfModelChange] = field(default_factory=list)
    reject_reasons: List[str] = field(default_factory=list)
    evolution_records: List[EvolutionRecord] = field(default_factory=list)
    new_snapshot: Optional[Any] = None
    original_snapshot: Optional[Any] = None
    is_noop: bool = True
    summary: str = ""
    schema_version: str = EVOLUTION_RECORD_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.accepted_changes = _coerce_change_list(self.accepted_changes)
        self.rejected_changes = _coerce_change_list(self.rejected_changes)
        if not isinstance(self.reject_reasons, list):
            try:
                self.reject_reasons = list(self.reject_reasons or [])
            except Exception:  # noqa: BLE001
                self.reject_reasons = []
        self.reject_reasons = [str(r) for r in self.reject_reasons]
        # is_noop 自动推导(若 caller 未显式指定)
        if self.accepted_changes:
            self.is_noop = False

    def accepted_count(self) -> int:
        return len(self.accepted_changes)

    def rejected_count(self) -> int:
        return len(self.rejected_changes)

    def record_count(self) -> int:
        return len(self.evolution_records)

    def confidence_avg(self) -> float:
        if not self.accepted_changes:
            return 0.0
        total = sum(c.confidence for c in self.accepted_changes)
        return round(total / len(self.accepted_changes), 4)

    def to_dict(self) -> Dict[str, Any]:
        snap_dict: Optional[Dict[str, Any]] = None
        if self.new_snapshot is not None and hasattr(
            self.new_snapshot, "to_dict"
        ):
            try:
                snap_dict = self.new_snapshot.to_dict()
            except Exception:  # noqa: BLE001
                snap_dict = None
        original_dict: Optional[Dict[str, Any]] = None
        if self.original_snapshot is not None and hasattr(
            self.original_snapshot, "to_dict"
        ):
            try:
                original_dict = self.original_snapshot.to_dict()
            except Exception:  # noqa: BLE001
                original_dict = None
        return {
            "accepted_changes": [c.to_dict() for c in self.accepted_changes],
            "rejected_changes": [c.to_dict() for c in self.rejected_changes],
            "reject_reasons": list(self.reject_reasons),
            "evolution_records": [
                r.to_dict() for r in self.evolution_records
            ],
            "new_snapshot": snap_dict,
            "original_snapshot": original_dict,
            "is_noop": self.is_noop,
            "summary": self.summary,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]] = None) -> "SelfModelEvolutionResult":
        d = dict(data or {})
        accepted_raw = d.get("accepted_changes", []) or []
        rejected_raw = d.get("rejected_changes", []) or []
        records_raw = d.get("evolution_records", []) or []
        new_snap = d.get("new_snapshot")
        original_snap = d.get("original_snapshot")
        if isinstance(new_snap, dict) and _HAS_SNAPSHOT_IMPORT:
            try:
                new_snap = _Snapshot.from_dict(new_snap)
            except Exception:  # noqa: BLE001
                new_snap = None
        if isinstance(original_snap, dict) and _HAS_SNAPSHOT_IMPORT:
            try:
                original_snap = _Snapshot.from_dict(original_snap)
            except Exception:  # noqa: BLE001
                original_snap = None
        return cls(
            accepted_changes=[
                SelfModelChange.from_dict(c) for c in accepted_raw
            ],
            rejected_changes=[
                SelfModelChange.from_dict(c) for c in rejected_raw
            ],
            reject_reasons=list(d.get("reject_reasons", []) or []),
            evolution_records=[
                EvolutionRecord.from_dict(r) for r in records_raw
            ],
            new_snapshot=new_snap,
            original_snapshot=original_snap,
            is_noop=bool(d.get("is_noop", True)),
            summary=str(d.get("summary", "") or ""),
            schema_version=str(
                d.get("schema_version", EVOLUTION_RECORD_SCHEMA_VERSION)
            ),
        )


# ============================================================
# 工具
# ============================================================
def _coerce_change_list(value: Any) -> List[SelfModelChange]:
    """把任意值尽力规范化为 List[SelfModelChange]。"""
    if not value:
        return []
    if isinstance(value, list):
        out: List[SelfModelChange] = []
        for item in value:
            if isinstance(item, SelfModelChange):
                out.append(item)
            elif isinstance(item, dict):
                try:
                    out.append(SelfModelChange.from_dict(item))
                except Exception:  # noqa: BLE001
                    continue
        return out
    return []


__all__ = [
    "SelfModelChange",
    "EvolutionRecord",
    "EvolutionSourceType",
    "SelfModelEvolutionResult",
    "EVOLUTION_RECORD_SCHEMA_VERSION",
    "MAX_REASON_TEXT",
    "MAX_EVIDENCE_TEXT",
    "MAX_FIELD_NAME",
    "MAX_CHANGE_LIST",
]
