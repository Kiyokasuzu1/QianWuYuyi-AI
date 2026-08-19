# -*- coding: utf-8 -*-
"""
src/runtime/self_model/audit/growth_audit_record.py

Phase 4.2.4: GrowthAuditRecord —— 成长审计记录

职责:
- 记录 SelfModelSnapshot 的单次变化(产生一条审计 record)
- 包含: identity_id / from_version / to_version / change_summary /
  category / severity / source / timestamp / metadata
- 不可变 record(创建后不再修改)
- 支持序列化(to_dict / from_dict)

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


GROWTH_AUDIT_RECORD_SCHEMA_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _new_id() -> str:
    return f"aud_{uuid.uuid4().hex[:12]}"


# 审计类别
class AuditCategory(str, Enum):
    """审计类别(Phase 4.2.4 / v1.0)。

    - TRAIT_CHANGE:    稳定特质变化
    - VALUE_CHANGE:    核心价值观变化
    - PREFERENCE:      偏好变化
    - STATE_CHANGE:    当前状态变化
    - ENTRY_ADDED:     新增 entry(成长 / 反思 / 事件)
    - ENTRY_REMOVED:   删除 entry
    - IDENTITY:        身份基础信息变化
    - VERSION_BUMP:    单纯版本号增加
    - INITIAL:         首次建立快照
    - OTHER:           其它
    """
    TRAIT_CHANGE = "trait_change"
    VALUE_CHANGE = "value_change"
    PREFERENCE = "preference"
    STATE_CHANGE = "state_change"
    ENTRY_ADDED = "entry_added"
    ENTRY_REMOVED = "entry_removed"
    IDENTITY = "identity"
    VERSION_BUMP = "version_bump"
    INITIAL = "initial"
    OTHER = "other"


# 审计严重度
class AuditSeverity(str, Enum):
    """审计严重度。

    - INFO:    一般信息(普通 snapshot 版本变化)
    - NOTICE:  需要注意(新增 / 删除 entry)
    - WARN:    警告(稳定特质 / 价值观变化)
    - ALERT:   重要告警(身份 / 状态显著变化)
    """
    INFO = "info"
    NOTICE = "notice"
    WARN = "warn"
    ALERT = "alert"


def _infer_category_from_diff(diff_result: Dict[str, Any]) -> List[AuditCategory]:
    """根据 diff 推断涉及的类别(去重)。"""
    cats: List[AuditCategory] = []
    if not isinstance(diff_result, dict):
        return cats
    if diff_result.get("identity", {}).get("added") or \
       diff_result.get("identity", {}).get("removed") or \
       diff_result.get("identity", {}).get("changed"):
        cats.append(AuditCategory.IDENTITY)
    if diff_result.get("core_values", {}).get("added") or \
       diff_result.get("core_values", {}).get("removed") or \
       diff_result.get("core_values", {}).get("changed"):
        cats.append(AuditCategory.VALUE_CHANGE)
    if diff_result.get("stable_traits", {}).get("added") or \
       diff_result.get("stable_traits", {}).get("removed") or \
       diff_result.get("stable_traits", {}).get("changed"):
        cats.append(AuditCategory.TRAIT_CHANGE)
    if diff_result.get("preferences", {}).get("added") or \
       diff_result.get("preferences", {}).get("removed") or \
       diff_result.get("preferences", {}).get("changed"):
        cats.append(AuditCategory.PREFERENCE)
    if diff_result.get("current_state", {}).get("added") or \
       diff_result.get("current_state", {}).get("removed") or \
       diff_result.get("current_state", {}).get("changed"):
        cats.append(AuditCategory.STATE_CHANGE)
    if diff_result.get("entries", {}).get("added"):
        cats.append(AuditCategory.ENTRY_ADDED)
    if diff_result.get("entries", {}).get("removed"):
        cats.append(AuditCategory.ENTRY_REMOVED)
    return cats


def _infer_severity(
    diff_result: Dict[str, Any], categories: List[AuditCategory],
) -> AuditSeverity:
    """根据 diff 总变更数 + 类别推断严重度。"""
    summary = diff_result.get("summary", {}) if isinstance(diff_result, dict) else {}
    total = int(summary.get("total_changes", 0) or 0)
    if AuditCategory.IDENTITY in categories and total > 0:
        return AuditSeverity.ALERT
    if AuditCategory.VALUE_CHANGE in categories and total > 0:
        return AuditSeverity.ALERT
    if (
        AuditCategory.TRAIT_CHANGE in categories
        or AuditCategory.STATE_CHANGE in categories
    ) and total > 0:
        return AuditSeverity.WARN
    if AuditCategory.ENTRY_ADDED in categories or \
       AuditCategory.ENTRY_REMOVED in categories or \
       AuditCategory.PREFERENCE in categories:
        return AuditSeverity.NOTICE
    return AuditSeverity.INFO


@dataclass
class GrowthAuditRecord:
    """单条成长审计记录(Phase 4.2.4 / v1.0)。

    字段:
    - record_id:     str               # 唯一 id
    - identity_id:   str               # SelfModel identity_id
    - from_version:  int               # 旧版本号
    - to_version:    int               # 新版本号
    - categories:    List[str]         # 涉及的审计类别
    - severity:      str               # 严重度
    - summary:       str               # 简短摘要(可读)
    - diff:          Optional[Dict]    # 详细 diff(可省略)
    - source:        str               # 来源(runtime / admin / migration)
    - timestamp:     str               # ISO 时间
    - metadata:      Dict[str, Any]    # 其它元数据
    - schema_version:str               # 固定 "1.0"
    """

    identity_id: str = ""
    from_version: int = 0
    to_version: int = 0
    categories: List[str] = field(default_factory=list)
    severity: str = AuditSeverity.INFO.value
    summary: str = ""
    diff: Optional[Dict[str, Any]] = None
    source: str = "runtime"
    timestamp: str = field(default_factory=_now_iso)
    metadata: Dict[str, Any] = field(default_factory=dict)
    record_id: str = field(default_factory=_new_id)
    schema_version: str = GROWTH_AUDIT_RECORD_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GrowthAuditRecord":
        if not isinstance(data, dict):
            raise TypeError(f"data must be dict, got {type(data).__name__}")
        return cls(
            identity_id=str(data.get("identity_id", "") or ""),
            from_version=int(data.get("from_version", 0) or 0),
            to_version=int(data.get("to_version", 0) or 0),
            categories=list(data.get("categories", []) or []),
            severity=str(data.get("severity", AuditSeverity.INFO.value)),
            summary=str(data.get("summary", "") or ""),
            diff=data.get("diff"),
            source=str(data.get("source", "runtime")),
            timestamp=str(data.get("timestamp", "") or _now_iso()),
            metadata=dict(data.get("metadata", {}) or {}),
            record_id=str(data.get("record_id", "") or _new_id()),
            schema_version=str(
                data.get("schema_version", GROWTH_AUDIT_RECORD_SCHEMA_VERSION)
            ),
        )

    def is_alert(self) -> bool:
        return self.severity == AuditSeverity.ALERT.value

    def is_warn_or_above(self) -> bool:
        return self.severity in (
            AuditSeverity.WARN.value,
            AuditSeverity.ALERT.value,
        )

    @classmethod
    def from_diff(
        cls,
        diff_result: Dict[str, Any],
        identity_id: Optional[str] = None,
        source: str = "runtime",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "GrowthAuditRecord":
        """从 diff 结果自动生成一条 audit record。"""
        categories = _infer_category_from_diff(diff_result)
        severity = _infer_severity(diff_result, categories)
        summary = _make_summary_text(diff_result, categories, severity)
        fid = identity_id or (diff_result.get("identity_id", "")
                              if isinstance(diff_result, dict) else "")
        return cls(
            identity_id=str(fid or ""),
            from_version=int(diff_result.get("from_version", 0) or 0)
                if isinstance(diff_result, dict) else 0,
            to_version=int(diff_result.get("to_version", 0) or 0)
                if isinstance(diff_result, dict) else 0,
            categories=[c.value if isinstance(c, AuditCategory) else str(c)
                        for c in categories],
            severity=severity.value if isinstance(severity, AuditSeverity) else str(severity),
            summary=summary,
            diff=diff_result,
            source=source,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def initial(
        cls,
        snapshot: Any,
        identity_id: Optional[str] = None,
        source: str = "runtime",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "GrowthAuditRecord":
        """生成一条 INITIAL 记录(首次建立快照)。"""
        if isinstance(snapshot, dict):
            fid = identity_id or snapshot.get("identity_id", "")
            version = snapshot.get("version", 1)
            updated_at = snapshot.get("updated_at", "")
        else:
            fid = identity_id or getattr(snapshot, "identity_id", "")
            version = getattr(snapshot, "version", 1)
            updated_at = getattr(snapshot, "updated_at", "")
        return cls(
            identity_id=str(fid or ""),
            from_version=0,
            to_version=int(version or 1),
            categories=[AuditCategory.INITIAL.value],
            severity=AuditSeverity.NOTICE.value,
            summary=f"Initial SelfModel snapshot established at v{version}",
            diff=None,
            source=source,
            metadata={
                "updated_at": updated_at,
                **(metadata or {}),
            },
        )


def _make_summary_text(
    diff_result: Dict[str, Any],
    categories: List[AuditCategory],
    severity: AuditSeverity,
) -> str:
    if not isinstance(diff_result, dict):
        return "snapshot change"
    s = diff_result.get("summary", {}) or {}
    total = int(s.get("total_changes", 0) or 0)
    if total == 0:
        return "snapshot version bump (no content changes)"
    from_v = diff_result.get("from_version", "?")
    to_v = diff_result.get("to_version", "?")
    cat_names = ",".join(
        c.value if isinstance(c, AuditCategory) else str(c)
        for c in categories
    ) or "other"
    return (
        f"SelfModel v{from_v} -> v{to_v}: {total} change(s) "
        f"[{cat_names}] severity={severity.value if isinstance(severity, AuditSeverity) else severity}"
    )


__all__ = [
    "GrowthAuditRecord",
    "GROWTH_AUDIT_RECORD_SCHEMA_VERSION",
    "AuditCategory",
    "AuditSeverity",
]
