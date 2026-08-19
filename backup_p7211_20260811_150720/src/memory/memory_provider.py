"""
Phase 4.0-R2.3.1 Memory Authority Provider
===========================================

目标：
    为核心模块建立**唯一的 MemoryStore 实例来源**，
    消除模块内部各自 `MemoryStore()` 自建导致的记忆孤岛问题。

设计原则（严格遵守 R2.3 Review 红线）：
    1) 不修改 MemoryStore 类本身（不改 memory_store.py 的 class 定义）。
    2) 只提供 `get_store()`，不改变任何 Memory 业务行为。
    3) 单例生命周期与 Python 进程一致（模块级锁 + _instance），
       避免多线程重复创建。
    4) RuntimeBridge / Legacy RuntimeCore / 测试用例依旧可以使用
       自己独立创建的 MemoryStore（Provider 只是"默认共享来源"，
       不强制替换所有场景）。

使用方式：
    >>> from src.memory.memory_provider import MemoryProvider
    >>> store = MemoryProvider.get_store()  # 全局同一对象
    >>> store2 = MemoryProvider.get_store()
    >>> store is store2
    True

R2.3 预期结果：
    MemoryService / MemorySystem / EventExtractor / ContextManager /
    TopicTracker / SelfCheck / Orchestrator → 都指向同一个
    `MemoryProvider.get_store()` 实例（按批逐步完成）。
"""

from __future__ import annotations

import threading
from typing import Optional

from src.memory.memory_store import MemoryStore


class MemoryProvider:
    """默认共享 MemoryStore 的进程级单例 Provider。

    注意：
    - 这里使用**模块级类单例**（不依赖第三方依赖注入库），
      目的是让调用方式最轻：`MemoryProvider.get_store()` 一行。
    - 保持 thread-safe：__init__/get_store 期间加锁，避免并发场景
      出现两个不同的 MemoryStore 被创建并返回。
    - 故意不留 setter / reset（除非是测试用 reset_for_testing），
      避免生产代码运行中被替换 Authority。
    """

    _instance: Optional[MemoryStore] = None
    _lock = threading.Lock()

    # ------------------------------------------------------------------
    # 主 API
    # ------------------------------------------------------------------
    @classmethod
    def get_store(cls) -> MemoryStore:
        """返回**进程唯一**的共享 MemoryStore 实例。

        返回对象始终相同（线程安全）。
        """
        if cls._instance is None:  # 无锁 fast-path（绝大多数情况命中）
            with cls._lock:
                # 二次检查（double-checked locking）防止并发重复构造
                if cls._instance is None:
                    cls._instance = MemoryStore()
        return cls._instance

    # ------------------------------------------------------------------
    # 重置能力（仅测试用，生产代码不应调用）
    # ------------------------------------------------------------------
    @classmethod
    def reset_for_testing(cls) -> None:
        """**仅 tests 目录使用**：清空单例，保证测试之间互相隔离。

        生产代码禁止调用（否则会在运行中把 Memory Authority 换新对象，
        导致之前写入的记忆"消失"，这属于严重 bug）。
        """
        with cls._lock:
            cls._instance = None
