# -*- coding: utf-8 -*-
"""Yuyi AI 统一版本识别模块（P2.0 Version System）。

纯工具模块：零业务依赖、零配置加载，只负责版本信息读取。

版本层级（唯一可信链）：
    Git tag      = 发布版本（封板时打）
    VERSION.txt  = 当前代码声明版本（SemVer，单行）
    本模块       = 运行时识别（版本 + commit + 构建时间）

commit 解析优先级：
    1. 环境变量 YUYI_GIT_COMMIT（部署注入，最高优先）
    2. 仓库根目录 git rev-parse --short HEAD
    3. "unknown"

设计约束：
    - 任何异常都必须 fallback，绝不允许导致启动失败
    - subprocess 带 timeout
    - 结果进程内缓存（测试可用 reset_cache() 清除）
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VERSION_FILE = _REPO_ROOT / "VERSION.txt"
_UNKNOWN_VERSION = "0.0.0-unknown"
_UNKNOWN_COMMIT = "unknown"
_GIT_TIMEOUT_SECONDS = 5
_PROCESS_START_UTC = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

_cache: Dict[str, str] = {}


def reset_cache() -> None:
    """清除进程内缓存（供测试使用；生产无需调用）。"""
    _cache.clear()


def get_version() -> str:
    """读取 VERSION.txt 声明的 SemVer；读取失败返回 0.0.0-unknown。"""
    if "version" in _cache:
        return _cache["version"]
    version = ""
    try:
        text = _VERSION_FILE.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        version = lines[0] if lines else ""
    except Exception:  # noqa: BLE001
        version = ""
    if not version:
        version = _UNKNOWN_VERSION
    _cache["version"] = version
    return version


def _resolve_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


def get_commit() -> str:
    """解析当前运行 commit（环境变量 > git rev-parse > unknown）。"""
    if "commit" in _cache:
        return _cache["commit"]
    commit = os.environ.get("YUYI_GIT_COMMIT", "").strip()
    if not commit:
        commit = _resolve_git_commit()
    if not commit:
        commit = _UNKNOWN_COMMIT
    _cache["commit"] = commit
    return commit


def get_build_time() -> str:
    """构建时间：环境变量 YUYI_BUILD_TIME（ISO8601）> 进程启动 UTC 时间。"""
    return os.environ.get("YUYI_BUILD_TIME", "").strip() or _PROCESS_START_UTC


def get_version_info() -> Dict[str, Any]:
    """聚合版本信息（version / commit / build）。"""
    return {
        "version": get_version(),
        "commit": get_commit(),
        "build": get_build_time(),
    }


def get_banner() -> str:
    """启动横幅（多行文本，逐行 logger.info 输出）。"""
    info = get_version_info()
    return (
        "================================\n"
        "Yuyi AI Runtime\n"
        "\n"
        f"Version: {info['version']}\n"
        f"Commit:  {info['commit']}\n"
        f"Build:   {info['build']}\n"
        "================================"
    )
