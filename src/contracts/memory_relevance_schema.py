"""
Phase 3.5.16: Memory Relevance Schema

定义记忆检索相关性评估的结构化契约。

目标：
- 不删除原始记忆
- 保留 relevance audit
- 支持时间衰减、关系权重、身份相关性、情绪相关性
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class MemoryRelevanceBreakdown:
    """单条记忆的相关性因子分解。"""

    importance_score: float = 0.0
    query_match_score: float = 0.0
    semantic_score: float = 0.0
    time_decay_score: float = 0.0
    relationship_score: float = 0.0
    identity_score: float = 0.0
    emotional_score: float = 0.0
    final_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryRelevanceRecord:
    """记忆相关性评估审计记录。"""

    record_id: str = field(default_factory=lambda: f"mrr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    memory_id: str = ""
    memory_class: str = ""
    query: str = ""
    content_preview: str = ""

    retrieval_priority: str = "low"
    reasons: List[str] = field(default_factory=list)
    breakdown: MemoryRelevanceBreakdown = field(default_factory=MemoryRelevanceBreakdown)

    source: str = "memory_relevance_evaluator"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["breakdown"] = self.breakdown.to_dict()
        return data


@dataclass
class MemoryRelevanceSnapshot:
    """评估器快照。"""

    snapshot_id: str = field(default_factory=lambda: f"mrs_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    total_records: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0

    last_record_id: str = ""
    last_memory_id: str = ""
    last_priority: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
