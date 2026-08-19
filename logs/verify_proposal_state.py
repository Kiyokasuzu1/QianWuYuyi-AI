# -*- coding: utf-8 -*-
"""Verify final state of proposal and SelfModel data."""
import json
from pathlib import Path

p = Path("data/proposals/proposals.jsonl")
last_lines = []
with open(p, "r", encoding="utf-8") as f:
    for line in f:
        if "prop_9785d8035aab" in line:
            last_lines.append(line.strip())

print(f"Total lines with prop_9785d8035aab: {len(last_lines)}")
for l in last_lines:
    d = json.loads(l)
    print(f"  id={d.get('id')} status={d.get('status')} decision={d.get('decision')} reviewer_id={d.get('reviewer_id', '')}")
    print(f"  source_event_id={d.get('source_event_id')} confidence={d.get('confidence')} evidence_count={len(d.get('evidence_ids') or [])}")
