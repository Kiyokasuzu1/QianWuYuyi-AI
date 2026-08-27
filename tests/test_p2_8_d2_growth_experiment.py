# -*- coding: utf-8 -*-
"""tests/test_p2_8_d2_growth_experiment.py

P2.8 Phase D-2.0 Growth Cycle 隔离实验验收测试(Phase C-1.5 / D-1 模式)。

subprocess + 临时 cwd 运行实验脚本,断言:
- growth_cycle SUCCESS 生成 pending 提案,立即再 tick → SKIPPED
- 去重轮:同事件 + 新任务 → 0 新提案、2 去重,提案行数不变
- 全部提案 status=pending + 治理链接(origin=growth_cycle_task)
- personality/identity/self_model/relationship/growth_state/memory hash 全部不变
- 生产 data/ 零污染 + 生产 config 无 D-2 key 泄漏
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = REPO_ROOT / "scripts" / "p2_8_d2" / "run_experiment.py"


def test_growth_cycle_experiment_acceptance(tmp_path):
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
        timeout=600,
    )
    assert proc.returncode == 0, f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"

    results_path = out_dir / "results.json"
    assert results_path.exists(), "results.json 未生成"
    results = json.loads(results_path.read_text(encoding="utf-8"))

    acceptance = results["acceptance"]
    assert acceptance["all_passed"] is True
    for key in (
        "tick1_growth_success",
        "tick2_skipped",
        "proposals_created_two",
        "proposals_all_pending",
        "proposals_governance_linked",
        "proposal_ids_audited",
        "dedupe_verified",
        "no_apply_status_present",
        "personality_unchanged",
        "identity_unchanged",
        "self_model_unchanged",
        "relationship_unchanged",
        "growth_state_unchanged",
        "memory_unchanged",
        "proposal_store_allowed_change",
        "production_data_untouched",
        "config_clean",
    ):
        assert acceptance[key] is True, f"acceptance[{key}] = {acceptance[key]}"

    # 点检:hash 表全无变化(六个目标全 False)
    diffs = results["diffs"]
    assert set(diffs.keys()) == {
        "personality_state.json",
        "relationship_state.json",
        "self_model.json",
        "growth_state.json",
        "memory.json",
        "identity_core.py",
    }
    assert set(diffs.values()) == {False}

    # 点检:提案存储仅 2 行,全部 pending,治理来源与决策正确
    proposals = results["proposals"]
    assert proposals["line_count"] == 2
    assert proposals["statuses"] == ["pending"]
    assert proposals["all_pending"] is True
    assert proposals["origins"] == ["growth_cycle_task"]
    assert proposals["decisions"] == ["pending_review"]

    # 点检:tick1 SUCCESS 且 produced_events == 存储中的提案 ids
    tick1 = results["tick1"]
    assert len(tick1) == 1
    assert tick1[0]["status"] == "SUCCESS"
    assert tick1[0]["metrics"]["proposals_created"] == 2
    assert set(tick1[0]["produced_events"]) == set(proposals["ids"])

    # 点检:tick2 立即再 tick → SKIPPED(间隔保护)
    assert {x["status"] for x in results["tick2"]} == {"SKIPPED"}

    # 点检:去重轮 SUCCESS 者 0 新建、2 去重
    d_success = [x for x in results["dedupe_round"] if x["status"] == "SUCCESS"]
    assert len(d_success) == 1
    assert d_success[0]["metrics"]["proposals_created"] == 0
    assert d_success[0]["metrics"]["proposals_deduped"] == 2

    # 点检:审计 ≥4 行且样例含 7 字段
    assert results["audit"]["line_count"] >= 4
    sample = results["audit"]["sample"]
    for key in ("event_id", "timestamp", "trigger_type", "task_type", "source", "result", "audit"):
        assert key in sample

    # 点检:生产 data/ 与生产 config 零污染
    prod = results["production"]
    for name in ("memory.json", "proposals.jsonl"):
        assert prod["baseline"][name]["hash"] == prod["after"][name]["hash"]
    assert results["config"]["clean"] is True
    assert results["config"]["d2_keys_leaked"] == []
