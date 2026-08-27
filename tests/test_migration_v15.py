# -*- coding: utf-8 -*-
"""v1.5-T7: 迁移脚本 + 治理守卫测试。

覆盖：
- dry-run 不写任何文件
- 复制成功（规则 A/B）
- 已存在目标不覆盖（无 --force）
- --force 覆盖 + 旧目标备份
- 规则 B 复制后旧文件改名 *.pre_v15_bak
- proposal 禁写清单包含新路径（relationship_states_v06 / users）
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.governance.proposal_store import (
    FORBIDDEN_DIR_SEQUENCES,
    FORBIDDEN_FILE_NAMES,
    GovernanceProposalStore,
)

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "migrate_relationship_v15_buckets.py"
CREATOR = "366648462"


def _make_tree(tmp_path) -> Path:
    """构造含规则 A/B 源文件的最小 data 树，返回 root。"""
    root = tmp_path / "proj"
    (root / "data" / "users" / "yuyi").mkdir(parents=True, exist_ok=True)
    (root / "data" / "users" / CREATOR).mkdir(parents=True, exist_ok=True)
    (root / "data").mkdir(parents=True, exist_ok=True)
    (root / "data" / "users" / "yuyi" / "relationship_state.json").write_text(
        json.dumps({"bucket": "old_single_v3527"}), encoding="utf-8"
    )
    (root / "data" / "relationship_state.json").write_text(
        json.dumps({"version": "v06"}), encoding="utf-8"
    )
    return root


def _run(root: Path, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["YUYI_MIGRATE_ROOT"] = str(root)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env,
    )


# ============================================================
# dry-run 不写
# ============================================================
def test_dry_run_writes_nothing(tmp_path):
    root = _make_tree(tmp_path)
    r = _run(root)  # 默认 dry-run
    assert r.returncode == 0
    assert "DRY-RUN" in r.stdout
    assert not (root / "data" / "users" / CREATOR / "relationship_state.json").exists()
    assert not (root / "data" / "relationship_states_v06" / f"{CREATOR}.json").exists()
    assert not (root / "data" / "relationship_state.json.pre_v15_bak").exists()
    # 源文件原样保留
    assert (root / "data" / "users" / "yuyi" / "relationship_state.json").exists()


# ============================================================
# 复制成功
# ============================================================
def test_force_copies_and_renames(tmp_path):
    root = _make_tree(tmp_path)
    r = _run(root, "--force")
    assert r.returncode == 0
    # 规则 A
    dst_a = root / "data" / "users" / CREATOR / "relationship_state.json"
    assert dst_a.exists()
    assert json.loads(dst_a.read_text(encoding="utf-8"))["bucket"] == "old_single_v3527"
    # 规则 B
    dst_b = root / "data" / "relationship_states_v06" / f"{CREATOR}.json"
    assert dst_b.exists()
    assert json.loads(dst_b.read_text(encoding="utf-8"))["version"] == "v06"
    # B 复制后旧文件改名
    assert not (root / "data" / "relationship_state.json").exists()
    assert (root / "data" / "relationship_state.json.pre_v15_bak").exists()


# ============================================================
# 已存在目标：无 --force 不覆盖；--force 覆盖+备份旧目标
# ============================================================
def test_existing_target_skipped_without_force(tmp_path):
    root = _make_tree(tmp_path)
    # 预置已存在目标
    dst_a = root / "data" / "users" / CREATOR / "relationship_state.json"
    dst_a.parent.mkdir(parents=True, exist_ok=True)
    dst_a.write_text(json.dumps({"bucket": "existing"}), encoding="utf-8")
    # 无 --force（默认 dry-run 仅打印计划，不写入）→ 目标不被覆盖
    r = _run(root)
    assert "DRY-RUN" in r.stdout
    assert json.loads(dst_a.read_text(encoding="utf-8"))["bucket"] == "existing"  # 未被覆盖
    # dry-run 计划中应提示目标已存在需 --force
    assert "需 --force" in r.stdout


def test_force_overwrites_with_backup(tmp_path):
    root = _make_tree(tmp_path)
    dst_a = root / "data" / "users" / CREATOR / "relationship_state.json"
    dst_a.parent.mkdir(parents=True, exist_ok=True)
    dst_a.write_text(json.dumps({"bucket": "existing"}), encoding="utf-8")
    r = _run(root, "--force")
    assert r.returncode == 0
    # 已覆盖为源内容
    assert json.loads(dst_a.read_text(encoding="utf-8"))["bucket"] == "old_single_v3527"
    # 旧目标被备份
    assert (root / "data" / "users" / CREATOR / "relationship_state.json.pre_v15_old").exists()


# ============================================================
# proposal 禁写清单
# ============================================================
def test_proposal_forbidden_file_names_include_new():
    assert "relationship_states_v06" in FORBIDDEN_FILE_NAMES


def test_proposal_forbidden_dir_sequences_include_new():
    assert ("data", "users") in FORBIDDEN_DIR_SEQUENCES
    assert ("data", "relationship_states_v06") in FORBIDDEN_DIR_SEQUENCES


def test_proposal_store_rejects_v06_dir(tmp_path):
    store = GovernanceProposalStore(data_dir=str(tmp_path))
    with pytest.raises(ValueError):
        store._resolve_data_dir(
            tmp_path / "data" / "relationship_states_v06" / "366648462"
        )
    with pytest.raises(ValueError):
        store._resolve_data_dir(tmp_path / "data" / "users" / "3556983027")


def test_proposal_store_allows_governance_dir(tmp_path):
    store = GovernanceProposalStore(data_dir=str(tmp_path))
    resolved = store._resolve_data_dir(tmp_path / "data" / "governance")
    assert resolved == (tmp_path / "data" / "governance").resolve()
