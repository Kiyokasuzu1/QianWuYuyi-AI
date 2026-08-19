"""
Phase 3.5.8: Self Model Schema

定义 Self Model 系统的契约数据结构。

不引入 LLM，仅做结构化骨架。
所有自我描述均有「来源 + 置信度」，避免自我认知幻觉。

来源于：
- GrowthRecord (成长累积)
- PersonalityVector / TraitState (当前人格参数)
- ReflectionInsight (反思结论)
- RelationshipState (关系状态)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. SelfIdentity: 结构化核心身份（非 LLM 描述）
# ============================================================

@dataclass
class CoreValue:
    """核心价值观条目"""
    value_id: str
    name: str
    weight: float = 0.7
    confidence: float = 0.7  # 来源可信度
    sources: List[str] = field(default_factory=list)  # 来源列表


@dataclass
class StableTrait:
    """稳定人格特质（从长期 TraitState 聚合而来）"""
    trait: str
    current_value: float
    direction: str = "stable"  # increase / stable / decrease
    stability: float = 0.3
    confidence: float = 0.3    # 多次验证后提高
    sources: List[str] = field(default_factory=list)
    last_updated: str = ""


@dataclass
class Preference:
    """偏好（来源于 GrowthRecord pattern）"""
    preference_id: str = field(default_factory=lambda: f"pref_{uuid.uuid4().hex[:8]}")
    domain: str = ""              # 如 "communication" / "topic" / "tone"
    key: str = ""                 # 偏好键，如 "message_tone"
    value: Any = None             # 偏好值，如 "温柔"
    evidence_count: int = 0       # 支撑证据数
    confidence: float = 0.3
    sources: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)


@dataclass
class BehavioralPattern:
    """行为模式（来源于 ReflectionInsight pattern_detected）"""
    pattern_id: str = field(default_factory=lambda: f"bp_{uuid.uuid4().hex[:8]}")
    pattern_detected: str = ""    # 如 "high_frequency_proactive"
    description: str = ""
    frequency: int = 0
    confidence: float = 0.3
    last_observed: str = ""
    sources: List[str] = field(default_factory=list)


@dataclass
class SelfContradiction:
    """自我认知矛盾（不消除，保留复杂性）"""
    contradiction_id: str = field(default_factory=lambda: f"sc_{uuid.uuid4().hex[:8]}")
    dimension_a: str = ""
    dimension_b: str = ""
    description: str = ""
    intensity: float = 0.0
    trait_values: Dict[str, float] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    first_seen: str = field(default_factory=now_iso)


@dataclass
class GrowthHistoryEntry:
    """成长历史条目（可审计）"""
    entry_id: str = field(default_factory=lambda: f"gh_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=now_iso)
    source_type: str = ""        # growth_record / reflection_insight / personality_change_request
    source_id: str = ""
    affected_traits: Dict[str, float] = field(default_factory=dict)  # trait → delta
    summary: str = ""
    evidence_count: int = 0
    confidence: float = 0.0


@dataclass
class DevelopmentHistoryItem:
    """发展历史条目：回答 what changed / why changed。"""

    item_id: str = field(default_factory=lambda: f"dh_{uuid.uuid4().hex[:8]}")
    timestamp: str = field(default_factory=now_iso)
    source_type: str = ""
    source_id: str = ""
    category: str = ""  # trait / value / pattern / relationship / reflection
    what_changed: str = ""
    why_changed: str = ""
    evidence_count: int = 0
    confidence: float = 0.0
    sources: List[str] = field(default_factory=list)


@dataclass
class IdentityUnderstanding:
    """结构化自我理解层，不直接修改人格。"""

    who_i_am: List[str] = field(default_factory=list)
    what_i_value: List[str] = field(default_factory=list)
    what_changed: List[str] = field(default_factory=list)
    why_changed: List[str] = field(default_factory=list)
    updated_at: str = field(default_factory=now_iso)


# ============================================================
# 2. SelfIdentity 顶层：结构化自我认知
# ============================================================

@dataclass
class SelfIdentity:
    """
    SelfIdentity —— 结构化自我模型（Phase 3.5.8 主数据结构）。

    不做自然语言生成，仅做结构化聚合。
    所有「自我理解」都有 sources + confidence，可审计、可追溯。
    """

    identity_id: str = field(default_factory=lambda: f"si_{uuid.uuid4().hex[:10]}")

    # --- 核心身份锚点 ---
    core_values: List[CoreValue] = field(default_factory=list)

    # --- 稳定特质（来自 TraitState 长期聚合） ---
    stable_traits: List[StableTrait] = field(default_factory=list)

    # --- 偏好（来自 GrowthRecord 模式） ---
    preferences: List[Preference] = field(default_factory=list)

    # --- 行为模式（来自 ReflectionInsight） ---
    behavioral_patterns: List[BehavioralPattern] = field(default_factory=list)

    # --- 矛盾（保留复杂性，不消除） ---
    contradictions: List[SelfContradiction] = field(default_factory=list)

    # --- 成长历史（可审计） ---
    growth_history: List[GrowthHistoryEntry] = field(default_factory=list)

    # --- 发展历史（显式回答 what changed / why changed） ---
    development_history: List[DevelopmentHistoryItem] = field(default_factory=list)

    # --- 身份理解层 ---
    identity_understanding: IdentityUnderstanding = field(default_factory=IdentityUnderstanding)

    # --- 自我理解水平（三维度结构化） ---
    experience_awareness: float = 0.0   # 经历理解深度 0~1
    trait_awareness: float = 0.0        # 人格理解深度 0~1
    identity_continuity: float = 0.0    # 身份连续性 0~1
    overall_understanding: float = 0.0  # 综合 0~1

    # --- 元数据 ---
    created_at: str = field(default_factory=now_iso)
    last_updated: str = field(default_factory=now_iso)
    version: int = 1

    # ============================================================
    # 辅助：导出/导入
    # ============================================================

    def to_snapshot(self) -> Dict[str, Any]:
        """生成 self_snapshot（结构化字典，可序列化）"""
        return {
            "identity_id": self.identity_id,
            "version": self.version,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "core_values_count": len(self.core_values),
            "stable_traits_count": len(self.stable_traits),
            "preferences_count": len(self.preferences),
            "behavioral_patterns_count": len(self.behavioral_patterns),
            "contradictions_count": len(self.contradictions),
            "growth_history_count": len(self.growth_history),
            "development_history_count": len(self.development_history),
            "understanding": {
                "experience_awareness": round(self.experience_awareness, 4),
                "trait_awareness": round(self.trait_awareness, 4),
                "identity_continuity": round(self.identity_continuity, 4),
                "overall": round(self.overall_understanding, 4),
            },
            "identity_understanding": {
                "who_i_am": list(self.identity_understanding.who_i_am[:6]),
                "what_i_value": list(self.identity_understanding.what_i_value[:6]),
                "what_changed": list(self.identity_understanding.what_changed[:6]),
                "why_changed": list(self.identity_understanding.why_changed[:6]),
            },
            "top_traits": [
                {"trait": s.trait, "value": s.current_value, "stability": s.stability}
                for s in sorted(
                    self.stable_traits,
                    key=lambda x: x.confidence * x.stability,
                    reverse=True,
                )[:6]
            ],
            "top_patterns": [
                {"pattern": p.pattern_detected, "frequency": p.frequency, "confidence": p.confidence}
                for p in sorted(
                    self.behavioral_patterns,
                    key=lambda x: x.confidence * x.frequency,
                    reverse=True,
                )[:5]
            ],
            "core_values_summary": [
                {"id": v.value_id, "name": v.name, "weight": v.weight}
                for v in sorted(self.core_values, key=lambda x: x.weight, reverse=True)[:5]
            ],
        }

    def to_dict(self) -> Dict[str, Any]:
        """完整导出（用于持久化）"""
        return {
            "identity_id": self.identity_id,
            "core_values": [asdict(v) for v in self.core_values],
            "stable_traits": [asdict(t) for t in self.stable_traits],
            "preferences": [asdict(p) for p in self.preferences],
            "behavioral_patterns": [asdict(b) for b in self.behavioral_patterns],
            "contradictions": [asdict(c) for c in self.contradictions],
            "growth_history": [asdict(g) for g in self.growth_history],
            "development_history": [asdict(d) for d in self.development_history],
            "identity_understanding": asdict(self.identity_understanding),
            "experience_awareness": self.experience_awareness,
            "trait_awareness": self.trait_awareness,
            "identity_continuity": self.identity_continuity,
            "overall_understanding": self.overall_understanding,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
            "version": self.version,
        }


# ============================================================
# 3. SelfModelChangeSuggestion: SelfModelUpdater 输出
# ============================================================

@dataclass
class SelfModelChangeSuggestion:
    """
    SelfModelUpdater 的输出：SelfModel 变化建议。

    不直接修改 SelfIdentity，只是描述「建议怎么变」。
    外部调用方（如 RuntimeCore）需显式调用 .apply() / accept() / reject()。
    """
    suggestion_id: str = field(default_factory=lambda: f"sug_{uuid.uuid4().hex[:10]}")
    source_type: str = ""                    # growth_record / reflection_insight / pcr
    source_id: str = ""
    timestamp: str = field(default_factory=now_iso)

    # --- 建议的变化（增量） ---
    add_core_values: List[CoreValue] = field(default_factory=list)
    update_core_value_weights: Dict[str, float] = field(default_factory=dict)  # value_id → weight_delta

    add_stable_traits: List[StableTrait] = field(default_factory=list)
    update_stable_traits: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # trait → {"delta":..., "confidence":...}

    add_preferences: List[Preference] = field(default_factory=list)
    add_patterns: List[BehavioralPattern] = field(default_factory=list)
    add_contradictions: List[SelfContradiction] = field(default_factory=list)
    add_history_entries: List[GrowthHistoryEntry] = field(default_factory=list)

    # --- 理解水平建议增量 ---
    understanding_delta: Dict[str, float] = field(default_factory=dict)  # experience_awareness/+x.x 等

    # --- 元数据 ---
    confidence: float = 0.0
    evidence_count: int = 0
    reasons: List[str] = field(default_factory=list)
    requires_approval: bool = True   # 默认需要审批（不自动应用）

    def summary(self) -> str:
        parts = []
        if self.add_core_values:
            parts.append(f"+{len(self.add_core_values)} core_values")
        if self.add_stable_traits:
            parts.append(f"+{len(self.add_stable_traits)} traits")
        if self.update_stable_traits:
            parts.append(f"~{len(self.update_stable_traits)} traits")
        if self.add_preferences:
            parts.append(f"+{len(self.add_preferences)} prefs")
        if self.add_patterns:
            parts.append(f"+{len(self.add_patterns)} patterns")
        if self.add_history_entries:
            parts.append(f"+{len(self.add_history_entries)} history")
        return ", ".join(parts) or "no changes"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "add_core_values_count": len(self.add_core_values),
            "add_stable_traits_count": len(self.add_stable_traits),
            "update_stable_traits_count": len(self.update_stable_traits),
            "add_preferences_count": len(self.add_preferences),
            "add_patterns_count": len(self.add_patterns),
            "add_contradictions_count": len(self.add_contradictions),
            "add_history_entries_count": len(self.add_history_entries),
            "confidence": round(self.confidence, 4),
            "evidence_count": self.evidence_count,
            "requires_approval": self.requires_approval,
            "summary": self.summary(),
            "reasons": self.reasons,
        }
