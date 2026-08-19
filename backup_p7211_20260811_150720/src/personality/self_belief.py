"""
Self Belief (Phase 6.1)

自我信念的轻量 dataclass。
聚合来自 Preference / CoreValue / BehavioralPattern / SelfContradiction 的结构化内容。

不修改 SelfIdentity，不直接调用 SelfModelStore。
所有 SelfBelief 必须通过 SelfModelAdapter 创建。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 合法 domain 列表（与 SelfModelAdapter 校验对齐）
# ============================================================

VALID_BELIEF_DOMAINS: frozenset = frozenset({
    "value",          # 核心价值观
    "preference",     # 偏好
    "pattern",        # 行为模式
    "contradiction",  # 自我矛盾
    "identity",       # 身份陈述
})


@dataclass
class SelfBelief:
    """
    SelfBelief —— 羽依持有的一个自我信念条目。

    字段说明：
    - belief_id: 唯一标识
    - domain: 域（value / preference / pattern / contradiction / identity）
    - content: 信念内容（结构化描述或短文本）
    - confidence: 置信度 0~1
    - sources: 来源 ID 列表（proposal_id / insight_id / gr_id / pcr_id）
    - first_seen: 首次发现时间
    - last_confirmed: 最近一次确认时间
    - evidence_count: 累计证据数
    - version: 自身版本号（每次更新 +1）
    """

    belief_id: str = field(default_factory=lambda: f"bel_{uuid.uuid4().hex[:10]}")
    domain: str = "value"
    content: str = ""
    confidence: float = 0.3
    sources: List[str] = field(default_factory=list)
    first_seen: str = field(default_factory=_now_iso)
    last_confirmed: str = field(default_factory=_now_iso)
    evidence_count: int = 1
    version: int = 1
    # Phase 6.2: Guardian - active 标记；retract 时改为 False
    active: bool = True

    # ============================================================
    # 校验
    # ============================================================

    def validate(self) -> List[str]:
        """返回错误信息列表（空表示合法）"""
        errors: List[str] = []
        if self.domain not in VALID_BELIEF_DOMAINS:
            errors.append(f"invalid domain: {self.domain} (must be in {sorted(VALID_BELIEF_DOMAINS)})")
        if not isinstance(self.content, str) or not self.content.strip():
            errors.append("content must be non-empty string")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence out of [0,1]: {self.confidence}")
        if not isinstance(self.sources, list):
            errors.append("sources must be list")
        if self.evidence_count < 1:
            errors.append(f"evidence_count must be >= 1: {self.evidence_count}")
        if self.version < 1:
            errors.append(f"version must be >= 1: {self.version}")
        if not isinstance(self.active, bool):
            errors.append("active must be bool")
        return errors

    def is_valid(self) -> bool:
        return len(self.validate()) == 0

    # ============================================================
    # 更新：合并新 evidence
    # ============================================================

    def reinforce(
        self,
        new_source: str = "",
        confidence_boost: float = 0.0,
    ) -> "SelfBelief":
        """
        强化现有 SelfBelief（不修改原对象，返回新对象）。

        - evidence_count +1
        - sources 追加（去重）
        - confidence: 0.9*old + 0.1*boost 后限幅到 [0, 1]
        - last_confirmed 更新
        - version +1
        """
        new_sources = list(self.sources)
        if new_source and new_source not in new_sources:
            new_sources.append(new_source)

        new_conf = min(1.0, 0.9 * self.confidence + 0.1 * max(0.0, confidence_boost))
        return SelfBelief(
            belief_id=self.belief_id,
            domain=self.domain,
            content=self.content,
            confidence=round(new_conf, 4),
            sources=new_sources,
            first_seen=self.first_seen,
            last_confirmed=_now_iso(),
            evidence_count=self.evidence_count + 1,
            version=self.version + 1,
        )

    # ============================================================
    # 序列化
    # ============================================================

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfBelief":
        return cls(
            belief_id=data.get("belief_id") or f"bel_{uuid.uuid4().hex[:10]}",
            domain=data.get("domain", "value"),
            content=data.get("content", ""),
            confidence=float(data.get("confidence", 0.3)),
            sources=list(data.get("sources") or []),
            first_seen=data.get("first_seen", _now_iso()),
            last_confirmed=data.get("last_confirmed", _now_iso()),
            evidence_count=int(data.get("evidence_count", 1)),
            version=int(data.get("version", 1)),
            active=bool(data.get("active", True)),
        )


# ============================================================
# SelfBelief 容器（轻量）
# ============================================================

class SelfBeliefStore:
    """
    SelfBelief 集合容器。

    Phase 6.1 轻量实现：
    - 按 belief_id 去重
    - 支持 reinforce（按 content+domain 命中同一 belief）
    - 支持 query / latest
    """

    def __init__(self) -> None:
        self._beliefs: Dict[str, SelfBelief] = {}

    def add(self, belief: SelfBelief) -> bool:
        """添加新 belief；已存在则按 content+domain 匹配并 reinforce"""
        if not belief.is_valid():
            return False
        # 去重：content + domain 命中已有 belief → reinforce
        for existing in self._beliefs.values():
            if existing.domain == belief.domain and existing.content == belief.content:
                merged = existing.reinforce(
                    new_source=(belief.sources[0] if belief.sources else ""),
                    confidence_boost=belief.confidence,
                )
                self._beliefs[existing.belief_id] = merged
                return True
        # 否则新增
        self._beliefs[belief.belief_id] = belief
        return True

    def get(self, belief_id: str) -> Optional[SelfBelief]:
        return self._beliefs.get(belief_id)

    def all(self) -> List[SelfBelief]:
        return list(self._beliefs.values())

    def count(self) -> int:
        return len(self._beliefs)

    def query(
        self,
        domain: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> List[SelfBelief]:
        out: List[SelfBelief] = []
        for b in self._beliefs.values():
            if domain and b.domain != domain:
                continue
            if b.confidence < min_confidence:
                continue
            out.append(b)
            if len(out) >= limit:
                break
        return out

    def latest(self, n: int = 10) -> List[SelfBelief]:
        items = sorted(self._beliefs.values(), key=lambda b: b.last_confirmed, reverse=True)
        return items[:n]

    def clear(self) -> None:
        self._beliefs.clear()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "count": len(self._beliefs),
            "beliefs": [b.to_dict() for b in self._beliefs.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SelfBeliefStore":
        store = cls()
        for b in (data.get("beliefs") or []):
            belief = SelfBelief.from_dict(b)
            if belief.is_valid():
                store._beliefs[belief.belief_id] = belief
        return store
