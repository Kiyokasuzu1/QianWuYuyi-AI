# -*- coding: utf-8 -*-
"""
src/runtime/self_model/persistence/self_model_store.py

Phase 4.6: SelfModelStore —— SelfModelSnapshot 持久化层。

职责:
- 把 SelfModelSnapshot 原子写入磁盘(JSON 后端,默认目录 data/self_model/)
- 支持 load(identity_id) / save(snapshot) / exists / delete
- corrupted file 隔离:解析失败不抛,返回 None + 写入 self._last_error
- version / schema 检查:与 SelfModelSnapshot.schema_version 兼容
- 健康检查 + 反向校验(validate)

约束:
- 不 import openai / qwen / llava / anthropic / google.generativeai
- 不 import src.personality.*
- 仅依赖 stdlib + 同包 self_model_data
- atomic write: 写到 <id>.json.tmp, fsync, os.replace
- 异常隔离:任何 IO / 解析失败返回 None 或 False,写入 self._last_error
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

try:
    from src.runtime.self_model.self_model_data import (
        SelfModelSnapshot,
        SELF_MODEL_FOUNDATION_SCHEMA_VERSION,
    )
    _HAS_SNAPSHOT = True
except Exception:  # noqa: BLE001
    _HAS_SNAPSHOT = False
    SelfModelSnapshot = None  # type: ignore[assignment]
    SELF_MODEL_FOUNDATION_SCHEMA_VERSION = "1.0"


logger = logging.getLogger(__name__)


SELF_MODEL_STORE_SCHEMA_VERSION = "1.0"
DEFAULT_SELF_MODEL_DIR = "data/self_model"
MAX_SNAPSHOT_FILE_BYTES = 8 * 1024 * 1024  # 8 MB safety guard


# 允许的 schema 兼容集合(主版本号必须匹配,次版本向后兼容)
_COMPATIBLE_FOUNDATION_VERSIONS = frozenset({"1.0"})


def _safe_id(s: str) -> str:
    """sanitize identity_id 防止目录穿越。"""
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if not s:
        return ""
    # 只允许 [A-Za-z0-9_.-],其余替换为 _
    out_chars: List[str] = []
    for ch in s:
        if ch.isalnum() or ch in ("_", "-", "."):
            out_chars.append(ch)
        else:
            out_chars.append("_")
    sanitized = "".join(out_chars)
    if not sanitized:
        return ""
    if len(sanitized) > 128:
        sanitized = sanitized[:128]
    return sanitized


def _now_iso() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except (AttributeError, TypeError):  # pragma: no cover
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"


class SelfModelStore:
    """SelfModelSnapshot 持久化层 (Phase 4.6 / v1.0)。

    典型用法:
        store = SelfModelStore(root_dir="data/self_model")
        store.save(snapshot)               # 原子写
        snap = store.load(identity_id)     # 反序列化
        assert store.exists(identity_id)

    后端:JSON 文件 —— <root_dir>/<identity_id>.json
    """

    name: str = "self_model_store"
    schema_version: str = SELF_MODEL_STORE_SCHEMA_VERSION

    def __init__(
        self,
        root_dir: str = DEFAULT_SELF_MODEL_DIR,
        max_file_bytes: int = MAX_SNAPSHOT_FILE_BYTES,
        auto_create_dir: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._root = str(root_dir or DEFAULT_SELF_MODEL_DIR)
        try:
            self._max_bytes = int(max_file_bytes)
        except (TypeError, ValueError):
            self._max_bytes = MAX_SNAPSHOT_FILE_BYTES
        if self._max_bytes < 1024:
            self._max_bytes = MAX_SNAPSHOT_FILE_BYTES
        self._auto_create = bool(auto_create_dir)

        # 内部统计(只读 health_check 可读)
        self._save_count: int = 0
        self._load_count: int = 0
        self._delete_count: int = 0
        self._save_failures: int = 0
        self._load_failures: int = 0
        self._last_error: Optional[str] = None
        self._last_loaded_id: Optional[str] = None
        self._last_saved_id: Optional[str] = None

        if self._auto_create:
            self._safe_mkdir(self._root)

    # --------------------------------------------------------
    # 路径与目录
    # --------------------------------------------------------
    def _safe_mkdir(self, path: str) -> bool:
        try:
            os.makedirs(path, exist_ok=True)
            return True
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"mkdir_failed: {exc}"
            logger.warning("SelfModelStore 创建目录失败 %s: %s", path, exc)
            return False

    def _path_for(self, identity_id: str) -> Optional[str]:
        sid = _safe_id(identity_id)
        if not sid:
            return None
        return os.path.join(self._root, f"{sid}.json")

    @property
    def root_dir(self) -> str:
        return self._root

    # --------------------------------------------------------
    # validate
    # --------------------------------------------------------
    def validate(self, snapshot: Any) -> bool:
        """轻量校验:snapshot 必填字段是否完整。"""
        if snapshot is None:
            return False
        if not _HAS_SNAPSHOT or SelfModelSnapshot is None:
            # 无法校验结构 — 至少看 to_dict 存在
            return hasattr(snapshot, "to_dict")
        if not isinstance(snapshot, SelfModelSnapshot):
            return False
        try:
            iid = str(getattr(snapshot, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            iid = ""
        if not iid:
            return False
        try:
            sv = str(getattr(snapshot, "schema_version", "") or "")
        except Exception:  # noqa: BLE001
            sv = ""
        if sv and sv not in _COMPATIBLE_FOUNDATION_VERSIONS:
            return False
        return True

    # --------------------------------------------------------
    # save
    # --------------------------------------------------------
    def save(self, snapshot: Any) -> bool:
        """原子保存 snapshot。

        流程:
        1) 校验 snapshot 合法
        2) 写到 <id>.json.tmp
        3) flush + fsync
        4) os.replace → 原子替换到 <id>.json
        5) 任何异常 → 返回 False, 不抛
        """
        if not self.validate(snapshot):
            self._save_failures += 1
            self._last_error = "validate_failed"
            return False
        try:
            identity_id = str(getattr(snapshot, "identity_id", "") or "")
        except Exception:  # noqa: BLE001
            self._save_failures += 1
            self._last_error = "identity_id_invalid"
            return False
        path = self._path_for(identity_id)
        if path is None:
            self._save_failures += 1
            self._last_error = "identity_id_unsafe"
            return False

        # 序列化
        try:
            payload = snapshot.to_dict() if hasattr(snapshot, "to_dict") else None
        except Exception as exc:  # noqa: BLE001
            self._save_failures += 1
            self._last_error = f"to_dict_failed: {exc}"
            logger.warning("SelfModelStore.to_dict 失败: %s", exc)
            return False
        if not isinstance(payload, dict):
            self._save_failures += 1
            self._last_error = "to_dict_not_dict"
            return False

        envelope: Dict[str, Any] = {
            "schema_version": SELF_MODEL_STORE_SCHEMA_VERSION,
            "stored_at": _now_iso(),
            "snapshot": payload,
        }
        try:
            text = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            self._save_failures += 1
            self._last_error = f"json_dumps_failed: {exc}"
            logger.warning("SelfModelStore json 序列化失败: %s", exc)
            return False

        # 字节大小保护
        encoded = text.encode("utf-8")
        if len(encoded) > self._max_bytes:
            self._save_failures += 1
            self._last_error = f"file_too_large:{len(encoded)}"
            logger.warning(
                "SelfModelStore 快照过大: %d > %d",
                len(encoded), self._max_bytes,
            )
            return False

        with self._lock:
            try:
                if self._auto_create:
                    self._safe_mkdir(self._root)
                # 写到临时文件
                dir_name = os.path.dirname(path) or "."
                fd, tmp_path = tempfile.mkstemp(
                    prefix=".sm_", suffix=".tmp", dir=dir_name,
                )
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(encoded)
                        try:
                            f.flush()
                            os.fsync(f.fileno())
                        except Exception:  # noqa: BLE001
                            pass
                    os.replace(tmp_path, path)
                except Exception:
                    # 清理 tmp
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:  # noqa: BLE001
                        pass
                    raise
            except Exception as exc:  # noqa: BLE001
                self._save_failures += 1
                self._last_error = f"write_failed: {exc}"
                logger.warning("SelfModelStore 写盘失败: %s", exc)
                return False

        self._save_count += 1
        self._last_saved_id = identity_id
        self._last_error = None
        return True

    # --------------------------------------------------------
    # load
    # --------------------------------------------------------
    def load(self, identity_id: str) -> Optional[SelfModelSnapshot]:
        """读取 snapshot。失败时返回 None + 设置 last_error。

        corrupted file 隔离:解析失败 / schema 不匹配时
        把坏文件隔离到 <id>.corrupt.json(避免反复触发解析错误)。
        """
        path = self._path_for(identity_id)
        if path is None:
            self._load_failures += 1
            self._last_error = "identity_id_unsafe"
            return None
        with self._lock:
            try:
                if not os.path.exists(path):
                    self._last_error = "not_found"
                    return None
                size = os.path.getsize(path)
                if size > self._max_bytes:
                    self._load_failures += 1
                    self._last_error = f"file_too_large:{size}"
                    return None
                with open(path, "rb") as f:
                    raw = f.read()
            except Exception as exc:  # noqa: BLE001
                self._load_failures += 1
                self._last_error = f"read_failed: {exc}"
                logger.warning("SelfModelStore 读盘失败: %s", exc)
                return None

        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            self._load_failures += 1
            self._last_error = f"decode_failed: {exc}"
            return None

        try:
            obj = json.loads(text)
        except Exception as exc:  # noqa: BLE001
            self._load_failures += 1
            self._last_error = f"json_decode_failed: {exc}"
            # 隔离坏文件
            self._quarantine(path)
            return None

        if not isinstance(obj, dict):
            self._load_failures += 1
            self._last_error = "envelope_not_dict"
            self._quarantine(path)
            return None

        # schema 校验
        sv = str(obj.get("schema_version", "") or "")
        if sv != SELF_MODEL_STORE_SCHEMA_VERSION:
            self._load_failures += 1
            self._last_error = f"schema_mismatch:{sv}"
            self._quarantine(path)
            return None

        snap_data = obj.get("snapshot")
        if not isinstance(snap_data, dict):
            self._load_failures += 1
            self._last_error = "snapshot_payload_invalid"
            self._quarantine(path)
            return None

        if not _HAS_SNAPSHOT or SelfModelSnapshot is None:
            self._load_failures += 1
            self._last_error = "snapshot_class_unavailable"
            return None

        try:
            snap = SelfModelSnapshot.from_dict(snap_data)
        except Exception as exc:  # noqa: BLE001
            self._load_failures += 1
            self._last_error = f"snapshot_from_dict_failed: {exc}"
            self._quarantine(path)
            return None

        self._load_count += 1
        self._last_loaded_id = str(getattr(snap, "identity_id", identity_id))
        self._last_error = None
        return snap

    def _quarantine(self, path: str) -> None:
        """把坏文件重命名,避免反复解析失败。"""
        try:
            target = f"{path}.corrupt"
            if os.path.exists(target):
                # 加随机后缀避免覆盖
                import uuid as _uuid
                target = f"{path}.corrupt.{_uuid.uuid4().hex[:6]}"
            os.replace(path, target)
        except Exception:  # noqa: BLE001
            pass

    # --------------------------------------------------------
    # exists / delete / list
    # --------------------------------------------------------
    def exists(self, identity_id: str) -> bool:
        path = self._path_for(identity_id)
        if path is None:
            return False
        try:
            return os.path.exists(path)
        except Exception:  # noqa: BLE001
            return False

    def delete(self, identity_id: str) -> bool:
        path = self._path_for(identity_id)
        if path is None:
            return False
        with self._lock:
            try:
                if os.path.exists(path):
                    os.remove(path)
                # 也尝试删 corrupt 副本
                for suf in (".corrupt",):
                    p2 = path + suf
                    if os.path.exists(p2):
                        try:
                            os.remove(p2)
                        except Exception:  # noqa: BLE001
                            pass
            except Exception as exc:  # noqa: BLE001
                self._last_error = f"delete_failed: {exc}"
                return False
        self._delete_count += 1
        return True

    def list_identities(self) -> List[str]:
        """列出已存储的 identity_id(过滤 .corrupt / 临时文件)。"""
        out: List[str] = []
        try:
            if not os.path.isdir(self._root):
                return out
            for name in os.listdir(self._root):
                if not name.endswith(".json"):
                    continue
                if name.endswith(".corrupt.json"):
                    continue
                base = name[:-5]  # 去掉 .json
                if base:
                    out.append(base)
        except Exception:  # noqa: BLE001
            return out
        return sorted(out)

    # --------------------------------------------------------
    # 健康检查
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "root_dir": self._root,
            "max_file_bytes": self._max_bytes,
            "save_count": self._save_count,
            "load_count": self._load_count,
            "delete_count": self._delete_count,
            "save_failures": self._save_failures,
            "load_failures": self._load_failures,
            "last_error": self._last_error,
            "last_saved_id": self._last_saved_id,
            "last_loaded_id": self._last_loaded_id,
        }

    # 方便只读属性
    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    @property
    def save_count(self) -> int:
        return self._save_count

    @property
    def load_count(self) -> int:
        return self._load_count

    @property
    def delete_count(self) -> int:
        return self._delete_count


__all__ = [
    "SelfModelStore",
    "SELF_MODEL_STORE_SCHEMA_VERSION",
    "DEFAULT_SELF_MODEL_DIR",
    "MAX_SNAPSHOT_FILE_BYTES",
]
