"""
Phase 3.5.17: Identity Continuity Engine (Stability Report Layer)

现有工程已经具备：
- IdentityContinuityChecker + ContinuityReport（Phase 3.5.9）
- IdentityAnchorManager + AnchorIntegrityReport（Phase 3.5.10）

本模块补齐“统一稳定性报告”：
- 合并连续性与锚点完整性信号
- 增加记忆污染检测（不删除，只报告）
- 输出 IdentityStabilityReport，供 Runtime / 管理后台审计

约束：
- 不自动修复，不自动应用 proposal
- 不修改 Persona / TraitState
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.contracts.identity_stability_schema import (
    IdentityStabilityReport,
    IdentityStabilitySnapshot,
    MemoryPollutionSummary,
    StabilityIssue,
)


@dataclass
class IdentityStabilityEngineConfig:
    stability_threshold: float = 0.75
    history_limit: int = 200

    # penalty weights
    penalty_continuity: float = 0.70
    penalty_anchor: float = 0.30
    penalty_memory_pollution: float = 0.35


class IdentityStabilityHistory:
    """保存稳定性报告历史，支持审计。"""

    def __init__(self, limit: int = 200):
        self._reports: List[IdentityStabilityReport] = []
        self._limit = limit

    def add_report(self, report: IdentityStabilityReport) -> None:
        self._reports.append(report)
        if len(self._reports) > self._limit:
            self._reports = self._reports[-self._limit :]

    def get_latest_report(self) -> Optional[IdentityStabilityReport]:
        return self._reports[-1] if self._reports else None

    def get_reports(self, limit: int = 50) -> List[IdentityStabilityReport]:
        items = list(self._reports)
        items.reverse()
        return items[:limit]

    def get_snapshot(self) -> IdentityStabilitySnapshot:
        last = self.get_latest_report()
        unstable = [r for r in self._reports if not r.is_stable]
        critical = 0
        for r in self._reports:
            critical += sum(1 for i in r.issues if i.severity == "critical")
        return IdentityStabilitySnapshot(
            total_reports=len(self._reports),
            unstable_reports=len(unstable),
            critical_issues=critical,
            last_report_id=last.report_id if last else "",
            last_is_stable=last.is_stable if last else True,
        )


class IdentityStabilityEngine:
    """统一身份稳定性评估器。"""

    def __init__(self, config: Optional[IdentityStabilityEngineConfig] = None):
        self.config = config or IdentityStabilityEngineConfig()
        self.history = IdentityStabilityHistory(limit=self.config.history_limit)

    def generate_report(
        self,
        *,
        identity_id: str,
        continuity_report: Optional[Dict[str, Any]] = None,
        anchor_integrity_report: Optional[Dict[str, Any]] = None,
        memories: Optional[List[Dict[str, Any]]] = None,
    ) -> IdentityStabilityReport:
        continuity_report = dict(continuity_report or {})
        anchor_integrity_report = dict(anchor_integrity_report or {})
        memories = list(memories or [])

        issues: List[StabilityIssue] = []
        notes: List[str] = []

        continuity_score = float(continuity_report.get("continuity_score", 1.0) or 1.0)
        is_continuous = bool(continuity_report.get("is_continuous", True))
        before_snapshot_id = str(continuity_report.get("before_snapshot_id", "") or "")
        after_snapshot_id = str(continuity_report.get("after_snapshot_id", "") or "")

        anchor_intact = bool(anchor_integrity_report.get("is_intact", True))

        # 1) continuity-based issues
        conflicts = continuity_report.get("conflicts", []) or []
        if not is_continuous:
            issues.append(StabilityIssue(
                issue_type="continuity_break",
                severity="high" if continuity_score >= 0.55 else "critical",
                description="身份连续性低于阈值",
                details={
                    "continuity_score": continuity_score,
                    "threshold": continuity_report.get("threshold"),
                    "conflicts_count": len(conflicts),
                },
            ))

        for c in conflicts:
            ctype = c.get("conflict_type", "")
            sev = c.get("severity", "low")
            if ctype == "value_drop":
                issues.append(StabilityIssue(
                    issue_type="value_conflict",
                    severity=sev,
                    description="核心价值观权重出现异常下降",
                    details=dict(c),
                ))
            elif ctype in {"trait_reversal", "identity_break"}:
                issues.append(StabilityIssue(
                    issue_type="personality_mutation",
                    severity=sev,
                    description="人格特质出现反转或剧烈变化",
                    details=dict(c),
                ))
            elif ctype == "understanding_regression":
                issues.append(StabilityIssue(
                    issue_type="wrong_growth",
                    severity=sev,
                    description="理解水平出现倒退",
                    details=dict(c),
                ))

        # 2) anchor integrity issues
        if not anchor_intact:
            deviations = anchor_integrity_report.get("deviations", []) or []
            violations = anchor_integrity_report.get("constraint_violations", []) or []
            sev = "high" if violations else "medium"
            issues.append(StabilityIssue(
                issue_type="anchor_deviation",
                severity=sev,
                description="身份锚点完整性不通过",
                details={
                    "deviations_count": len(deviations),
                    "violations_count": len(violations),
                    "risk_level": anchor_integrity_report.get("risk_level", ""),
                },
            ))

        # 3) memory pollution detection
        mp = self._detect_memory_pollution(memories)
        if mp.suspicious_count > 0:
            severe_reasons = {
                "user_statement_used_for_growth_or_persona",
                "assistant_output_used_for_growth_or_persona",
                "unknown_truth_too_high",
            }
            severity = "high" if any(r in severe_reasons for r in mp.reasons) else "medium"
            issues.append(StabilityIssue(
                issue_type="memory_pollution",
                severity=severity,
                description="检测到疑似记忆污染信号",
                details=mp.to_dict(),
            ))

        # 4) stability score
        stability_score = self._score(
            continuity_score=continuity_score,
            anchor_intact=anchor_intact,
            memory_pollution=mp,
            issues=issues,
        )
        has_blocking_pollution = any(
            i.issue_type == "memory_pollution" and i.severity in {"high", "critical"}
            for i in issues
        )
        is_stable = (
            stability_score >= self.config.stability_threshold
            and not any(i.severity == "critical" for i in issues)
            and not has_blocking_pollution
        )

        if not is_stable:
            notes.append("identity_stability_failed")
        if mp.suspicious_count > 0:
            notes.append(f"memory_pollution_suspicious={mp.suspicious_count}")

        report = IdentityStabilityReport(
            identity_id=identity_id,
            before_snapshot_id=before_snapshot_id,
            after_snapshot_id=after_snapshot_id,
            continuity_score=round(continuity_score, 4),
            anchor_intact=anchor_intact,
            stability_score=round(stability_score, 4),
            stability_threshold=self.config.stability_threshold,
            is_stable=is_stable,
            continuity_report=continuity_report,
            anchor_integrity_report=anchor_integrity_report,
            memory_pollution=mp,
            issues=issues,
            notes=notes,
        )
        self.history.add_report(report)
        return report

    def _score(
        self,
        *,
        continuity_score: float,
        anchor_intact: bool,
        memory_pollution: MemoryPollutionSummary,
        issues: List[StabilityIssue],
    ) -> float:
        score = 1.0
        # continuity penalty (only when not perfect)
        score -= self.config.penalty_continuity * max(0.0, 1.0 - continuity_score)
        # anchor penalty
        if not anchor_intact:
            score -= self.config.penalty_anchor * 0.6
        # memory pollution penalty
        if memory_pollution.suspicious_count > 0:
            level = min(1.0, memory_pollution.suspicious_count / 5.0)
            score -= self.config.penalty_memory_pollution * level
        # critical issues extra penalty
        if any(i.severity == "critical" for i in issues):
            score -= 0.25
        return max(0.0, min(1.0, score))

    def _detect_memory_pollution(self, memories: List[Dict[str, Any]]) -> MemoryPollutionSummary:
        suspicious_ids: List[str] = []
        reasons: List[str] = []

        for m in memories:
            mid = str(m.get("id", "") or "")
            role = str(m.get("role", "") or "")
            mc = str(m.get("memory_class", "") or m.get("metadata", {}).get("memory_class", "") or "").strip().lower()
            usage = m.get("usage", []) or []
            truth = float(m.get("truth", m.get("metadata", {}).get("truth", 0.5)) or 0.5)

            # 1) assistant output should never carry truth > 0
            if role == "assistant" and truth > 0:
                suspicious_ids.append(mid)
                reasons.append("assistant_truth_gt_0")

            # 2) user_statement must not be used for growth/persona
            if mc == "user_statement" and any(u in {"growth", "persona"} for u in usage):
                suspicious_ids.append(mid)
                reasons.append("user_statement_used_for_growth_or_persona")

            # 3) assistant_output must not be used for growth/persona
            if mc == "assistant_output" and any(u in {"growth", "persona"} for u in usage):
                suspicious_ids.append(mid)
                reasons.append("assistant_output_used_for_growth_or_persona")

            # 4) extremely high truth but unknown class is suspicious
            if mc in {"unknown", ""} and truth >= 0.95:
                suspicious_ids.append(mid)
                reasons.append("unknown_truth_too_high")

        # dedupe
        seen = set()
        dedup_ids: List[str] = []
        for x in suspicious_ids:
            if x in seen:
                continue
            seen.add(x)
            dedup_ids.append(x)

        # dedupe reasons
        reasons_out: List[str] = []
        seen_r = set()
        for r in reasons:
            if r in seen_r:
                continue
            seen_r.add(r)
            reasons_out.append(r)

        return MemoryPollutionSummary(
            suspicious_count=len(dedup_ids),
            suspicious_memory_ids=dedup_ids[:20],
            reasons=reasons_out,
        )
