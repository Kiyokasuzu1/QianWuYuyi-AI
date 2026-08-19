"""
反思候选数据类 v1.1
增加 confidence 范围保护
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class ReflectionCandidate:
    source_event_ids: List[str]
    possible_changes: List[str]
    possible_beliefs: List[str]
    event_summary: str
    previous_self_view: str = ""
    tentative_understanding: str = ""
    causal_chain: List[str] = field(default_factory=list)
    confidence: float = 0.5

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, self.confidence))