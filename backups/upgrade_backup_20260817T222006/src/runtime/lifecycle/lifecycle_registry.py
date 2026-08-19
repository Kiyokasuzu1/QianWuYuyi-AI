# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_registry.py

Phase 5.0-D1: 任务注册表。

职责:
- 集中管理所有 LifecycleTask 实例
- 提供 task_id -> task 的快速查找
- 提供按 owner / priority 过滤
- 注册 / 注销时联动 EventEmitter 发射事件
- 检测重复 task_id(可选覆盖或拒绝)
- 线程安全

约束:
- 不依赖任何业务模块
- 仅依赖 Lifecycle 内部组件(Clock / Event / Task)
- Manager 未就绪前 Registry 仍可独立工作
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from src.runtime.lifecycle.internal.event_emitter import (
    EVENT_TASK_REGISTERED,
    EVENT_TASK_UNREGISTERED,
    EventEmitter,
)

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle.lifecycle_task import LifecycleTask

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================
# 注册策略
REJECT_DUPLICATE = "reject"      # 重复 task_id 抛错
REPLACE_DUPLICATE = "replace"    # 重复 task_id 静默替换
IGNORE_DUPLICATE = "ignore"      # 重复 task_id 静默忽略(不替换)

ALL_DUPLICATE_POLICIES: Tuple[str, ...] = (
    REJECT_DUPLICATE,
    REPLACE_DUPLICATE,
    IGNORE_DUPLICATE,
)


# ============================================================
# 异常
# ============================================================
class RegistryError(Exception):
    """Registry 错误基类。"""


class DuplicateTaskError(RegistryError):
    """重复注册时抛错(仅在 REJECT_DUPLICATE 策略下)。"""


# ============================================================
# LifecycleRegistry
# ============================================================
class LifecycleRegistry:
    """任务注册表。

    设计:
    - task_id 必须非空
    - 同一 task_id 重复注册策略可配置
    - 与 EventEmitter 集成,注册/注销时发射事件
    - 提供按 owner / priority 的查询
    - 线程安全(RLock)

    使用方式:
        registry = LifecycleRegistry()
        registry.register(task)
        task = registry.get("memory.cleanup")
        for t in registry.list_by_owner("memory"):
            ...
    """

    def __init__(
        self,
        *,
        duplicate_policy: str = REJECT_DUPLICATE,
        emitter: Optional[EventEmitter] = None,
        name: str = "registry",
    ) -> None:
        if duplicate_policy not in ALL_DUPLICATE_POLICIES:
            raise ValueError(
                f"duplicate_policy 必须是 {ALL_DUPLICATE_POLICIES} 之一,实际: {duplicate_policy!r}"
            )
        self._duplicate_policy = duplicate_policy
        self._emitter = emitter
        self._name = str(name or "registry")

        self._lock = threading.RLock()
        # task_id -> task
        self._tasks: Dict[str, "LifecycleTask"] = {}
        # owner -> set(task_id) (用于 owner 索引)
        self._owner_index: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def duplicate_policy(self) -> str:
        return self._duplicate_policy

    @property
    def emitter(self) -> Optional[EventEmitter]:
        return self._emitter

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._tasks)

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._tasks) == 0

    def owners(self) -> List[str]:
        """返回已注册 owner 列表(已排序)。"""
        with self._lock:
            return sorted(self._owner_index.keys())

    def owner_count(self) -> int:
        with self._lock:
            return len(self._owner_index)

    def task_ids(self) -> List[str]:
        """返回所有已注册 task_id 列表(已排序)。"""
        with self._lock:
            return sorted(self._tasks.keys())

    # ------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------
    def register(self, task: "LifecycleTask", *, replace: Optional[bool] = None) -> bool:
        """注册任务。

        - replace=None: 使用实例 duplicate_policy
        - replace=True: 强制覆盖
        - replace=False: 强制不覆盖

        返回是否真正新增(替换也返回 True,忽略返回 False)。
        """
        if task is None:
            raise ValueError("task 不能为 None")
        # 协议层只要求 task_id / owner,具体校验由 Task 类保证
        task_id = getattr(task, "task_id", None)
        owner = getattr(task, "owner", None)
        if not isinstance(task_id, str) or not task_id:
            raise ValueError("task.task_id 必须为非空字符串")
        if not isinstance(owner, str) or not owner:
            raise ValueError("task.owner 必须为非空字符串")

        effective_replace = bool(replace) if replace is not None else (self._duplicate_policy == REPLACE_DUPLICATE)
        effective_ignore = (not effective_replace) and (self._duplicate_policy == IGNORE_DUPLICATE)

        with self._lock:
            existing = self._tasks.get(task_id)
            if existing is not None:
                if effective_ignore:
                    return False
                if not effective_replace:
                    raise DuplicateTaskError(
                        f"task_id 重复: {task_id!r} (owner={owner!r})"
                    )
                # 替换:从 owner 索引中移除旧 owner
                old_owner = getattr(existing, "owner", None)
                if isinstance(old_owner, str) and old_owner in self._owner_index:
                    self._owner_index[old_owner].discard(task_id)
                    if not self._owner_index[old_owner]:
                        del self._owner_index[old_owner]

            self._tasks[task_id] = task
            self._owner_index.setdefault(owner, set()).add(task_id)
        # 锁外发射事件,避免重入
        if self._emitter is not None:
            try:
                self._emitter.emit(
                    EVENT_TASK_REGISTERED,
                    {"task_id": task_id, "owner": owner, "replaced": existing is not None},
                    source=self._name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Registry(%s) emit 注册事件失败: %s", self._name, exc)
        return True

    def unregister(self, task_id: str) -> Optional["LifecycleTask"]:
        """注销任务,返回被注销的实例(若不存在则返回 None)。

        对空 / 非字符串 task_id 视为不存在,返回 None(查询语义)。
        """
        if not isinstance(task_id, str) or not task_id:
            return None
        with self._lock:
            task = self._tasks.pop(task_id, None)
            if task is None:
                return None
            owner = getattr(task, "owner", None)
            if isinstance(owner, str) and owner in self._owner_index:
                self._owner_index[owner].discard(task_id)
                if not self._owner_index[owner]:
                    del self._owner_index[owner]
        if self._emitter is not None:
            try:
                self._emitter.emit(
                    EVENT_TASK_UNREGISTERED,
                    {"task_id": task_id, "owner": owner},
                    source=self._name,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Registry(%s) emit 注销事件失败: %s", self._name, exc)
        return task

    def clear(self) -> int:
        """清空注册表,返回清空数量(不发射事件)。"""
        with self._lock:
            n = len(self._tasks)
            self._tasks.clear()
            self._owner_index.clear()
            return n

    # ------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------
    def get(self, task_id: str) -> Optional["LifecycleTask"]:
        if not isinstance(task_id, str) or not task_id:
            return None
        with self._lock:
            return self._tasks.get(task_id)

    def contains(self, task_id: str) -> bool:
        if not isinstance(task_id, str) or not task_id:
            return False
        with self._lock:
            return task_id in self._tasks

    def has_owner(self, owner: str) -> bool:
        if not isinstance(owner, str) or not owner:
            return False
        with self._lock:
            return owner in self._owner_index

    def list_by_owner(self, owner: str) -> List["LifecycleTask"]:
        """按 owner 过滤任务列表。"""
        if not isinstance(owner, str) or not owner:
            return []
        with self._lock:
            ids = list(self._owner_index.get(owner, ()))
            return [self._tasks[tid] for tid in ids if tid in self._tasks]

    def list_by_owners(self, owners: Sequence[str]) -> List["LifecycleTask"]:
        """按多个 owner 过滤(并集)。"""
        out: List["LifecycleTask"] = []
        seen: Set[str] = set()
        with self._lock:
            for o in owners:
                for tid in self._owner_index.get(o, ()):
                    if tid in self._tasks and tid not in seen:
                        seen.add(tid)
                        out.append(self._tasks[tid])
        return out

    def list_all(self) -> List["LifecycleTask"]:
        """返回所有已注册任务(按 task_id 排序)。"""
        with self._lock:
            return [self._tasks[tid] for tid in sorted(self._tasks.keys())]

    def list_by_priority_desc(self) -> List["LifecycleTask"]:
        """按 priority 降序排序(同 priority 按 task_id 升序)。"""
        with self._lock:
            tasks = list(self._tasks.values())
        return sorted(
            tasks,
            key=lambda t: (-int(getattr(t, "priority", 0) or 0), str(getattr(t, "task_id", ""))),
        )

    def list_by_priority_asc(self) -> List["LifecycleTask"]:
        """按 priority 升序排序。"""
        with self._lock:
            tasks = list(self._tasks.values())
        return sorted(
            tasks,
            key=lambda t: (int(getattr(t, "priority", 0) or 0), str(getattr(t, "task_id", ""))),
        )

    def find(self, predicate) -> List["LifecycleTask"]:
        """按自定义谓词过滤。"""
        if not callable(predicate):
            raise ValueError("predicate 必须可调用")
        with self._lock:
            tasks = list(self._tasks.values())
        return [t for t in tasks if predicate(t)]

    # ------------------------------------------------------------
    # 批量
    # ------------------------------------------------------------
    def register_many(self, tasks: Iterable["LifecycleTask"], *, replace: Optional[bool] = None) -> int:
        """批量注册,返回成功注册数(忽略 / 失败不计入)。"""
        count = 0
        for t in tasks:
            try:
                if self.register(t, replace=replace):
                    count += 1
            except DuplicateTaskError:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Registry(%s) 批量注册失败: %s", self._name, exc)
        return count

    def unregister_many(self, task_ids: Iterable[str]) -> int:
        """批量注销,返回实际注销数。"""
        count = 0
        for tid in task_ids:
            try:
                if self.unregister(tid) is not None:
                    count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Registry(%s) 批量注销失败: %s", self._name, exc)
        return count

    # ------------------------------------------------------------
    # 迭代
    # ------------------------------------------------------------
    def __iter__(self) -> Iterator["LifecycleTask"]:
        with self._lock:
            return iter([self._tasks[tid] for tid in sorted(self._tasks.keys())])

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def __contains__(self, task_id: object) -> bool:
        if not isinstance(task_id, str):
            return False
        with self._lock:
            return task_id in self._tasks

    # ------------------------------------------------------------
    # 调试
    # ------------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        """返回注册表快照(用于调试 / 健康检查)。"""
        with self._lock:
            return {
                "name": self._name,
                "duplicate_policy": self._duplicate_policy,
                "size": len(self._tasks),
                "owner_count": len(self._owner_index),
                "task_ids": sorted(self._tasks.keys()),
                "owners": sorted(self._owner_index.keys()),
            }

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"LifecycleRegistry(name={self._name!r}, "
                f"size={len(self._tasks)}, "
                f"owners={len(self._owner_index)}, "
                f"policy={self._duplicate_policy!r})"
            )
