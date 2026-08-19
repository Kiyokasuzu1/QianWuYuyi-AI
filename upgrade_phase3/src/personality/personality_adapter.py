"""PersonalityAdapter implementation (MVP mapping & in-memory apply)

- map_proposal_to_evolution_record: maps GrowthProposal -> EvolutionRecord-like dict
- apply_proposal: attempts an in-memory apply using TraitStateUpdater if available
  but does NOT persist changes to any personality store. Returns an 'envelope'
  describing applied status and before/after digests.

This implementation is intentionally conservative: it uses in-memory trait_states
constructed from proposal.before values or defaults, applies via TraitStateUpdater,
and returns before/after snapshots. No external writes are performed.
"""
from __future__ import annotations
from typing import Dict, Any, Optional
from datetime import datetime
import uuid

from src.contracts import growth_schema
from src.personality import evolution_record as evolution_record_module

# Try to import TraitStateUpdater and create_trait_state
try:
    from src.personality.trait_state_updater import TraitStateUpdater
    from src.personality.trait_state import create_trait_state
except Exception:
    TraitStateUpdater = None
    create_trait_state = None


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class PersonalityAdapter:
    def __init__(self, runtime_context: Optional[Any] = None):
        self.runtime_context = runtime_context

    def map_proposal_to_evolution_record(self, proposal: growth_schema.GrowthProposal) -> Dict[str, Any]:
        """Map GrowthProposal -> EvolutionRecord-like dict.

        EvolutionRecord format (partial) expected by TraitStateUpdater:
          - record_id
          - timestamp
          - trigger_candidates
          - source_growth_records
          - trait_changes: { trait: {"delta": float, "before": float} }
          - approved: bool
          - confidence: float
          - decision_reason: str

        This mapping extracts the final segment of ChangeItem.path as trait name.
        """
        record_id = f"rec_{uuid.uuid4().hex[:8]}"
        trait_changes: Dict[str, Dict[str, float]] = {}
        trigger_candidates = []

        for ci in proposal.proposed_changes or []:
            # path like 'personality.traits.shyness' -> trait 'shyness'
            if isinstance(ci.path, str) and "." in ci.path:
                trait = ci.path.split(".")[-1]
            else:
                trait = ci.path or "unknown"

            before = None
            if ci.before is not None:
                try:
                    before = float(ci.before)
                except Exception:
                    before = None
            after = None
            if ci.after is not None:
                try:
                    after = float(ci.after)
                except Exception:
                    after = None

            delta = None
            if after is not None and before is not None:
                delta = round(after - before, 4)
            elif after is not None:
                # assume delta relative to 0.0 baseline if before unknown
                delta = round(after, 4)
            elif before is not None:
                # no after provided: delta unknown -> 0.0
                delta = 0.0
            else:
                delta = 0.0

            trait_changes[trait] = {"delta": delta, "before": before if before is not None else 0.5}
            trigger_candidates.append(trait)

        record = {
            "record_id": record_id,
            "timestamp": now_iso(),
            "trigger_candidates": trigger_candidates,
            "source_growth_records": [],
            "trait_changes": trait_changes,
            "approved": True,
            "confidence": float(getattr(proposal, "confidence", 0.5) or 0.5),
            "decision_reason": "applied_via_proposal_adapter",
            "rejection_reasons": {},
            "rejected_dimensions": [],
            "evolution_level": "proposal",
            "requires_validation": False,
        }
        return record

    def apply_proposal(self, proposal: growth_schema.GrowthProposal, actor: str = "system") -> Dict[str, Any]:
        """Attempt to apply proposal in-memory using TraitStateUpdater.

        Returns envelope:
          - applied: bool
          - before: {trait: value}
          - after: {trait: value}
          - note: str

        This method DOES NOT persist changes to any personality store.
        """
        record = self.map_proposal_to_evolution_record(proposal)

        # Build in-memory trait_states from record.trait_changes 'before' values
        trait_states: Dict[str, Any] = {}
        for trait, ch in record.get("trait_changes", {}).items():
            before_val = ch.get("before", 0.5)
            if create_trait_state:
                trait_states[trait] = create_trait_state(trait, before_val)
            else:
                # minimal dict fallback
                trait_states[trait] = {"trait": trait, "current_value": before_val}

        before_snapshot = {t: (s.get("current_value") if isinstance(s, dict) else getattr(s, "current_value", None)) for t, s in trait_states.items()}

        if TraitStateUpdater is None:
            return {"applied": False, "before": before_snapshot, "after": before_snapshot, "note": "TraitStateUpdater not available"}

        updater = TraitStateUpdater()
        try:
            updated = updater.apply(record, trait_states)
        except Exception as e:
            return {"applied": False, "before": before_snapshot, "after": before_snapshot, "note": f"apply_exception: {e}"}

        after_snapshot = {}
        for t, s in updated.items():
            if isinstance(s, dict):
                after_snapshot[t] = s.get("current_value")
            else:
                after_snapshot[t] = getattr(s, "current_value", None)

        return {"applied": True, "before": before_snapshot, "after": after_snapshot, "note": "applied_in_memory"}
