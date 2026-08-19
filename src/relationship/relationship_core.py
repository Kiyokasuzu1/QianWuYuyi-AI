# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段1:RelationshipCore 数据模型。

YUI_CORE 与 RELATIONSHIP_CORE 严格分离:
- YUI_CORE           = 羽依是谁(identity_core / yui_core_profile)
- RELATIONSHIP_CORE  = 羽依与特定人的关系(本模块)—— 是关系,不是身份

治理约束:
- visibility 默认 relationship_only(私有),global 只能显式声明;
- 本模型只承载事实与已审核约定,不含审批/激活逻辑(见 relationship_proposal.py);
- 禁止「LLM 认为重要 → 直接构造落库」:入库必须经过人工审核生命周期;
- 旧数据零迁移:from_dict 缺字段回退默认值,不写回。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

VISIBILITY_GLOBAL = "global"
VISIBILITY_RELATIONSHIP_ONLY = "relationship_only"
DEFAULT_VISIBILITY = VISIBILITY_RELATIONSHIP_ONLY

VALID_VISIBILITIES = (VISIBILITY_GLOBAL, VISIBILITY_RELATIONSHIP_ONLY)

RELATIONSHIP_TYPES = ("creator", "friend", "partner", "other")


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _safe_float(value: Any, default: float = 0.5) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_str_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


@dataclass
class RelationshipCore:
    """羽依与特定用户的关系核心(已审核的事实、约定与边界)。

    字段语义:
    - relationship_id   "yuyi:<user_id>",关系核心主键
    - source_user_id    关系对象
    - events            支撑事实(事件来源,可追溯)
    - agreements        已审核关系约定(对羽依的约束)
    - boundaries        可以影响行为的边界
    - anchor_memory_ids 核心记忆锚点(跨会话可见的白名单来源)
    - visibility        relationship_only(默认) / global(显式)
    """

    relationship_id: str = ""
    source_user_id: str = ""
    relationship_type: str = "other"
    importance: float = 0.5
    events: List[Any] = field(default_factory=list)
    agreements: List[str] = field(default_factory=list)
    boundaries: List[str] = field(default_factory=list)
    anchor_memory_ids: List[str] = field(default_factory=list)
    visibility: str = DEFAULT_VISIBILITY
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relationship_id": self.relationship_id,
            "source_user_id": self.source_user_id,
            "relationship_type": self.relationship_type,
            "importance": self.importance,
            "events": list(self.events),
            "agreements": list(self.agreements),
            "boundaries": list(self.boundaries),
            "anchor_memory_ids": list(self.anchor_memory_ids),
            "visibility": self.visibility,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RelationshipCore":
        """缺字段容错:旧数据可读,全部回退默认值(零迁移,不写回)。"""
        data = data if isinstance(data, dict) else {}
        now = _now()
        visibility = str(data.get("visibility") or DEFAULT_VISIBILITY)
        if visibility not in VALID_VISIBILITIES:
            visibility = DEFAULT_VISIBILITY
        relationship_type = str(data.get("relationship_type") or "other")
        if relationship_type not in RELATIONSHIP_TYPES:
            relationship_type = "other"
        events = data.get("events")
        return cls(
            relationship_id=str(data.get("relationship_id") or ""),
            source_user_id=str(data.get("source_user_id") or ""),
            relationship_type=relationship_type,
            importance=_safe_float(data.get("importance"), 0.5),
            events=list(events) if isinstance(events, list) else [],
            agreements=_safe_str_list(data.get("agreements")),
            boundaries=_safe_str_list(data.get("boundaries")),
            anchor_memory_ids=_safe_str_list(data.get("anchor_memory_ids")),
            visibility=visibility,
            created_at=str(data.get("created_at") or now),
            updated_at=str(data.get("updated_at") or now),
        )


__all__ = [
    "VISIBILITY_GLOBAL",
    "VISIBILITY_RELATIONSHIP_ONLY",
    "DEFAULT_VISIBILITY",
    "VALID_VISIBILITIES",
    "RELATIONSHIP_TYPES",
    "RelationshipCore",
]
