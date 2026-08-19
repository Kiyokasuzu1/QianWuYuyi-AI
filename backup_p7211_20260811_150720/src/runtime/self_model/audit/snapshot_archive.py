# -*- coding: utf-8 -*-
"""
src/runtime/self_model/audit/snapshot_archive.py

Phase 4.2.4: SnapshotArchive —— 快照归档

职责:
- 提供更细粒度的快照组织(按 identity / version / 段位)
- 接收 SelfModelSnapshot,生成 archive entry(包含元数据 + 快照字典)
- 支持按版本号区间查询
- 不可变(append-only),已归档的快照不再被修改

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 异常隔离:任何归档失败不影响上层
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


SNAPSHOT_ARCHIVE_SCHEMA_VERSION = "1.0"
DEFAULT_ARCHIVE_LIMIT = 200  # 总容量上限


def _safe_to_dict(snap: Any) -> Optional[Dict[str, Any]]:
    """把 snapshot 转为 dict(可能为 None 表示无 to_dict)。"""
    if snap is None:
        return None
    fn = getattr(snap, "to_dict", None)
    if callable(fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return None
    if isinstance(snap, dict):
        return dict(snap)
    return None


def _safe_get(snap: Any, key: str, default: Any = None) -> Any:
    if snap is None:
        return default
    if isinstance(snap, dict):
        return snap.get(key, default)
    return getattr(snap, key, default)


class SnapshotArchive:
    """SelfModelSnapshot 归档(Phase 4.2.4 / v1.0)。

    字段:
    - archive_limit:    int                          # 总容量上限
    - _entries:         List[Dict[str, Any]]         # 归档 entry 列表(append-only)
    - _by_identity:     Dict[str, List[int]]         # identity_id -> [entry_index]
    - _archive_count:   int                          # 归档次数
    - _last_archived:   Optional[Dict[str, Any]]     # 最近一次归档信息
    - _last_error:      Optional[str]                # 最近错误

    方法:
    - archive(snap) -> Optional[Dict]            # 归档一个快照,返回 entry
    - get_by_identity(identity_id) -> List[Dict]
    - get_by_version(identity_id, version) -> Optional[Dict]
    - get_by_index(index) -> Optional[Dict]
    - latest(identity_id) -> Optional[Dict]
    - range_query(identity_id, v_min, v_max) -> List[Dict]
    - total() / count(identity_id) / identities()
    - clear(identity_id=None)
    - health_check() / describe()
    """

    def __init__(self, archive_limit: int = DEFAULT_ARCHIVE_LIMIT) -> None:
        if archive_limit <= 0:
            archive_limit = DEFAULT_ARCHIVE_LIMIT
        self.archive_limit: int = archive_limit
        self._entries: List[Dict[str, Any]] = []
        self._by_identity: Dict[str, List[int]] = {}
        self._archive_count: int = 0
        self._last_archived: Optional[Dict[str, Any]] = None
        self._dropped_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def archive(self, snapshot: Any) -> Optional[Dict[str, Any]]:
        """归档一个 snapshot,返回 entry(失败返回 None)。"""
        if snapshot is None:
            self._last_error = "archive_none_snapshot"
            return None
        try:
            data = _safe_to_dict(snapshot)
            if data is None:
                self._last_error = "snapshot_to_dict_failed"
                return None
            fid = _safe_get(snapshot, "identity_id", "")
            version = _safe_get(snapshot, "version", 0)
            updated_at = _safe_get(snapshot, "updated_at", "")
            entry: Dict[str, Any] = {
                "index": len(self._entries),
                "identity_id": str(fid) if fid is not None else "",
                "version": int(version) if isinstance(version, (int, float)) else 0,
                "updated_at": str(updated_at) if updated_at is not None else "",
                "schema_version": str(
                    _safe_get(snapshot, "schema_version", "1.0")
                ),
                "data": data,
            }
            self._entries.append(entry)
            if fid:
                self._by_identity.setdefault(str(fid), []).append(entry["index"])
            # 容量淘汰(从头淘汰)
            while len(self._entries) > self.archive_limit:
                old = self._entries.pop(0)
                self._dropped_count += 1
                # 同步 _by_identity 索引(全部 -1;如果被 pop 的是 first)
                for k, v in list(self._by_identity.items()):
                    new_v = [i - 1 for i in v if i > 0]
                    if not new_v:
                        self._by_identity.pop(k, None)
                    else:
                        self._by_identity[k] = new_v
                # 同时旧 entry 的 index 已无效,但 data 仍可在 entries 里查到
                _ = old
            self._archive_count += 1
            self._last_archived = {
                "index": entry["index"],
                "identity_id": entry["identity_id"],
                "version": entry["version"],
            }
            self._last_error = None
            return dict(entry)
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"archive_failed: {exc}"
            logger.warning("SnapshotArchive.archive 失败: %s", exc)
            return None

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def get_by_identity(self, identity_id: str) -> List[Dict[str, Any]]:
        if not identity_id:
            return []
        indices = self._by_identity.get(identity_id, [])
        return [self._entries[i] for i in indices if 0 <= i < len(self._entries)]

    def get_by_version(
        self, identity_id: str, version: int,
    ) -> Optional[Dict[str, Any]]:
        if not identity_id:
            return None
        for entry in self.get_by_identity(identity_id):
            if entry.get("version") == version:
                return entry
        return None

    def get_by_index(self, index: int) -> Optional[Dict[str, Any]]:
        if 0 <= index < len(self._entries):
            return self._entries[index]
        return None

    def latest(self, identity_id: str) -> Optional[Dict[str, Any]]:
        entries = self.get_by_identity(identity_id)
        if not entries:
            return None
        return entries[-1]

    def range_query(
        self,
        identity_id: str,
        v_min: Optional[int] = None,
        v_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """按版本区间过滤。"""
        result: List[Dict[str, Any]] = []
        for entry in self.get_by_identity(identity_id):
            v = entry.get("version", 0)
            if v_min is not None and v < v_min:
                continue
            if v_max is not None and v > v_max:
                continue
            result.append(entry)
        return result

    def identities(self) -> List[str]:
        return list(self._by_identity.keys())

    def total(self) -> int:
        return len(self._entries)

    def count(self, identity_id: str) -> int:
        if not identity_id:
            return 0
        return len(self._by_identity.get(identity_id, []))

    # --------------------------------------------------------
    # 清理
    # --------------------------------------------------------
    def clear(self, identity_id: Optional[str] = None) -> int:
        if identity_id is None:
            n = len(self._entries)
            self._entries.clear()
            self._by_identity.clear()
            return n
        indices = set(self._by_identity.pop(identity_id, []))
        removed = len(indices)
        # 重建 entries(保留非被清空的)
        self._entries = [
            e for i, e in enumerate(self._entries) if i not in indices
        ]
        # 重建 _by_identity 索引
        new_by_identity: Dict[str, List[int]] = {}
        for new_idx, entry in enumerate(self._entries):
            fid = entry.get("identity_id")
            if fid:
                new_by_identity.setdefault(str(fid), []).append(new_idx)
        self._by_identity = new_by_identity
        return removed

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def archive_count(self) -> int:
        return self._archive_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def last_archived(self) -> Optional[Dict[str, Any]]:
        return self._last_archived

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "schema_version": SNAPSHOT_ARCHIVE_SCHEMA_VERSION,
            "archive_limit": self.archive_limit,
            "archive_count": self._archive_count,
            "dropped_count": self._dropped_count,
            "current_size": len(self._entries),
            "identity_count": len(self._by_identity),
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
        return result

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_ARCHIVE_SCHEMA_VERSION,
            "archive_limit": self.archive_limit,
            "archive_count": self._archive_count,
            "dropped_count": self._dropped_count,
            "current_size": len(self._entries),
            "identity_count": len(self._by_identity),
            "identities": self.identities(),
            "last_error": self._last_error,
        }


__all__ = [
    "SnapshotArchive",
    "SNAPSHOT_ARCHIVE_SCHEMA_VERSION",
    "DEFAULT_ARCHIVE_LIMIT",
]
