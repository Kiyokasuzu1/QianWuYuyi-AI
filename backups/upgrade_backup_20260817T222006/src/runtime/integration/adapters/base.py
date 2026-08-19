# -*- coding: utf-8 -*-
"""
src/runtime/integration/adapters/base.py

Phase 5.0-D2 Step 2: Adapter 协议 + 基类(Skeleton)。

职责:
- 定义 Adapter 协议(Duck-typed)
- 提供 BaseAdapter 通用实现
- 提供 is_available() 状态查询
- 提供 safe_call() 错误隔离入口
- 提供与 IntegrationEvent 的桥接(emit)

约束:
- 不直接 import 任何业务模块
- 通过 _resolve_target() 延迟导入,避免硬依赖
- 不调用 LLM / DB / Network
- 不修改业务模块源码
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from src.runtime.integration.integration_event import (
    IntegrationEvent,
    make_integration_event,
)


logger = logging.getLogger(__name__)


# ============================================================
# Adapter 协议
# ============================================================
@runtime_checkable
class Adapter(Protocol):
    """Adapter 协议(Duck-typed)。

    所有 Adapter 必须实现:
    - name          : str
    - owner         : str
    - is_available() -> bool
    - get_target()  -> Optional[Any]
    - emit(event)   -> None
    """

    @property
    def name(self) -> str: ...

    @property
    def owner(self) -> str: ...

    def is_available(self) -> bool: ...

    def get_target(self) -> Optional[Any]: ...

    def emit(self, event: IntegrationEvent) -> None: ...


# ============================================================
# BaseAdapter
# ============================================================
class BaseAdapter:
    """Adapter 通用基类。

    设计:
    - 持有 target_resolver(延迟解析目标的 callable)
    - 持有 event_emitter(可选,接收 IntegrationEvent)
    - 提供 _safe_call() 错误隔离入口
    - 提供 emit() 把 IntegrationEvent 推给外部 emitter
    - 状态自描述:name / owner / available
    """

    def __init__(
        self,
        name: str,
        owner: str,
        *,
        target_resolver: Optional[Callable[[], Optional[Any]]] = None,
        event_emitter: Optional[Callable[[IntegrationEvent], None]] = None,
        fallback_event_emitter: Optional[Callable[[IntegrationEvent], None]] = None,
    ) -> None:
        self._name = str(name or owner or "adapter")
        self._owner = str(owner or "unknown")
        self._target_resolver = target_resolver
        self._event_emitter = event_emitter
        self._fallback_event_emitter = fallback_event_emitter
        self._lock = threading.RLock()
        self._emitted_count = 0
        self._call_count = 0
        self._error_count = 0
        self._last_error: str = ""
        self._last_emit_event_id: str = ""
        # 不缓存 target,允许动态切换
        self._resolved_once = False
        self._resolved_available: Optional[bool] = None

    # --------------------------------------------------------
    # 身份
    # --------------------------------------------------------
    @property
    def name(self) -> str:
        return self._name

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def emitted_count(self) -> int:
        with self._lock:
            return self._emitted_count

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    @property
    def last_emit_event_id(self) -> str:
        with self._lock:
            return self._last_emit_event_id

    # --------------------------------------------------------
    # 目标解析
    # --------------------------------------------------------
    def get_target(self) -> Optional[Any]:
        """解析并返回目标业务对象(Skeleton 阶段:可返回 None)。"""
        if self._target_resolver is None:
            return None
        try:
            return self._target_resolver()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = f"target_resolver 失败: {exc}"
            logger.warning(
                "BaseAdapter(%s) target_resolver failed: %s",
                self._name,
                exc,
            )
            return None

    def is_available(self) -> bool:
        """目标业务模块是否可用。"""
        try:
            target = self.get_target()
        except Exception:
            return False
        if target is None:
            return False
        return True

    # --------------------------------------------------------
    # 事件发射
    # --------------------------------------------------------
    def emit(self, event: IntegrationEvent) -> None:
        """把 IntegrationEvent 推给外部 emitter(可注入)。"""
        if not isinstance(event, IntegrationEvent):
            return
        emitter = self._event_emitter or self._fallback_event_emitter
        if emitter is None:
            # Skeleton 阶段没有 emitter 时静默忽略(不抛错)
            with self._lock:
                self._emitted_count += 1
                self._last_emit_event_id = event.event_id
            return
        try:
            emitter(event)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = f"emit 失败: {exc}"
            logger.warning(
                "BaseAdapter(%s) emit failed: %s",
                self._name,
                exc,
            )
            return
        with self._lock:
            self._emitted_count += 1
            self._last_emit_event_id = event.event_id

    def make_event(
        self,
        event_type: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        related_ids: Optional[List[str]] = None,
        severity: str = "info",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IntegrationEvent:
        """构造 IntegrationEvent 的便捷方法(Skeleton 阶段使用 default source=owner)。"""
        return make_integration_event(
            event_type=str(event_type or ""),
            source=self._owner,
            payload=payload,
            related_ids=related_ids,
            severity=severity,
            metadata=metadata,
        )

    # --------------------------------------------------------
    # 安全调用包装
    # --------------------------------------------------------
    def _safe_call(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """调用 func,异常被捕获并记录,默认返回 None。"""
        if not callable(func):
            return None
        with self._lock:
            self._call_count += 1
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._error_count += 1
                self._last_error = str(exc)
            logger.warning(
                "BaseAdapter(%s) call failed: %s",
                self._name,
                exc,
            )
            return None

    # --------------------------------------------------------
    # 调试
    # --------------------------------------------------------
    def describe(self) -> Dict[str, Any]:
        """返回 Adapter 状态描述(Skeleton 阶段用于测试与诊断)。"""
        with self._lock:
            return {
                "name": self._name,
                "owner": self._owner,
                "available": self.is_available(),
                "call_count": self._call_count,
                "error_count": self._error_count,
                "emitted_count": self._emitted_count,
                "last_error": self._last_error,
                "last_emit_event_id": self._last_emit_event_id,
            }

    def __repr__(self) -> str:
        return (
            f"BaseAdapter(name={self._name!r}, owner={self._owner!r}, "
            f"avail={self.is_available()})"
        )
