"""
Phase 3.5.13: Reflection → Growth Integration Layer

职责：
- 接收 ReflectionInsight、ReflectionEvaluation 与 Identity 状态
- 生成 GrowthProposal candidates
- 保留完整证据链和桥接层审计历史

约束：
- 不自动应用任何 proposal
- 不绕过审批流
- 不隐藏状态变更
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.memory.atomic_write import atomic_write_json, backup_corrupt_file, get_path_lock
from src.contracts.reflection_growth_schema import (
    BRIDGE_DECISION_CREATED,
    BRIDGE_DECISION_REJECTED,
    BRIDGE_DECISION_REUSED,
    BRIDGE_DECISION_SKIPPED,
    EvidenceLink,
    ReflectionGrowthRecord,
    ReflectionGrowthSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class ReflectionGrowthBridgeConfig:
    """桥接层策略配置。"""

    create_on_accept: bool = True
    create_on_defer: bool = True
    create_on_revisit: bool = False
    reject_on_identity_failure: bool = True
    history_limit: int = 200


class ReflectionGrowthBridge:
    """
    反思到成长的桥接器。

    依赖：
    - growth_adapter.create_proposal_from_insight()
    - growth_adapter.update_proposal()
    """

    def __init__(
        self,
        growth_adapter: Any,
        *,
        config: Optional[ReflectionGrowthBridgeConfig] = None,
        history_path: Optional[str] = None,
    ):
        self._growth_adapter = growth_adapter
        self.config = config or ReflectionGrowthBridgeConfig()
        self._history: List[ReflectionGrowthRecord] = []
        self._history_path = Path(history_path) if history_path else None
        if self._history_path:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            self._history = self._load_history()

        self._total_created = 0
        self._total_rejected = 0
        self._total_reused = 0
        self._total_skipped = 0

        for item in self._history:
            self._increment_counter(item.decision)

    def process(
        self,
        insight: Any,
        evaluation: Any,
        identity_status: Optional[Dict[str, Any]] = None,
    ) -> ReflectionGrowthRecord:
        """执行一次 Reflection → Growth 桥接。"""
        if insight is None:
            return self._record_rejected(
                insight_id="",
                evaluation_id=self._get_field(evaluation, "evaluation_id", ""),
                source_summary="missing insight",
                decision_reason="missing insight",
                identity_status=identity_status or {},
                risk_flags=list(self._get_field(evaluation, "risk_flags", []) or []),
                evidence_chain=[],
            )

        if evaluation is None:
            return self._record_rejected(
                insight_id=self._get_field(insight, "insight_id", ""),
                evaluation_id="",
                source_summary=self._get_field(insight, "summary", ""),
                decision_reason="missing reflection evaluation",
                identity_status=identity_status or {},
                risk_flags=[],
                evidence_chain=[],
            )

        insight_id = self._get_field(insight, "insight_id", "")
        evaluation_id = self._get_field(evaluation, "evaluation_id", "")
        source_summary = self._get_field(insight, "summary", "")
        risk_flags = list(self._get_field(evaluation, "risk_flags", []) or [])
        normalized_identity = self._normalize_identity_status(identity_status or {})
        evidence_chain = self._build_evidence_chain(insight, evaluation, normalized_identity)

        reusable = self._find_existing_proposals(insight_id)
        if reusable:
            record = ReflectionGrowthRecord(
                insight_id=insight_id,
                evaluation_id=evaluation_id,
                source_summary=source_summary,
                decision=BRIDGE_DECISION_REUSED,
                decision_reason="existing proposal candidates reused",
                proposal_ids=[p.id for p in reusable],
                risk_flags=risk_flags,
                identity_status=normalized_identity,
                evidence_chain=evidence_chain,
                metadata={
                    "recommendation": self._get_field(evaluation, "recommendation", ""),
                    "proposal_count": len(reusable),
                },
            )
            self._push_record(record)
            return record

        should_create, reason = self._should_create_proposal(
            evaluation=evaluation,
            identity_status=normalized_identity,
        )
        if not should_create:
            return self._record_rejected(
                insight_id=insight_id,
                evaluation_id=evaluation_id,
                source_summary=source_summary,
                decision_reason=reason,
                identity_status=normalized_identity,
                risk_flags=risk_flags,
                evidence_chain=evidence_chain,
                metadata={
                    "recommendation": self._get_field(evaluation, "recommendation", ""),
                    "overall_score": self._get_field(evaluation, "overall_score", 0.0),
                },
            )

        proposal = self._growth_adapter.create_proposal_from_insight(insight)
        proposal.evaluator_meta = dict(getattr(proposal, "evaluator_meta", {}) or {})
        proposal.evaluator_meta.update({
            "reflection_evaluation_id": evaluation_id,
            "reflection_recommendation": self._get_field(evaluation, "recommendation", ""),
            "reflection_overall_score": self._get_field(evaluation, "overall_score", 0.0),
            "reflection_risk_flags": risk_flags,
            "identity_status": normalized_identity,
            "bridge_source": "reflection_growth_bridge",
        })
        self._growth_adapter.update_proposal(proposal)

        record = ReflectionGrowthRecord(
            insight_id=insight_id,
            evaluation_id=evaluation_id,
            source_summary=source_summary,
            decision=BRIDGE_DECISION_CREATED,
            decision_reason=reason,
            proposal_ids=[proposal.id],
            risk_flags=risk_flags,
            identity_status=normalized_identity,
            evidence_chain=evidence_chain,
            metadata={
                "recommendation": self._get_field(evaluation, "recommendation", ""),
                "overall_score": self._get_field(evaluation, "overall_score", 0.0),
                "proposal_confidence": getattr(proposal, "confidence", 0.0),
            },
        )
        self._push_record(record)
        return record

    def get_history(self, limit: int = 50) -> List[ReflectionGrowthRecord]:
        hist = list(self._history)
        hist.reverse()
        return hist[:limit]

    def get_latest_record(self) -> Optional[ReflectionGrowthRecord]:
        if not self._history:
            return None
        return self._history[-1]

    def get_snapshot(self) -> ReflectionGrowthSnapshot:
        latest = self.get_latest_record()
        return ReflectionGrowthSnapshot(
            enabled=True,
            total_records=len(self._history),
            total_created=self._total_created,
            total_rejected=self._total_rejected,
            total_reused=self._total_reused,
            total_skipped=self._total_skipped,
            last_record_id=latest.record_id if latest else "",
            last_decision=latest.decision if latest else "",
            last_insight_id=latest.insight_id if latest else "",
        )

    def clear_history(self) -> int:
        n = len(self._history)
        self._history.clear()
        self._total_created = 0
        self._total_rejected = 0
        self._total_reused = 0
        self._total_skipped = 0
        self._persist_history()
        return n

    def _should_create_proposal(
        self,
        *,
        evaluation: Any,
        identity_status: Dict[str, Any],
    ) -> tuple[bool, str]:
        recommendation = str(self._get_field(evaluation, "recommendation", "") or "")
        if self.config.reject_on_identity_failure and not identity_status.get("identity_ok", True):
            return False, f"identity gate rejected: {identity_status.get('reason', 'unknown')}"

        if recommendation == "accept" and self.config.create_on_accept:
            return True, "evaluation accepted for proposal generation"
        if recommendation == "defer" and self.config.create_on_defer:
            return True, "evaluation deferred but preserved as low-confidence candidate"
        if recommendation == "revisit" and self.config.create_on_revisit:
            return True, "evaluation revisit preserved for manual review"
        if recommendation == "reject":
            return False, "evaluation rejected proposal generation"
        if recommendation == "revisit":
            return False, "evaluation requires revisit before proposal generation"
        return False, f"unsupported evaluation recommendation: {recommendation or 'empty'}"

    def _normalize_identity_status(self, identity_status: Dict[str, Any]) -> Dict[str, Any]:
        if not identity_status:
            return {
                "identity_ok": True,
                "continuity_ok": True,
                "anchor_ok": True,
                "reason": "identity status unavailable, treated as neutral",
            }

        continuity_ok = True
        anchor_ok = True
        notes: List[str] = []

        continuity = identity_status.get("continuity_report") or {}
        if continuity:
            continuity_ok = bool(continuity.get("is_continuous", True))
            if not continuity_ok:
                notes.append("continuity_report indicates non-continuous identity")

        anchor = identity_status.get("anchor_integrity") or {}
        if anchor:
            anchor_ok = bool(anchor.get("is_intact", True))
            if not anchor_ok:
                notes.append("anchor_integrity indicates identity deviation")

        if identity_status.get("identity_ok") is not None:
            identity_ok = bool(identity_status.get("identity_ok"))
        else:
            identity_ok = continuity_ok and anchor_ok

        reason = identity_status.get("reason") or ("; ".join(notes) if notes else "identity checks passed")
        normalized = dict(identity_status)
        normalized.update({
            "identity_ok": identity_ok,
            "continuity_ok": continuity_ok,
            "anchor_ok": anchor_ok,
            "reason": reason,
        })
        return normalized

    def _build_evidence_chain(
        self,
        insight: Any,
        evaluation: Any,
        identity_status: Dict[str, Any],
    ) -> List[EvidenceLink]:
        experience_ids = list(self._get_field(insight, "experience_ids", []) or [])
        evidence: List[EvidenceLink] = [
            EvidenceLink(
                evidence_type="reflection_insight",
                evidence_id=self._get_field(insight, "insight_id", ""),
                summary=self._get_field(insight, "summary", ""),
                metadata={
                    "pattern_detected": self._get_field(insight, "pattern_detected", ""),
                    "pattern_frequency": self._get_field(insight, "pattern_frequency", 0),
                    "experience_ids": experience_ids,
                },
            ),
            EvidenceLink(
                evidence_type="reflection_evaluation",
                evidence_id=self._get_field(evaluation, "evaluation_id", ""),
                summary=self._get_field(evaluation, "recommendation_reason", ""),
                metadata={
                    "recommendation": self._get_field(evaluation, "recommendation", ""),
                    "overall_score": self._get_field(evaluation, "overall_score", 0.0),
                    "risk_flags": list(self._get_field(evaluation, "risk_flags", []) or []),
                },
            ),
        ]

        if experience_ids:
            evidence.append(
                EvidenceLink(
                    evidence_type="experience_batch",
                    evidence_id="|".join(experience_ids[:5]),
                    summary=f"{len(experience_ids)} linked experiences",
                    metadata={"experience_count": len(experience_ids)},
                )
            )

        evidence.append(
            EvidenceLink(
                evidence_type="identity_status",
                evidence_id=identity_status.get("report_id", "") or identity_status.get("anchor_snapshot_id", ""),
                summary=identity_status.get("reason", ""),
                metadata=dict(identity_status),
            )
        )
        return evidence

    def _find_existing_proposals(self, insight_id: str) -> List[Any]:
        if not insight_id or not hasattr(self._growth_adapter, "list_proposals"):
            return []
        proposals = self._growth_adapter.list_proposals(limit=1000) or []
        return [p for p in proposals if getattr(p, "source_event_id", None) == insight_id]

    def _record_rejected(
        self,
        *,
        insight_id: str,
        evaluation_id: str,
        source_summary: str,
        decision_reason: str,
        identity_status: Dict[str, Any],
        risk_flags: List[str],
        evidence_chain: List[EvidenceLink],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ReflectionGrowthRecord:
        record = ReflectionGrowthRecord(
            insight_id=insight_id,
            evaluation_id=evaluation_id,
            source_summary=source_summary,
            decision=BRIDGE_DECISION_REJECTED,
            decision_reason=decision_reason,
            proposal_ids=[],
            risk_flags=list(risk_flags),
            identity_status=dict(identity_status),
            evidence_chain=list(evidence_chain),
            metadata=metadata or {},
        )
        self._push_record(record)
        return record

    def _push_record(self, record: ReflectionGrowthRecord) -> None:
        self._history.append(record)
        if len(self._history) > self.config.history_limit:
            self._history = self._history[-self.config.history_limit :]
        self._increment_counter(record.decision)
        self._persist_history()

    def _increment_counter(self, decision: str) -> None:
        if decision == BRIDGE_DECISION_CREATED:
            self._total_created += 1
        elif decision == BRIDGE_DECISION_REJECTED:
            self._total_rejected += 1
        elif decision == BRIDGE_DECISION_REUSED:
            self._total_reused += 1
        else:
            self._total_skipped += 1

    def _persist_history(self) -> None:
        if not self._history_path:
            return
        try:
            # V1.1: 锁 + 原子写（替换裸 open("w") 截断写）
            with get_path_lock(str(self._history_path)):
                atomic_write_json(
                    str(self._history_path),
                    [item.to_dict() for item in self._history],
                )
        except Exception as e:
            logger.error(f"保存 ReflectionGrowthBridge 历史失败: {e}")

    def _load_history(self) -> List[ReflectionGrowthRecord]:
        if not self._history_path or not self._history_path.exists():
            return []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            records: List[ReflectionGrowthRecord] = []
            for item in data:
                evidence = [EvidenceLink(**ev) for ev in item.get("evidence_chain", [])]
                records.append(
                    ReflectionGrowthRecord(
                        record_id=item.get("record_id", ""),
                        timestamp=item.get("timestamp", ""),
                        insight_id=item.get("insight_id", ""),
                        evaluation_id=item.get("evaluation_id", ""),
                        source_summary=item.get("source_summary", ""),
                        decision=item.get("decision", BRIDGE_DECISION_SKIPPED),
                        decision_reason=item.get("decision_reason", ""),
                        proposal_ids=item.get("proposal_ids", []),
                        risk_flags=item.get("risk_flags", []),
                        identity_status=item.get("identity_status", {}),
                        evidence_chain=evidence,
                        metadata=item.get("metadata", {}),
                    )
                )
            return records
        except Exception as e:
            logger.error(f"加载 ReflectionGrowthBridge 历史失败: {e}")
            backup_corrupt_file(self._history_path)
            return []

    @staticmethod
    def _get_field(obj: Any, name: str, default: Any = None) -> Any:
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)
