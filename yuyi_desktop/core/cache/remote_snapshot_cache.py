# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/cache/remote_snapshot_cache.py

Phase C.10.4.1 —— Remote Snapshot Cache

仅做一件事:缓存最近一次成功获取的 server snapshot。

规则:
- 写入:仅当 envelope.success == True 时写入;失败请求严禁写入
- 读取:返回最后一次成功的数据 + 写入时间戳;无数据返回 None
- TTL:默认 60 秒;过期不主动删除(只是标记 stale)
- 线程安全:RLock 保护所有访问
- 严禁修改缓存内容(返回 deepcopy,避免外部 mutate)

约束(强):
- 不直接 import src.* 任何业务模块
- 不发起任何网络请求(仅本地 in-memory 缓存)
- 不持久化(进程内;重启即丢)
"""
from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 缓存条目
# ============================================================
@dataclass
class CacheEntry:
    """单条缓存记录(只读语义:写入后不应被外部修改)。"""

    key: str
    data: Dict[str, Any]
    timestamp: str  # ISO8601 UTC
    latency_ms: float = 0.0
    success: bool = True
    created_at_monotonic: float = 0.0  # 用于 TTL 判定

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": str(self.key),
            "data": copy.deepcopy(self.data),
            "timestamp": str(self.timestamp),
            "latency_ms": float(self.latency_ms),
            "success": bool(self.success),
            "age_seconds": max(0.0, time.monotonic() - float(self.created_at_monotonic)),
        }


# ============================================================
# Remote Snapshot Cache
# ============================================================
class RemoteSnapshotCache:
    """
    远程 snapshot 缓存。

    典型用法:
        cache = RemoteSnapshotCache(ttl_seconds=60.0)
        # 成功响应时:
        cache.put("runtime", data, latency_ms=12.0)
        # 后续读取:
        entry = cache.get("runtime")
        if entry and not entry.expired:
            ...
    """

    DEFAULT_TTL_SECONDS = 60.0

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl_seconds = float(ttl_seconds) if ttl_seconds > 0 else DEFAULT_TTL_SECONDS
        self._lock = threading.RLock()
        self._entries: Dict[str, CacheEntry] = {}
        self._stats = {
            "total_puts": 0,
            "total_puts_rejected": 0,
            "total_hits": 0,
            "total_misses": 0,
            "total_expired_hits": 0,
        }

    # --------------------------------------------------------
    # 写入
    # --------------------------------------------------------
    def put(
        self,
        key: str,
        data: Dict[str, Any],
        latency_ms: float = 0.0,
        success: bool = True,
    ) -> Optional[CacheEntry]:
        """
        写入一条缓存。

        规则:
        - success=False 一律拒绝(防止把错误响应当快照缓存)
        - data 不是 dict 时拒绝
        - key 为空时拒绝

        Returns:
            写入的 CacheEntry;拒绝时返回 None。
        """
        if not success:
            with self._lock:
                self._stats["total_puts_rejected"] += 1
            logger.debug("RemoteSnapshotCache: 拒绝写入 success=False key=%s", key)
            return None
        if not isinstance(data, dict):
            with self._lock:
                self._stats["total_puts_rejected"] += 1
            logger.debug("RemoteSnapshotCache: 拒绝写入 data 非 dict key=%s", key)
            return None
        if not key or not isinstance(key, str):
            with self._lock:
                self._stats["total_puts_rejected"] += 1
            return None

        # 深拷贝,避免外部修改影响缓存
        safe_data = copy.deepcopy(data)
        now_iso = datetime.now(timezone.utc).isoformat()
        now_mono = time.monotonic()
        entry = CacheEntry(
            key=str(key),
            data=safe_data,
            timestamp=now_iso,
            latency_ms=float(latency_ms),
            success=True,
            created_at_monotonic=now_mono,
        )
        with self._lock:
            self._entries[str(key)] = entry
            self._stats["total_puts"] += 1
        return entry

    def put_envelope(
        self,
        key: str,
        envelope: Optional[Dict[str, Any]],
    ) -> Optional[CacheEntry]:
        """
        从标准 envelope 写入。

        仅当 envelope.success == True 时写入。
        """
        if not isinstance(envelope, dict):
            with self._lock:
                self._stats["total_puts_rejected"] += 1
            return None
        if not envelope.get("success", False):
            with self._lock:
                self._stats["total_puts_rejected"] += 1
            return None
        data = envelope.get("data", {}) or {}
        if not isinstance(data, dict):
            data = {"value": data}
        return self.put(
            key=key,
            data=data,
            latency_ms=float(envelope.get("latency_ms", 0.0)),
            success=True,
        )

    # --------------------------------------------------------
    # 读取
    # --------------------------------------------------------
    def get(
        self,
        key: str,
        allow_stale: bool = True,
    ) -> Optional[CacheEntry]:
        """
        读取缓存。

        Args:
            key: 缓存键。
            allow_stale: True=即使过期也返回(标记 is_stale);
                        False=过期返回 None。

        Returns:
            CacheEntry 或 None。
        """
        with self._lock:
            entry = self._entries.get(str(key))
            if entry is None:
                self._stats["total_misses"] += 1
                return None
            age = time.monotonic() - entry.created_at_monotonic
            is_stale = age > self._ttl_seconds
            if is_stale and not allow_stale:
                self._stats["total_expired_hits"] += 1
                return None
            if is_stale:
                self._stats["total_expired_hits"] += 1
            else:
                self._stats["total_hits"] += 1
            # 标记 stale 状态(但不修改 entry 本身)
            return CacheEntry(
                key=entry.key,
                data=copy.deepcopy(entry.data),
                timestamp=entry.timestamp,
                latency_ms=entry.latency_ms,
                success=entry.success,
                created_at_monotonic=entry.created_at_monotonic,
            )

    def get_data(
        self,
        key: str,
        allow_stale: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """便捷:仅返回 data 字段(不返回 entry 包装)。"""
        entry = self.get(key, allow_stale=allow_stale)
        if entry is None:
            return None
        return copy.deepcopy(entry.data)

    def get_status(self, key: str) -> Dict[str, Any]:
        """
        返回某 key 的缓存状态(用于 Service 暴露给 UI)。

        Returns:
            {
                "key": str,
                "has_data": bool,
                "timestamp": str,         # ISO8601 上次成功时间
                "age_seconds": float,
                "ttl_seconds": float,
                "is_stale": bool,         # age > ttl
                "success": bool,
                "offline": bool,          # 业务层根据 has_data + is_stale 推断
                "last_update_time": str,  # alias of timestamp
            }
        """
        with self._lock:
            entry = self._entries.get(str(key))
            if entry is None:
                return {
                    "key": str(key),
                    "has_data": False,
                    "timestamp": "",
                    "age_seconds": 0.0,
                    "ttl_seconds": float(self._ttl_seconds),
                    "is_stale": False,
                    "success": False,
                    "offline": True,
                    "last_update_time": "",
                }
            age = time.monotonic() - entry.created_at_monotonic
            is_stale = age > self._ttl_seconds
            return {
                "key": str(entry.key),
                "has_data": True,
                "timestamp": str(entry.timestamp),
                "age_seconds": float(age),
                "ttl_seconds": float(self._ttl_seconds),
                "is_stale": bool(is_stale),
                "success": bool(entry.success),
                "offline": bool(is_stale),  # stale = offline(数据已过期)
                "last_update_time": str(entry.timestamp),
            }

    # --------------------------------------------------------
    # 管理
    # --------------------------------------------------------
    def invalidate(self, key: str) -> bool:
        """删除单条缓存。"""
        with self._lock:
            return self._entries.pop(str(key), None) is not None

    def clear(self) -> int:
        """清空所有缓存。返回清理条数。"""
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            return n

    def keys(self) -> list:
        with self._lock:
            return list(self._entries.keys())

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._stats)

    def set_ttl(self, ttl_seconds: float) -> None:
        if ttl_seconds > 0:
            with self._lock:
                self._ttl_seconds = float(ttl_seconds)

    def get_ttl(self) -> float:
        with self._lock:
            return float(self._ttl_seconds)


# ============================================================
# 模块级单例
# ============================================================
_cache_instance: Optional[RemoteSnapshotCache] = None
_cache_lock = threading.Lock()


def get_remote_snapshot_cache() -> RemoteSnapshotCache:
    """获取 RemoteSnapshotCache 单例(懒加载,默认 TTL=60s)。"""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = RemoteSnapshotCache()
    return _cache_instance


def reset_remote_snapshot_cache_for_testing(
    ttl_seconds: float = RemoteSnapshotCache.DEFAULT_TTL_SECONDS,
) -> RemoteSnapshotCache:
    """测试用:重置单例。"""
    global _cache_instance
    with _cache_lock:
        _cache_instance = RemoteSnapshotCache(ttl_seconds=ttl_seconds)
    return _cache_instance


__all__ = [
    "CacheEntry",
    "RemoteSnapshotCache",
    "get_remote_snapshot_cache",
    "reset_remote_snapshot_cache_for_testing",
]
