# -*- coding: utf-8 -*-
"""
src/runtime/self_model/reflection/reflection_store.py

Phase 4.3: ReflectionStore —— 反思历史存储

职责:
- 保存 ReflectionRecord 列表(append-only)
- 按 identity_id 索引
- 支持按 identity / priority / kind / 时间窗口 / limit 查询
- 异常隔离:append / query 失败不抛

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 容量受 limit 控制,超出 FIFO 淘汰
- 不调用 LLM
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.self_model.reflection.reflection_record import (
    ReflectionRecord,
)


logger = logging.getLogger(__name__)


REFLECTION_STORE_SCHEMA_VERSION = "1.0"
DEFAULT_REFLECTION_STORE_LIMIT = 100


def _record_identity(record: Any) -> str:
    """安全读取 identity_id。"""
    if record is None:
        return ""
    fid = getattr(record, "identity_id", None)
    if isinstance(fid, str):
        return fid
    return ""


class ReflectionStore:
    """自我反思历史 Store(Phase 4.3 / v1.0)。

    字段:
    - _records:         Dict[str, List[ReflectionRecord]]  # identity_id -> [record]
    - _append_count:    int                                # 总 append 次数
    - _last_error:      Optional[str]
    - _dropped_count:   int                                # 容量淘汰次数
    - history_limit:    int                                # 每个 identity 最多保留 N 条

    方法:
    - append(record) -> bool
    - get(identity_id) -> List[ReflectionRecord]
    - latest(identity_id) -> Optional[ReflectionRecord]
    - query(identity_id, priority, kind, since, until, limit) -> List[ReflectionRecord]
    - count(identity_id) -> int
    - total_count() -> int
    - list_identities() -> List[str]
    - clear(identity_id=None) -> int
    - health_check() / describe()
    """

    def __init__(self, history_limit: int = DEFAULT_REFLECTION_STORE_LIMIT) -> None:
        if history_limit <= 0:
            history_limit = DEFAULT_REFLECTION_STORE_LIMIT
        self.history_limit: int = history_limit
        self._records: Dict[str, List[ReflectionRecord]] = {}
        self._append_count: int = 0
        self._dropped_count: int = 0
        self._last_error: Optional[str] = None
        self._last_appended: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def append(self, record: Optional[ReflectionRecord]) -> bool:
        """追加一条 reflection record。"""
        if record is None:
            self._last_error = "append_none_record"
            return False
        if not isinstance(record, ReflectionRecord):
            self._last_error = "append_invalid_type"
            return False
        try:
            fid = _record_identity(record)
            if not fid:
                self._last_error = "append_record_missing_identity_id"
                return False
            bucket = self._records.setdefault(fid, [])
            bucket.append(record)
            while len(bucket) > self.history_limit:
                bucket.pop(0)
                self._dropped_count += 1
            self._append_count += 1
            self._last_appended = {
                "identity_id": fid,
                "reflection_id": record.reflection_id,
                "timestamp": record.timestamp,
                "kind": record.reflection_kind,
                "priority": record.priority,
            }
            self._last_error = None
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"append_failed: {exc}"
            logger.warning("ReflectionStore.append 失败: %s", exc)
            return False

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def get(self, identity_id: str) -> List[ReflectionRecord]:
        """获取某 identity 的全部反思(按时间升序)。"""
        if not identity_id:
            return []
        return list(self._records.get(identity_id, []) or [])

    def latest(self, identity_id: str) -> Optional[ReflectionRecord]:
        """获取某 identity 的最新反思。"""
        if not identity_id:
            return None
        bucket = self._records.get(identity_id, [])
        if not bucket:
            return None
        return bucket[-1]

    def get_by_reflection_id(
        self, identity_id: str, reflection_id: str,
    ) -> Optional[ReflectionRecord]:
        """按 reflection_id 查某条记录。"""
        if not identity_id or not reflection_id:
            return None
        for r in self._records.get(identity_id, []):
            if r.reflection_id == reflection_id:
                return r
        return None

    def query(
        self,
        identity_id: Optional[str] = None,
        priority: Optional[str] = None,
        kind: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[ReflectionRecord]:
        """按条件过滤。"""
        ids: List[str]
        if identity_id:
            ids = [identity_id]
        else:
            ids = list(self._records.keys())

        result: List[ReflectionRecord] = []
        for fid in ids:
            for r in self._records.get(fid, []):
                if priority and r.priority != priority:
                    continue
                if kind and r.reflection_kind != kind:
                    continue
                if since and r.timestamp and r.timestamp < since:
                    continue
                if until and r.timestamp and r.timestamp > until:
                    continue
                result.append(r)
        # 默认按时间升序
        if limit is not None and limit > 0:
            result = result[-limit:]
        return result

    def count(self, identity_id: str) -> int:
        if not identity_id:
            return 0
        return len(self._records.get(identity_id, []) or [])

    def total_count(self) -> int:
        return sum(len(b) for b in self._records.values())

    def list_identities(self) -> List[str]:
        return list(self._records.keys())

    def clear(self, identity_id: Optional[str] = None) -> int:
        """清空 store(可选按 identity)。返回删除数量。"""
        if identity_id is None:
            n = self.total_count()
            self._records.clear()
            return n
        bucket = self._records.get(identity_id)
        if not bucket:
            return 0
        n = len(bucket)
        del self._records[identity_id]
        return n

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def append_count(self) -> int:
        return self._append_count

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def last_appended(self) -> Optional[Dict[str, Any]]:
        return self._last_appended

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": REFLECTION_STORE_SCHEMA_VERSION,
            "history_limit": self.history_limit,
            "append_count": self._append_count,
            "dropped_count": self._dropped_count,
            "total_records": self.total_count(),
            "identity_count": len(self._records),
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": REFLECTION_STORE_SCHEMA_VERSION,
            "history_limit": self.history_limit,
            "append_count": self._append_count,
            "dropped_count": self._dropped_count,
            "total_records": self.total_count(),
            "identities": self.list_identities(),
            "last_error": self._last_error,
        }


__all__ = [
    "ReflectionStore",
    "REFLECTION_STORE_SCHEMA_VERSION",
    "DEFAULT_REFLECTION_STORE_LIMIT",
]
