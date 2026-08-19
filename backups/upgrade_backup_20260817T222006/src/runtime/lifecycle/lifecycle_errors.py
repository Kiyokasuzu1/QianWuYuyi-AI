# -*- coding: utf-8 -*-
"""
src/runtime/lifecycle/lifecycle_errors.py

Phase 5.0-D1: 统一生命周期异常体系。

设计:
- 单一 LifecycleError 类承载 category 字段
- 5 个 category 枚举: TRANSIENT / PERMANENT / TIMEOUT / CANCELLED / INTERNAL
- 每个 category 默认重试策略
- 支持 to_dict / from_dict 序列化
- 保留原始 cause 链路
- 线程安全(无共享状态,天然线程安全)
- 不依赖任何业务模块
"""
from __future__ import annotations

import enum
import logging
import traceback
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


# ============================================================
# 错误分类
# ============================================================
class ErrorCategory(str, enum.Enum):
    """Lifecycle 错误分类。

    - TRANSIENT: 瞬时错误,可重试(如 IO 暂时失败)
    - PERMANENT: 永久错误,不可重试(如配置错误)
    - TIMEOUT:   超时
    - CANCELLED: 外部取消
    - INTERNAL:  LifecycleManager 自身 bug
    """

    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    INTERNAL = "INTERNAL"


# ============================================================
# 默认重试策略
# ============================================================
_DEFAULT_RETRYABLE: Dict[ErrorCategory, bool] = {
    ErrorCategory.TRANSIENT: True,
    ErrorCategory.PERMANENT: False,
    ErrorCategory.TIMEOUT: True,
    ErrorCategory.CANCELLED: False,
    ErrorCategory.INTERNAL: False,
}


# ============================================================
# LifecycleError
# ============================================================
class LifecycleError(Exception):
    """统一 Lifecycle 异常。

    用法:
        try:
            ...
        except ValueError as exc:
            raise LifecycleError(
                category=ErrorCategory.TRANSIENT,
                message="io failed",
                cause=exc,
                retry_after=1.0,
            )

    字段:
    - category: 错误分类
    - message:  错误描述
    - retryable: 是否可重试(默认根据 category)
    - retry_after: 建议重试间隔(秒);None = 不指定
    - cause: 原始异常
    - cause_traceback: 原始异常 traceback 字符串(序列化用)
    - context: 附加上下文 dict
    """

    __slots__ = (
        "category",
        "_message",
        "_retryable",
        "retry_after",
        "_cause",
        "_cause_traceback",
        "context",
    )

    def __init__(
        self,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        message: str = "",
        *,
        retryable: Optional[bool] = None,
        retry_after: Optional[float] = None,
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self._message = str(message) if message else ""
        if retryable is None:
            self._retryable = _DEFAULT_RETRYABLE.get(category, False)
        else:
            self._retryable = bool(retryable)
        if retry_after is not None:
            try:
                self.retry_after = float(retry_after)
            except Exception:
                self.retry_after = None
        else:
            self.retry_after = None
        self._cause = cause
        if cause is not None:
            try:
                self._cause_traceback = "".join(
                    traceback.format_exception(type(cause), cause, cause.__traceback__)
                )
            except Exception:
                self._cause_traceback = ""
        else:
            self._cause_traceback = ""
        if context is not None:
            try:
                self.context = dict(context)
            except Exception:
                self.context = {}
        else:
            self.context = {}

    # --------------------------------------------------------
    # 属性
    # --------------------------------------------------------
    @property
    def message(self) -> str:
        return self._message

    @property
    def retryable(self) -> bool:
        return self._retryable

    @property
    def cause(self) -> Optional[BaseException]:
        return self._cause

    @property
    def cause_traceback(self) -> str:
        return self._cause_traceback

    # --------------------------------------------------------
    # 分类快捷构造
    # --------------------------------------------------------
    @classmethod
    def transient(
        cls,
        message: str = "",
        *,
        retry_after: Optional[float] = None,
        cause: Optional[BaseException] = None,
        **kwargs: Any,
    ) -> "LifecycleError":
        return cls(
            category=ErrorCategory.TRANSIENT,
            message=message,
            retry_after=retry_after,
            cause=cause,
            **kwargs,
        )

    @classmethod
    def permanent(
        cls,
        message: str = "",
        *,
        cause: Optional[BaseException] = None,
        **kwargs: Any,
    ) -> "LifecycleError":
        return cls(
            category=ErrorCategory.PERMANENT,
            message=message,
            cause=cause,
            **kwargs,
        )

    @classmethod
    def timeout(
        cls,
        message: str = "",
        *,
        retry_after: Optional[float] = None,
        cause: Optional[BaseException] = None,
        **kwargs: Any,
    ) -> "LifecycleError":
        return cls(
            category=ErrorCategory.TIMEOUT,
            message=message,
            retry_after=retry_after,
            cause=cause,
            **kwargs,
        )

    @classmethod
    def cancelled(
        cls,
        message: str = "",
        *,
        cause: Optional[BaseException] = None,
        **kwargs: Any,
    ) -> "LifecycleError":
        return cls(
            category=ErrorCategory.CANCELLED,
            message=message,
            cause=cause,
            **kwargs,
        )

    @classmethod
    def internal(
        cls,
        message: str = "",
        *,
        cause: Optional[BaseException] = None,
        **kwargs: Any,
    ) -> "LifecycleError":
        return cls(
            category=ErrorCategory.INTERNAL,
            message=message,
            cause=cause,
            **kwargs,
        )

    # --------------------------------------------------------
    # 序列化
    # --------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self._message,
            "retryable": self._retryable,
            "retry_after": self.retry_after,
            "cause_type": (
                type(self._cause).__name__ if self._cause is not None else None
            ),
            "cause_message": (
                str(self._cause) if self._cause is not None else None
            ),
            "cause_traceback": self._cause_traceback,
            "context": dict(self.context),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LifecycleError":
        try:
            category_str = str(data.get("category", "INTERNAL"))
            category = ErrorCategory(category_str)
        except (ValueError, KeyError):
            category = ErrorCategory.INTERNAL
        try:
            retryable = data.get("retryable")
            if retryable is None:
                retryable = None
            else:
                retryable = bool(retryable)
        except Exception:
            retryable = None
        try:
            retry_after = data.get("retry_after")
            if retry_after is not None:
                retry_after = float(retry_after)
        except Exception:
            retry_after = None
        try:
            ctx = data.get("context") or {}
            if not isinstance(ctx, dict):
                ctx = {}
        except Exception:
            ctx = {}
        return cls(
            category=category,
            message=str(data.get("message", "")),
            retryable=retryable,
            retry_after=retry_after,
            context=ctx,
        )

    # --------------------------------------------------------
    # 比较
    # --------------------------------------------------------
    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, LifecycleError):
            return False
        return (
            self.category == other.category
            and self._message == other._message
            and self._retryable == other._retryable
        )

    def __hash__(self) -> int:
        return hash((self.category, self._message, self._retryable))

    def __repr__(self) -> str:
        return (
            f"LifecycleError(category={self.category.value!r}, "
            f"message={self._message!r}, retryable={self._retryable!r}, "
            f"retry_after={self.retry_after!r})"
        )

    def __str__(self) -> str:
        return f"[{self.category.value}] {self._message}"


# ============================================================
# 工具函数
# ============================================================
def classify_exception(exc: BaseException) -> LifecycleError:
    """将任意 Exception 包装为 LifecycleError。

    规则:
    - 已是 LifecycleError: 原样返回
    - TimeoutError / asyncio.TimeoutError → TIMEOUT
    - PermissionError / ValueError / TypeError / KeyError → PERMANENT
    - IOError / OSError / ConnectionError → TRANSIENT
    - 其他 → INTERNAL
    """
    if isinstance(exc, LifecycleError):
        return exc
    try:
        import asyncio  # noqa: F401
        if isinstance(exc, asyncio.TimeoutError):
            return LifecycleError.timeout(str(exc), cause=exc)
    except ImportError:
        pass
    if isinstance(exc, (IOError, OSError, ConnectionError)):
        return LifecycleError.transient(str(exc), cause=exc)
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError)):
        return LifecycleError.permanent(str(exc), cause=exc)
    if isinstance(exc, TimeoutError):
        return LifecycleError.timeout(str(exc), cause=exc)
    return LifecycleError.internal(str(exc), cause=exc)


__all__ = [
    "ErrorCategory",
    "LifecycleError",
    "classify_exception",
]
