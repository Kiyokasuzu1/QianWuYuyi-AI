from pydantic import BaseModel, Field, validator
from typing import List, Dict, Optional
from uuid import uuid4
from datetime import datetime


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Evidence(BaseModel):
    text: str
    role: str  # "user" | "assistant"
    source_index: int
    memory_id: Optional[str] = None


class Event(BaseModel):
    # Raw event as produced by EventExtractor
    event: str
    topic: str
    canonical_topic: Optional[str] = None
    event_type: str
    evidence: List[Evidence] = []


class NormalizedEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: make_id("evt"))
    event_identity: Optional[str] = None
    topic: str
    canonical_topic: Optional[str] = None
    category: Optional[str] = None
    category_id: Optional[str] = None
    meaning: Optional[str] = None
    event_type: str
    event_scope: Optional[str] = None
    importance: float = 0.5
    growth_weight: float = 0.5
    emotion_tag: Optional[List[str]] = []
    confidence: float = 0.5
    source_ids: List[str] = []
    evidence: List[Evidence] = []
    is_first_occurrence: bool = True
    memory_mode: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict = {}

    @validator('importance', 'confidence', 'growth_weight')
    def clamp_scores(cls, v):
        try:
            v = float(v)
        except Exception:
            v = 0.0
        if v < 0:
            return 0.0
        if v > 1:
            return 1.0
        return round(v, 4)


class ChangeLogEntry(BaseModel):
    change_id: str = Field(default_factory=lambda: make_id("chg"))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    event_id: Optional[str] = None
    event_topic: Optional[str] = None
    mode: Optional[str] = None
    delta: Dict[str, float] = {}
    old_state: Dict[str, float] = {}
    new_state: Dict[str, float] = {}
    rationale: Optional[str] = None
    confidence: float = 0.0
    reversible: bool = True
    metadata: Dict = {}


class PersonalityState(BaseModel):
    metrics: Dict[str, float] = {}
    identities: List[str] = []
    behaviors: Dict = {}
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    change_history: List[str] = []
