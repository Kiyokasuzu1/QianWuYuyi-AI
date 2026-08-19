# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/reflection_record.py

Phase 4.3: ReflectionRecord —— 自我反思记录
Phase 4.7: 扩展 —— 增加一致性验证相关字段 (reflection_type / affected_fields /
           conflicts / severity / evidence_ids)

职责:
- 描述一次"对 SelfModel 变化的理解"记录
- evidence-based:每条 reflection 都必须由上游 audit record 触发,不凭空创造
- 可序列化(to_dict / from_dict)
- 不可变 record(创建后不再修改)

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 不修改 ResponseEngine
- 反射结果不直接写人格,只生成"理解记录"
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


REFLECTION_RECORD_SCHEMA_VERSION = "1.0"  # Phase 4.7: 扩展字段(optional),schema 保持 v1.0 向后兼容


def _now_iso() -> str:
    # 兼容 Python 3.11+ 的 timezone-aware UTC;旧版本回退到 utcnow()
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        return datetime.utcnow().isoformat() + "Z"


def _new_id() -> str:
    return f"ref_{uuid.uuid4().hex[:12]}"


# ============================================================
# 反思优先级
# ============================================================
class ReflectionPriority(str, Enum):
    """反思优先级(Phase 4.3 / v1.0)。

    - HIGH:    身份 / 核心价值观变化 → 必须记录
    - MEDIUM:  稳定特质 / 偏好变化 → 趋势分析
    - LOW:     当前状态 / entry 变化 → 低权重记录
    - NONE:    无意义变化(如纯版本号变化) → 跳过
    """
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


# ============================================================
# 反思类型
# ============================================================
class ReflectionKind(str, Enum):
    """反思类型(Phase 4.3 / v1.0)。

    - INITIAL:        首次建立自我模型(版本=1)
    - IDENTITY_DRIFT: 身份基础信息变化
    - VALUE_SHIFT:    核心价值观变化
    - TRAIT_TREND:    稳定特质变化(趋势)
    - PREFERENCE:     偏好形成
    - STATE_NOTE:     状态变化记录
    - GROWTH_NOTE:    成长条目记录
    - SILENT:         无明显变化
    """
    INITIAL = "initial"
    IDENTITY_DRIFT = "identity_drift"
    VALUE_SHIFT = "value_shift"
    TRAIT_TREND = "trait_trend"
    PREFERENCE = "preference"
    STATE_NOTE = "state_note"
    GROWTH_NOTE = "growth_note"
    SILENT = "silent"


_VALID_KINDS = frozenset(k.value for k in ReflectionKind)
_VALID_PRIORITIES = frozenset(p.value for p in ReflectionPriority)


# ============================================================
# Phase 4.7: 反思类型(一致性验证)
# ============================================================
class ReflectionType(str, Enum):
    """Phase 4.7 一致性验证反思类型。

    - VALIDATION:        一致性验证反思(由 SelfModelReflectionEngine 产生)
    - CONTRADICTION:     字段冲突反思(由 ContradictionDetector 产生)
    - DRIFT:             人格漂移反思(由 ConsistencyChecker 产生)
    - CONSISTENT:        验证通过(无冲突)
    - UNKNOWN:           未知类型
    """
    VALIDATION = "validation"
    CONTRADICTION = "contradiction"
    DRIFT = "drift"
    CONSISTENT = "consistent"
    UNKNOWN = "unknown"


_VALID_REFLECTION_TYPES = frozenset(t.value for t in ReflectionType)


# ============================================================
# Phase 4.7: 冲突类型
# ============================================================
class ConflictType(str, Enum):
    """Phase 4.7 冲突类型(由 ContradictionDetector 识别)。"""
    IDENTITY_CONFLICT = "identity_conflict"
    VALUE_CONFLICT = "value_conflict"
    TRAIT_CONFLICT = "trait_conflict"
    PREFERENCE_CONFLICT = "preference_conflict"
    BEHAVIOR_CONFLICT = "behavior_conflict"
    NONE = "none"


_VALID_CONFLICT_TYPES = frozenset(c.value for c in ConflictType)


def _clamp_confidence(v: Any) -> float:
    """把 confidence 限制在 [0.0, 1.0]。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _clamp_severity(v: Any) -> float:
    """把 severity 限制在 [0.0, 1.0]。"""
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
# ReflectionRecord
# ============================================================
@dataclass
class ReflectionRecord:
    """自我反思记录(Phase 4.3 / v1.0)。

    字段:
    - reflection_id:       str                  # 唯一 id
    - identity_id:         str                  # SelfModel identity_id
    - timestamp:           str                  # ISO 时间
    - source_audit_id:     str                  # 触发本次反思的 audit record id
    - trigger_category:    str                  # 触发类别(initial/trait_change/...)
    - reflection_kind:     str                  # 反思类型(ReflectionKind)
    - priority:            str                  # 优先级(ReflectionPriority)
    - observation:         str                  # 客观观察:发生了什么
    - interpretation:      str                  # 主观理解:这意味着什么
    - relation_to_values:  List[str]            # 关联核心价值
    - confidence:          float                # 置信度 [0.0, 1.0]
    - from_version:        int                  # 旧版本号
    - to_version:          int                  # 新版本号
    - evidence:            Dict[str, Any]       # 证据(截断的 diff 摘要)
    - metadata:            Dict[str, Any]       # 其它元数据
    - source:              str                  # 来源(runtime / import / admin)
    - schema_version:      str                  # 固定 "1.0"

    含义说明:
    - observation:  客观、可验证的事实陈述(基于 audit diff)。
                   例如: "warmth trait increased from 0.7 to 0.8"
    - interpretation: 系统对此变化的"理解",必须与 evidence 对应,
                     不创造没有证据的事实。
    - relation_to_values: 与核心价值观的关联(启发式映射,非强制)。
    - confidence: 0~1 之间的可信度。变化越大 / 越模糊,confidence 越低。
    """

    identity_id: str = ""
    source_audit_id: str = ""
    trigger_category: str = ""
    reflection_kind: str = ReflectionKind.SILENT.value
    priority: str = ReflectionPriority.NONE.value
    observation: str = ""
    interpretation: str = ""
    relation_to_values: List[str] = field(default_factory=list)
    confidence: float = 0.5
    from_version: int = 0
    to_version: int = 0
    evidence: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = "runtime"
    timestamp: str = field(default_factory=_now_iso)
    reflection_id: str = field(default_factory=_new_id)
    schema_version: str = REFLECTION_RECORD_SCHEMA_VERSION
    # --------------------------------------------------------
    # Phase 4.7: 一致性验证相关扩展字段(可选,默认空)
    # --------------------------------------------------------
    reflection_type: str = ReflectionType.UNKNOWN.value
    affected_fields: List[str] = field(default_factory=list)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    severity: float = 0.0
    evidence_ids: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # 字段合法性校验
        if self.reflection_kind not in _VALID_KINDS:
            # 允许自定义,降级为 silent
            self.reflection_kind = ReflectionKind.SILENT.value
        if self.priority not in _VALID_PRIORITIES:
            self.priority = ReflectionPriority.NONE.value
        self.confidence = _clamp_confidence(self.confidence)
        # Phase 4.7: 校验 reflection_type
        if self.reflection_type not in _VALID_REFLECTION_TYPES:
            self.reflection_type = ReflectionType.UNKNOWN.value
        # Phase 4.7: severity 自动 clamp
        self.severity = _clamp_severity(self.severity)
        # Phase 4.7: 规范化 affected_fields / conflicts / evidence_ids
        if not isinstance(self.affected_fields, list):
            try:
                self.affected_fields = list(self.affected_fields or [])
            except Exception:  # noqa: BLE001
                self.affected_fields = []
        self.affected_fields = [str(x) for x in self.affected_fields if x is not None]
        if not isinstance(self.conflicts, list):
            try:
                self.conflicts = list(self.conflicts or [])
            except Exception:  # noqa: BLE001
                self.conflicts = []
        # conflicts 列表中的 dict 元素保持原样,只过滤非 dict 项
        self.conflicts = [c for c in self.conflicts if isinstance(c, dict)]
        if not isinstance(self.evidence_ids, list):
            try:
                self.evidence_ids = list(self.evidence_ids or [])
            except Exception:  # noqa: BLE001
                self.evidence_ids = []
        self.evidence_ids = [str(x) for x in self.evidence_ids if x is not None]

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReflectionRecord":
        if not isinstance(data, dict):
            raise TypeError(
                f"ReflectionRecord.from_dict 需要 dict,实际 {type(data).__name__}"
            )
        return cls(
            identity_id=str(data.get("identity_id", "") or ""),
            source_audit_id=str(data.get("source_audit_id", "") or ""),
            trigger_category=str(data.get("trigger_category", "") or ""),
            reflection_kind=str(
                data.get("reflection_kind", ReflectionKind.SILENT.value)
            ),
            priority=str(
                data.get("priority", ReflectionPriority.NONE.value)
            ),
            observation=str(data.get("observation", "") or ""),
            interpretation=str(data.get("interpretation", "") or ""),
            relation_to_values=list(data.get("relation_to_values", []) or []),
            confidence=_clamp_confidence(data.get("confidence", 0.5)),
            from_version=int(data.get("from_version", 0) or 0),
            to_version=int(data.get("to_version", 0) or 0),
            evidence=dict(data.get("evidence", {}) or {}),
            metadata=dict(data.get("metadata", {}) or {}),
            source=str(data.get("source", "runtime")),
            timestamp=str(data.get("timestamp", "") or _now_iso()),
            reflection_id=str(data.get("reflection_id", "") or _new_id()),
            schema_version=str(
                data.get("schema_version", REFLECTION_RECORD_SCHEMA_VERSION)
            ),
            # Phase 4.7: 扩展字段
            reflection_type=str(
                data.get("reflection_type", ReflectionType.UNKNOWN.value)
            ),
            affected_fields=list(data.get("affected_fields", []) or []),
            conflicts=list(data.get("conflicts", []) or []),
            severity=_clamp_severity(data.get("severity", 0.0)),
            evidence_ids=list(data.get("evidence_ids", []) or []),
        )

    # --------------------------------------------------------
    # 便利查询
    # --------------------------------------------------------
    def is_high_priority(self) -> bool:
        return self.priority == ReflectionPriority.HIGH.value

    def is_silent(self) -> bool:
        return self.reflection_kind == ReflectionKind.SILENT.value

    # --------------------------------------------------------
    # Phase 4.7: 便利查询
    # --------------------------------------------------------
    def is_validation(self) -> bool:
        return self.reflection_type == ReflectionType.VALIDATION.value

    def is_contradiction(self) -> bool:
        return self.reflection_type == ReflectionType.CONTRADICTION.value

    def is_drift(self) -> bool:
        return self.reflection_type == ReflectionType.DRIFT.value

    def is_consistent(self) -> bool:
        return self.reflection_type == ReflectionType.CONSISTENT.value

    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    def conflict_count(self) -> int:
        return len(self.conflicts)

    def is_severe(self, threshold: float = 0.7) -> bool:
        return self.severity >= threshold

    def has_evidence(self) -> bool:
        return len(self.evidence_ids) > 0


__all__ = [
    "ReflectionRecord",
    "REFLECTION_RECORD_SCHEMA_VERSION",
    "ReflectionPriority",
    "ReflectionKind",
    # Phase 4.7
    "ReflectionType",
    "ConflictType",
]
