"""
src/memory/atomic_write.py

Phase 2.2 Memory Persistence Hardening — 统一原子写工具

目标:
    memory.json 等小 JSON 文件的写入从「直接截断覆盖」升级为「原子替换」:
    同目录临时文件 → 完整写入 JSON → flush → fsync → os.replace。
    任何失败路径下旧文件保持完好,不存在半写文件覆盖原文件的窗口。

对齐项目内既有原子写模式(emotion_repository.py R2.7.6 / proposal_store.py /
runtime_snapshot.py 等),不引入第三方依赖。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PATH_LOCKS: dict = {}
_PATH_LOCKS_GUARD = threading.Lock()


def get_path_lock(path) -> "threading.RLock":
    """返回给定路径的进程级可重入锁（按绝对路径归一键）。

    用途:
        读-改-写类持久化操作在调用 atomic_write_json 前后加锁,
        避免多线程/多入口并发写入同一文件时丢失更新。

    锁按绝对路径缓存于进程内,同一路径多次获取返回同一把锁,
    不存在跨进程互斥(跨进程仍由 os.replace 的原子性兜底)。
    """
    key = os.path.abspath(os.fspath(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def backup_corrupt_file(path) -> Optional[str]:
    """损坏文件复制备份（copy 不移动,旧文件原样保留供人工修复）。

    V1.1 Foundation Hardening: 统一损坏兜底——各持久化模块加载失败时
    调用本函数保留现场,绝不覆盖旧文件。返回备份路径或 None。
    """
    try:
        p = os.fspath(path)
        if os.path.exists(p):
            backup = f"{p}.corrupt.{datetime.now().strftime('%Y%m%dT%H%M%S%f')}"
            shutil.copy2(p, backup)
            logger.warning("已备份损坏文件: %s -> %s", p, backup)
            return backup
    except Exception:  # noqa: BLE001
        logger.exception("损坏文件备份失败: %s", path)
    return None


def atomic_write_json(path, data: Any, **json_kwargs) -> None:
    """原子写入 JSON 文件。

    流程:
        1. 在目标文件同目录生成临时文件(保证 os.replace 同卷原子);
        2. UTF-8 写入完整 JSON(默认 ensure_ascii=False, indent=2,可用 kwargs 覆盖);
        3. flush + fsync,确保替换发生时新内容已完整落盘;
        4. os.replace 原子替换目标文件。

    异常语义:
        任一步失败 → 清理临时文件 → 重新抛出异常。
        旧文件自始至终未被触碰,保持完好。

    Args:
        path: 目标文件路径(str / PathLike)。
        data: 待写入的 JSON 数据。
        **json_kwargs: 透传给 json.dump 的额外参数(覆盖默认值)。
    """
    path = os.fspath(path)
    folder = os.path.dirname(path) or "."
    tmp_path = None
    defaults = {"ensure_ascii": False, "indent": 2}
    defaults.update(json_kwargs)
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.tmp.",
            dir=folder,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **defaults)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # 个别环境(虚拟文件系统等)不支持 fsync,不阻断主流程
                pass
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path is not None:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
        raise
