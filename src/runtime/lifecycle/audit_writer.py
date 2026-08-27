# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/audit_writer.py

P2.7 Phase D-1: Lifecycle 审计写入器(append-only JSONL)。

职责:
- 把 LifecycleEvent 追加写入 JSONL 审计文件
- 支持读取最近 N 条 / 按 task_type 过滤(供观测与幂等查询)
- 失败隔离:写入失败不抛异常,返回 False(审计不可用绝不能拖垮任务)

约束(任务书红线):
- 只写审计文件,禁止修改任何已有状态文件
- 每条记录一行 JSON,不重写历史(append-only)

并发:
- 进程内按路径 RLock(与项目内 _path_lock 惯例一致:audit/storage.py、
  growth/growth_state.py、growth/proposal/storage.py 同款模式)
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.contracts.lifecycle_event_schema import LifecycleEvent


# 按路径写锁(进程内);跨进程追加不做强保证,审计属尽力而为日志
_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: Union[str, Path]) -> threading.RLock:
    key = str(Path(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


class LifecycleAuditWriter:
    """append-only JSONL 生命周期审计写入器。"""

    def __init__(self, path: Union[str, Path] = ".cache/audit/lifecycle_events.jsonl"):
        self._path = Path(path)
        self._lock = _path_lock(self._path)

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(self, event: LifecycleEvent) -> bool:
        """追加一条事件。成功返回 True;任何失败返回 False(不抛)。"""
        if event is None:
            return False
        try:
            line = json.dumps(event.to_dict(), ensure_ascii=False, default=str)
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.write("\n")
            return True
        except Exception:
            return False

    def append_dict(self, data: Dict[str, Any]) -> bool:
        """追加一个 dict 形态的事件(便利入口,内部包装为 LifecycleEvent)。"""
        try:
            return self.append(LifecycleEvent.from_dict(data))
        except Exception:
            return False

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def read_recent(
        self,
        limit: int = 100,
        task_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """从尾部读取最近 N 条事件(可选按 task_type 过滤)。"""
        out: List[Dict[str, Any]] = []
        try:
            cap = max(1, int(limit or 100))
        except Exception:
            cap = 100
        try:
            with self._lock:
                if not self._path.exists():
                    return out
                with open(self._path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
        except Exception:
            return out
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            if task_type is not None and str(item.get("task_type", "")) != task_type:
                continue
            out.append(item)
            if len(out) >= cap:
                break
        return out

    def count(self) -> int:
        """审计文件当前有效行数(损坏行不计)。"""
        return len(self.read_recent(limit=100000))

    # --------------------------------------------------------
    # 视图
    # --------------------------------------------------------
    def __repr__(self) -> str:
        return f"LifecycleAuditWriter(path={str(self._path)!r})"


__all__ = [
    "LifecycleAuditWriter",
    "_path_lock",
]
