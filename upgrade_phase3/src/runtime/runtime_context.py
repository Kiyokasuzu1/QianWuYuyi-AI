import json
from pathlib import Path
from typing import Any, Dict, Optional


class RuntimeContext:
    """A lightweight RuntimeContext responsible for assembling the prompt/context
    used by the Response Engine. This implementation prioritizes loading IMMUTABLE
    agreements (e.g. manifesto) and injecting them as system-level messages.

    The implementation is intentionally conservative and file-system based so it
    can be adopted into different persistence backends later.
    """

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        # default paths
        self.agreements_dir = self.repo_root / "src" / "storage" / "agreements"

    def load_agreement(self, agreement_id: str) -> Optional[Dict[str, Any]]:
        """Load a specific agreement by filename (agreement_id corresponds to file stem).

        e.g. agreement_id='manifesto' -> loads src/storage/agreements/manifesto.json
        """
        path = self.agreements_dir / f"{agreement_id}.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def load_immutable_agreements(self) -> Dict[str, Dict[str, Any]]:
        """Load all agreements in the agreements directory that are marked as IMMUTABLE.
        Returns a dict keyed by agreement id (filename without extension).
        """
        results: Dict[str, Dict[str, Any]] = {}
        if not self.agreements_dir.exists():
            return results
        for p in sorted(self.agreements_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            level = data.get("level") or data.get("priority")
            if isinstance(level, str) and level.upper() == "IMMUTABLE":
                key = p.stem
                results[key] = data
        return results

    def assemble_context(
        self,
        user_id: str,
        conversation: Dict[str, Any],
        self_model_snapshot: Optional[Dict[str, Any]] = None,
        memory_summary: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Assemble prioritized context for the Response Engine.

        Priority (highest -> lowest):
          1. IMMUTABLE Agreements (enforced as system messages)
          2. SelfModel snapshot (identity, core beliefs)
          3. Relationship summary (if present in options)
          4. Memory summary
          5. Recent conversation turns

        Returns a dictionary representing the assembled context including
        `system_messages` (list), `prompt_blocks`, and debugging `trace`.
        """
        trace = []

        # 1) Load immutable agreements
        immutable_agreements = self.load_immutable_agreements()
        system_messages = []
        for aid, agr in immutable_agreements.items():
            text = agr.get("text") or agr.get("title")
            if text:
                system_messages.append({
                    "id": aid,
                    "text": text,
                    "enforced_as_system_message": agr.get("enforced_as_system_message", False),
                    "injection_protection": agr.get("injection_protection", False),
                })
                trace.append(f"loaded immutable agreement: {aid}")

        # 2) SelfModel snapshot
        self_model = self_model_snapshot or {}
        if self_model:
            trace.append("self_model_snapshot loaded")

        # 3) Relationship summary (optionally provided)
        relationship = None
        if options and options.get("relationship_summary"):
            relationship = options.get("relationship_summary")
            trace.append("relationship_summary loaded")

        # 4) Memory summary
        mem = memory_summary or {}
        if mem:
            trace.append("memory_summary loaded")

        # 5) Recent conversation
        recent = conversation.get("recent_turns") if isinstance(conversation, dict) else None
        if recent:
            trace.append("recent turns attached")

        # Build prioritized prompt context
        prompt_blocks = []
        # Agreements as top-priority system block(s)
        for sm in system_messages:
            prompt_blocks.append({"role": "system", "content": sm["text"], "source": f"agreement:{sm['id']}"})

        # SelfModel core identity block
        if self_model:
            identity_text = self_model.get("display_name") or self_model.get("canonical_name") or ""
            if identity_text:
                prompt_blocks.append({"role": "system", "content": f"Identity: {identity_text}", "source": "self_model"})

        # Relationship
        if relationship:
            prompt_blocks.append({"role": "system", "content": f"Relationship summary: {relationship}", "source": "relationship"})

        # Memory summary
        if mem:
            prompt_blocks.append({"role": "system", "content": f"Memory summary: {mem}", "source": "memory"})

        # Recent conversation goes in assistant/user role blocks
        assembled_conversation = []
        if recent:
            for turn in recent:
                assembled_conversation.append(turn)

        context = {
            "system_messages": system_messages,
            "prompt_blocks": prompt_blocks,
            "conversation": assembled_conversation,
            "self_model": self_model,
            "memory_summary": mem,
            "relationship": relationship,
            "trace": trace,
        }

        return context


# Convenience function for backward compatibility
def assemble_context(*args, **kwargs):
    rc = RuntimeContext()
    return rc.assemble_context(*args, **kwargs)
