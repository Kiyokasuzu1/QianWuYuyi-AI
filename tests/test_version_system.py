# -*- coding: utf-8 -*-
"""P2.0 Version System 单元测试。

覆盖：
    1. VERSION.txt 格式符合 SemVer
    2. 环境变量 YUYI_GIT_COMMIT 优先
    3. 无环境变量时从 git 读取 commit
    4. 非 git 目录 fallback "unknown"
    5. banner 包含 Version / Commit / Build
    6. /health 包含 status / version / commit
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import version as v  # noqa: E402

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
GIT_SHORT_RE = re.compile(r"^[0-9a-f]{7,40}$")


@pytest.fixture(autouse=True)
def _reset_version_cache():
    """每个用例后清缓存，避免跨用例污染。"""
    yield
    v.reset_cache()


def test_01_version_txt_is_semver():
    version = v.get_version()
    assert SEMVER_RE.match(version), f"VERSION.txt 不是 SemVer: {version!r}"


def test_02_env_commit_has_priority(monkeypatch):
    monkeypatch.setenv("YUYI_GIT_COMMIT", "abc1234")
    v.reset_cache()
    assert v.get_commit() == "abc1234"


def test_03_git_commit_without_env(monkeypatch):
    monkeypatch.delenv("YUYI_GIT_COMMIT", raising=False)
    v.reset_cache()
    commit = v.get_commit()
    # 本仓库内运行：应解析出真实 git short hash，而非 unknown
    assert GIT_SHORT_RE.match(commit), f"应为 git short hash: {commit!r}"
    assert commit != "unknown"


def test_04_unknown_fallback_outside_git(tmp_path, monkeypatch):
    monkeypatch.delenv("YUYI_GIT_COMMIT", raising=False)
    monkeypatch.setattr(v, "_REPO_ROOT", tmp_path)  # 指向无 .git 的临时目录
    v.reset_cache()
    assert v.get_commit() == "unknown"


def test_05_banner_contains_fields():
    banner = v.get_banner()
    assert "Yuyi AI Runtime" in banner
    assert "Version:" in banner
    assert "Commit:" in banner
    assert "Build:" in banner
    assert v.get_version() in banner


def test_06_health_contains_status_version_commit():
    import api_server

    with api_server.app.test_client() as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert SEMVER_RE.match(data["version"]), f"/health version 非 SemVer: {data['version']!r}"
    assert data["commit"] == v.get_commit()
    assert data["commit"] == "unknown" or GIT_SHORT_RE.match(data["commit"])


def test_07_version_info_aggregates():
    info = v.get_version_info()
    assert set(info.keys()) == {"version", "commit", "build"}
    assert info["version"] == v.get_version()
    assert info["commit"] == v.get_commit()
    assert info["build"]  # build 时间永不为空


def test_08_missing_version_file_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "_VERSION_FILE", tmp_path / "no_such_file.txt")
    v.reset_cache()
    assert v.get_version() == "0.0.0-unknown"
