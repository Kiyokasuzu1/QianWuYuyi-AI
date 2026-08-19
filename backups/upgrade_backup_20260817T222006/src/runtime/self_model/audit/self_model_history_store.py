# -*- coding: utf-8 -*-
"""
src/runtime/self_model/audit/self_model_history_store.py

Phase 4.2.4: SelfModelHistoryStore —— SelfModelSnapshot 历史追踪

职责:
- 维护一个按 identity_id 索引的快照历史
- 默认保留最近 N 个快照(LIFO,FIFO 淘汰)
- 支持按 identity_id / version / 时间窗口查询
- 不修改 snapshot 内容(只读)
- 不调用 LLM,纯结构化

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 异常隔离:任何 append/query 异常返回空结果

设计:
- 单 Store 可管理多个 identity(每个 identity 独立 LIFO 队列)
- 容量由 history_limit 控制
- 线程不安全(单线程 Runtime 使用)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.self_model.self_model_data import (
    SelfModelSnapshot,
    SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
)


logger = logging.getLogger(__name__)


SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION = "1.0"
DEFAULT_HISTORY_LIMIT = 20


def _snapshot_identity(snap: Any) -> str:
    """安全读取 identity_id。"""
    if snap is None:
        return ""
    fid = getattr(snap, "identity_id", None)
    if isinstance(fid, str):
        return fid
    return ""


def _snapshot_version(snap: Any) -> int:
    """安全读取 version。"""
    if snap is None:
        return 0
    v = getattr(snap, "version", None)
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


def _snapshot_updated_at(snap: Any) -> str:
    """安全读取 updated_at。"""
    if snap is None:
        return ""
    ua = getattr(snap, "updated_at", None)
    if isinstance(ua, str):
        return ua
    return ""


class SelfModelHistoryStore:
    """SelfModelSnapshot 历史 Store(Phase 4.2.4 / v1.0)。

    字段:
    - history_limit:    int                       # 每个 identity 最多保留 N 个
    - _history:         Dict[str, List[SelfModelSnapshot]]  # identity_id -> [快照]
    - _append_count:    int                       # 总 append 次数
    - _last_appended:   Optional[Dict]            # 最近一次 append 信息
    - _dropped_count:   int                       # 容量淘汰次数
    - _last_error:      Optional[str]             # 最近一次错误

    方法:
    - append(snap)                              # 追加快照
    - get(identity_id) -> List[SelfModelSnapshot]
    - latest(identity_id) -> Optional[SelfModelSnapshot]
    - get_by_version(identity_id, version) -> Optional[SelfModelSnapshot]
    - list_identities() -> List[str]
    - total_count() -> int                      # 全部快照数
    - count(identity_id) -> int                 # 某 identity 快照数
    - clear(identity_id=None)                   # 清空(可选)
    - query(identity_id=None, since=None, until=None) -> List[SelfModelSnapshot]
    - health_check() / describe()
    """

    def __init__(self, history_limit: int = DEFAULT_HISTORY_LIMIT) -> None:
        if history_limit <= 0:
            history_limit = DEFAULT_HISTORY_LIMIT
        self.history_limit: int = history_limit
        self._history: Dict[str, List[SelfModelSnapshot]] = {}
        self._append_count: int = 0
        self._last_appended: Optional[Dict[str, Any]] = None
        self._dropped_count: int = 0
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(self, snapshot: Optional[SelfModelSnapshot]) -> bool:
        """追加一个 snapshot,返回是否成功(失败 / None 不算)。"""
        if snapshot is None:
            self._last_error = "append_none_snapshot"
            return False
        try:
            fid = _snapshot_identity(snapshot)
            if not fid:
                self._last_error = "append_snapshot_missing_identity_id"
                return False
            bucket = self._history.setdefault(fid, [])
            bucket.append(snapshot)
            # 容量淘汰(FIFO)
            while len(bucket) > self.history_limit:
                bucket.pop(0)
                self._dropped_count += 1
            self._append_count += 1
            self._last_appended = {
                "identity_id": fid,
                "version": _snapshot_version(snapshot),
                "updated_at": _snapshot_updated_at(snapshot),
            }
            self._last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"append_failed: {exc}"
            logger.warning("SelfModelHistoryStore.append 失败: %s", exc)
            return False

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def get(self, identity_id: str) -> List[SelfModelSnapshot]:
        """获取某 identity 的全部快照(按时间升序)。"""
        if not identity_id:
            return []
        return list(self._history.get(identity_id, []) or [])

    def latest(self, identity_id: str) -> Optional[SelfModelSnapshot]:
        """获取某 identity 的最新快照。"""
        if not identity_id:
            return None
        bucket = self._history.get(identity_id, [])
        if not bucket:
            return None
        return bucket[-1]

    def get_by_version(
        self, identity_id: str, version: int,
    ) -> Optional[SelfModelSnapshot]:
        """按 version 获取某 identity 的指定快照。"""
        if not identity_id:
            return None
        for snap in self._history.get(identity_id, []):
            if _snapshot_version(snap) == version:
                return snap
        return None

    def list_identities(self) -> List[str]:
        """列出所有已记录的 identity_id。"""
        return list(self._history.keys())

    def total_count(self) -> int:
        """全部 identity 的快照总数。"""
        return sum(len(b) for b in self._history.values())

    def count(self, identity_id: str) -> int:
        """某 identity 的快照数。"""
        if not identity_id:
            return 0
        return len(self._history.get(identity_id, []) or [])

    # --------------------------------------------------------
    # 时间窗口查询
    # --------------------------------------------------------
    def query(
        self,
        identity_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[SelfModelSnapshot]:
        """按条件过滤快照。

        - identity_id: 限定到某个 identity(默认全部)
        - since:       updated_at >= since
        - until:       updated_at <= until
        - limit:       最多返回 N 条
        """
        if identity_id:
            candidates = self.get(identity_id)
        else:
            candidates = []
            for bucket in self._history.values():
                candidates.extend(bucket)
        if since or until:
            filtered: List[SelfModelSnapshot] = []
            for snap in candidates:
                ua = _snapshot_updated_at(snap)
                if since and ua and ua < since:
                    continue
                if until and ua and ua > until:
                    continue
                filtered.append(snap)
            candidates = filtered
        if limit is not None and limit >= 0:
            candidates = candidates[-limit:] if limit > 0 else []
        return list(candidates)

    # --------------------------------------------------------
    # 清理
    # --------------------------------------------------------
    def clear(self, identity_id: Optional[str] = None) -> int:
        """清空(全部 / 指定 identity),返回被清空的快照数。"""
        if identity_id is None:
            n = self.total_count()
            self._history.clear()
            return n
        bucket = self._history.pop(identity_id, None)
        return len(bucket) if bucket else 0

    # --------------------------------------------------------
    # 状态查询
    # --------------------------------------------------------
    @property
    def append_count(self) -> int:
        return self._append_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def last_appended(self) -> Optional[Dict[str, Any]]:
        return self._last_appended

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_check(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "healthy": True,
            "schema_version": SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION,
            "history_limit": self.history_limit,
            "append_count": self._append_count,
            "dropped_count": self._dropped_count,
            "identity_count": len(self._history),
            "total_snapshots": self.total_count(),
        }
        if self._last_error is not None:
            result["last_error"] = self._last_error
        return result

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION,
            "history_limit": self.history_limit,
            "append_count": self._append_count,
            "dropped_count": self._dropped_count,
            "identity_count": len(self._history),
            "total_snapshots": self.total_count(),
            "identities": self.list_identities(),
            "last_error": self._last_error,
        }


__all__ = [
    "SelfModelHistoryStore",
    "SELF_MODEL_HISTORY_STORE_SCHEMA_VERSION",
    "DEFAULT_HISTORY_LIMIT",
]
