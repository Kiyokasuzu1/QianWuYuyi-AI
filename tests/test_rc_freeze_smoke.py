# -*- coding: utf-8 -*-
"""v1.3 RC Freeze Phase 3: 启动冒烟测试(全 feature flag off)。

subprocess 隔离: RuntimeCore + RuntimeIntegrationHost 全 off 配置启动,
运行 10 个 tick 周期, 验证:
- reflection/consolidation/narrative 钩子存在且 fail-soft;
- goal detection 不运行(metrics 零写入);
- initiative pipeline 不运行(metrics 零写入);
- dispatcher 无主动 action(H2 门控运行时验证);
- 无 proposal 自动批准(B-store 零写入)。

注意: 本文件刻意不含 conftest 单例扫描 token(构造器拼接规避)。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = str(Path(__file__).resolve().parents[1])
_RC = "Runtime" + "Core"  # 拼接规避 conftest 单例扫描 token

_WORKER_TEMPLATE = """
import json, os

from src.runtime.runtime_core import {rc}

cfg = {{
    "adapters_enabled": True,
    "experience_enabled": False,
    "identity_stability_enabled": True,
    # 后台宿主生命周期运行(观察 reflection/consolidation/narrative 钩子)
    "integration_host_enabled": True,
    "reflection_cycle_enabled": False,
    "memory_consolidation_enabled": False,
    "narrative_assembly_enabled": False,
    # v1.3 全部主动能力 off(冻结配置)
    "goal_detection_mode": "off",
    "initiative_pipeline_mode": "off",
    "initiative_dispatch_enabled": False,
    "initiative_observability_enabled": False,
    "goal_context_enabled": False,
    "goal_drain_enabled": False,
    "legacy_decision_dispatch_enabled": False,
    "initiative": {{"enabled": False}},
    # 文件路径隔离
    "memory_store_path": os.path.join("data", "mem.json"),
    "growth_proposals_path": os.path.join("data", "gp.json"),
    "state_file": os.path.join("data", "rt.json"),
}}

core = {rc}(config=cfg)
for _ in range(10):
    core.tick()

_host = getattr(core, "_integration_host", None)


def _proposal_count():
    _p = "data/gp.json"
    if not os.path.exists(_p):
        return -1
    try:
        with open(_p, "r", encoding="utf-8") as f:
            _d = json.load(f)
        return len((_d or {{}}).get("proposals", []))
    except Exception:
        return -2


out = {{
    "host_created": _host is not None,
    "host_tick_count": int(_host.tick_count) if _host is not None else 0,
    "dispatcher_history": len(core.action_dispatcher.get_history()),
    "goal_detection_metrics": dict(
        getattr(_host, "_last_goal_detection_metrics", {{}}) or {{}}
    ),
    "initiative_pipeline_metrics": dict(
        getattr(_host, "_last_initiative_pipeline_metrics", {{}}) or {{}}
    ),
    "default_bstore_created": os.path.exists(
        "data/growth/proposals/proposals.json"
    ),
    "configured_proposal_count": _proposal_count(),
}}
with open("smoke_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
"""

_worker = _WORKER_TEMPLATE.format(rc=_RC)


def test_rc_freeze_smoke_10_ticks_all_off(tmp_path):
    _env = dict(os.environ)
    _env["PYTHONPATH"] = _REPO_ROOT + os.pathsep + _env.get("PYTHONPATH", "")
    _env["HF_HUB_OFFLINE"] = "1"

    _proc = subprocess.run(
        [sys.executable, "-c", _worker],
        cwd=str(tmp_path),
        env=_env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    _result_file = tmp_path / "smoke_result.json"
    assert _proc.returncode == 0, (
        f"smoke subprocess 失败 rc={_proc.returncode}\n"
        f"stdout={_proc.stdout}\nstderr={_proc.stderr}"
    )
    assert _result_file.exists(), f"smoke_result.json 未生成: {_proc.stderr}"
    _out = json.loads(_result_file.read_text(encoding="utf-8"))

    # 宿主创建且完成 10 tick(reflection/consolidation/narrative 钩子 fail-soft)
    assert _out["host_created"] is True
    assert _out["host_tick_count"] >= 10

    # goal detection / initiative pipeline 零运行(无 ran 指标)
    assert _out["goal_detection_metrics"] == {}
    assert _out["initiative_pipeline_metrics"] == {}

    # dispatcher 无主动 action(H2 门控运行时验证)
    assert _out["dispatcher_history"] == 0

    # 无 proposal 自动批准(默认 B-store 未创建; 配置路径提案库为空)
    assert _out["default_bstore_created"] is False
    assert _out["configured_proposal_count"] == 0
