# -*- coding: utf-8 -*-
"""
src/runtime/self_model/persistence/evolution_history_store.py

Phase 4.6: EvolutionHistoryStore —— append-only JSONL 风格 history store。

职责:
- 顺序追加 SelfModelEvolutionRecord(每行一个 JSON object)
- 不覆盖旧 history(只 append,从不 truncate)
- 保留 rejected_changes 与原 timestamp(支持 replay)
- 支持按 identity_id 列出 / 取最新 / 计数 / 清点
- corrupted line 隔离:某行解析失败 → 跳过 + 计入 corrupt_count

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 仅依赖 stdlib + evolution_record 同包
- atomic append: 用 "a" 模式 + flush,fsync(尽量)确保落盘
- 异常隔离:任何失败不抛,返回 False / [] / 0,写入 self._last_error
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional


try:
    from src.runtime.self_model.evolution.evolution_record import (
        EvolutionRecord,
    )
    _HAS_EVOLUTION_RECORD = True
except Exception:  # noqa: BLE001
    _HAS_EVOLUTION_RECORD = False
    EvolutionRecord = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)


EVOLUTION_HISTORY_STORE_SCHEMA_VERSION = "1.0"
DEFAULT_EVOLUTION_HISTORY_DIR = "data/evolution_history"
MAX_HISTORY_FILE_BYTES = 64 * 1024 * 1024  # 64 MB safety guard


def _safe_id(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if not s:
        return ""
    out: List[str] = []
    for ch in s:
        if ch.isalnum() or ch in ("_", "-", "."):
            out.append(ch)
        else:
            out.append("_")
    sanitized = "".join(out)
    if not sanitized:
        return ""
    if len(sanitized) > 128:
        sanitized = sanitized[:128]
    return sanitized


def _safe_str(value: Any, max_len: int = 80) -> str:
    try:
        s = str(value)
    except Exception:  # noqa: BLE001
        return ""
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


class EvolutionHistoryStore:
    """EvolutionRecord append-only 历史存储 (Phase 4.6 / v1.0)。

    后端:JSONL 文件 —— <root_dir>/<identity_id>.jsonl

    典型用法:
        store = EvolutionHistoryStore(root_dir="data/evolution_history")
        store.append(record)
        all_records = store.list_history(identity_id)
        latest = store.latest(identity_id)
        n = store.count(identity_id)
    """

    name: str = "evolution_history_store"
    schema_version: str = EVOLUTION_HISTORY_STORE_SCHEMA_VERSION

    def __init__(
        self,
        root_dir: str = DEFAULT_EVOLUTION_HISTORY_DIR,
        max_file_bytes: int = MAX_HISTORY_FILE_BYTES,
        auto_create_dir: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._root = str(root_dir or DEFAULT_EVOLUTION_HISTORY_DIR)
        try:
            self._max_bytes = int(max_file_bytes)
        except (TypeError, ValueError):
            self._max_bytes = MAX_HISTORY_FILE_BYTES
        if self._max_bytes < 1024:
            self._max_bytes = MAX_HISTORY_FILE_BYTES
        self._auto_create = bool(auto_create_dir)

        # 统计
        self._append_count: int = 0
        self._append_failures: int = 0
        self._read_count: int = 0
        self._corrupt_lines: int = 0
        self._last_error: Optional[str] = None
        self._last_appended_id: Optional[str] = None
        self._last_appended_record_id: Optional[str] = None

        if self._auto_create:
            self._safe_mkdir(self._root)

    def _safe_mkdir(self, path: str) -> bool:
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"mkdir_failed: {exc}"
            logger.warning("EvolutionHistoryStore 创建目录失败 %s: %s", path, exc)
            return False

    def _path_for(self, identity_id: str) -> Optional[str]:
        sid = _safe_id(identity_id)
        if not sid:
            return None
        return os.path.join(self._root, f"{sid}.jsonl")

    @property
    def root_dir(self) -> str:
        return self._root

    # --------------------------------------------------------
    # append
    # --------------------------------------------------------
    def append(self, record: Any) -> bool:
        """追加一条 EvolutionRecord。

        接受:
        - EvolutionRecord 实例(优先 to_dict)
        - dict(已序列化的 record)
        - 其它对象(尝试 __dict__ / str)

        返回:True 成功 / False 失败
        """
        identity_id: Optional[str] = None
        if record is None:
            self._append_failures += 1
            self._last_error = "record_is_none"
            return False

        # 提取 identity_id
        try:
            if isinstance(record, dict):
                identity_id = str(record.get("identity_id", "") or "")
            else:
                identity_id = str(getattr(record, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            identity_id = None
        if not identity_id:
            self._append_failures += 1
            self._last_error = "identity_id_missing"
            return False

        path = self._path_for(identity_id)
        if path is None:
            self._append_failures += 1
            self._last_error = "identity_id_unsafe"
            return False

        # 序列化为 dict
        try:
            if isinstance(record, dict):
                payload: Dict[str, Any] = dict(record)
            elif hasattr(record, "to_dict"):
                payload = record.to_dict()
            else:
                # 兜底:用 __dict__
                payload = dict(getattr(record, "__dict__", {}) or {})
        except Exception as exc:  # noqa: BLE001
            self._append_failures += 1
            self._last_error = f"serialize_failed: {exc}"
            logger.warning("EvolutionHistoryStore 序列化失败: %s", exc)
            return False

        if not isinstance(payload, dict):
            self._append_failures += 1
            self._last_error = "payload_not_dict"
            return False

        # 注入 schema_version(用于 replay 兼容)
        payload.setdefault("schema_version", EVOLUTION_HISTORY_STORE_SCHEMA_VERSION)

        try:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            self._append_failures += 1
            self._last_error = f"json_dumps_failed: {exc}"
            logger.warning("EvolutionHistoryStore json 序列化失败: %s", exc)
            return False

        # size check(粗略)
        encoded = (text + "\n").encode("utf-8")
        if len(encoded) > self._max_bytes // 4:
            # 单条大于 max/4 直接拒绝(避免塞爆)
            self._append_failures += 1
            self._last_error = f"record_too_large:{len(encoded)}"
            return False

        with self._lock:
            try:
                if self._auto_create:
                    self._safe_mkdir(self._root)
                with open(path, "ab") as f:
                    f.write(encoded)
                    try:
                        f.flush()
                        os.fsync(f.fileno())
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001
                self._append_failures += 1
                self._last_error = f"write_failed: {exc}"
                logger.warning("EvolutionHistoryStore 写盘失败: %s", exc)
                return False

        self._append_count += 1
        self._last_appended_id = identity_id
        try:
            self._last_appended_record_id = str(
                payload.get("record_id", "") or "",
            ) or None
        except Exception:  # noqa: BLE001
            self._last_appended_record_id = None
        self._last_error = None
        return True

    # --------------------------------------------------------
    # list / latest / count
    # --------------------------------------------------------
    def list_history(
        self,
        identity_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """读取 identity_id 的所有 record(按文件顺序)。

        参数:
        - limit: 最多返回多少条(None 表示全部)
        返回:list[dict] —— corrupted line 会被跳过 + 计入 _corrupt_lines
        """
        path = self._path_for(identity_id)
        if path is None:
            return []
        out: List[Dict[str, Any]] = []
        with self._lock:
            try:
                if not os.path.exists(path):
                    return out
                size = os.path.getsize(path)
                if size > self._max_bytes:
                    self._last_error = f"file_too_large:{size}"
                    return out
                with open(path, "rb") as f:
                    raw = f.read()
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"read_failed: {exc}"
                return out

        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"decode_failed: {exc}"
            return out

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception as exc:  # noqa: BLE001
                self._corrupt_lines += 1
                logger.debug(
                    "EvolutionHistoryStore 跳过坏行 %s:%d (%s)",
                    path, line_no, _safe_str(exc),
                )
                continue
            if isinstance(obj, dict):
                out.append(obj)
            else:
                self._corrupt_lines += 1

        self._read_count += 1
        self._last_error = None
        if limit is not None:
            try:
                lim = int(limit)
            except (TypeError, ValueError):
                lim = 0
            if lim >= 0:
                return out[-lim:] if lim > 0 else []
        return out

    def latest(self, identity_id: str) -> Optional[Dict[str, Any]]:
        """读取最后一条 record。"""
        all_recs = self.list_history(identity_id)
        if not all_recs:
            return None
        return all_recs[-1]

    def count(self, identity_id: str) -> int:
        """计数(实际遍历 — 不维护计数器,保证 correctness)。"""
        path = self._path_for(identity_id)
        if path is None:
            return 0
        with self._lock:
            try:
                if not os.path.exists(path):
                    return 0
                size = os.path.getsize(path)
                if size > self._max_bytes:
                    return 0
                with open(path, "rb") as f:
                    raw = f.read()
            except Exception:  # noqa: BLE001
                return 0
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return 0
        n = 0
        for line in text.splitlines():
            if line.strip():
                n += 1
        return n

    def exists(self, identity_id: str) -> bool:
        path = self._path_for(identity_id)
        if path is None:
            return False
        try:
            return os.path.exists(path)
        except Exception:  # noqa: BLE001
            return False

    def list_identities(self) -> List[str]:
        out: List[str] = []
        try:
            if not os.path.isdir(self._root):
                return out
            for name in os.listdir(self._root):
                if not name.endswith(".jsonl"):
                    continue
                base = name[:-6]
                if base:
                    out.append(base)
        except Exception:  # noqa: BLE001
            return out
        return sorted(out)

    # --------------------------------------------------------
    # replay —— 不重新执行,仅返回 record 序列(由调用方决定)
    # --------------------------------------------------------
    def replay(
        self,
        identity_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """返回 history 序列(供外部做 replay / audit / 调试)。

        与 list_history 的差别:replay 是面向"重放"语义的别名。
        """
        return self.list_history(identity_id, limit=limit)

    # --------------------------------------------------------
    # 健康检查
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "root_dir": self._root,
            "max_file_bytes": self._max_bytes,
            "append_count": self._append_count,
            "append_failures": self._append_failures,
            "read_count": self._read_count,
            "corrupt_lines": self._corrupt_lines,
            "last_error": self._last_error,
            "last_appended_id": self._last_appended_id,
            "last_appended_record_id": self._last_appended_record_id,
        }

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def append_count(self) -> int:
        return self._append_count


__all__ = [
    "EvolutionHistoryStore",
    "EVOLUTION_HISTORY_STORE_SCHEMA_VERSION",
    "DEFAULT_EVOLUTION_HISTORY_DIR",
    "MAX_HISTORY_FILE_BYTES",
]
