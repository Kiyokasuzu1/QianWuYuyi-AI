# -*- coding: utf-8 -*-
"""
src/runtime/self_model/audit/audit_chain.py

Phase 4.2.4: AuditChain —— 审计链

职责:
- 编排 HistoryStore + Archive + DiffEngine + AuditRecord
- 提供 record_snapshot(snap) 一站式入口:写入 history + archive + 创建 audit
- 提供 query 接口(按 category / severity / 时间窗口 / version 区间)
- 不修改 snapshot 内容
- 不调用 LLM

约束:
- 不 import openai / qwen / llava / vision SDK
- 不 import src.personality.* 业务模块
- 不修改 RuntimeContext schema
- 异常隔离:任何子组件失败不影响其他
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.runtime.self_model.audit.self_model_history_store import (
    SelfModelHistoryStore,
)
from src.runtime.self_model.audit.snapshot_archive import (
    SnapshotArchive,
)
from src.runtime.self_model.audit.snapshot_diff_engine import (
    SnapshotDiffEngine,
)
from src.runtime.self_model.audit.growth_audit_record import (
    GrowthAuditRecord,
    AuditSeverity,
    AuditCategory,
)


logger = logging.getLogger(__name__)


AUDIT_CHAIN_SCHEMA_VERSION = "1.0"


def _snapshot_identity(snap: Any) -> str:
    if snap is None:
        return ""
    if isinstance(snap, dict):
        return str(snap.get("identity_id", "") or "")
    return str(getattr(snap, "identity_id", "") or "")


def _snapshot_version(snap: Any) -> int:
    if snap is None:
        return 0
    if isinstance(snap, dict):
        v = snap.get("version", 0)
    else:
        v = getattr(snap, "version", 0)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class AuditChain:
    """SelfModel 审计链(Phase 4.2.4 / v1.0)。

    字段:
    - history_store:  SelfModelHistoryStore
    - archive:        SnapshotArchive
    - diff_engine:    SnapshotDiffEngine
    - _records:       List[GrowthAuditRecord]   # 按时间追加
    - _record_count:  int
    - _last_error:    Optional[str]
    - _max_records:   int                       # 审计 record 上限

    方法:
    - record_snapshot(snapshot, source="runtime", metadata=None)
        -> Optional[GrowthAuditRecord]
    - get_record(record_id) -> Optional[GrowthAuditRecord]
    - records(limit=None, severity=None, category=None, since=None, until=None)
        -> List[GrowthAuditRecord]
    - diff(identity_id, from_version, to_version) -> Optional[Dict]
    - diff_latest(identity_id) -> Optional[Dict]
    - get_snapshot(identity_id, version=None) -> Optional[SelfModelSnapshot]
    - latest_snapshot(identity_id) -> Optional[SelfModelSnapshot]
    - snapshot_history(identity_id) -> List[SelfModelSnapshot]
    - health_check() / describe()
    """

    def __init__(
        self,
        history_store: Optional[SelfModelHistoryStore] = None,
        archive: Optional[SnapshotArchive] = None,
        diff_engine: Optional[SnapshotDiffEngine] = None,
        max_records: int = 500,
    ) -> None:
        self.history_store: SelfModelHistoryStore = (
            history_store or SelfModelHistoryStore()
        )
        self.archive: SnapshotArchive = archive or SnapshotArchive()
        self.diff_engine: SnapshotDiffEngine = diff_engine or SnapshotDiffEngine()
        self._records: List[GrowthAuditRecord] = []
        self._record_count: int = 0
        self._max_records: int = max_records if max_records > 0 else 500
        self._last_error: Optional[str] = None

    # --------------------------------------------------------
    # 主入口
    # --------------------------------------------------------
    def record_snapshot(
        self,
        snapshot: Any,
        source: str = "runtime",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[GrowthAuditRecord]:
        """记录一个 snapshot 到审计链。

        流程:
        1) 写入 history_store
        2) 写入 archive
        3) 跟 history 里上一版本做 diff
        4) 创建 GrowthAuditRecord
        5) 追加到内部 _records
        """
        if snapshot is None:
            self._last_error = "record_snapshot_none"
            return None
        try:
            fid = _snapshot_identity(snapshot)
            version = _snapshot_version(snapshot)
            # 1) history
            self.history_store.append(snapshot)
            # 2) archive
            self.archive.archive(snapshot)
            # 3) diff(若有上一版本)
            prev = self.history_store.get_by_version(fid, version - 1) \
                if version > 1 else None
            record: Optional[GrowthAuditRecord] = None
            if prev is None:
                # INITIAL
                record = GrowthAuditRecord.initial(
                    snapshot, identity_id=fid, source=source, metadata=metadata,
                )
            else:
                diff_result = self.diff_engine.diff(
                    prev, snapshot, identity_id=fid,
                )
                merged_meta = {
                    "diff_total_changes": (
                        diff_result.get("summary", {}).get("total_changes", 0)
                        if isinstance(diff_result, dict) else 0
                    ),
                }
                if metadata:
                    merged_meta.update(metadata)
                record = GrowthAuditRecord.from_diff(
                    diff_result,
                    identity_id=fid,
                    source=source,
                    metadata=merged_meta,
                )
            # 4) append
            self._append_record(record)
            self._last_error = None
            return record
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"record_snapshot_failed: {exc}"
            logger.warning("AuditChain.record_snapshot 失败: %s", exc)
            return None

    def _append_record(self, record: GrowthAuditRecord) -> None:
        if record is None:
            return
        self._records.append(record)
        self._record_count += 1
        while len(self._records) > self._max_records:
            self._records.pop(0)

    # --------------------------------------------------------
    # 查询
    # --------------------------------------------------------
    def get_record(self, record_id: str) -> Optional[GrowthAuditRecord]:
        for r in self._records:
            if r.record_id == record_id:
                return r
        return None

    def records(
        self,
        limit: Optional[int] = None,
        severity: Optional[str] = None,
        category: Optional[str] = None,
        identity_id: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[GrowthAuditRecord]:
        """按条件过滤审计 record。"""
        result: List[GrowthAuditRecord] = []
        for r in self._records:
            if identity_id and r.identity_id != identity_id:
                continue
            if severity and r.severity != severity:
                continue
            if category and category not in r.categories:
                continue
            if since and r.timestamp and r.timestamp < since:
                continue
            if until and r.timestamp and r.timestamp > until:
                continue
            result.append(r)
        if limit is not None and limit > 0:
            result = result[-limit:]
        return result

    # --------------------------------------------------------
    # Diff / Snapshot 便利方法
    # --------------------------------------------------------
    def diff(
        self,
        identity_id: str,
        from_version: int,
        to_version: int,
    ) -> Optional[Dict[str, Any]]:
        a = self.history_store.get_by_version(identity_id, from_version)
        b = self.history_store.get_by_version(identity_id, to_version)
        if a is None or b is None:
            return None
        return self.diff_engine.diff(a, b, identity_id=identity_id)

    def diff_latest(self, identity_id: str) -> Optional[Dict[str, Any]]:
        """比较 latest 跟 latest-1。"""
        bucket = self.history_store.get(identity_id)
        if len(bucket) < 2:
            return None
        a = bucket[-2]
        b = bucket[-1]
        return self.diff_engine.diff(a, b, identity_id=identity_id)

    def get_snapshot(
        self, identity_id: str, version: Optional[int] = None,
    ) -> Any:
        if version is None:
            return self.history_store.latest(identity_id)
        return self.history_store.get_by_version(identity_id, version)

    def latest_snapshot(self, identity_id: str) -> Any:
        return self.history_store.latest(identity_id)

    def snapshot_history(self, identity_id: str) -> List[Any]:
        return self.history_store.get(identity_id)

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------
    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def records_total(self) -> int:
        return len(self._records)

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "schema_version": AUDIT_CHAIN_SCHEMA_VERSION,
            "record_count": self._record_count,
            "current_records": len(self._records),
            "max_records": self._max_records,
            "history_store": self.history_store.health_check(),
            "archive": self.archive.health_check(),
            "diff_engine": self.diff_engine.health_check(),
            "last_error": self._last_error,
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "schema_version": AUDIT_CHAIN_SCHEMA_VERSION,
            "record_count": self._record_count,
            "current_records": len(self._records),
            "max_records": self._max_records,
            "history_store": self.history_store.describe(),
            "archive": self.archive.describe(),
            "diff_engine": self.diff_engine.describe(),
            "last_error": self._last_error,
        }


__all__ = [
    "AuditChain",
    "AUDIT_CHAIN_SCHEMA_VERSION",
]
