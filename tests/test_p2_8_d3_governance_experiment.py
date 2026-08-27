# -*- coding: utf-8 -*-
"""tests/test_p2_8_d3_governance_experiment.py

P2.8 Phase D-3.0 Growth Governance 隔离实验验收测试(subprocess + 临时 cwd)。

断言:
- GrowthCycle 生成 2 个 pending 提案
- 红线: 未审批直接 apply 失败(管理器 not_accepted + 演化管线 blocked),
  审批前 personality_state 文件不变
- approve → apply: applied+saved,curiosity 0.7 → 0.7011,文件变化仅此时发生
- 重复 apply → already_applied(EP-2),无二次变化
- reject → 演化 blocked,无人格变化
- self_model/growth_state/relationship/memory/identity_core hash 不变
- 生产 data/ 零污染 + 生产 config 无 D-3 key 泄漏
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = REPO_ROOT / "scripts" / "p2_8_d3" / "run_governance_experiment.py"


def test_governance_experiment_acceptance(tmp_path):
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
        "cycle_generated_pending",
        "redline_manager_rejects_pending",
        "redline_pipeline_blocks_pending",
        "redline_no_file_change_before_approval",
        "approve_succeeded",
        "apply_succeeded",
        "personality_changed_only_after_apply",
        "duplicate_apply_already_applied",
        "duplicate_apply_no_extra_change",
        "manager_apply_closes_store_status",
        "reject_blocks_evolution",
        "other_states_unchanged",
        "personality_only_allowed_change",
        "audit_complete",
        "evolution_history_recorded",
        "production_data_untouched",
        "config_clean",
    ):
        assert acceptance[key] is True, f"acceptance[{key}] = {acceptance[key]}"

    # 点检: hash —— personality 变化,其余五个目标全部不变
    diffs = results["diffs"]
    assert diffs["personality_state.json"] is True
    for key in ("relationship_state.json", "self_model.json",
                "growth_state.json", "memory.json", "identity_core.py"):
        assert diffs[key] is False

    # 点检: 周期 SUCCESS 生成 2 个 pending
    tick = results["cycle_tick"]
    assert tick[0]["status"] == "SUCCESS"
    assert tick[0]["metrics"]["proposals_created"] == 2
    assert results["pending_after_cycle"]["count"] == 2

    # 点检: 红线细节 —— 未审批直接 apply 双层失败 + 文件未变
    redline = results["redline_pre_approval"]
    assert redline["manager_status"] == "not_accepted"
    assert "not accepted" in redline["pipeline_reason"]
    assert redline["personality_hash_unchanged"] is True
    assert redline["store_status_still"] == "pending"

    # 点检: apply 细节 —— applied+saved+record_id+curiosity 0.7→0.7011
    apply_info = results["apply"]
    assert apply_info["applied"] is True and apply_info["saved"] is True
    assert apply_info["record_id"]
    assert apply_info["affected_traits"]["curiosity"]["before"] == 0.7
    assert apply_info["affected_traits"]["curiosity"]["after"] == 0.7011

    # 点检: 重复 apply → already_applied,hash 稳定
    assert results["duplicate_apply"]["reason"] == "already_applied"
    assert results["duplicate_apply"]["hash_stable"] is True

    # 点检: reject → store rejected + 演化 blocked + 人格文件稳定
    reject_info = results["reject"]
    assert reject_info["store_status"] == "rejected"
    assert reject_info["pipeline_applied"] is False
    assert reject_info["personality_hash_stable"] is True

    # 点检: 审计完整(accepted/applied/rejected 三类,审批者可追溯)
    audit = results["audit_entries"]
    assert {"proposal_accepted", "proposal_applied", "proposal_rejected"} <= set(
        audit["reasons"]
    )
    assert "admin_exp" in audit["actors"]

    # 点检: 演化历史含 blocked(红线拦截) + applied(正式应用)
    assert set(results["evolution_history"]["statuses"]) == {"blocked", "applied"}

    # 点检: 生产 data/ 与生产 config 零污染
    prod = results["production"]
    for name in ("memory.json", "proposals.jsonl", "personality_state.json"):
        assert prod["baseline"][name]["hash"] == prod["after"][name]["hash"]
    assert results["config"]["clean"] is True
    assert results["config"]["d3_keys_leaked"] == []
