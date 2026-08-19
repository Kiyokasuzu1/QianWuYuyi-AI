"""
Phase 3.5.9: Identity Continuity Layer Schema

定义身份连续性检测所需的契约数据结构。

不引入 LLM，不做自然语言叙事。
所有判断均为结构化字段 + 数值指标，可审计、可追溯。

核心结构：
- IdentitySnapshot: 某一时刻的身份快照（从 SelfModel 派生）
- IdentityChange: 两个快照之间的变化项
- IdentityConflict: 变化中检测到的身份冲突
- ContinuityReport: 完整的连续性报告
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 1. IdentitySnapshot: 某一时刻的身份快照
# ============================================================

@dataclass
class TraitSnapshot:
    """快照中的单个特质状态"""
    trait: str
    value: float
    direction: str = "stable"    # increase / stable / decrease
    stability: float = 0.0
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CoreValueSnapshot:
    """快照中的核心价值观状态"""
    value_id: str
    name: str
    weight: float = 0.7
    confidence: float = 0.7

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdentitySnapshot:
    """
    身份快照 —— 从 SelfModelManager 的 snapshot 派生。

    设计原则：
    - 只包含「可比较」的核心字段，不包含历史细节
    - 结构化、可序列化、可 diff
    - 不包含自然语言描述
    """
    snapshot_id: str = field(default_factory=lambda: f"isnap_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # --- 核心字段 ---
    identity_id: str = ""
    version: int = 1

    # --- 特质摘要 ---
    traits: List[TraitSnapshot] = field(default_factory=list)

    # --- 核心价值观摘要 ---
    core_values: List[CoreValueSnapshot] = field(default_factory=list)

    # --- 理解水平 ---
    experience_awareness: float = 0.0
    trait_awareness: float = 0.0
    identity_continuity: float = 0.0
    overall_understanding: float = 0.0

    # --- 统计 ---
    growth_history_count: int = 0
    preferences_count: int = 0
    behavioral_patterns_count: int = 0
    contradictions_count: int = 0

    # --- 来源标记 ---
    source: str = ""  # "self_model_manager" / "external" / "restored"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "identity_id": self.identity_id,
            "version": self.version,
            "traits": [t.to_dict() for t in self.traits],
            "core_values": [v.to_dict() for v in self.core_values],
            "understanding": {
                "experience_awareness": round(self.experience_awareness, 4),
                "trait_awareness": round(self.trait_awareness, 4),
                "identity_continuity": round(self.identity_continuity, 4),
                "overall": round(self.overall_understanding, 4),
            },
            "growth_history_count": self.growth_history_count,
            "preferences_count": self.preferences_count,
            "behavioral_patterns_count": self.behavioral_patterns_count,
            "contradictions_count": self.contradictions_count,
            "source": self.source,
        }

    @classmethod
    def from_self_model_snapshot(cls, snap: Dict[str, Any], identity_id: str = "") -> "IdentitySnapshot":
        """从 SelfModelManager.snapshot() 返回的 dict 派生 IdentitySnapshot"""
        traits = [
            TraitSnapshot(
                trait=t.get("trait", ""),
                value=float(t.get("value", 0.0)),
                direction=t.get("direction", "stable"),
                stability=float(t.get("stability", 0.0)),
                confidence=float(t.get("confidence", 0.0)),
            )
            for t in (snap.get("top_traits") or [])
        ]
        cvs = [
            CoreValueSnapshot(
                value_id=v.get("id", ""),
                name=v.get("name", ""),
                weight=float(v.get("weight", 0.7)),
                confidence=float(v.get("confidence", 0.7)),
            )
            for v in (snap.get("core_values_summary") or [])
        ]
        understanding = snap.get("understanding", {}) or {}
        return cls(
            identity_id=identity_id or snap.get("identity_id", ""),
            version=int(snap.get("version", 1)),
            traits=traits,
            core_values=cvs,
            experience_awareness=float(understanding.get("experience_awareness", 0.0)),
            trait_awareness=float(understanding.get("trait_awareness", 0.0)),
            identity_continuity=float(understanding.get("identity_continuity", 0.0)),
            overall_understanding=float(understanding.get("overall", 0.0)),
            growth_history_count=int(snap.get("growth_history_count", 0)),
            preferences_count=int(snap.get("preferences_count", 0)),
            behavioral_patterns_count=int(snap.get("behavioral_patterns_count", 0)),
            contradictions_count=int(snap.get("contradictions_count", 0)),
            source="self_model_manager",
        )


# ============================================================
# 2. IdentityChange: 两个快照之间的变化项
# ============================================================

@dataclass
class TraitChange:
    """单个特质的变化"""
    trait: str
    old_value: float
    new_value: float
    delta: float = 0.0          # new - old
    relative_delta: float = 0.0  # (new - old) / max(old, 0.01)
    direction: str = "stable"   # increase / stable / decrease
    magnitude: str = "negligible"  # negligible / minor / moderate / major

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CoreValueChange:
    """核心价值观权重变化"""
    value_id: str
    name: str = ""
    old_weight: float = 0.0
    new_weight: float = 0.0
    delta: float = 0.0
    magnitude: str = "negligible"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IdentityChange:
    """
    两个 IdentitySnapshot 之间的完整变化集。
    """
    changes: List[TraitChange] = field(default_factory=list)
    core_value_changes: List[CoreValueChange] = field(default_factory=list)

    # 新增 / 消失
    added_traits: List[str] = field(default_factory=list)
    removed_traits: List[str] = field(default_factory=list)
    added_core_values: List[str] = field(default_factory=list)
    removed_core_values: List[str] = field(default_factory=list)

    # 统计变化
    growth_history_delta: int = 0
    preferences_delta: int = 0
    patterns_delta: int = 0
    contradictions_delta: int = 0

    # 理解水平变化
    understanding_delta: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trait_changes": [c.to_dict() for c in self.changes],
            "core_value_changes": [c.to_dict() for c in self.core_value_changes],
            "added_traits": self.added_traits,
            "removed_traits": self.removed_traits,
            "added_core_values": self.added_core_values,
            "removed_core_values": self.removed_core_values,
            "growth_history_delta": self.growth_history_delta,
            "preferences_delta": self.preferences_delta,
            "patterns_delta": self.patterns_delta,
            "contradictions_delta": self.contradictions_delta,
            "understanding_delta": dict(self.understanding_delta),
        }

    def max_trait_delta(self) -> float:
        """返回最大特质变化绝对值"""
        if not self.changes:
            return 0.0
        return max(abs(c.delta) for c in self.changes)

    def max_cv_delta(self) -> float:
        """返回最大核心价值观权重变化绝对值"""
        if not self.core_value_changes:
            return 0.0
        return max(abs(c.delta) for c in self.core_value_changes)


# ============================================================
# 3. IdentityConflict: 身份冲突
# ============================================================

@dataclass
class IdentityConflict:
    """
    身份冲突记录。

    类型：
    - trait_reversal: 特质方向反转（如 warmth 从高变低）
    - value_drop: 核心价值观权重大幅下降
    - identity_break: 特质集合剧烈变化（多个特质同时大幅变化）
    - understanding_regression: 理解水平倒退
    """
    conflict_id: str = field(default_factory=lambda: f"ic_{uuid.uuid4().hex[:8]}")
    conflict_type: str = ""   # trait_reversal / value_drop / identity_break / understanding_regression
    severity: str = "low"     # low / medium / high / critical
    description: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    detected_at: str = field(default_factory=now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 4. ContinuityReport: 完整的连续性报告
# ============================================================

@dataclass
class ContinuityReport:
    """
    身份连续性报告。

    核心指标：
    - continuity_score (0~1): 1 = 完全连续，0 = 完全断裂
    - is_continuous: continuity_score >= threshold
    - changes: 详细变化项
    - conflicts: 检测到的冲突列表
    """
    report_id: str = field(default_factory=lambda: f"cr_{uuid.uuid4().hex[:10]}")
    timestamp: str = field(default_factory=now_iso)

    # --- 比较的两个快照 ---
    before_snapshot_id: str = ""
    after_snapshot_id: str = ""
    before_timestamp: str = ""
    after_timestamp: str = ""
    before_version: int = 0
    after_version: int = 0

    # --- 核心指标 ---
    continuity_score: float = 1.0   # 0~1
    is_continuous: bool = True       # score >= threshold
    threshold: float = 0.7           # 默认阈值

    # --- 变化详情 ---
    changes: IdentityChange = field(default_factory=IdentityChange)
    conflicts: List[IdentityConflict] = field(default_factory=list)

    # --- 分项评分 ---
    trait_stability_score: float = 1.0      # 特质稳定性
    core_value_stability_score: float = 1.0 # 核心价值观稳定性
    understanding_progress_score: float = 1.0  # 理解水平进步
    structural_integrity_score: float = 1.0   # 结构完整性（无新增/消失）

    # --- 元数据 ---
    time_gap_seconds: float = 0.0  # 两个快照间的时间间隔
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "before_snapshot_id": self.before_snapshot_id,
            "after_snapshot_id": self.after_snapshot_id,
            "before_timestamp": self.before_timestamp,
            "after_timestamp": self.after_timestamp,
            "before_version": self.before_version,
            "after_version": self.after_version,
            "continuity_score": round(self.continuity_score, 4),
            "is_continuous": self.is_continuous,
            "threshold": self.threshold,
            "changes": self.changes.to_dict(),
            "conflicts": [c.to_dict() for c in self.conflicts],
            "scores": {
                "trait_stability": round(self.trait_stability_score, 4),
                "core_value_stability": round(self.core_value_stability_score, 4),
                "understanding_progress": round(self.understanding_progress_score, 4),
                "structural_integrity": round(self.structural_integrity_score, 4),
            },
            "time_gap_seconds": round(self.time_gap_seconds, 2),
            "notes": self.notes,
        }

    def summary(self) -> str:
        n_conflicts = len(self.conflicts)
        n_changes = len(self.changes.changes) + len(self.changes.core_value_changes)
        status = "CONTINUOUS" if self.is_continuous else "BROKEN"
        return (
            f"[{status}] score={self.continuity_score:.3f} "
            f"changes={n_changes} conflicts={n_conflicts} "
            f"threshold={self.threshold}"
        )
