# -*- coding: utf-8 -*-
"""
src/runtime/personality_context.py

Phase 3.8.0: PersonalityRuntimeContext —— Runtime 层人格上下文快照

职责：
- 在一次 `process(event)` 周期内,把与人格相关的运行时状态聚合到一个对象里,
  供 PersonalityGuard 在生成回复前进行约束检查。

约束：
- 仅持有"快照"或"引用",不修改 / 不缓存人格系统内部数据。
- 不直接 import src.personality.* 中的任何具体实现。
- schema_version = "1.0"。

字段:
- personality_snapshot   Optional[Any]   # 来自 PersonalityAdapter.snapshot()（dict）
- emotion_state          Optional[Any]   # 来自 EmotionAdapter.update()（dict）
- relationship_state     Optional[Any]   # 来自 personality_snapshot 或独立注入
- communication_style    Dict[str, Any]  # 风格约束（从 personality_snapshot 抽取）
- behavior_constraints   List[str]       # 禁止行为 / 必须行为（从 personality_snapshot 抽取）
- schema_version         str             # 固定 "1.0"

注意:
- 不修改 src/personality/* 任何代码。
- 不修改 RuntimeContext schema。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import copy


PERSONALITY_RUNTIME_CONTEXT_SCHEMA_VERSION = "1.0"


def _empty_dict() -> Dict[str, Any]:
    return {}


def _empty_list() -> List[str]:
    return []


@dataclass
class PersonalityRuntimeContext:
    """Runtime 层人格上下文（v1.0）。

    PersonalityGuard 在生成回复前读取该对象进行守门检查。
    """

    personality_snapshot: Optional[Any] = None
    emotion_state: Optional[Any] = None
    relationship_state: Optional[Any] = None
    communication_style: Dict[str, Any] = field(default_factory=_empty_dict)
    behavior_constraints: List[str] = field(default_factory=_empty_list)
    schema_version: str = PERSONALITY_RUNTIME_CONTEXT_SCHEMA_VERSION

    # --------------------------------------------------------
    # 派生字段抽取
    # --------------------------------------------------------
    @classmethod
    def from_sources(
        cls,
        personality_snapshot: Optional[Any] = None,
        emotion_state: Optional[Any] = None,
    ) -> "PersonalityRuntimeContext":
        """从 personality_snapshot 中抽取 communication_style / behavior_constraints。

        抽取规则（v1.0）：
        - communication_style.tone            <- snapshot["tone"] 或 snapshot["communication_tone"]
        - communication_style.warmth          <- snapshot["warmth"] 或 snapshot["warmth_level"]
        - communication_style.formality       <- snapshot["formality"]
        - communication_style.verbosity       <- snapshot["verbosity"]
        - behavior_constraints                <- snapshot["behavior_constraints"] / ["prohibited_behaviors"]
        - relationship_state                  <- snapshot["relationship"] 或 snapshot["relationship_state"]
        """
        style: Dict[str, Any] = {}
        constraints: List[str] = []
        rel_state: Optional[Any] = None

        if isinstance(personality_snapshot, dict):
            for k in ("tone", "communication_tone"):
                if k in personality_snapshot:
                    style["tone"] = personality_snapshot[k]
                    break
            for k in ("warmth", "warmth_level"):
                if k in personality_snapshot:
                    style["warmth"] = personality_snapshot[k]
                    break
            for k in ("formality",):
                if k in personality_snapshot:
                    style["formality"] = personality_snapshot[k]
            for k in ("verbosity",):
                if k in personality_snapshot:
                    style["verbosity"] = personality_snapshot[k]
            # behavior constraints
            for k in ("behavior_constraints", "prohibited_behaviors"):
                v = personality_snapshot.get(k)
                if isinstance(v, list):
                    constraints = [str(x) for x in v]
                    break
            # relationship
            for k in ("relationship", "relationship_state"):
                if k in personality_snapshot:
                    rel_state = personality_snapshot[k]
                    break

        return cls(
            personality_snapshot=personality_snapshot,
            emotion_state=emotion_state,
            relationship_state=rel_state,
            communication_style=style,
            behavior_constraints=constraints,
        )

    # --------------------------------------------------------
    # 工具方法
    # --------------------------------------------------------
    def has_personality(self) -> bool:
        return self.personality_snapshot is not None

    def has_emotion(self) -> bool:
        return self.emotion_state is not None

    def has_relationship(self) -> bool:
        return self.relationship_state is not None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonalityRuntimeContext":
        payload = dict(data or {})
        return cls(
            personality_snapshot=payload.get("personality_snapshot"),
            emotion_state=payload.get("emotion_state"),
            relationship_state=payload.get("relationship_state"),
            communication_style=dict(payload.get("communication_style") or {}),
            behavior_constraints=list(payload.get("behavior_constraints") or []),
            schema_version=(
                payload.get("schema_version")
                or PERSONALITY_RUNTIME_CONTEXT_SCHEMA_VERSION
            ),
        )


__all__ = [
    "PersonalityRuntimeContext",
    "PERSONALITY_RUNTIME_CONTEXT_SCHEMA_VERSION",
]
