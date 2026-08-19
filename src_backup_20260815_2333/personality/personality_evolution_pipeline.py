"""
Phase 3.5.18: Personality Evolution Pipeline

目标：
将「已批准的 GrowthProposal」转换为可审计的人格演化执行结果：

Proposal (accepted)
↓
PersonalityAdapter → PersonalityChangeRequest
↓
TraitStateUpdater (EvolutionRecord)
↓
SelfModelUpdater (生成 SelfModelChangeSuggestion，默认不自动应用)
↓
Evolution history (PersonalityEvolutionRecord)

约束：
- 不自动接受 proposal
- 不绕过 ApprovalLayer
- 不修改 Persona 文档
- 所有结果必须可追溯

Phase 6.2: 增加可选 self_model_adapter 参数；注入时通过 Adapter 写入（Authority Closure）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.contracts.growth_schema import GrowthProposal
from src.contracts.personality_evolution_schema import (
    PersonalityEvolutionRecord,
    PersonalityEvolutionSnapshot,
)
from src.personality.personality_adapter import PersonalityAdapter
from src.personality.self_model_updater import SelfModelUpdater
try:
    from src.personality.trait_state_updater import TraitStateUpdater
except Exception:
    TraitStateUpdater = None

logger = logging.getLogger(__name__)


class PersonalityEvolutionPipeline:
    def __init__(
        self,
        *,
        history_path: Optional[str] = None,
        block_when_identity_unstable: bool = True,
        auto_apply_self_model: bool = False,
        history_limit: int = 300,
        self_model_adapter: Optional[Any] = None,
    ):
        self.personality_adapter = PersonalityAdapter()
        self.self_model_updater = SelfModelUpdater()
        self.block_when_identity_unstable = block_when_identity_unstable
        self.auto_apply_self_model = auto_apply_self_model
        self.history_limit = history_limit
        # Phase 6.2: 可选 SelfModelAdapter 注入（Authority Closure）
        self._self_model_adapter = self_model_adapter
        self._history_path = Path(history_path) if history_path else None
        self._history: List[PersonalityEvolutionRecord] = []
        if self._history_path:
            try:
                self._history_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self._history = self._load_history()

    def set_self_model_adapter(self, adapter: Any) -> None:
        """Phase 6.2: 运行时注入 Adapter"""
        self._self_model_adapter = adapter

    def apply_approved_proposal(
        self,
        *,
        proposal: GrowthProposal,
        trait_states: Dict[str, Any],
        actor: str = "runtime",
        approval_record: Optional[Dict[str, Any]] = None,
        identity_stability_report: Optional[Dict[str, Any]] = None,
        self_model_manager: Optional[Any] = None,
    ) -> PersonalityEvolutionRecord:
        """
        对已批准的 proposal 执行人格演化（TraitStateUpdater）。

        注意：
        - 这里不负责批准 proposal；只接受 status=accepted 的输入
        - SelfModelUpdater 默认只生成建议，不自动应用（可配置）
        """
        if proposal is None:
            return self._record_failed("missing proposal", actor=actor)

        if getattr(proposal, "status", "") != "accepted":
            return self._record_blocked(
                proposal_id=getattr(proposal, "id", ""),
                actor=actor,
                reason=f"proposal status not accepted: {getattr(proposal, 'status', '')}",
                identity_stability_report=identity_stability_report,
            )

        # identity stability gate
        if self.block_when_identity_unstable and identity_stability_report:
            if not bool(identity_stability_report.get("is_stable", True)):
                return self._record_blocked(
                    proposal_id=getattr(proposal, "id", ""),
                    actor=actor,
                    reason="blocked by identity stability report",
                    identity_stability_report=identity_stability_report,
                )

        approval_id = ""
        if approval_record:
            approval_id = str(approval_record.get("record_id", "") or approval_record.get("approval_id", "") or "")

        # Build change request
        pcr = self.personality_adapter.build_change_request(
            proposal,
            source_insight_id=getattr(proposal, "source_event_id", None),
            evaluator_meta=getattr(proposal, "evaluator_meta", {}) or {},
        )

        # Apply to provided trait states via TraitStateUpdater (唯一合法修改入口)
        before = json.loads(json.dumps(trait_states, ensure_ascii=False))
        evo_record = self.personality_adapter.map_proposal_to_evolution_record(proposal)
        evo_record["approved"] = True
        evo_record["decision_reason"] = f"approved_by:{actor}_via_pipeline"

        if TraitStateUpdater is None:
            return self._record_blocked(
                proposal_id=getattr(proposal, "id", ""),
                actor=actor,
                reason="TraitStateUpdater not available",
                identity_stability_report=identity_stability_report,
            )

        updater = TraitStateUpdater()
        try:
            updated_states = updater.apply(evo_record, trait_states)
        except Exception as e:
            return self._record_failed(f"trait_state_apply_exception: {e}", actor=actor)

        after = json.loads(json.dumps(updated_states, ensure_ascii=False))

        # Self model suggestion
        suggestions: List[Dict[str, Any]] = []
        try:
            sug = self.self_model_updater.from_pcr(pcr)
            if sug:
                suggestions.append(sug.to_dict())
                if self.auto_apply_self_model and self_model_manager and not sug.requires_approval:
                    self_model_manager.apply_suggestion(sug)
        except Exception as e:
            logger.warning(f"SelfModelUpdater 生成建议失败（已隔离）: {e}")

        # Phase 6.2: Authority Closure — 通过 Adapter 写入 SelfModel
        if self._self_model_adapter is not None:
            try:
                affected: Dict[str, float] = {}
                for tname, tval in (updated_states or {}).items():
                    try:
                        new_v = float(tval.get("current_value", 0.0) if isinstance(tval, dict) else float(tval))
                    except Exception:
                        new_v = 0.0
                    old_v = 0.0
                    try:
                        old_v = float((before.get(tname) or {}).get("current_value", 0.0) if isinstance(before.get(tname), dict) else float(before.get(tname, 0.0) or 0.0))
                    except Exception:
                        old_v = 0.0
                    delta = round(new_v - old_v, 5)
                    if abs(delta) > 1e-6:
                        affected[tname] = delta
                self._self_model_adapter.apply_external_change(
                    change_type="personality",
                    reason=f"personality_evolution_pipeline:{actor}",
                    source="personality_evolution_pipeline",
                    proposal_id=getattr(proposal, "id", ""),
                    confidence=float(getattr(proposal, "confidence", 0.5) or 0.5),
                    affected_traits=affected,
                )
            except Exception as e:
                logger.warning(f"SelfModelAdapter.apply_external_change failed（已隔离）: {e}")

        rec = PersonalityEvolutionRecord(
            proposal_id=getattr(proposal, "id", ""),
            approval_record_id=approval_id,
            actor=actor,
            status="applied",
            reason="applied approved proposal",
            identity_stability_report_id=str(identity_stability_report.get("report_id", "") or "") if identity_stability_report else "",
            identity_stability_snapshot=dict(identity_stability_report or {}),
            evolution_record=dict(evo_record),
            trait_states_before=before,
            trait_states_after=after,
            self_model_suggestions=suggestions,
            metadata={
                "pcr_id": pcr.get("request_id", ""),
                "confidence": float(getattr(proposal, "confidence", 0.0) or 0.0),
            },
        )
        self._push(rec)
        return rec

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._history)
        items.reverse()
        return [r.to_dict() for r in items[:limit]]

    def get_snapshot(self) -> Dict[str, Any]:
        snap = PersonalityEvolutionSnapshot(
            total_records=len(self._history),
            applied=sum(1 for r in self._history if r.status == "applied"),
            blocked=sum(1 for r in self._history if r.status == "blocked"),
            failed=sum(1 for r in self._history if r.status == "failed"),
            last_record_id=self._history[-1].record_id if self._history else "",
        )
        return snap.to_dict()

    def _record_failed(self, reason: str, *, actor: str) -> PersonalityEvolutionRecord:
        rec = PersonalityEvolutionRecord(status="failed", reason=reason, actor=actor)
        self._push(rec)
        return rec

    def _record_blocked(
        self,
        *,
        proposal_id: str,
        actor: str,
        reason: str,
        identity_stability_report: Optional[Dict[str, Any]] = None,
    ) -> PersonalityEvolutionRecord:
        rec = PersonalityEvolutionRecord(
            proposal_id=proposal_id,
            actor=actor,
            status="blocked",
            reason=reason,
            identity_stability_report_id=str(identity_stability_report.get("report_id", "") or "") if identity_stability_report else "",
            identity_stability_snapshot=dict(identity_stability_report or {}),
        )
        self._push(rec)
        return rec

    def _push(self, rec: PersonalityEvolutionRecord) -> None:
        self._history.append(rec)
        if len(self._history) > self.history_limit:
            self._history = self._history[-self.history_limit :]
        self._persist()

    def _persist(self) -> None:
        if not self._history_path:
            return
        try:
            with open(self._history_path, "w", encoding="utf-8") as f:
                json.dump([r.to_dict() for r in self._history], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 PersonalityEvolutionPipeline 历史失败: {e}")

    def _load_history(self) -> List[PersonalityEvolutionRecord]:
        if not self._history_path or not self._history_path.exists():
            return []
        try:
            with open(self._history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            out: List[PersonalityEvolutionRecord] = []
            for item in data:
                out.append(PersonalityEvolutionRecord(**item))
            return out
        except Exception as e:
            logger.error(f"加载 PersonalityEvolutionPipeline 历史失败: {e}")
            return []
