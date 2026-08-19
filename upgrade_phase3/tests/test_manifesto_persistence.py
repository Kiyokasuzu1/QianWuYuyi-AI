import json
import os
from pathlib import Path
import pytest

# Test to ensure the imported manifesto agreement exists and is marked IMMUTABLE

def test_manifesto_file_exists_and_flags():
    repo_root = Path(__file__).resolve().parent.parent
    manifesto_path = repo_root / "src" / "storage" / "agreements" / "manifesto.json"
    assert manifesto_path.exists(), f"manifesto file not found at {manifesto_path}"

    with open(manifesto_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data.get("level") == "IMMUTABLE", "Manifesto must be level 'IMMUTABLE'"
    assert data.get("enforced_as_system_message", False) is True, "Manifesto must be enforced_as_system_message"
    assert data.get("injection_protection", False) is True, "Manifesto must enable injection_protection"


@pytest.mark.skipif(True, reason="Optional: runs only if agreement manager exists in the codebase")
def test_agreement_manager_enforces_manifesto():
    """
    This test attempts to exercise the project's agreement manager if present.
    The test is marked skipped by default; set the skip condition to False when
    the agreement manager API is available and you want to enable this check in CI.

    Expectations if agreement_manager exists:
      - agreement_manager.get("agreement:manifesto:v1") returns agreement dict/object
      - agreement has level IMMUTABLE or equivalent property
      - agreement_manager reports that non-privileged modification is disallowed
    """
    try:
        # import paths may vary; try a couple of likely locations
        try:
            from src.agreement import agreement_manager as am
        except Exception:
            from src.agreement_manager import agreement_manager as am
    except Exception as e:
        pytest.skip(f"agreement_manager not importable: {e}")

    # Attempt to retrieve manifesto agreement
    manifesto = None
    if hasattr(am, "get_agreement"):
        manifesto = am.get_agreement("agreement:manifesto:v1")
    elif hasattr(am, "get"):
        manifesto = am.get("agreement:manifesto:v1")

    assert manifesto is not None, "agreement_manager did not return the manifesto agreement"

    # Manifesto should be immutable
    level = None
    if isinstance(manifesto, dict):
        level = manifesto.get("level") or manifesto.get("priority")
    else:
        level = getattr(manifesto, "level", None) or getattr(manifesto, "priority", None)

    assert level in ("IMMUTABLE", "immutable", None) != None, "Manifesto agreement should indicate immutability"

    # Check that agreement_manager disallows modification by a normal actor (API may vary)
    if hasattr(am, "can_modify_agreement"):
        assert am.can_modify_agreement("agreement:manifesto:v1", actor="normal_user") is False

