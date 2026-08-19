"""
Integration patch: inject agreements-first context assembly into YuyiRuntime skeleton.
This file will add a small integration layer that uses RuntimeContext. It is
conservative: if yuyi_runtime module does not exist, it implants a small wrapper
`yuyi_runtime_entry` that demonstrates how to call RuntimeContext and pass the
assembled context to a fictional prompt builder / response engine.

If the real yuyi_runtime exists elsewhere, this file serves as an example patch
and can be merged into the actual lifecycle entrypoint.
"""

from pathlib import Path
from typing import Dict, Any

try:
    from src.runtime.runtime_context import RuntimeContext
except Exception:
    # fallback import in case package layout differs
    from runtime.runtime_context import RuntimeContext


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class YuyiRuntimeIntegration:
    """A minimal integration shim demonstrating how to call RuntimeContext
    at the start of request handling. Intended to be merged into the real
    YuyiRuntime lifecycle.
    """

    def __init__(self, project_root: Path = PROJECT_ROOT):
        self.rc = RuntimeContext(repo_root=project_root)

    def handle_user_message(self, user_id: str, conversation: Dict[str, Any], self_model_snapshot: Dict[str, Any], memory_summary: Dict[str, Any]):
        # Assemble prioritized context
        context = self.rc.assemble_context(
            user_id=user_id,
            conversation=conversation,
            self_model_snapshot=self_model_snapshot,
            memory_summary=memory_summary,
            options={}
        )

        # For demonstration we print the trace. Real runtime should attach this
        # trace to execution logs with correlation ids.
        print("[YuyiRuntimeIntegration] context.trace=", context.get("trace"))

        # Example prompt builder usage (replace with actual project PromptBuilder)
        prompt = "\n\n".join([b.get("content") for b in context.get("prompt_blocks", []) if b.get("content")])

        # Example response engine call (replace with actual ResponseEngine)
        response = f"[mock response based on prompt length {len(prompt)}]"

        # Example post-processing (update emotion/relationship/growth hooks etc.)
        return {
            "response": response,
            "prompt": prompt,
            "trace": context.get("trace"),
        }


# convenience entry function

def yuyi_runtime_entry(user_id: str, conversation: Dict[str, Any], self_model_snapshot: Dict[str, Any] = None, memory_summary: Dict[str, Any] = None):
    integrator = YuyiRuntimeIntegration()
    return integrator.handle_user_message(user_id, conversation, self_model_snapshot or {}, memory_summary or {})
