# -*- coding: utf-8 -*-
"""Restore proposals.jsonl from JSON to JSONL format.

The proposal_review.py tool accidentally wrote the file as JSON
(it was originally JSONL). This script restores JSONL format
without changing any data.
"""
import json
import shutil
from pathlib import Path

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
PROP_PATH = ROOT / "data" / "proposals" / "proposals.jsonl"
BACKUP_PATH = ROOT / "data" / "proposals" / "proposals.jsonl.json_format_backup"

# Load as JSON
with open(PROP_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

proposals = data.get("proposals", [])
print(f"Total proposals: {len(proposals)}")

# Backup original
shutil.copy(PROP_PATH, BACKUP_PATH)
print(f"Backup at: {BACKUP_PATH}")

# Write as JSONL
with open(PROP_PATH, "w", encoding="utf-8") as f:
    for p in proposals:
        f.write(json.dumps(p, ensure_ascii=False, default=str) + "\n")

# Verify
with open(PROP_PATH, "r", encoding="utf-8") as f:
    lines = f.readlines()
print(f"Lines after restore: {len(lines)}")

# Check our target
target_id = "prop_9785d8035aab"
target_lines = [l for l in lines if target_id in l]
print(f"Target ({target_id}) found in {len(target_lines)} line(s)")
for l in target_lines:
    d = json.loads(l)
    print(f"  status={d.get('status')}, source_event_id={d.get('source_event_id')}")
