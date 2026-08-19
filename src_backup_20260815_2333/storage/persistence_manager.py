"""
Phase 3.5.22: Persistence Layer

PersistenceManager 目标：
- 统一管理核心长期状态的持久化与恢复
- append-only（事件/记录日志不覆盖）
- 数据版本控制 + schema migration
- 自动恢复（从 log 重建 snapshot）
- snapshot（快速加载）
- backup（可离线归档）
- corruption detection（hash chain）

注意：
- 本模块只提供“底座能力”，不强制要求所有旧模块立刻迁移
- 不删除历史数据，不改 Persona，不自动接受 proposal
"""

from __future__ import annotations

import json
import os
import shutil
import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.identity.user_context import UserContext


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class PersistenceCorruption:
    entity: str
    log_path: str
    line_index: int
    reason: str


class PersistenceManager:
    """
    统一持久化管理器（append-only log + snapshot）。

    目录结构：
    data/persistence/
      global/<entity>/
        log.jsonl
        snapshot.json
        backups/
      users/<platform>_<user_id>/<entity>/
        log.jsonl
        snapshot.json
        backups/
    """

    CURRENT_SCHEMA_VERSION = 1

    def __init__(self, base_dir: str = "data/persistence", user_context: Optional[UserContext] = None):
        self.base_dir = Path(base_dir)
        self.user_context = user_context
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._migrations: Dict[str, Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]]] = {}

    # ===================== dir helpers =====================

    def _entity_dir(self, entity: str) -> Path:
        if self.user_context:
            user_key = f"{self.user_context.platform}_{self.user_context.user_id}"
            return self.base_dir / "users" / user_key / entity
        return self.base_dir / "global" / entity

    def _log_path(self, entity: str) -> Path:
        return self._entity_dir(entity) / "log.jsonl"

    def _snapshot_path(self, entity: str) -> Path:
        return self._entity_dir(entity) / "snapshot.json"

    def _backups_dir(self, entity: str) -> Path:
        return self._entity_dir(entity) / "backups"

    def register_migration(self, entity: str, from_version: int, fn: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
        self._migrations.setdefault(entity, {})[from_version] = fn

    # ===================== append-only log =====================

    def append(
        self,
        entity: str,
        data: Dict[str, Any],
        *,
        record_type: str = "upsert",
        schema_version: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        追加一条记录到 log（append-only）。
        """
        schema_version = int(self.CURRENT_SCHEMA_VERSION if schema_version is None else schema_version)
        ed = self._entity_dir(entity)
        ed.mkdir(parents=True, exist_ok=True)

        log_path = self._log_path(entity)
        prev_hash = self._read_last_hash(log_path)
        payload = {
            "ts": _now_iso(),
            "entity": entity,
            "record_type": record_type,
            "schema_version": schema_version,
            "metadata": metadata or {},
            "data": data,
            "prev_hash": prev_hash,
        }
        payload_hash = _sha256(_stable_json(payload))
        payload["hash"] = payload_hash

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_stable_json(payload) + "\n")

        # 快速更新 snapshot（best-effort，不影响 append 成功）
        try:
            self._apply_to_snapshot(entity, payload)
        except Exception:
            pass
        return payload

    def _read_last_hash(self, log_path: Path) -> str:
        if not log_path.exists():
            return ""
        try:
            with open(log_path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size == 0:
                    return ""
                # 从尾部向前找最后一行
                step = min(4096, size)
                f.seek(-step, os.SEEK_END)
                tail = f.read().decode("utf-8", errors="ignore")
            lines = [ln for ln in tail.splitlines() if ln.strip()]
            if not lines:
                return ""
            last = json.loads(lines[-1])
            return str(last.get("hash", "") or "")
        except Exception:
            return ""

    # ===================== snapshot =====================

    def load_snapshot(self, entity: str) -> Optional[Dict[str, Any]]:
        sp = self._snapshot_path(entity)
        if not sp.exists():
            return None
        try:
            with open(sp, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def rebuild_snapshot(self, entity: str) -> Tuple[Dict[str, Any], List[PersistenceCorruption]]:
        """
        从 log 重建 snapshot，并返回 corruption 列表。
        """
        log_path = self._log_path(entity)
        ed = self._entity_dir(entity)
        ed.mkdir(parents=True, exist_ok=True)
        corruptions: List[PersistenceCorruption] = []

        state = {
            "schema_version": self.CURRENT_SCHEMA_VERSION,
            "entity": entity,
            "generated_at": _now_iso(),
            "items": [],
            "meta": {},
        }

        prev_hash = ""
        if log_path.exists():
            with open(log_path, "r", encoding="utf-8") as f:
                for idx, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception as e:
                        corruptions.append(PersistenceCorruption(entity, str(log_path), idx, f"json_parse_error:{e}"))
                        break
                    if str(rec.get("prev_hash", "")) != prev_hash:
                        corruptions.append(PersistenceCorruption(entity, str(log_path), idx, "prev_hash_mismatch"))
                        break
                    expected = rec.get("hash", "")
                    rec_no_hash = dict(rec)
                    rec_no_hash.pop("hash", None)
                    calc = _sha256(_stable_json(rec_no_hash))
                    if expected != calc:
                        corruptions.append(PersistenceCorruption(entity, str(log_path), idx, "hash_mismatch"))
                        break
                    prev_hash = expected
                    # migrate record data if needed
                    rec = self._migrate_record(entity, rec)
                    self._apply_record_to_state(state, rec)

        sp = self._snapshot_path(entity)
        tmp = sp.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        tmp.replace(sp)

        # 若有损坏：保留原 log，另外复制一份标记文件（不删除历史）
        if corruptions:
            marker = ed / f"corruption_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            with open(marker, "w", encoding="utf-8") as f:
                json.dump([c.__dict__ for c in corruptions], f, ensure_ascii=False, indent=2)

        return state, corruptions

    def _migrate_record(self, entity: str, rec: Dict[str, Any]) -> Dict[str, Any]:
        sv = int(rec.get("schema_version", 0) or 0)
        while sv < self.CURRENT_SCHEMA_VERSION:
            fn = self._migrations.get(entity, {}).get(sv)
            if not fn:
                break
            try:
                rec["data"] = fn(rec.get("data", {}) or {})
                sv += 1
                rec["schema_version"] = sv
            except Exception:
                break
        return rec

    def _apply_record_to_state(self, state: Dict[str, Any], rec: Dict[str, Any]) -> None:
        # 默认：append items（兼容 list 型实体）
        if rec.get("record_type") == "snapshot":
            state["items"] = rec.get("data", [])
            return
        state["items"].append(rec.get("data", {}))

    def _apply_to_snapshot(self, entity: str, rec: Dict[str, Any]) -> None:
        sp = self._snapshot_path(entity)
        current = self.load_snapshot(entity)
        if not current:
            current = {
                "schema_version": self.CURRENT_SCHEMA_VERSION,
                "entity": entity,
                "generated_at": _now_iso(),
                "items": [],
                "meta": {},
            }
        current["generated_at"] = _now_iso()
        rec2 = self._migrate_record(entity, dict(rec))
        self._apply_record_to_state(current, rec2)
        tmp = sp.with_suffix(".tmp")
        sp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        tmp.replace(sp)

    # ===================== backup =====================

    def backup_entity(self, entity: str) -> str:
        """
        对单个 entity 做备份：复制 log + snapshot 到 backups/<timestamp>/。
        """
        bd = self._backups_dir(entity)
        bd.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dst = bd / stamp
        dst.mkdir(parents=True, exist_ok=True)

        for p in (self._log_path(entity), self._snapshot_path(entity)):
            if p.exists():
                shutil.copy2(p, dst / p.name)
        return str(dst)

    def restore_entity_from_backup(self, entity: str, backup_dir: str) -> bool:
        """
        从备份恢复（会覆盖 snapshot 文件，但不会覆盖 log；log 若存在则保留并以 .restored 形式另存）。
        """
        src = Path(backup_dir)
        if not src.exists():
            return False
        ed = self._entity_dir(entity)
        ed.mkdir(parents=True, exist_ok=True)

        log_src = src / "log.jsonl"
        snap_src = src / "snapshot.json"
        if log_src.exists():
            # 不覆盖当前 log，保留现有历史
            restored = ed / f"log.restored.{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jsonl"
            shutil.copy2(log_src, restored)
        if snap_src.exists():
            shutil.copy2(snap_src, self._snapshot_path(entity))
        return True

    # ===================== convenience =====================

    def ensure_entity_ready(self, entity: str) -> Dict[str, Any]:
        """
        确保 snapshot 可用：若缺失或无法解析，则重建。
        """
        sp = self.load_snapshot(entity)
        if sp is not None:
            return sp
        state, _ = self.rebuild_snapshot(entity)
        return state
