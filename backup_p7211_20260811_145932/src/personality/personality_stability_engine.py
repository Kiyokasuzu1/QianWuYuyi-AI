"""
Phase 3.5.26: Personality Stability Engine
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.contracts.personality_stability_schema import (
    PersonalityStabilityIssue,
    PersonalityStabilityReport,
)


@dataclass
class PersonalityStabilityConfig:
    stability_threshold: float = 0.72
    min_core_value_weight: float = 0.45
    max_contradictions: int = 8
    max_trait_delta: float = 0.18


class PersonalityStabilityEngine:
    def __init__(self, config: Optional[PersonalityStabilityConfig] = None):
        self.config = config or PersonalityStabilityConfig()
        self._history: List[PersonalityStabilityReport] = []

    def generate_report(
        self,
        *,
        identity_id: str,
        self_model_full: Optional[Dict[str, Any]] = None,
        self_model_snapshot: Optional[Dict[str, Any]] = None,
        trait_states: Optional[Dict[str, Any]] = None,
        evolution_history: Optional[List[Dict[str, Any]]] = None,
        identity_stability_report: Optional[Dict[str, Any]] = None,
    ) -> PersonalityStabilityReport:
        self_model_full = dict(self_model_full or {})
        self_model_snapshot = dict(self_model_snapshot or {})
        trait_states = dict(trait_states or {})
        evolution_history = list(evolution_history or [])
        identity_stability_report = dict(identity_stability_report or {})

        issues: List[PersonalityStabilityIssue] = []
        drift_score = 0.0

        core_values = self_model_full.get("core_values", []) or []
        weak_values = [v for v in core_values if float(v.get("weight", 0.0) or 0.0) < self.config.min_core_value_weight]
        if weak_values:
            issues.append(PersonalityStabilityIssue(
                issue_type="core_value_drift",
                severity="high",
                description="存在核心价值观权重过低",
                details={"weak_values": weak_values[:10]},
            ))
            drift_score += 0.28

        contradictions = self_model_full.get("contradictions", []) or []
        contradiction_count = len(contradictions)
        if contradiction_count > self.config.max_contradictions:
            issues.append(PersonalityStabilityIssue(
                issue_type="contradiction_risk",
                severity="medium" if contradiction_count <= self.config.max_contradictions + 4 else "high",
                description="自我矛盾数量过多",
                details={"contradiction_count": contradiction_count},
            ))
            drift_score += min(0.25, 0.02 * contradiction_count)

        latest_evo = evolution_history[-3:]
        trait_deltas = []
        for rec in latest_evo:
            before = rec.get("trait_states_before", {}) or {}
            after = rec.get("trait_states_after", {}) or {}
            for k, bv in before.items():
                av = after.get(k)
                if isinstance(bv, dict):
                    bv = bv.get("current_value", 0.0)
                if isinstance(av, dict):
                    av = av.get("current_value", 0.0)
                try:
                    delta = abs(float(av) - float(bv))
                    trait_deltas.append((k, delta))
                except Exception:
                    pass
        drifted = [{"trait": k, "delta": round(d, 4)} for k, d in trait_deltas if d > self.config.max_trait_delta]
        if drifted:
            issues.append(PersonalityStabilityIssue(
                issue_type="trait_drift",
                severity="high",
                description="近期特质变化幅度过大",
                details={"drifted_traits": drifted[:20]},
            ))
            drift_score += min(0.3, 0.08 * len(drifted))

        if identity_stability_report and not bool(identity_stability_report.get("is_stable", True)):
            issues.append(PersonalityStabilityIssue(
                issue_type="identity_instability",
                severity="high",
                description="身份稳定性未通过",
                details={
                    "report_id": identity_stability_report.get("report_id", ""),
                    "stability_score": identity_stability_report.get("stability_score", 0.0),
                },
            ))
            drift_score += 0.35

        stability_score = max(0.0, min(1.0, 1.0 - drift_score))
        is_stable = stability_score >= self.config.stability_threshold and not any(i.severity == "high" for i in issues)

        report = PersonalityStabilityReport(
            identity_id=identity_id,
            drift_score=round(drift_score, 4),
            stability_score=round(stability_score, 4),
            is_stable=is_stable,
            issues=[i.to_dict() for i in issues],
            core_value_summary={
                "total": len(core_values),
                "weak_count": len(weak_values),
            },
            contradiction_summary={
                "count": contradiction_count,
                "threshold": self.config.max_contradictions,
            },
            trait_summary={
                "tracked_traits": len(trait_states),
                "drifted_count": len(drifted),
            },
            identity_stability_link={
                "report_id": identity_stability_report.get("report_id", ""),
                "is_stable": identity_stability_report.get("is_stable", True),
            },
        )
        self._history.append(report)
        self._history = self._history[-200:]
        return report

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]
