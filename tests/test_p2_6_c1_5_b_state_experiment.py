# -*- coding: utf-8 -*-
"""P2.6 Phase C-1.5: B 态实验验证测试。

运行实验脚本（隔离 subprocess + 临时 cwd 真实组件链），断言 G1-G4 验收标准。
本文件不含任何生产单例引用，实验脚本自身在独立进程中运行。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "p2_6_c1_5" / "run_experiment.py"


def test_b_state_experiment_acceptance(tmp_path):
    """运行隔离 B 态实验，验证 G1-G4 全部验收标准。"""
    _out_dir = tmp_path / "artifacts"
    _env = dict(os.environ)
    _env["HF_HUB_OFFLINE"] = "1"
    _proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--out-dir", str(_out_dir)],
        cwd=str(_REPO_ROOT),
        env=_env,
        capture_output=True,
        text=True,
        timeout=360,
    )
    assert _proc.returncode == 0, (
        f"实验脚本失败 rc={_proc.returncode}\n"
        f"stdout={_proc.stdout}\nstderr={_proc.stderr}"
    )
    _results = json.loads((_out_dir / "results.json").read_text(encoding="utf-8"))
    _acceptance = _results["acceptance"]
    _failed = {_k: _v for _k, _v in _acceptance.items() if _v is not True}
    assert _acceptance["all_passed"] is True, f"验收未通过: {_failed}"

    # 关键不变量点检
    assert _results["g1"]["ledger_status"] == "applied"
    assert _results["g1"]["ledger_applied_by"] == "self_model_drain"
    assert _results["g1"]["ledger_applied_at"]
    assert _results["hashes"]["diffs"]["self_model.json"] is True
    assert _results["hashes"]["diffs"]["personality_state.json"] is False
    assert _results["hashes"]["diffs"]["growth_state.json"] is False
    assert _results["hashes"]["diffs"]["identity_core.py"] is False
    assert _results["hashes"]["diffs"]["relationship_state.json"] is False
    assert _results["probe"]["experiment_direct_apply"] == 0
    assert _results["probe"]["drain_metrics"]["applied"] > 0
    assert _results["probe"]["drain_metrics"]["failed"] == 0
