# src/contracts/growth_schema.py
"""Contracts: Growth Proposal schema (dataclasses)

Phase 3.6.5: Schema Governance Final Audit
- canonical schema is the single source of truth
- schema_version 字段用于版本追踪（Phase 3.6.5 引入，默认 "1.0"）
- 旧版本数据（无 schema_version）反序列化时自动补默认值（backward compatibility）

GrowthProposal encapsulates suggested state changes and evidence references.

字段契约（v1.0 — Phase 3.6.5 起冻结）:
- id                       str           # 主键（唯一）
- source_event_id          str|None      # 触发事件
- proposed_changes         List[ChangeItem]  # 提议的状态变更
- confidence               float         # 置信度 [0, 1]
- evidence_ids             List[str]     # 证据 id 列表
- evaluator_meta           Dict[str, Any]# 评估器元数据（含 _governance_origin / _source_schema 等）
- timestamp                str           # ISO 8601 with Z
- status                   str           # proposed / accepted / rejected / cancelled / expired
- accepted_at              str|None      # 接受时间
- rejected_at              str|None      # 拒绝时间
- schema_version           str           # schema 版本（Phase 3.6.5 引入，默认 "1.0"）

Phase 3.6.5 冻结：
- 字段集合不再变动（仅允许追加，向后兼容）
- 字段名 / 字段类型 / 字段顺序锁定
- 字段语义锁定
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime
import uuid


# Phase 3.6.5: 冻结的 schema 版本号
CANONICAL_SCHEMA_VERSION = "1.0"


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class ChangeItem:
    path: str
    before: Optional[Any] = None
    after: Optional[Any] = None
    reason: Optional[str] = None


@dataclass
class GrowthProposal:
    # Phase 3.6.5: 8 个核心字段（id / proposed_changes / evidence_ids / confidence /
    #              evaluator_meta / status / timestamp / schema_version）
    id: str = field(default_factory=lambda: f"prop_{uuid.uuid4().hex[:12]}")
    source_event_id: Optional[str] = None
    proposed_changes: List[ChangeItem] = field(default_factory=list)
    confidence: float = 0.0
    evidence_ids: List[str] = field(default_factory=list)
    # T2-1-P0: ExperienceTrace 引用（旧数据缺省空列表兼容；validator 规则 2 要求非空）
    evidence_trace_ids: List[str] = field(default_factory=list)
    evaluator_meta: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=now_iso)
    status: str = "proposed"
    accepted_at: Optional[str] = None
    rejected_at: Optional[str] = None
    # Phase 3.6.5: schema_version 字段（默认 "1.0"，向后兼容）
    # - 新建实例：自动填 "1.0"
    # - 旧数据反序列化：缺省时填 "1.0"（视为兼容 v1.0）
    # - legacy schema 不增加此字段（保持 legacy schema 不变）
    schema_version: str = CANONICAL_SCHEMA_VERSION
    # T1-B: before_snapshot（七字段快照列表，生成阶段冻结；旧数据缺省空列表兼容）
    before_snapshot: List[Dict[str, Any]] = field(default_factory=list)
    # T1-D: provenance（来源链统一：system_rule | llm_candidate | system_state_read）
    provenance: str = "system_rule"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["proposed_changes"] = [
            asdict(c) for c in self.proposed_changes
        ]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GrowthProposal":
        pcs = data.get("proposed_changes", [])

        change_items = [
            ChangeItem(**c)
            for c in pcs
        ]

        # Phase 3.6.5: 缺省 schema_version 时回退到 "1.0"（向后兼容）
        # 旧数据（无 schema_version 字段）反序列化时不会报错
        schema_version = (
            data.get("schema_version")
            or CANONICAL_SCHEMA_VERSION
        )

        return cls(
            id=data.get("id") or f"prop_{uuid.uuid4().hex[:12]}",
            source_event_id=data.get("source_event_id"),
            proposed_changes=change_items,
            confidence=float(data.get("confidence", 0.0)),
            evidence_ids=data.get("evidence_ids", []),
            evaluator_meta=data.get("evaluator_meta", {}),
            timestamp=data.get("timestamp") or now_iso(),
            status=data.get("status", "proposed"),
            accepted_at=data.get("accepted_at"),
            rejected_at=data.get("rejected_at"),
            schema_version=schema_version,
        )
