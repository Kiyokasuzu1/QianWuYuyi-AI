"""ProposalManager (skeleton)

Coordinates GrowthProposal lifecycle: creation, persistence, accept/reject.
Uses ProposalStore for persistence and PersonalityAdapter to apply accepted proposals.

This skeleton intentionally contains only interfaces and TODO markers.
"""
from __future__ import annotations
from typing import List, Optional, Dict, Any
from datetime import datetime

from src.contracts import growth_schema, audit_schema
from src.growth.proposal_store import ProposalStore
from src.personality.personality_adapter import PersonalityAdapter
from src.runtime.runtime_context import RuntimeContext  # may be None in runtime

# optional event helper
from src.growth import proposal_events


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class ProposalManager:
    def __init__(self, runtime_context: Optional[RuntimeContext] = None, store: Optional[ProposalStore] = None, config: Optional[Dict[str, Any]] = None):
        self.runtime_context = runtime_context
        self.store = store or ProposalStore()
        self.personality_adapter = PersonalityAdapter(runtime_context=runtime_context)
        self.config = config or {}
        # runtime hooks (may be None)
        self.audit = getattr(runtime_context, "audit_logger", None)
        self.event_bus = getattr(runtime_context, "event_bus", None)
        self.snapshot = getattr(runtime_context, "snapshot_manager", None)
        # default config (auto_accept disabled by default)
        self.auto_accept_enabled = bool(self.config.get("auto_accept_enabled", False))
        self.auto_accept_threshold = float(self.config.get("auto_accept_threshold", 0.95))

    def create_proposal(self, source_event: Dict[str, Any], proposed_changes: List[growth_schema.ChangeItem], confidence: float, evidence_ids: List[str], evaluator_meta: Dict[str, Any]) -> growth_schema.GrowthProposal:
        """
        Create a GrowthProposal from evaluator output or caller-provided changes,
        persist it and emit audit/event hooks.

        NOTE: skeleton only — persistence and hooks are TODO.
        """
        # Build proposal dataclass
        proposal = growth_schema.GrowthProposal(
            source_event_id=(source_event.get("id") if isinstance(source_event, dict) else None),
            proposed_changes=proposed_changes,
            confidence=confidence,
            evidence_ids=evidence_ids,
            evaluator_meta=evaluator_meta,
        )

        # TODO: persist: self.store.save(proposal)
        # TODO: audit: self._record_audit(...)
        # TODO: publish ProposalCreatedEvent via self._publish_event(...)
        # TODO: dedupe logic (exists_similar) if desired

        raise NotImplementedError("create_proposal not implemented in skeleton")

    def get_proposal(self, proposal_id: str) -> Optional[growth_schema.GrowthProposal]:
        """Return latest proposal by id (wraps store.load)."""
        # TODO: return self.store.load(proposal_id)
        raise NotImplementedError("get_proposal not implemented in skeleton")

    def list_proposals(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[growth_schema.GrowthProposal]:
        """List proposals, optionally filtered by status."""
        # TODO: return self.store.list(status=status, limit=limit, offset=offset)
        raise NotImplementedError("list_proposals not implemented in skeleton")

    def accept_proposal(self, proposal_id: str, actor: str = "system", auto: bool = False) -> growth_schema.GrowthProposal:
        """
        Accept a proposal: call PersonalityAdapter.apply_proposal, persist accepted state,
        record audit, create snapshot, publish accepted event.

        Must be idempotent.
        """
        # TODO: load proposal, check status, call adapter.apply_proposal(...)
        # TODO: on success, set proposal.status='accepted', proposal.accepted_at = now_iso(), self.store.update(proposal)
        # TODO: record audit and snapshot, publish ProposalAcceptedEvent
        raise NotImplementedError("accept_proposal not implemented in skeleton")

    def reject_proposal(self, proposal_id: str, actor: str = "system", reason: Optional[str] = None) -> growth_schema.GrowthProposal:
        """
        Reject a proposal: mark rejected_at, persist, write audit, publish event.
        """
        # TODO: load, set status='rejected', set rejected_at, update store, audit, publish
        raise NotImplementedError("reject_proposal not implemented in skeleton")

    # Helper methods (non-invasive; no runtime changes)
    def _publish_event(self, ev):
        if self.event_bus:
            try:
                self.event_bus.publish(ev)
            except Exception:
                # swallow exceptions in MVP; keep system resilient
                if self.audit:
                    try:
                        self.audit.record(audit_schema.AuditEntry(component="runtime", actor="proposal_manager", reason="event_publish_failed", after={"event": getattr(ev, "to_dict", lambda: {})()}))
                    except Exception:
                        pass

    def _record_audit(self, entry: audit_schema.AuditEntry):
        if self.audit:
            try:
                self.audit.record(entry)
            except Exception:
                # no-op on audit failure for skeleton stage
                pass
