# -*- coding: utf-8 -*-
"""Find which proposal in real simulation caused max_abs=0.7."""
import json
from pathlib import Path

# 重新跑一遍,但详细记录
import sys
import shutil
import tempfile
import uuid
import random
from datetime import datetime, timezone, timedelta
from collections import defaultdict

sys.path.insert(0, str("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI"))

from src.admin.selfmodel_consumer import SelfModelConsumer

ALLOWED_TRAIT_PATHS = {
    "warmth", "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism", "self_confidence", "empathy",
    "curiosity", "playfulness", "shyness", "initiative", "social_need",
    "energy",
}

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# 重新跑 70 轮,使用相同的 seed
tmp_dir = Path(tempfile.mkdtemp(prefix="c47_debug2_"))
try:
    consumer = SelfModelConsumer(data_dir=str(tmp_dir), actor="debug2", auto_save=True, bootstrap=True)
    rng = random.Random(20260802)
    start = datetime.now(timezone.utc)
    proposals = []
    max_delta_seen = 0.0
    max_delta_proposal = None

    for c in range(70):
        # 模拟 simulator 的逻辑
        roll = rng.random()
        if roll < 0.05:
            # core mutation
            path = "core_identity.traits.warmth"
            before, after = 0.7, 0.0
            delta = after - before
            kind = "ATTACK"
        elif roll < 0.10:
            # duplicate
            if proposals:
                last = proposals[-1]
                prop = dict(last)
                prop["id"] = f"prop_dup_{uuid.uuid4().hex[:8]}"
                proposals.append(prop)
                # 直接 apply
                prop["status"] = "approved"
                result = consumer.process(prop)
                if result.get("envelope", {}).get("applied"):
                    pass
                continue
            else:
                path = rng.choice(list(ALLOWED_TRAIT_PATHS))
                before, after = 0.5, 0.52
                delta = 0.02
                kind = "DUP_NIL"
        elif roll < 0.15:
            # conflicting
            path = rng.choice(list(ALLOWED_TRAIT_PATHS))
            before, after = 0.5, 0.49
            delta = -0.01
            kind = "CONFLICT"
        else:
            # normal
            path = rng.choice(list(ALLOWED_TRAIT_PATHS))
            before = 0.5
            delta = rng.choice([0.01, 0.02, 0.03]) * rng.choice([-1, 1])
            after = round(max(0.0, min(1.0, before + delta)), 4)
            kind = "NORMAL"

        sid = f"evt_{c:04d}"
        pid = f"prop_{uuid.uuid4().hex[:8]}"
        ts = (start + timedelta(seconds=c*10)).isoformat().replace("+00:00", "Z")
        prop = {
            "id": pid,
            "source_event_id": sid,
            "proposed_changes": [{"path": path, "before": before, "after": after, "reason": f"c{c}"}],
            "confidence": 0.85,
            "evidence_ids": [f"mem_{c}"],
            "evaluator_meta": {"growth_level": "context", "action_scope": "personality"},
            "timestamp": ts,
            "status": "approved",
            "schema_version": "1.0",
        }
        proposals.append(prop)
        result = consumer.process(prop)
        if abs(delta) > max_delta_seen:
            max_delta_seen = abs(delta)
            max_delta_proposal = (c, kind, path, before, after, delta)

        if c >= 50 and c <= 60:
            applied = result.get("envelope", {}).get("applied", False)
            error = result.get("error")
            print(f"cycle={c:>3} kind={kind:>8} path={path:>10} delta={delta:+.4f} applied={applied} error={error}")

    print(f"\nMax delta seen: {max_delta_seen:.4f}")
    print(f"Max delta proposal: {max_delta_proposal}")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)
