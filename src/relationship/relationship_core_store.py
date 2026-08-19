# -*- coding: utf-8 -*-
"""Phase 2.5-C 阶段1:RelationshipCoreStore —— append-only JSONL 持久化。

路径约定:data/relationship_core/relationship_core.jsonl

约束:
- append-only:save() 只追加新行,同一 relationship_id 重复写入被拒绝(不隐式覆盖);
- 损坏容错:坏行跳过;整体损坏备份为 *.corrupt.* 后降级空列表(不删除原文件);
- 文件不存在 → 空列表(安全降级);
- 任何 I/O 异常被隔离,不向调用方抛出;
- 旧 memory.json 不迁移——本 store 只读只写自己的 JSONL。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.relationship.relationship_core import RelationshipCore

logger = logging.getLogger(__name__)

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
    return lock


def _backup_corrupt(path: str) -> None:
    """损坏文件复制备份(不覆盖、不删除旧文件)。"""
    try:
        if os.path.exists(path):
            backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            shutil.copy2(path, backup)
            logger.warning("RelationshipCoreStore: 损坏文件已备份为 %s", backup)
    except Exception:  # noqa: BLE001
        pass


def _to_dict(core: Any) -> Optional[Dict[str, Any]]:
    if isinstance(core, RelationshipCore):
        return core.to_dict()
    if isinstance(core, dict):
        return dict(core)
    return None


class RelationshipCoreStore:
    """RelationshipCore 的 append-only JSONL 存储。

    不建内存索引:记录量小,读时全量解析,保证多实例/重启一致,
    坏行容错与损坏备份都在 load() 内完成。
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or "data/relationship_core/relationship_core.jsonl"
        self._lock = _path_lock(self.path)

    def save(self, core: Any) -> bool:
        """追加保存一条关系核心。

        Returns:
            True 已追加;False 表示 relationship_id 重复(append-only 不覆盖)
            或输入非法 / I/O 失败(全部降级,不抛异常)。
        """
        data = _to_dict(core)
        if not data:
            return False
        relationship_id = str(data.get("relationship_id") or "")
        if not relationship_id:
            return False
        with self._lock:
            try:
                if self.get(relationship_id) is not None:
                    return False
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:  # noqa: BLE001
                        pass
                return True
            except Exception as exc:  # noqa: BLE001
                logger.debug("RelationshipCoreStore.save 失败(已隔离): %s", exc)
                return False

    def load(self) -> List[Dict[str, Any]]:
        """读取全部有效记录;坏行跳过;整体损坏备份后降级空列表。"""
        with self._lock:
            if not os.path.exists(self.path):
                return []
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw_lines = f.readlines()
            except Exception:  # noqa: BLE001
                _backup_corrupt(self.path)
                return []

            records: List[Dict[str, Any]] = []
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(parsed, dict):
                    records.append(parsed)
            # 有内容但零有效记录 → 整体损坏,备份降级
            if raw_lines and not records:
                _backup_corrupt(self.path)
            return records

    def get(self, relationship_id: str) -> Optional[Dict[str, Any]]:
        for record in self.load():
            if record.get("relationship_id") == relationship_id:
                return record
        return None

    def list_all(self) -> List[Dict[str, Any]]:
        return self.load()


__all__ = ["RelationshipCoreStore"]
