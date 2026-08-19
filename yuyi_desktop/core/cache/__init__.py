# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/cache/__init__.py

Phase C.10.4.1 —— Yuyi Desktop Remote Snapshot Cache

仅缓存最近一次成功的服务器 snapshot,严禁修改缓存内容。
当 Server 不可达时,Service 可回退到缓存,UI 显示:
    offline=true
    last_update_time=<ts>
"""
from .remote_snapshot_cache import (
    CacheEntry,
    RemoteSnapshotCache,
    get_remote_snapshot_cache,
    reset_remote_snapshot_cache_for_testing,
)

__all__ = [
    "CacheEntry",
    "RemoteSnapshotCache",
    "get_remote_snapshot_cache",
    "reset_remote_snapshot_cache_for_testing",
]
