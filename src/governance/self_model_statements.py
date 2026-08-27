# -*- coding: utf-8 -*-
"""Self Model Statements（v1.5.5 Governance C2-c）—— 羽依自己表达的结构化保存。

与 data/self_model.json（系统计算 traits/narratives 层）并行：
    羽依自己说过的 → stated statements（yui_stated）→ 治理 → confirmed → 渲染优先

设计原则：
- yui_stated 是羽依自己的声音，但仍必须经治理才能成为 confirmed；
- "羽依说过" ≠ "系统永久认定这是羽依的核心自我"；
- append-only：review 追加状态转换，不覆盖；
- 与 FactCandidateStore 同构（复用状态机语义），独立存储路径。
"""
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
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_HELD = "held"
STATUS_SUPERSEDED = "superseded"
VALID_STATUSES = frozenset({STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_REJECTED, STATUS_HELD, STATUS_SUPERSEDED})

SOURCE_USER_CONFIRMED = "user_confirmed"
SOURCE_YUI_STATED = "yui_stated"
SOURCE_HISTORICAL_FACT = "historical_fact"
SOURCE_RELATIONAL_AGREEMENT = "relational_agreement"
SOURCE_SYSTEM_OBSERVED = "system_observed"
VALID_SOURCE_TYPES = frozenset({SOURCE_USER_CONFIRMED, SOURCE_YUI_STATED,
                                SOURCE_HISTORICAL_FACT, SOURCE_RELATIONAL_AGREEMENT,
                                SOURCE_SYSTEM_OBSERVED})


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _base_id(sid: Optional[str]) -> str:
    """去掉 #后缀 的 base id（语句族标识）。"""
    if not sid:
        return ""
    return str(sid).split("#")[0]


def _family_state(statement_id: str, records: List[Dict[str, Any]]) -> str:
    """语句族当前有效状态（confirmed/rejected/held/candidate）。"""
    base = _base_id(statement_id)
    ordered = sorted(
        [r for r in records if _base_id(r.get("statement_id") or "") == base or
         _base_id(r.get("lineage") or "") == base or
         _base_id(r.get("modified_from") or "") == base],
        key=lambda r: str(r.get("reviewed_at") or r.get("created_at") or ""),
    )
    state = STATUS_CANDIDATE
    for r in ordered:
        st = r.get("status")
        if st in (STATUS_CONFIRMED, STATUS_REJECTED, STATUS_HELD):
            state = st
    return state


def _path_lock(path: str) -> threading.RLock:
    key = os.path.abspath(str(path))
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _backup_corrupt(path: str) -> None:
    try:
        if os.path.exists(path):
            backup = f"{path}.corrupt.{datetime.now():%Y%m%dT%H%M%S%f}"
            import shutil
            shutil.copy2(path, backup)
            logger.warning("SelfModelStatementsStore: 损坏文件已备份为 %s", backup)
    except Exception:  # noqa: BLE001
        pass


class SelfModelStatementsStore:
    """stated statements append-only JSONL（路径 data/self_model_statements.jsonl）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join("data", "self_model_statements.jsonl")
        self._lock = _path_lock(self.path)

    def load(self) -> List[Dict[str, Any]]:
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
            if raw_lines and not records:
                _backup_corrupt(self.path)
            return records

    def get(self, statement_id: str) -> Optional[Dict[str, Any]]:
        for r in self.load():
            if r.get("statement_id") == statement_id:
                return r
        return None

    def confirmed_statements(self) -> List[Dict[str, Any]]:
        """confirmed 且未被 superseded 的 statements（供渲染层使用）。

        判定：status==confirmed 且该语句族（base id）没有对应的 superseded 记录
        （supersede 是追加新记录，被替换的旧族会有一条 status=superseded 记录）。
        """
        all_recs = self.load()
        superseded_bases = {
            _base_id(r.get("statement_id"))
            for r in all_recs if r.get("status") == STATUS_SUPERSEDED
        }
        out = []
        for r in all_recs:
            if r.get("status") == STATUS_CONFIRMED and _base_id(r.get("statement_id")) not in superseded_bases:
                out.append(r)
        return out

    def save(self, statement: Dict[str, Any]) -> bool:
        if not isinstance(statement, dict):
            return False
        sid = str(statement.get("statement_id") or "")
        if not sid:
            return False
        with self._lock:
            try:
                if self.get(sid) is not None:
                    return False
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(statement, ensure_ascii=False, default=str) + "\n")
                    f.flush()
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning("SelfModelStatementsStore.save 失败: %s", exc)
                return False

    def review(self, statement_id: str, decision: str, reviewer: str, note: str = "",
               modified_fact: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """治理审核：追加状态转换记录（append-only）。decision: confirm/reject/hold/modify。"""
        if decision not in ("confirm", "reject", "hold", "modify"):
            return None
        existing = self.get(statement_id)
        if existing is None:
            return None
        # 幂等（GOV-006）：该族已有 active confirmed 或 rejected/held 终态 → already
        family = _family_state(statement_id, self.load())
        if family == STATUS_CONFIRMED and decision == "confirm":
            return {"already": True, "status": STATUS_CONFIRMED}
        if family in (STATUS_REJECTED, STATUS_HELD):
            return {"already": True, "status": family}
        now = _now()
        new_status = {
            "confirm": STATUS_CONFIRMED, "reject": STATUS_REJECTED,
            "hold": STATUS_HELD, "modify": STATUS_CANDIDATE,
        }[decision]
        record = dict(existing)
        record["statement_id"] = f"{statement_id}#{now.replace(':', '').replace('-', '')}_{os.urandom(2).hex()}"
        record["status"] = new_status
        record["reviewed_at"] = now
        record["reviewed_by"] = reviewer or "unknown"
        record["review_note"] = note or ""
        record["lineage"] = existing.get("statement_id")
        if decision == "modify" and modified_fact:
            record["fact"] = modified_fact
            record["modified_from"] = existing.get("statement_id")
        if decision == "confirm":
            record["confirmed_at"] = now
            record["confirmed_by"] = reviewer or "unknown"
        return record if self.save(record) else None

    def supersede(self, statement_id: str, new_statement_id: str, reviewer: str, note: str = "") -> bool:
        """冲突解决：旧 statement 追加 superseded 记录，指向新语句的 confirmed 记录。

        superseded_by 指向新语句族的最新确认记录 id（而非原始 candidate id），
        保证 confirmed_statements() 的排除判定按 base 族匹配正确。
        """
        old = self.get(statement_id)
        if old is None:
            return False
        # 解析新语句族的 confirmed 记录（该族最新）
        target = new_statement_id
        base_new = _base_id(new_statement_id)
        for r in self.load():
            if _base_id(r.get("statement_id")) == base_new and r.get("status") == STATUS_CONFIRMED:
                target = r.get("statement_id")  # 取 confirmed 记录 id
        now = _now()
        old_rec = dict(old)
        old_rec["statement_id"] = f"{statement_id}#s{now.replace(':', '').replace('-', '')}_{os.urandom(2).hex()}"
        old_rec["status"] = STATUS_SUPERSEDED
        old_rec["superseded_by"] = target
        old_rec["reviewed_at"] = now
        old_rec["reviewed_by"] = reviewer or "unknown"
        old_rec["review_note"] = note or "superseded"
        return self.save(old_rec)

    def submit(self, fact: str, category: str, source_type: str, source_memory_ids: List[str],
               evidence_summary: str, confidence: float, statement_id: Optional[str] = None) -> Optional[str]:
        """提交羽依的自我表达候选。yui_stated 优先；system_observed 永不自动 confirmed。"""
        if source_type not in VALID_SOURCE_TYPES:
            return None
        if not fact or not fact.strip():
            return None
        confidence = max(0.0, min(1.0, float(confidence or 0.0)))
        if not source_memory_ids and not evidence_summary:
            return None
        if not statement_id:
            statement_id = f"sm_{datetime.now():%Y%m%d%H%M%S}_{os.urandom(2).hex()}"
        rec = {
            "statement_id": statement_id,
            "fact": fact.strip(),
            "category": category or "",
            "source_type": source_type,
            "source_memory_ids": list(source_memory_ids or []),
            "evidence_summary": evidence_summary or "",
            "confidence": confidence,
            "status": STATUS_CANDIDATE,
            "created_at": _now(),
            "reviewed_at": None, "reviewed_by": None, "review_note": "",
            "confirmed_at": None, "confirmed_by": None,
            "superseded_by": None, "modified_from": None, "lineage": None,
        }
        return statement_id if self.save(rec) else None
