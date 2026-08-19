# -*- coding: utf-8 -*-
"""P2.0 Version System 一致性测试。

验证唯一可信链：
    VERSION.txt → src/version.py → /health
三者版本一致；横幅与模块一致。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import version as v  # noqa: E402

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _read_version_txt_raw() -> str:
    return (_PROJECT_ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()


def test_01_txt_matches_module():
    """VERSION.txt 原文 == version.py 解析结果。"""
    assert _read_version_txt_raw() == v.get_version()


def test_02_health_matches_module_and_txt():
    """/health 的 version == version.py == VERSION.txt。"""
    import api_server

    with api_server.app.test_client() as client:
        data = client.get("/health").get_json()

    txt = _read_version_txt_raw()
    assert data["version"] == v.get_version() == txt
    assert SEMVER_RE.match(txt), f"VERSION.txt 非 SemVer: {txt!r}"


def test_03_banner_matches_module():
    """横幅中的版本与 commit 与模块一致。"""
    banner = v.get_banner()
    assert f"Version: {v.get_version()}" in banner
    assert f"Commit:  {v.get_commit()}" in banner


def test_04_changelog_records_current_version():
    """CHANGELOG 记录了当前开发版本条目（版本演进留痕）。"""
    changelog = (_PROJECT_ROOT / "docs" / "CHANGELOG.md").read_text(encoding="utf-8")
    version = v.get_version()
    assert f"## v{version}" in changelog, f"CHANGELOG 缺少 v{version} 条目"


def test_05_health_commit_equals_module_commit():
    """/health 的 commit 与模块解析一致（同进程内必然一致，防止旁路实现）。"""
    import api_server

    with api_server.app.test_client() as client:
        data = client.get("/health").get_json()
    assert data["commit"] == v.get_commit()
