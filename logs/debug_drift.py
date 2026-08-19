# -*- coding: utf-8 -*-
"""Debug: find which proposal caused max_abs=0.7."""
import json
import sys
from pathlib import Path

ROOT = Path("d:/Yuyi Project/QianWuYuyi-AI_自动驾驶版/QianWuYuyi-AI")
sys.path.insert(0, str(ROOT))

# 重新运行模拟,记录每个 cycle 的 proposal 和 delta
import shutil
import tempfile
import uuid
import random
from datetime import datetime, timezone, timedelta

from src.admin.selfmodel_consumer import SelfModelConsumer

CORE_TRAITS = {"温柔", "敏感", "害羞", "慢热", "重视陪伴", "善良"}
FORBIDDEN_PATTERNS = ["冷漠", "攻击性", "刻薄", "恶毒", "暴力", "失去温柔", "完全改变人格"]
ALLOWED_TRAIT_PATHS = {
    "warmth", "openness", "conscientiousness", "extraversion",
    "agreeableness", "neuroticism", "self_confidence", "empathy",
    "curiosity", "playfulness", "shyness", "initiative", "social_need",
    "energy",
}

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

# 跑 70 轮,找到 max_abs 异常的那个
tmp_dir = Path(tempfile.mkdtemp(prefix="c47_debug_"))
try:
    consumer = SelfModelConsumer(data_dir=str(tmp_dir), actor="debug", auto_save=True, bootstrap=True)
    rng = random.Random(20260802)
    start = datetime.now(timezone.utc)

    for c in range(70):
        path = rng.choice(list(ALLOWED_TRAIT_PATHS))
        delta = rng.choice([0.01, 0.02, 0.03]) * rng.choice([-1, 1])
        before = 0.5
        after = round(max(0.0, min(1.0, before + delta)), 4)
        actual_delta = round(after - before, 4)

        if c == 55:  # 大约 cycle 56 出现 max_abs 异常
            # 尝试构造一个大的 delta
            before = 0.3
            after = 0.0  # 极端负 delta
            actual_delta = -0.3

        sid = f"evt_debug_{c:04d}"
        pid = f"prop_debug_{uuid.uuid4().hex[:8]}"
        ts = (start + timedelta(seconds=c*10)).isoformat().replace("+00:00", "Z")

        prop = {
            "id": pid,
            "source_event_id": sid,
            "proposed_changes": [
                {"path": path, "before": before, "after": after, "reason": f"cycle {c}"}
            ],
            "confidence": 0.85,
            "evidence_ids": [f"mem_debug_{c}"],
            "evaluator_meta": {"growth_level": "context", "action_scope": "personality"},
            "timestamp": ts,
            "status": "approved",
            "schema_version": "1.0",
        }
        result = consumer.process(prop)
        applied = result.get("envelope", {}).get("applied", False)

        if c >= 50 and c <= 65:
            print(f"cycle={c:>3} path={path:>10} before={before:.3f} after={after:.3f} delta={actual_delta:.4f} applied={applied}")
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)
