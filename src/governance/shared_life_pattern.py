# -*- coding: utf-8 -*-
"""Shared-Life Pattern（v2.0 Phase 2）—— 长期共同生活模式治理层。

语义边界（与任务书 §五对齐，四种记忆绝不混为一谈）：
- Episodic Memory    = 「某一次发生了什么」（MemoryStore，检索可达）
- Shared-Life Pattern = 「多次真实经历形成的长期生活模式」（本模块）
- Relationship Fact  = 「我们之间有什么稳定关系事实」（RelationshipCore）
- Personality Growth = 「经历让我发生了人格变化」（Growth 链，须单独审批）

生命周期（append-only，绝不自动确认/自动永久化）：
    candidate → pending → confirmed → active → superseded / archived

设计原则：
- 复用 fact_candidate 的 append-only + 幂等 + family 投影模式；
- 模式是「证据之上的抽象」，绝不删除/覆盖原始 episodic evidence；
- 出现次数/检索次数都不自动触发确认——确认只经人工治理；
- 每个 pattern 必须可反查 source_memory_ids。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_PATH_LOCKS: Dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()

STATUS_CANDIDATE = "candidate"
STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_ARCHIVED = "archived"
STATUS_REJECTED = "rejected"
VALID_STATUSES = frozenset({
    STATUS_CANDIDATE, STATUS_PENDING, STATUS_CONFIRMED, STATUS_ACTIVE,
    STATUS_SUPERSEDED, STATUS_ARCHIVED, STATUS_REJECTED,
})

REVIEW_CONFIRM = "confirm"
REVIEW_REJECT = "reject"
REVIEW_MODIFY = "modify"
REVIEW_SUPERSEDE = "supersede"
REVIEW_ARCHIVE = "archive"
VALID_REVIEW_DECISIONS = frozenset({
    REVIEW_CONFIRM, REVIEW_REJECT, REVIEW_MODIFY, REVIEW_SUPERSEDE, REVIEW_ARCHIVE,
})

#: 最小出现次数（Pattern Discovery 的 recurrence 门槛，三条件之一）
MIN_OCCURRENCE_FOR_PATTERN = 3
#: 最小时间跨度（天，三条件之二：不是同一天集中爆发）
MIN_SPAN_DAYS_FOR_PATTERN = 2


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


class SharedLifePatternStore:
    """append-only JSONL 存储（data/governance/shared_life_patterns.jsonl）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(
            "data", "governance", "shared_life_patterns.jsonl")
        self._lock = _path_lock(self.path)

    # ============ 读 ============
    def load(self) -> List[Dict[str, Any]]:
        with self._lock:
            if not os.path.exists(self.path):
                return []
            records: List[Dict[str, Any]] = []
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line)
                        except Exception:  # noqa: BLE001
                            continue
                        if isinstance(parsed, dict):
                            records.append(parsed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("SharedLifePatternStore.load 失败（已隔离）: %s", exc)
            return records

    def list_active(self) -> List[Dict[str, Any]]:
        """当前活跃的 confirmed patterns（常驻注入用；≤ 注入上限）。"""
        recs = self.load()
        seen_roots = set()
        out = []
        # 按时间倒序（文件 append 顺序），取每 family 最新状态
        for r in reversed(recs):
            root = str(r.get("pattern_id") or "").split("#")[0]
            if root in seen_roots:
                continue
            seen_roots.add(root)
            if r.get("status") in (STATUS_CONFIRMED, STATUS_ACTIVE):
                out.append(r)
        return list(reversed(out))  # 恢复文件顺序（先确认的在前）

    def get(self, pattern_id: str) -> Optional[Dict[str, Any]]:
        for r in self.load():
            if str(r.get("pattern_id") or "") == pattern_id:
                return r
        return None

    def current_status(self, pattern_id: str) -> str:
        """家族最新有效状态（append-only 下原始记录恒 candidate，取最新后代）。"""
        root = str(pattern_id).split("#")[0]
        latest = None
        for r in self.load():
            if str(r.get("pattern_id") or "").split("#")[0] == root:
                latest = r
        return str(latest.get("status") or STATUS_CANDIDATE) if latest else STATUS_CANDIDATE

    # ============ 写 ============
    def _append(self, record: Dict[str, Any]) -> bool:
        with self._lock:
            try:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("SharedLifePatternStore._append 失败: %s", exc)
                return False

    def submit(
        self,
        *,
        title: str,
        summary: str,
        category: str = "shared_life",
        confidence: float = 0.0,
        occurrence_count: int = 0,
        first_seen: str = "",
        last_seen: str = "",
        source_memory_ids: Optional[List[str]] = None,
        evidence_summary: str = "",
        pattern_id: Optional[str] = None,
    ) -> Optional[str]:
        """提交一条 pattern 候选（默认 candidate；绝不自动确认）。"""
        if not title or not summary:
            return None
        if not source_memory_ids:
            return None
        if not pattern_id:
            pattern_id = f"pat_{datetime.now():%Y%m%d%H%M%S}_{os.urandom(2).hex()}"
        rec = {
            "pattern_id": pattern_id,
            "category": category,
            "title": title,
            "summary": summary,
            "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
            "occurrence_count": int(occurrence_count or 0),
            "first_seen": str(first_seen or ""),
            "last_seen": str(last_seen or ""),
            "source_memory_ids": list(source_memory_ids or []),
            "evidence_summary": str(evidence_summary or ""),
            "status": STATUS_CANDIDATE,
            "created_at": _now(),
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": "",
            "lineage": None,
        }
        return pattern_id if self._append(rec) else None

    def review(
        self,
        pattern_id: str,
        decision: str,
        reviewer: str,
        note: str = "",
        modified_summary: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """人工治理审核（append-only 状态转换；绝不自动确认）。

        幂等：同一 decision 的重复执行不追加记录（终态保护）。
        confirm → confirmed；reject → rejected；archive → archived；
        supersede → superseded；modify → 仍 candidate（等待再审）。
        """
        if decision not in VALID_REVIEW_DECISIONS:
            return None
        existing = self.get(pattern_id)
        if existing is None:
            return None
        # 幂等门：按家族最新状态判断（原始记录恒 candidate，须查最新后代）
        cur = self.current_status(pattern_id)
        if cur == STATUS_REJECTED:
            return {"already": True, "status": cur}
        if cur == STATUS_CONFIRMED and decision == REVIEW_CONFIRM:
            return {"already": True, "status": cur}
        if cur == STATUS_ACTIVE and decision not in (REVIEW_SUPERSEDE, REVIEW_ARCHIVE):
            return {"already": True, "status": cur}
        if cur == STATUS_SUPERSEDED or cur == STATUS_ARCHIVED:
            return {"already": True, "status": cur}

        new_status = {
            REVIEW_CONFIRM: STATUS_CONFIRMED,
            REVIEW_REJECT: STATUS_REJECTED,
            REVIEW_MODIFY: STATUS_CANDIDATE,
            REVIEW_SUPERSEDE: STATUS_SUPERSEDED,
            REVIEW_ARCHIVE: STATUS_ARCHIVED,
        }[decision]
        record = dict(existing)
        record["pattern_id"] = f"{pattern_id}#{_now().replace(':', '').replace('-', '')}_{os.urandom(2).hex()}"
        record["status"] = new_status
        record["reviewed_at"] = _now()
        record["reviewed_by"] = reviewer or "unknown"
        record["review_note"] = note or ""
        record["lineage"] = existing.get("pattern_id")
        if decision == REVIEW_MODIFY and modified_summary:
            record["summary"] = modified_summary
        if decision == REVIEW_CONFIRM:
            record["confirmed_at"] = _now()
            record["confirmed_by"] = reviewer or "unknown"
        return record if self._append(record) else None


__all__ = [
    "SharedLifePatternStore",
    "STATUS_CANDIDATE", "STATUS_PENDING", "STATUS_CONFIRMED", "STATUS_ACTIVE",
    "STATUS_SUPERSEDED", "STATUS_ARCHIVED", "STATUS_REJECTED",
    "VALID_REVIEW_DECISIONS",
    "MIN_OCCURRENCE_FOR_PATTERN", "MIN_SPAN_DAYS_FOR_PATTERN",
]
