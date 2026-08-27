# -*- coding: utf-8 -*-
"""tests/test_p2_8_d4_self_model_experiment.py

P2.8 Phase D-4.0 SelfModel Cycle 隔离实验验收测试(subprocess + 临时 cwd)。

断言:
- 周期任务脱离聊天由 runtime.tick 触发, 生成 2 个 pending 提案
- 治理键完整: _governance_origin=self_model_cycle +
  _governance.decision=pending_review + proposal_type=self_model
- self_model_proposal 载荷与 C-1 drain 字段 1:1 兼容
- 红线: self_model.json / identity_core / personality_state /
  relationship_state / growth_state / memory 全部 hash 不变
- 允许变化: proposals.jsonl + audit.jsonl 增长
- tick2 → SKIPPED(间隔门控); 去重轮 created=0, deduped=2
- 生产 data/ 零污染 + 生产 config 无 D-4 key 泄漏
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = (
    REPO_ROOT / "scripts" / "p2_8_d4" / "run_self_model_experiment.py"
)

ACCEPTANCE_KEYS = (
    "cycle_ran_success",
    "proposals_all_pending",
    "origin_all_self_model_cycle",
    "decision_all_pending_review",
    "proposal_type_all_self_model",
    "self_model_payload_c1_compatible",
    "proposal_change_paths_prefix_self_model",
    "self_model_json_hash_unchanged",
    "identity_core_hash_unchanged",
    "personality_state_unchanged",
    "relationship_state_unchanged",
    "growth_state_unchanged",
    "memory_unchanged",
    "proposals_jsonl_grew",
    "audit_jsonl_grew",
    "second_tick_skipped",
    "dedupe_created_zero",
    "dedupe_count_matches",
    "audit_traces_proposals",
    "production_data_untouched",
    "config_clean",
)


def test_self_model_experiment_acceptance(tmp_path):
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
    for key in ACCEPTANCE_KEYS:
        assert acceptance[key] is True, f"acceptance[{key}] = {acceptance[key]}"

    # 点检: hash —— 全部六个红线目标不变
    diffs = results["diffs"]
    for key in ("self_model.json", "identity_core.py", "personality_state.json",
                "relationship_state.json", "growth_state.json", "memory.json"):
        assert diffs[key] is False, f"红线目标 {key} 发生了变化"

    # 点检: 周期 SUCCESS 生成 2 个 pending
    tick = results["cycle_tick"]
    assert tick[0]["status"] == "SUCCESS"
    assert tick[0]["metrics"]["proposals_created"] == 2

    # 点检: 第二次 tick → SKIPPED(间隔门控)
    second = results["second_tick"]
    assert second[0]["status"] == "SKIPPED"
    assert second[0]["reason"]

    # 点检: 去重轮 created=0, deduped=2
    assert results["dedupe_round"]["proposals_created"] == 0
    assert results["dedupe_round"]["proposals_deduped"] == 2

    # 点检: 落盘提案治理键 + C-1 载荷
    stored = results["stored_proposals"]
    assert len(stored) == 2
    for s in stored:
        assert s["status"] == "pending"
        assert s["origin"] == "self_model_cycle"
        assert s["decision"] == "pending_review"
        assert s["proposal_type"] == "self_model"
        assert s["payload_fields_ok"] is True
        assert s["change_paths"]
        assert all(pth.startswith("self_model.") for pth in s["change_paths"])
        assert s["source_events"]
        assert s["evidence_ids"]

    # 点检: 审计完整(1 条 SUCCESS, produced_events == 2 个 proposal ids)
    audit = results["audit"]
    assert audit["success_rows"] == 1
    assert len(audit["success_produced_events"][0]) == 2
    assert audit["success_metrics"][0]["proposals_created"] == 2

    # 点检: 生产 data/ 与生产 config 零污染
    prod = results["production"]
    for name in ("memory.json", "proposals.jsonl", "personality_state.json",
                 "self_model.json", "personality_growth_history.json"):
        assert prod["baseline"][name]["hash"] == prod["after"][name]["hash"], name
    assert results["config"]["clean"] is True
    assert results["config"]["d4_keys_leaked"] == []
