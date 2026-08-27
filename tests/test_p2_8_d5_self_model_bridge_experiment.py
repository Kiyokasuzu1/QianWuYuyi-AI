# -*- coding: utf-8 -*-
"""tests/test_p2_8_d5_self_model_bridge_experiment.py

P2.8 Phase D-5.0 SelfModel Governance Bridge 隔离实验验收测试
(subprocess + 临时 cwd)。

断言:
- pending 被 bridge 拒绝(blocked), 审批前 self_model.json hash 不变
- 人工审批后 bridge 消费 → applied, self_model.json 变化, A-store 回写 applied
- 重复消费 → already_applied, hash 稳定
- identity 类型 → hard deny, hash 稳定
- 缺审批证明 → rejected, hash 稳定
- 审计含 consumed/applied/rejected 三类 + 条目形状(proposal_id/actor/before/after)
- 红线目标(identity_core/personality/relationship/growth/memory/B-store)不变
- 生产 data/ 零污染 + 生产 config 无 D-5 key 泄漏
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_SCRIPT = (
    REPO_ROOT / "scripts" / "p2_8_d5" / "run_self_model_bridge_experiment.py"
)

ACCEPTANCE_KEYS = (
    "pending_blocked",
    "approve_apply_succeeded",
    "self_model_changed_only_after_approval",
    "approval_proof_recorded",
    "duplicate_already_applied",
    "identity_hard_deny",
    "missing_proof_rejected",
    "audit_reasons_complete",
    "audit_applied_entry_shape",
    "self_model_json_allowed_change",
    "a_store_grew",
    "audit_jsonl_grew",
    "forbidden_states_unchanged",
    "production_data_untouched",
    "config_clean",
)


def test_self_model_bridge_experiment_acceptance(tmp_path):
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

    # 点检: hash —— self_model 允许变化, 其余六个红线目标不变
    diffs = results["diffs"]
    assert diffs["self_model.json"] is True
    for key in ("identity_core.py", "personality_state.json",
                "relationship_state.json", "growth_state.json",
                "memory.json", "b_store_proposals.json"):
        assert diffs[key] is False, f"红线目标 {key} 发生了变化"

    # 点检: pending → blocked 细节
    blocked = results["pending_blocked"]
    assert blocked["status"] == "blocked"
    assert "status_not_accepted" in blocked["reason"]
    assert blocked["self_model_hash_unchanged"] is True

    # 点检: 审批后 applied 细节
    applied = results["approve_apply"]
    assert applied["status"] == "applied"
    assert applied["record_id"] == "gr_d5_1"
    assert applied["store_status"] == "applied"
    assert applied["approval_proof"]["approved_by"] == "admin_exp"
    assert applied["approval_proof"]["approval_id"] == "apv_d5_1"

    # 点检: 重复消费 → already_applied, 叙事不重复
    dup = results["duplicate_apply"]
    assert dup["status"] == "already_applied"
    assert dup["hash_stable"] is True
    assert dup["narratives_count"] == 1

    # 点检: identity → hard deny
    deny = results["identity_deny"]
    assert deny["status"] == "denied"
    assert "identity_change_denied" in deny["reason"]

    # 点检: 缺审批证明 → rejected
    missing = results["missing_proof"]
    assert missing["status"] == "rejected"
    assert missing["reason"].startswith("missing_approval_proof")

    # 点检: 审计三类 + 条目形状
    audit = results["audit"]
    assert audit["reasons"].count("self_model_proposal_consumed") >= 4
    assert audit["reasons"].count("self_model_evolution_rejected") >= 3
    applied_entry = audit["applied_entry"]
    assert applied_entry is not None
    assert applied_entry["actor"] == "self_model_governance_bridge"
    assert applied_entry["after"]["approval"]["approved_by"] == "admin_exp"
    assert "record_ids" in applied_entry["before"]
    assert "record_ids" in applied_entry["after"]

    # 点检: 生产 data/ 与生产 config 零污染
    prod = results["production"]
    for name in ("memory.json", "proposals.jsonl", "personality_state.json",
                 "self_model.json", "personality_growth_history.json",
                 "b_store_proposals.json"):
        assert prod["baseline"][name]["hash"] == prod["after"][name]["hash"], name
    assert results["config"]["clean"] is True
    assert results["config"]["d5_keys_leaked"] == []
