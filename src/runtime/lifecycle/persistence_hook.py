# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/persistence_hook.py

Phase 6.2 —— RuntimeContext 持久化桥接 Hook。

职责:
    提供**纯 Runtime 层**的 RuntimeContext 持久化桥接:
        - 调用 Phase 6.1 RuntimeContextStorage.save()
        - 不直接访问文件系统
        - 异常全部捕获(不能影响 Runtime 主流程)
        - 不修改 RuntimeContext 本体

设计原则:
    1. Hook 是**旁路**:Runtime 主流程与持久化解耦。
    2. Storage 失败 **不能** 导致 Runtime 失败,只记录日志 + 返回 reason。
    3. 不引入任何业务 Authority / EventHub / LLM 依赖。
    4. 最小协议:Hook 只需要一个 save(context) -> Dict 接口。
       任何实现该接口的对象都可作为 hook(便于测试注入 fake)。

依赖:
    - stdlib (logging, threading)
    - RuntimeContext (仅做能力检查，P2.3-A.2.6 起替代 isinstance 硬门)
    - RuntimeContextStorage (Phase 6.1, 通过构造注入)

典型用法:
    from src.runtime.context_storage import RuntimeContextStorage
    from src.runtime.lifecycle.persistence_hook import RuntimePersistenceHook

    storage = RuntimeContextStorage()
    hook = RuntimePersistenceHook(storage)
    new_ctx = run_with_context(manager, ctx, persistence_hook=hook)
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Dict, Optional, Protocol

if TYPE_CHECKING:  # pragma: no cover
    from src.runtime.lifecycle_context import RuntimeContext

logger = logging.getLogger(__name__)


# ============================================================
# Schema 版本常量
# ============================================================
RUNTIME_PERSISTENCE_HOOK_SCHEMA_VERSION = "1.0"

# P2.3-A.2.6：能力判断用受支持 schema 版本集（替代 isinstance 硬门）。
# lifecycle v1.0 与 request_context v2 均具备 schema_version + to_dict，
# 均可交由 storage.save 持久化；其余类型维持 saved=False 拒绝。
_SUPPORTED_CONTEXT_SCHEMA_VERSIONS = frozenset({"1.0", "2.0"})


# ============================================================
# Storage 协议(最小接口,允许测试注入 fake)
# ============================================================
class StorageLike(Protocol):
    """RuntimePersistenceHook 所需的最小 storage 接口(Protocol)。

    任何实现 ``save(context) -> Dict`` 的对象都可被注入。
    RuntimeContextStorage(Phase 6.1) 满足此协议。
    """

    def save(self, context: "RuntimeContext") -> Dict[str, Any]:  # pragma: no cover
        ...


# ============================================================
# 主类
# ============================================================
class RuntimePersistenceHook:
    """RuntimeContext 持久化桥接 Hook。

    接口契约:
        persist(context) -> {
            "saved": bool,
            "lifecycle_id": str,
            "reason": str
        }

    关键保证:
        - 异常隔离:任何 storage 异常都被捕获,persist 不会抛错。
        - Context 安全:不修改传入的 context。
        - 线程安全:内部用 RLock 保护(允许多线程并发 persist)。
    """

    def __init__(
        self,
        storage: Any,
        *,
        enabled: bool = True,
    ) -> None:
        """构造 PersistenceHook。

        Args:
            storage: 实现 ``save(context) -> Dict`` 接口的对象(通常为
                RuntimeContextStorage 实例)。允许为 None,但 persist 会
                返回 saved=False,reason="storage not configured"。
            enabled: 总开关;设为 False 时 persist 立即返回 saved=False。
        """
        self._storage = storage
        self._enabled = bool(enabled)
        self._lock = threading.RLock()
        # 统计: 成功 / 失败 次数(便于上层监控)
        self._success_count: int = 0
        self._failure_count: int = 0

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def storage(self) -> Any:
        return self._storage

    @property
    def success_count(self) -> int:
        with self._lock:
            return self._success_count

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    # --------------------------------------------------------
    # 核心接口
    # --------------------------------------------------------
    def persist(self, context: Any) -> Dict[str, Any]:
        """持久化 RuntimeContext(异常安全)。

        Args:
            context: RuntimeContext 实例(任意状态)。

        Returns:
            {
                "saved": bool,        # 是否成功保存
                "lifecycle_id": str,  # context 的 lifecycle_id(失败时也可返回)
                "reason": str,        # "ok" / "disabled" / "no storage" / 异常说明
            }

        保证:
            - **不抛**异常
            - **不修改** context
        """
        # 1) 能力判断（P2.3-A.2.6，替代 isinstance 硬门）：
        #    schema_version ∈ {1.0, 2.0} + 可调用 to_dict()。
        #    legacy v1.0 行为不变；v2 不再被静默跳过（交由 storage.save 落盘）。
        try:
            sv = getattr(context, "schema_version", None)
            if not (
                isinstance(sv, str)
                and sv in _SUPPORTED_CONTEXT_SCHEMA_VERSIONS
                and callable(getattr(context, "to_dict", None))
            ):
                return {
                    "saved": False,
                    "lifecycle_id": "",
                    "reason": (
                        f"invalid context: schema_version={sv!r}, "
                        f"type={type(context).__name__}"
                    ),
                }
        except Exception:  # noqa: BLE001
            return {
                "saved": False,
                "lifecycle_id": "",
                "reason": "context capability check failed",
            }

        lifecycle_id = str(getattr(context, "lifecycle_id", "") or "")

        # 2) Hook 关闭 -> 跳过
        if not self._enabled:
            return {
                "saved": False,
                "lifecycle_id": lifecycle_id,
                "reason": "disabled",
            }

        # 3) storage 未配置 -> 跳过
        if self._storage is None:
            return {
                "saved": False,
                "lifecycle_id": lifecycle_id,
                "reason": "no storage configured",
            }

        # 4) storage 缺少 save 接口 -> 跳过
        save_fn = getattr(self._storage, "save", None)
        if not callable(save_fn):
            with self._lock:
                self._failure_count += 1
            return {
                "saved": False,
                "lifecycle_id": lifecycle_id,
                "reason": "storage missing save() method",
            }

        # 5) 调用 storage.save(异常隔离)
        try:
            with self._lock:
                result = save_fn(context)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._failure_count += 1
            try:
                err_str = f"{type(exc).__name__}: {exc}"
            except Exception:  # noqa: BLE001
                err_str = "unknown error"
            logger.warning(
                "[PersistenceHook] save 失败 lifecycle_id=%s: %s",
                lifecycle_id, err_str,
            )
            return {
                "saved": False,
                "lifecycle_id": lifecycle_id,
                "reason": f"save exception: {err_str}",
            }

        # 6) 校验返回值
        if not isinstance(result, dict):
            with self._lock:
                self._failure_count += 1
            return {
                "saved": False,
                "lifecycle_id": lifecycle_id,
                "reason": "storage returned non-dict result",
            }

        saved_flag = result.get("saved", False)
        if not saved_flag:
            with self._lock:
                self._failure_count += 1
            return {
                "saved": False,
                "lifecycle_id": lifecycle_id,
                "reason": str(result.get("reason") or "storage reported not saved"),
            }

        # 7) 成功
        with self._lock:
            self._success_count += 1
        return {
            "saved": True,
            "lifecycle_id": lifecycle_id or str(result.get("lifecycle_id", "")),
            "reason": "ok",
        }

    # --------------------------------------------------------
    # 辅助
    # --------------------------------------------------------
    def disable(self) -> None:
        """关闭 hook(后续 persist 立即返回 disabled)。"""
        with self._lock:
            self._enabled = False

    def enable(self) -> None:
        """开启 hook。"""
        with self._lock:
            self._enabled = True

    def reset_counts(self) -> None:
        """重置统计计数(测试用)。"""
        with self._lock:
            self._success_count = 0
            self._failure_count = 0

    def __repr__(self) -> str:
        return (
            f"RuntimePersistenceHook(storage={self._storage!r}, "
            f"enabled={self._enabled})"
        )


__all__ = [
    "RUNTIME_PERSISTENCE_HOOK_SCHEMA_VERSION",
    "StorageLike",
    "RuntimePersistenceHook",
]
