"""ProposalStore (skeleton)
JSONL append-only proposal store for GrowthProposal objects.

This is a minimal skeleton implementing file placement and method signatures.
Full persistence logic (append/compaction/indexing) is TODO in implementation phase.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional, List

from src.contracts import growth_schema


class ProposalStore:
    """Append-only JSONL store for GrowthProposal objects.

    Data file (default): data/proposals/proposals.jsonl
    Each line should be a JSON-serialized GrowthProposal dict. The store
    should maintain an in-memory index for quick lookup in a full implementation.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = Path(path or "data/proposals/proposals.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ensure file exists
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")
        # TODO: load index from file (id -> file offset or last line)
        self._index = {}

    def save(self, proposal: growth_schema.GrowthProposal) -> None:
        """Persist a new proposal by appending a JSON line.

        TODO: implement append + index update + atomic fsync.
        """
        raise NotImplementedError("ProposalStore.save not implemented")

    def update(self, proposal: growth_schema.GrowthProposal) -> None:
        """Update an existing proposal's status/fields.

        Implementation strategy (MVP): append a new JSON line with the same id
        and updated fields; index should point to the latest version.
        """
        raise NotImplementedError("ProposalStore.update not implemented")

    def load(self, proposal_id: str) -> Optional[growth_schema.GrowthProposal]:
        """Load and return the latest proposal by id (or None).
        """
        raise NotImplementedError("ProposalStore.load not implemented")

    def list(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[growth_schema.GrowthProposal]:
        """List proposals optionally filtered by status.
        """
        raise NotImplementedError("ProposalStore.list not implemented")

    def exists_similar(self, source_event_id: str, fingerprint: str) -> Optional[str]:
        """Optional: detect duplicates by source_event_id + fingerprint.
        Return existing proposal id if found, else None.
        """
        # TODO: implement dedupe logic
        return None
