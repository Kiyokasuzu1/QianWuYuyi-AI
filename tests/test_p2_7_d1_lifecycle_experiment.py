# -*- coding: utf-8 -*-
"""tests/test_p2_7_d1_lifecycle_experiment.py

P2.7 Phase D-1 隔离实验验收测试(Phase C-1.5 模式)。

subprocess + 临时 cwd 运行实验脚本,断言:
- LifecycleEvent 生成 + 间隔跳过
- Audit JSONL 生成
- memory/emotion/personality/growth/identity hash 全部不变
- 生产 data/ 零污染
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = REPO_ROOT / "scripts" / "p2_7_d1" / "run_experiment.py"


def test_lifecycle_experiment_acceptance(tmp_path):
    out_dir = tmp_path / "exp"
    env = dict(os.environ)
    env["HF_HUB_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT)

    proc = subprocess.run(
        [sys.executable, str(EXPERIMENT_SCRIPT), str(out_dir)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"

    results_path = out_dir / "results.json"
    assert results_path.exists(), "results.json 未生成"
    results = json.loads(results_path.read_text(encoding="utf-8"))

    acceptance = results["acceptance"]
    assert acceptance["all_passed"] is True
    assert acceptance["events_generated"] is True
    assert acceptance["skipped_verified"] is True
    assert acceptance["audit_written"] is True
    assert acceptance["idempotency_keys_present"] is True
    assert acceptance["memory_unchanged"] is True
    assert acceptance["emotion_unchanged"] is True
    assert acceptance["personality_unchanged"] is True
    assert acceptance["growth_unchanged"] is True
    assert acceptance["identity_unchanged"] is True
    assert acceptance["production_data_untouched"] is True
    assert acceptance["config_clean"] is True

    # 点检:hash 表全无变化
    diffs = results["diffs"]
    assert set(diffs.values()) == {False}
    # 点检:审计 ≥4 行且样例含 7 字段
    assert results["audit"]["line_count"] >= 4
    sample = results["audit"]["sample"]
    for key in ("event_id", "timestamp", "trigger_type", "task_type", "source", "result", "audit"):
        assert key in sample
    assert sample["audit"]["idempotency_key"]
    # 点检:tick2 两条均为 SKIPPED(间隔保护)
    assert {x["status"] for x in results["tick2"]} == {"SKIPPED"}
