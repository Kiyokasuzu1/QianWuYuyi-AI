"""
Self Reflection (Phase 6.1)

SelfReflection 是羽依对自身变化的元认知笔记。

关键约束：
- SelfReflection 不直接修改 SelfIdentity / SelfBelief / SelfHistory。
- 它只生成"我意识到..."的笔记，供 prompt context 使用。
- 所有 SelfReflection 笔记由 SelfModelAdapter 在写入 SelfHistory 后追加。

不要混淆：
- SelfReflection ≠ ReflectionInsight
  - ReflectionInsight 来源于 ReflectionEngine（认知层面）
  - SelfReflection 来源于 SelfModel 自身变化（meta-cognition 层面）
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 合法 trigger_source 与 reflection_type
# ============================================================

VALID_TRIGGER_SOURCES: frozenset = frozenset({
    "self_belief_change",       # SelfBelief 新增或强化
    "self_understanding_update", # 理解水平变化
    "snapshot_diff",            # snapshot 之间差异
    "self_contradiction",       # 自我矛盾出现
    "rollback",                 # 回滚后反思
    "manual",                   # 人工触发
    "pcr_applied",              # PCR 应用后
})

VALID_REFLECTION_TYPES: frozenset = frozenset({
    "identity",      # 身份层面的反思
    "value",         # 价值观层面
    "behavior",      # 行为模式层面
    "contradiction", # 矛盾层面
    "growth",        # 成长层面
    "continuity",    # 连续性层面（Self Continuity Test 使用）
})


@dataclass
class SelfReflectionNote:
    """
    SelfReflectionNote —— 一条自我反思笔记。

    字段：
    - note_id: 唯一标识
    - timestamp: ISO8601
    - trigger_source: 触发源（合法值之一）
    - reflection_type: 反思类型
    - content: 自然语言反思内容（不修改 SelfModel）
    - related_belief_ids: 关联 SelfBelief ID
    - related_trait_changes: 关联的特质变化 {trait: delta}
    - confidence: 这条反思的置信度 0~1
    - sources: 触发源 ID 列表
    """

    note_id: str = field(default_factory=lambda: f"refl_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=_now_iso)
    trigger_source: str = "manual"
    reflection_type: str = "identity"
    content: str = ""
    related_belief_ids: List[str] = field(default_factory=list)
    related_trait_changes: Dict[str, float] = field(default_factory=dict)
    confidence: float = 0.5
    sources: List[str] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.trigger_source not in VALID_TRIGGER_SOURCES:
            errors.append(f"invalid trigger_source: {self.trigger_source}")
        if self.reflection_type not in VALID_REFLECTION_TYPES:
            errors.append(f"invalid reflection_type: {self.reflection_type}")
        if not isinstance(self.content, str) or not self.content.strip():
            errors.append("content must be non-empty string")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence out of [0,1]: {self.confidence}")
        if not isinstance(self.related_belief_ids, list):
            errors.append("related_belief_ids must be list")
        if not isinstance(self.related_trait_changes, dict):
            errors.append("related_trait_changes must be dict")
        if not isinstance(self.sources, list):
            errors.append("sources must be list")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfReflectionNote":
        return cls(
            note_id=data.get("note_id") or f"refl_{uuid.uuid4().hex[:10]}",
            timestamp=data.get("timestamp", _now_iso()),
            trigger_source=data.get("trigger_source", "manual"),
            reflection_type=data.get("reflection_type", "identity"),
            content=data.get("content", ""),
            related_belief_ids=list(data.get("related_belief_ids") or []),
            related_trait_changes=dict(data.get("related_trait_changes") or {}),
            confidence=float(data.get("confidence", 0.5)),
            sources=list(data.get("sources") or []),
        )


# ============================================================
# SelfReflection 容器
# ============================================================

class SelfReflectionStore:
    """
    SelfReflectionNote 容器。

    约束：
    - 仅 append / query / latest
    - 不提供 update（一旦写入不可变）
    - 不直接修改 SelfModel
    """

    MAX_NOTES = 500

    def __init__(self) -> None:
        self._notes: List[SelfReflectionNote] = []

    def append(self, note: SelfReflectionNote) -> bool:
        if not note.is_valid():
            return False
        self._notes.append(note)
        if len(self._notes) > self.MAX_NOTES:
            self._notes = self._notes[-self.MAX_NOTES:]
        return True

    def all(self) -> List[SelfReflectionNote]:
        return list(self._notes)

    def count(self) -> int:
        return len(self._notes)

    def latest(self, n: int = 10) -> List[SelfReflectionNote]:
        return self._notes[-n:]

    def query(
        self,
        trigger_source: Optional[str] = None,
        reflection_type: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> List[SelfReflectionNote]:
        out: List[SelfReflectionNote] = []
        for n in self._notes:
            if trigger_source and n.trigger_source != trigger_source:
                continue
            if reflection_type and n.reflection_type != reflection_type:
                continue
            if since and n.timestamp < since:
                continue
            if until and n.timestamp > until:
                continue
            if n.confidence < min_confidence:
                continue
            out.append(n)
            if len(out) >= limit:
                break
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "count": len(self._notes),
            "notes": [n.to_dict() for n in self._notes],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfReflectionStore":
        store = cls()
        for n in (data.get("notes") or []):
            try:
                note = SelfReflectionNote.from_dict(n)
                if note.is_valid():
                    store._notes.append(note)
            except Exception:
                continue
        return store

    def clear(self) -> None:
        self._notes.clear()
