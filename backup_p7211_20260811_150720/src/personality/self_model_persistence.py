"""
Phase 6.2: SelfModel Persistence Layer

职责：
- SelfBelief / SelfHistory / SelfReflection 的磁盘持久化
- 使用 JSONL 格式（每行一条记录）
- 防止单大文件；支持增量追加和全量恢复

文件布局：
    data/self_model/
        beliefs.jsonl
        history.jsonl
        reflection.jsonl
        meta.json   <- 写入时间戳、版本、计数

约束：
- 写盘失败不应影响内存中数据
- load 失败时返回空 store，不抛异常
- 任何 I/O 错误均被隔离
- 原子写入（写临时文件 + 替换）

设计原则：
- PersistenceManager 不持有数据；只负责序列化和 I/O
- 与 SelfModelAdapter 解耦：adapter 持有 store 实例，仅在初始化时
  调用 persistence.load_state() 恢复
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


# ============================================================
# 路径常量
# ============================================================

DEFAULT_DATA_DIR = "data/self_model"
BELIEFS_FILENAME = "beliefs.jsonl"
HISTORY_FILENAME = "history.jsonl"
REFLECTION_FILENAME = "reflection.jsonl"
META_FILENAME = "meta.json"

META_VERSION = "1.0"
SCHEMA_VERSION_BELIEFS = "1.0"
SCHEMA_VERSION_HISTORY = "1.0"
SCHEMA_VERSION_REFLECTION = "1.0"


# ============================================================
# SelfModelPersistence
# ============================================================

class SelfModelPersistence:
    """
    SelfModel 三个 Store 的持久化管理器。

    接口：
        save_beliefs(store)   → 增量保存 beliefs
        load_beliefs()        → 恢复 SelfBeliefStore
        save_history(history) → 增量保存 history
        load_history()        → 恢复 SelfHistory
        save_reflections(store)
        load_reflections()
        export_snapshot(...)  → 导出完整 snapshot
        restore_from(...)     → 从 snapshot 恢复
        backup()              → 创建 .bak 备份
    """

    def __init__(self, data_dir: Optional[str] = None) -> None:
        self._data_dir = Path(data_dir) if data_dir else Path(DEFAULT_DATA_DIR)
        self._ensure_dir()

    # ============================================================
    # 路径
    # ============================================================

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _path(self, name: str) -> Path:
        return self._data_dir / name

    def _ensure_dir(self) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"SelfModelPersistence: cannot create data dir {self._data_dir}: {e}")

    # ============================================================
    # 通用 JSONL 读写
    # ============================================================

    @staticmethod
    def _write_jsonl(path: Path, items: Iterable[Dict[str, Any]]) -> bool:
        """原子写入 JSONL 文件。"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for item in items:
                    try:
                        f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
                    except Exception as e:
                        logger.warning(f"SelfModelPersistence: skip item due to {e}")
            os.replace(tmp, path)
            return True
        except Exception as e:
            logger.error(f"SelfModelPersistence._write_jsonl failed: {e}")
            return False

    @staticmethod
    def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
        """读取 JSONL 文件，跳过错误行。"""
        if not path.exists():
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception as e:
                        logger.warning(f"SelfModelPersistence: skip bad line: {e}")
        except Exception as e:
            logger.error(f"SelfModelPersistence._read_jsonl failed: {e}")
        return out

    # ============================================================
    # Beliefs
    # ============================================================

    def save_beliefs(self, store: Any) -> bool:
        """保存 SelfBeliefStore（按行写）。"""
        try:
            items = [b.to_dict() for b in store.all()]
            ok = self._write_jsonl(self._path(BELIEFS_FILENAME), items)
            if ok:
                self._update_meta("beliefs", count=len(items))
            return ok
        except Exception as e:
            logger.error(f"save_beliefs failed: {e}")
            return False

    def load_beliefs(self) -> List[Dict[str, Any]]:
        return self._read_jsonl(self._path(BELIEFS_FILENAME))

    # ============================================================
    # History
    # ============================================================

    def save_history(self, history: Any) -> bool:
        try:
            items = [e.to_dict() for e in history.all()]
            ok = self._write_jsonl(self._path(HISTORY_FILENAME), items)
            if ok:
                self._update_meta("history", count=len(items))
            return ok
        except Exception as e:
            logger.error(f"save_history failed: {e}")
            return False

    def load_history(self) -> List[Dict[str, Any]]:
        return self._read_jsonl(self._path(HISTORY_FILENAME))

    # ============================================================
    # Reflections
    # ============================================================

    def save_reflections(self, store: Any) -> bool:
        try:
            items = [n.to_dict() for n in store.all()]
            ok = self._write_jsonl(self._path(REFLECTION_FILENAME), items)
            if ok:
                self._update_meta("reflections", count=len(items))
            return ok
        except Exception as e:
            logger.error(f"save_reflections failed: {e}")
            return False

    def load_reflections(self) -> List[Dict[str, Any]]:
        return self._read_jsonl(self._path(REFLECTION_FILENAME))

    # ============================================================
    # 全量 save / load
    # ============================================================

    def save_all(
        self,
        beliefs_store: Any,
        history: Any,
        reflections_store: Any,
    ) -> Dict[str, bool]:
        return {
            "beliefs": self.save_beliefs(beliefs_store),
            "history": self.save_history(history),
            "reflections": self.save_reflections(reflections_store),
        }

    def load_all(self) -> Dict[str, List[Dict[str, Any]]]:
        return {
            "beliefs": self.load_beliefs(),
            "history": self.load_history(),
            "reflections": self.load_reflections(),
        }

    # ============================================================
    # Snapshot 导出/恢复
    # ============================================================

    def export_snapshot(
        self,
        beliefs_store: Any,
        history: Any,
        reflections_store: Any,
        note: str = "export",
    ) -> Optional[Dict[str, Any]]:
        """导出完整 JSON snapshot（用于外部备份）。"""
        try:
            return {
                "version": META_VERSION,
                "exported_at": _now_iso(),
                "note": note,
                "beliefs": [b.to_dict() for b in beliefs_store.all()],
                "history": [e.to_dict() for e in history.all()],
                "reflections": [n.to_dict() for n in reflections_store.all()],
            }
        except Exception as e:
            logger.error(f"export_snapshot failed: {e}")
            return None

    def write_snapshot_to_file(
        self,
        beliefs_store: Any,
        history: Any,
        reflections_store: Any,
        file_path: str,
        note: str = "export",
    ) -> bool:
        snap = self.export_snapshot(beliefs_store, history, reflections_store, note=note)
        if snap is None:
            return False
        try:
            p = Path(file_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False, indent=2, default=str)
            os.replace(tmp, p)
            return True
        except Exception as e:
            logger.error(f"write_snapshot_to_file failed: {e}")
            return False

    # ============================================================
    # Meta
    # ============================================================

    def _update_meta(self, section: str, count: int) -> None:
        try:
            meta_path = self._path(META_FILENAME)
            meta: Dict[str, Any] = {}
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    meta = {}
            meta.setdefault("version", META_VERSION)
            meta["last_updated"] = _now_iso()
            meta.setdefault(section, {})
            meta[section]["count"] = int(count)
            meta[section]["last_saved"] = _now_iso()
            tmp = meta_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(tmp, meta_path)
        except Exception as e:
            logger.warning(f"_update_meta failed: {e}")

    def get_meta(self) -> Dict[str, Any]:
        meta_path = self._path(META_FILENAME)
        if not meta_path.exists():
            return {}
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # ============================================================
    # Backup
    # ============================================================

    def backup(self, suffix: Optional[str] = None) -> Optional[str]:
        """为所有数据文件创建 .bak 副本；返回 backup_dir。"""
        try:
            ts = suffix or datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_dir = self._data_dir / "backups" / ts
            backup_dir.mkdir(parents=True, exist_ok=True)
            for fname in (BELIEFS_FILENAME, HISTORY_FILENAME, REFLECTION_FILENAME, META_FILENAME):
                src = self._path(fname)
                if src.exists():
                    dst = backup_dir / fname
                    try:
                        with open(src, "rb") as fr, open(dst, "wb") as fw:
                            fw.write(fr.read())
                    except Exception as e:
                        logger.warning(f"backup {fname} failed: {e}")
            return str(backup_dir)
        except Exception as e:
            logger.error(f"backup failed: {e}")
            return None

    # ============================================================
    # 清空
    # ============================================================

    def clear(self) -> bool:
        """清空所有数据文件（仅用于测试）。"""
        try:
            for fname in (BELIEFS_FILENAME, HISTORY_FILENAME, REFLECTION_FILENAME, META_FILENAME):
                p = self._path(fname)
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass
            return True
        except Exception as e:
            logger.error(f"clear failed: {e}")
            return False

    # ============================================================
    # 信息
    # ============================================================

    def get_stats(self) -> Dict[str, Any]:
        """返回数据目录文件统计。"""
        out: Dict[str, Any] = {
            "data_dir": str(self._data_dir),
            "files": {},
        }
        for fname in (BELIEFS_FILENAME, HISTORY_FILENAME, REFLECTION_FILENAME, META_FILENAME):
            p = self._path(fname)
            try:
                if p.exists():
                    out["files"][fname] = {
                        "exists": True,
                        "size_bytes": p.stat().st_size,
                    }
                else:
                    out["files"][fname] = {"exists": False}
            except Exception:
                out["files"][fname] = {"exists": False}
        return out
