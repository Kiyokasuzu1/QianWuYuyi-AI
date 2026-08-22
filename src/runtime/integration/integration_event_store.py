# -*- coding: utf-8 -*-
"""
src/runtime/integration/integration_event_store.py

Phase 5.0-D2 Step 4: IntegrationEvent JSONL 持久化器。

职责:
- 把 IntegrationEvent 追加写入 JSONL 文件(append-only)
- 提供 read_all / tail / count 等查询接口
- 写入失败被隔离,不抛错
- 文件不存在时自动创建
- 目录不存在时自动创建
- 提供健康检查

约束:
- 不直接 import 任何业务模块
- 不调用 LLM / DB / Network
- 仅依赖 Python 标准库(os / json / threading)
- 不使用第三方依赖
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from src.runtime.integration.integration_event import IntegrationEvent


logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
DEFAULT_STORE_PATH = "data/integration_event_store.jsonl"
# 部署加固: 诊断事件文件单代轮转阈值（超过后整文件归档为 .old 并从空文件继续）。
MAX_STORE_BYTES = 50 * 1024 * 1024  # 50 MB


class IntegrationEventStoreError(Exception):
    """IntegrationEventStore 错误基类。"""


# ============================================================
# IntegrationEventStore
# ============================================================
class IntegrationEventStore:
    """IntegrationEvent JSONL 持久化器。

    设计:
    - 追加写(JSONL,每行一条)
    - 写入失败被隔离,记录到 error_count
    - 文件不存在 / 目录不存在:首次 append 时自动创建
    - 读操作线程安全;写操作加锁
    """

    def __init__(
        self,
        *,
        path: str = DEFAULT_STORE_PATH,
        name: str = "integration_event_store",
        enabled: bool = True,
    ) -> None:
        self._name = str(name or "integration_event_store")
        self._path = str(path or DEFAULT_STORE_PATH)
        self._enabled = bool(enabled)
        self._lock = threading.RLock()

        # 统计
        self._write_count = 0
        self._write_failures = 0
        self._read_count = 0
        self._read_failures = 0
        self._last_event_id: str = ""
        self._last_event_type: str = ""
        self._last_event_at: float = 0.0
        self._last_write_at: float = 0.0
        self._last_error: str = ""
        self._closed = False

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> str:
        return self._path

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def write_count(self) -> int:
        with self._lock:
            return self._write_count

    @property
    def write_failures(self) -> int:
        with self._lock:
            return self._write_failures

    @property
    def read_count(self) -> int:
        with self._lock:
            return self._read_count

    @property
    def read_failures(self) -> int:
        with self._lock:
            return self._read_failures

    @property
    def last_event_id(self) -> str:
        with self._lock:
            return self._last_event_id

    @property
    def last_event_type(self) -> str:
        with self._lock:
            return self._last_event_type

    @property
    def last_write_at(self) -> float:
        with self._lock:
            return self._last_write_at

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def exists(self) -> bool:
        try:
            return os.path.exists(self._path)
        except Exception:
            return False

    # --------------------------------------------------------
    # 写
    # --------------------------------------------------------
    def append(
        self,
        event: Any,
        *,
        silent: bool = True,
    ) -> bool:
        """追加一个事件到 JSONL 文件。

        返回 True 表示写入成功,False 表示失败。
        异常默认被隔离(silent=True);silent=False 时异常上抛。
        """
        try:
            if not isinstance(event, IntegrationEvent):
                if silent:
                    with self._lock:
                        self._write_failures += 1
                        self._last_error = "append 收到非 IntegrationEvent"
                    return False
                raise IntegrationEventStoreError(
                    f"append 需要 IntegrationEvent,实际: {type(event).__name__}"
                )
            with self._lock:
                if self._closed:
                    self._write_failures += 1
                    self._last_error = "store 已关闭"
                    return False
                if not self._enabled:
                    # 未启用时静默跳过(不报错)
                    return False
                # 准备目录
                try:
                    parent = os.path.dirname(self._path)
                    if parent and not os.path.exists(parent):
                        os.makedirs(parent, exist_ok=True)
                except Exception as exc:
                    with self._lock:
                        self._write_failures += 1
                        self._last_error = f"创建目录失败: {exc}"
                    logger.warning(
                        "IntegrationEventStore(%s) 创建目录失败: %s",
                        self._name, exc,
                    )
                    return False

            # 序列化为单行 JSON
            try:
                line = json.dumps(event.to_dict(), ensure_ascii=False)
            except Exception as exc:
                with self._lock:
                    self._write_failures += 1
                    self._last_error = f"serialize 失败: {exc}"
                if silent:
                    logger.warning(
                        "IntegrationEventStore(%s) serialize 失败: %s",
                        self._name, exc,
                    )
                    return False
                raise

            # 追加写（flush 降低崩溃丢尾概率; 超阈值先轮转再追加）
            try:
                with self._lock:
                    try:
                        if os.path.exists(self._path) and os.path.getsize(self._path) > MAX_STORE_BYTES:
                            os.replace(self._path, self._path + ".old")
                    except OSError as _rot_exc:
                        logger.warning(
                            "IntegrationEventStore(%s) 轮转失败（继续追加）: %s",
                            self._name, _rot_exc,
                        )
                    with open(self._path, "a", encoding="utf-8") as f:
                        f.write(line)
                        f.write("\n")
                        f.flush()
                    self._write_count += 1
                    self._last_event_id = event.event_id
                    self._last_event_type = event.event_type
                    self._last_event_at = float(getattr(event, "timestamp", 0.0) or 0.0)
                    self._last_write_at = time.time()
                return True
            except Exception as exc:
                with self._lock:
                    self._write_failures += 1
                    self._last_error = f"write 失败: {exc}"
                if silent:
                    logger.warning(
                        "IntegrationEventStore(%s) write 失败: %s",
                        self._name, exc,
                    )
                    return False
                raise
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._write_failures += 1
                self._last_error = f"append 整体失败: {exc}"
            if silent:
                logger.warning(
                    "IntegrationEventStore(%s) append 整体失败(已隔离): %s",
                    self._name, exc,
                )
                return False
            raise

    def append_many(self, events: List[Any]) -> int:
        """批量追加,返回成功数量。"""
        if not events:
            return 0
        success = 0
        for ev in events:
            if self.append(ev, silent=True):
                success += 1
        return success

    # --------------------------------------------------------
    # 读
    # --------------------------------------------------------
    def read_all(self, limit: Optional[int] = None) -> List[IntegrationEvent]:
        """读取全部事件(按文件顺序)。"""
        with self._lock:
            if not os.path.exists(self._path):
                self._read_count += 1
                return []
            try:
                events: List[IntegrationEvent] = []
                with open(self._path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except Exception:
                            self._read_failures += 1
                            continue
                        if not isinstance(data, dict):
                            self._read_failures += 1
                            continue
                        try:
                            events.append(IntegrationEvent.from_dict(data))
                        except Exception:
                            self._read_failures += 1
                            continue
                self._read_count += 1
            except Exception as exc:
                self._read_failures += 1
                self._last_error = f"read 失败: {exc}"
                logger.warning(
                    "IntegrationEventStore(%s) read 失败: %s",
                    self._name, exc,
                )
                return []
        if limit is not None:
            try:
                n = int(limit)
                if n <= 0:
                    return []
                events = events[-n:]
            except Exception:
                pass
        return events

    def tail(self, n: int = 10) -> List[IntegrationEvent]:
        """读取最后 N 条。"""
        try:
            count = int(n)
        except Exception:
            count = 10
        if count <= 0:
            return []
        return self.read_all(limit=count)

    # --------------------------------------------------------
    # 维护
    # --------------------------------------------------------
    def clear(self) -> bool:
        """清空文件(删除)。"""
        with self._lock:
            try:
                if os.path.exists(self._path):
                    os.remove(self._path)
                return True
            except Exception as exc:
                self._last_error = f"clear 失败: {exc}"
                return False

    def enable(self) -> None:
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        with self._lock:
            self._enabled = False

    def close(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._closed = True
        return True

    # --------------------------------------------------------
    # 健康检查
    # --------------------------------------------------------
    def health_check(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "path": self._path,
                "enabled": self._enabled,
                "is_closed": self._closed,
                "exists": self.exists(),
                "write_count": self._write_count,
                "write_failures": self._write_failures,
                "read_count": self._read_count,
                "read_failures": self._read_failures,
                "last_event_id": self._last_event_id,
                "last_event_type": self._last_event_type,
                "last_write_at": self._last_write_at,
                "last_error": self._last_error,
            }

    def describe(self) -> Dict[str, Any]:
        return self.health_check()

    def __repr__(self) -> str:
        return (
            f"IntegrationEventStore(name={self._name!r}, path={self._path!r}, "
            f"writes={self.write_count}, failures={self.write_failures})"
        )


# ============================================================
# 工厂
# ============================================================
def build_default_event_store(
    *,
    path: str = DEFAULT_STORE_PATH,
    name: str = "default_event_store",
    enabled: bool = True,
) -> IntegrationEventStore:
    """构造默认 IntegrationEventStore。"""
    return IntegrationEventStore(path=path, name=name, enabled=enabled)


__all__ = [
    "IntegrationEventStore",
    "IntegrationEventStoreError",
    "DEFAULT_STORE_PATH",
    "build_default_event_store",
]
