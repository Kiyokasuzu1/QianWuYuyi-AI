# -*- coding: utf-8 -*-
"""Verify max_abs_delta in simulation result."""
import json
from pathlib import Path

p = Path("logs/c47_drift_simulation_result.json")
data = json.loads(p.read_text(encoding="utf-8"))

# 找出 max_abs_delta 异常的具体行
print("=== Final metrics ===")
print(json.dumps(data["final_metrics"], ensure_ascii=False, indent=2))

# 列出所有 cycle 的 deltas
print("\n=== Per-cycle deltas ===")
for m in data["history"]:
    print(f"cycle={m['cycle']:>4} max_abs={m['deltas']['max_abs']:.3f} mean={m['deltas']['mean']:.4f} records={m['records']['total']}")
