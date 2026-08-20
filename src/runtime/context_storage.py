# -*- coding: utf-8 -*-
"""
src/runtime/context_storage.py

Phase 6.1 —— RuntimeContext 持久化适配层。

职责:
    将 RuntimeContext 序列化到 JSON 文件,并支持按 lifecycle_id 恢复、查询、删除。
    本模块作为 Runtime **外部 Adapter** 存在,**不**修改 RuntimeContext 本体。

设计原则:
    1. Persistence 是外部 Adapter:
        - 不修改 RuntimeContext
        - 不修改 LifecycleManager
        - 不引用 Authority (memory / growth / personality / ...)
        - 不引用 EventHub
        - 不调用 LLM
    2. 原子写入: tmp 文件 -> os.replace(原子 rename)
    3. 安全文件名: lifecycle_id 严格白名单 [a-zA-Z0-9_.-]
    4. 容错恢复: 非法 JSON / schema_version 不匹配 -> 返回 None (不抛)
    5. 仅依赖 stdlib + RuntimeContext

存储结构:
    base_dir (默认 "data/runtime_context/") /
        <lifecycle_id>.json
        <lifecycle_id>.json.tmp    (写入中临时)

JSON schema:
    v1.0 信封（lifecycle_context.RuntimeContext）:
        {
            "schema_version": "1.0",
            "saved_at": "<ISO 8601 UTC>",
            "context": { ... context.to_dict() ... }
        }
    v2.0 信封（request_context.RuntimeContext，P2.3-A.2.6 起）:
        同构，schema_version="2.0"，context 为 v2 七层展开 dict。
    load 按信封 schema_version 分派反序列化器：
        "1.0" → lifecycle_context.RuntimeContext.from_dict（旧逻辑不变）
        "2.0" → request_context.RuntimeContext.from_dict
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle_context import RuntimeContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_CONTEXT_STORAGE_SCHEMA_VERSION = "1.0"
# P2.3-A.2.6：新增 "2.0"（request_context v2）支持。信封版本与 context 自身
# schema_version 一致；"1.0" 读路径与既有文件完全不变。
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})

# 默认存储目录
DEFAULT_BASE_DIR = "data/runtime_context"

# 允许的 lifecycle_id 字符集(防 path traversal)
_VALID_LIFECYCLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+$")
MAX_LIFECYCLE_ID_LENGTH = 255

# 异常
class ContextStorageError(Exception):
    """ContextStorage 错误基类。"""


class InvalidLifecycleIdError(ValueError):
    """非法 lifecycle_id 错误。"""


# ============================================================
# 内部工具
# ============================================================
def _now_iso() -> str:
    """返回当前 UTC ISO 8601 时间戳(带 Z)。"""
    try:
        return datetime.utcnow().isoformat() + "Z"
    except Exception:  # noqa: BLE001
        return "1970-01-01T00:00:00Z"


def _validate_lifecycle_id(lifecycle_id: Any) -> str:
    """校验 lifecycle_id 合法性,防止 path traversal。

    规则:
        - 必须是 str
        - 非空
        - 长度 <= MAX_LIFECYCLE_ID_LENGTH
        - 仅允许 [A-Za-z0-9_.-]
        - 不允许 '.' / '..' (路径组件)
        - 不允许路径分隔符
    """
    if not isinstance(lifecycle_id, str):
        raise InvalidLifecycleIdError(
            f"lifecycle_id 必须是 str,实际: {type(lifecycle_id).__name__}"
        )
    if not lifecycle_id or not lifecycle_id.strip():
        raise InvalidLifecycleIdError("lifecycle_id 不能为空")
    if len(lifecycle_id) > MAX_LIFECYCLE_ID_LENGTH:
        raise InvalidLifecycleIdError(
            f"lifecycle_id 超过最大长度 {MAX_LIFECYCLE_ID_LENGTH}"
        )
    if lifecycle_id in (".", ".."):
        raise InvalidLifecycleIdError(
            f"lifecycle_id 不允许是 '.' 或 '..': {lifecycle_id!r}"
        )
    if not _VALID_LIFECYCLE_ID_PATTERN.match(lifecycle_id):
        raise InvalidLifecycleIdError(
            f"lifecycle_id 含非法字符: {lifecycle_id!r} "
            f"(允许: A-Z a-z 0-9 _ . -)"
        )
    return lifecycle_id


def _atomic_write_json(
    target_path: Path,
    payload: Dict[str, Any],
) -> None:
    """原子写入 JSON: tmp -> os.replace。

    1. 写入 target_path 同目录下的 .tmp 文件
    2. fsync
    3. os.replace 原子 rename
    """
    target_dir = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    # 使用 NamedTemporaryFile 在同目录创建 tmp
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=str(target_dir),
    )
    tmp_path = Path(tmp_path_str)
    try:
        # 写入 JSON
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:  # noqa: BLE001
                # fsync 失败不致命(部分 FS 不支持)
                pass
        # 原子 rename
        os.replace(str(tmp_path), str(target_path))
    except Exception:
        # 失败时清理 tmp 文件
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:  # noqa: BLE001
            pass
        raise


# ============================================================
# 主类
# ============================================================
class RuntimeContextStorage:
    """RuntimeContext 持久化适配器。

    用法:
        storage = RuntimeContextStorage()  # 默认 data/runtime_context/
        storage.save(context)              # 保存
        ctx = storage.load("boot_001")     # 恢复
        ok = storage.exists("boot_001")    # 查询
        ok = storage.delete("boot_001")    # 删除

    线程安全: 内部用 RLock 保护文件系统操作。
    """

    def __init__(
        self,
        base_dir: Optional[Any] = None,
        *,
        create_dir: bool = True,
    ) -> None:
        """构造 Storage。

        Args:
            base_dir: 存储根目录。None/缺省 -> "data/runtime_context/"。
            create_dir: 是否自动创建目录(默认 True)。
        """
        if base_dir is None or base_dir == "":
            base_dir = DEFAULT_BASE_DIR
        # 校验 base_dir 不为相对路径穿越(本身应不含 .. 组件)
        bd = Path(base_dir)
        # 解析为绝对路径后校验
        try:
            bd_resolved = bd.resolve()
        except Exception:  # noqa: BLE001
            bd_resolved = bd
        self._base_dir: Path = bd_resolved
        if create_dir:
            try:
                self._base_dir.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ContextStorage] 无法创建目录 %s: %s",
                    self._base_dir, exc,
                )
        self._lock = threading.RLock()

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def base_dir(self) -> Path:
        """存储根目录。"""
        return self._base_dir

    # --------------------------------------------------------
    # save
    # --------------------------------------------------------
    def save(
        self,
        context: "RuntimeContext",
    ) -> Dict[str, Any]:
        """保存 RuntimeContext。

        Args:
            context: RuntimeContext 实例。

        Returns:
            {
                "saved": True,
                "lifecycle_id": "...",
                "path": "...",
                "timestamp": "..."
            }

        Raises:
            ContextStorageError: 序列化失败 / 写入失败。
            InvalidLifecycleIdError: lifecycle_id 非法。
            ValueError: context 不具备受支持 RuntimeContext 能力
                （schema_version ∉ SUPPORTED_SCHEMA_VERSIONS 或无 to_dict）。
        """
        # P2.3-A.2.6：能力判断替代 isinstance 硬门。
        # 契约面 = schema_version ∈ SUPPORTED_SCHEMA_VERSIONS + 可调用 to_dict()；
        # lifecycle v1.0 与 request_context v2 均满足，legacy 行为不变，
        # 其余类型照旧 ValueError（不静默、不猜测）。
        context_schema = getattr(context, "schema_version", None)
        if not (
            isinstance(context_schema, str)
            and context_schema in SUPPORTED_SCHEMA_VERSIONS
            and callable(getattr(context, "to_dict", None))
        ):
            raise ValueError(
                f"context 必须是受支持的 RuntimeContext "
                f"(schema_version ∈ {sorted(SUPPORTED_SCHEMA_VERSIONS)})，"
                f"实际: {type(context).__name__}"
            )

        lifecycle_id = _validate_lifecycle_id(context.lifecycle_id)
        timestamp = _now_iso()
        target_path = self._resolve_path(lifecycle_id)

        # 1) context -> dict
        try:
            context_dict = context.to_dict()
        except Exception as exc:  # noqa: BLE001
            raise ContextStorageError(
                f"context.to_dict() 失败: {exc}"
            ) from exc

        # 2) 构造 payload（信封版本与 context 自身 schema_version 一致：
        #    v1 文件字节级不变，v2 使用 "2.0" 信封，load 侧按信封分派）
        payload: Dict[str, Any] = {
            "schema_version": context_schema,
            "saved_at": timestamp,
            "context": context_dict,
        }

        # 3) 原子写入
        with self._lock:
            try:
                _atomic_write_json(target_path, payload)
            except Exception as exc:  # noqa: BLE001
                raise ContextStorageError(
                    f"写入 {target_path} 失败: {exc}"
                ) from exc

        return {
            "saved": True,
            "lifecycle_id": lifecycle_id,
            "path": str(target_path),
            "timestamp": timestamp,
        }

    # --------------------------------------------------------
    # load
    # --------------------------------------------------------
    def load(
        self,
        lifecycle_id: str,
    ) -> Optional["RuntimeContext"]:
        """根据 lifecycle_id 恢复 Context。

        异常/失败时返回 None (不抛),便于上层用 exists() 区分。

        Args:
            lifecycle_id: 之前保存时使用的 id。

        Returns:
            RuntimeContext 实例,或 None (失败 / 不存在 / 非法 JSON / schema 不兼容)。
        """
        from src.runtime.lifecycle_context import RuntimeContext

        # 1) 校验 id
        try:
            lid = _validate_lifecycle_id(lifecycle_id)
        except InvalidLifecycleIdError:
            return None

        path = self._resolve_path(lid)
        with self._lock:
            if not path.exists():
                return None
            # 2) 读取
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ContextStorage] 读取 %s 失败: %s", path, exc
                )
                return None

            # 3) 解析 JSON
            try:
                payload = json.loads(raw)
            except (ValueError, json.JSONDecodeError) as exc:
                logger.warning(
                    "[ContextStorage] 解析 %s 失败: %s", path, exc
                )
                return None

            if not isinstance(payload, dict):
                return None

            # 4) 校验 schema_version
            sv = payload.get("schema_version", "")
            if sv not in SUPPORTED_SCHEMA_VERSIONS:
                logger.warning(
                    "[ContextStorage] 不支持的 schema_version=%r in %s",
                    sv, path,
                )
                return None

            # 5) 提取 context dict
            ctx_dict = payload.get("context")
            if not isinstance(ctx_dict, dict):
                return None

            # 6) 按信封版本分派反序列化器：
            #    "2.0" → request_context v2 from_dict（P2.3-A.2.6）
            #    "1.0" → lifecycle_context v1 from_dict（旧逻辑不变）
            try:
                if sv == "2.0":
                    # 延迟 import：本模块顶层仅 stdlib（isolation 契约）
                    from src.runtime.request_context import (
                        RuntimeContext as RuntimeContextV2,
                    )
                    ctx = RuntimeContextV2.from_dict(ctx_dict)
                else:
                    ctx = RuntimeContext.from_dict(ctx_dict)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ContextStorage] RuntimeContext.from_dict 失败: %s", exc
                )
                return None
            return ctx

    # --------------------------------------------------------
    # exists
    # --------------------------------------------------------
    def exists(self, lifecycle_id: str) -> bool:
        """查询 lifecycle_id 是否已存在。"""
        try:
            lid = _validate_lifecycle_id(lifecycle_id)
        except InvalidLifecycleIdError:
            return False
        path = self._resolve_path(lid)
        with self._lock:
            return path.exists() and path.is_file()

    # --------------------------------------------------------
    # delete
    # --------------------------------------------------------
    def delete(self, lifecycle_id: str) -> bool:
        """删除已保存的 context。

        Returns:
            True  = 删除了文件(或已不存在,幂等)
            False = 失败
        """
        try:
            lid = _validate_lifecycle_id(lifecycle_id)
        except InvalidLifecycleIdError:
            return False
        path = self._resolve_path(lid)
        # 同时清理可能的 tmp 残留
        with self._lock:
            if not path.exists():
                return True  # 幂等
            try:
                path.unlink()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ContextStorage] 删除 %s 失败: %s", path, exc
                )
                return False
            return True

    # --------------------------------------------------------
    # list_ids (便捷方法)
    # --------------------------------------------------------
    def list_ids(self) -> list:
        """列出所有已保存的 lifecycle_id(按文件名升序)。"""
        with self._lock:
            if not self._base_dir.exists():
                return []
            ids = []
            for p in self._base_dir.iterdir():
                if p.is_file() and p.suffix == ".json" and not p.name.startswith("."):
                    ids.append(p.stem)
            ids.sort()
            return ids

    # --------------------------------------------------------
    # 内部工具
    # --------------------------------------------------------
    def _resolve_path(self, lifecycle_id: str) -> Path:
        """根据 lifecycle_id 解析文件路径。

        重要: lifecycle_id 已被 _validate_lifecycle_id 校验过,安全。
        """
        return self._base_dir / f"{lifecycle_id}.json"

    def __repr__(self) -> str:
        return f"RuntimeContextStorage(base_dir={self._base_dir!s})"


__all__ = [
    "RUNTIME_CONTEXT_STORAGE_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "DEFAULT_BASE_DIR",
    "ContextStorageError",
    "InvalidLifecycleIdError",
    "RuntimeContextStorage",
]
