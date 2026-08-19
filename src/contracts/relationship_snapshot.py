# -*- coding: utf-8 -*-
"""
Phase 4.2 — RelationshipSnapshot Contract

职责：统一关系事实读取契约。

设计原则：
- 只存关系事实，不存推导（warmth/intimacy/formality 属于 CommunicationStyle）
- 同名字段不合并（current.trust ≠ long_term.trust）
- 缺失字段不猜测（bond_strength 不存在就是不存在）
- immutable（消费者不能修改 Snapshot）

数据来源：
- current  → v3.5.27 (src/relationship/relationship_state.py)  实时互动域
- long_term → v0.6    (src/personality/relationship_state.py)  长期积累域

禁止：
- 把 communication_style 标签当关系事实
- 用 trust × 0.7 推导 bond_strength
- 用 collaboration 替代 shared_history
- 用 interaction_frequency 替代 activity_level
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List


# ============================================================
# CurrentRelationshipState — 当前互动域（来源：v3.5.27）
# ============================================================

@dataclass(frozen=True)
class CurrentRelationshipState:
    """当前互动状态快照。

    语义："最近的互动让关系状态表现为什么样"
    频率：每轮互动更新
    用途：CommunicationStyle 推导
    """
    trust: float = 0.0
    familiarity: float = 0.0
    collaboration: float = 0.0
    interaction_frequency: float = 0.0
    relationship_stage: str = "initial"


# ============================================================
# LongTermRelationshipState — 长期积累域（来源：v0.6）
# ============================================================

@dataclass(frozen=True)
class LongTermRelationshipState:
    """长期关系积累快照。

    语义："这些年我们之间已经积累了什么"
    频率：事件驱动，不频繁更新
    用途：Personality / Behavior / SelfModel
    """
    trust: float = 0.0
    familiarity: float = 0.0
    bond_strength: float = 0.0
    promise_level: float = 0.0
    shared_history: float = 0.0
    activity_level: float = 0.0
    milestones: list = field(default_factory=list)
    important_events: list = field(default_factory=list)


# ============================================================
# RelationshipSnapshotProvenance — 数据溯源
# ============================================================

@dataclass(frozen=True)
class RelationshipSnapshotProvenance:
    """Snapshot 数据溯源。

    记录当前快照的每一份数据来自哪个版本、哪个系统。
    """
    current_source: str = ""
    long_term_source: str = ""

    current_version: str = ""
    long_term_version: str = ""

    captured_at: str = ""


# ============================================================
# RelationshipSnapshot — 顶层契约
# ============================================================

@dataclass(frozen=True)
class RelationshipSnapshot:
    """统一关系事实读取契约。

    不负责：
    - 推导 warmth / intimacy / formality / initiative
    - 合并同名字段
    - 猜测缺失字段
    - 修改任何 RelationshipState
    """
    user_id: str = ""
    preferred_name: str = ""

    current: CurrentRelationshipState = field(
        default_factory=CurrentRelationshipState
    )

    long_term: LongTermRelationshipState = field(
        default_factory=LongTermRelationshipState
    )

    provenance: RelationshipSnapshotProvenance = field(
        default_factory=RelationshipSnapshotProvenance
    )