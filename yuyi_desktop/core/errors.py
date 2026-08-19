# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/errors.py

Phase C.10.4.4 —— Yuyi Desktop Error Taxonomy

定义统一的 DesktopError 层级,所有 API / Connection / Schema 异常
必须转换为 DesktopError 子类,严禁吞掉或返回 None。

层级:
    DesktopError (基类)
    ├── NetworkError       — 连接 / 超时 / SSL 等
    ├── ServerError        — 5xx / envelope.success=False
    ├── AuthError          — 401 / 403
    ├── SchemaError        — schema_version 不兼容
    └── TimeoutError       — (NetworkError 子类)请求超时
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# ============================================================
# 错误码常量
# ============================================================
ERROR_NETWORK = "network_error"
ERROR_TIMEOUT = "timeout"
ERROR_CONNECTION = "connection_error"
ERROR_SSL = "ssl_error"
ERROR_SERVER = "server_error"
ERROR_AUTH = "auth_error"
ERROR_SCHEMA = "schema_mismatch"
ERROR_UNKNOWN = "unknown_error"
ERROR_OFFLINE = "offline"


# ============================================================
# DesktopError 基类
# ============================================================
class DesktopError(Exception):
    """
    Yuyi Desktop 统一错误基类。

    所有跨进程 / 跨模块异常必须转换为 DesktopError 或其子类,
    由调用方统一处理(降级、UI 提示、日志)。
    """

    def __init__(
        self,
        message: str,
        error_code: str = ERROR_UNKNOWN,
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.error_code = str(error_code or ERROR_UNKNOWN)
        self.cause = cause
        self.context = dict(context or {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.error_code,
            "message": str(self),
            "context": dict(self.context),
            "type": type(self).__name__,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"{type(self).__name__}({self.error_code!r}, {super().__repr__()})"


# ============================================================
# 子类
# ============================================================
class NetworkError(DesktopError):
    """网络层错误(连接失败 / DNS / SSL / timeout 等)。"""

    def __init__(
        self,
        message: str,
        error_code: str = ERROR_NETWORK,
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, error_code=error_code, cause=cause, context=context)


class TimeoutError(NetworkError):  # noqa: A001  (与 builtin 同名,是有意为之)
    """请求超时(connect or read)。"""

    def __init__(
        self,
        message: str = "request timeout",
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            error_code=ERROR_TIMEOUT,
            cause=cause,
            context=context,
        )


class ConnectionError_(NetworkError):  # noqa: A001
    """连接层错误(ConnectionError / DNS / etc.)。"""

    def __init__(
        self,
        message: str = "connection error",
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            error_code=ERROR_CONNECTION,
            cause=cause,
            context=context,
        )


class SSLError(NetworkError):
    """SSL / TLS 错误。"""

    def __init__(
        self,
        message: str = "ssl error",
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            error_code=ERROR_SSL,
            cause=cause,
            context=context,
        )


class ServerError(DesktopError):
    """Server 返回 5xx 或 envelope.success=False(业务失败)。"""

    def __init__(
        self,
        message: str = "server error",
        status_code: int = 0,
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            error_code=ERROR_SERVER,
            cause=cause,
            context=context,
        )
        self.status_code = int(status_code or 0)


class AuthError(DesktopError):
    """鉴权失败(401 / 403 / token 无效)。"""

    def __init__(
        self,
        message: str = "auth error",
        status_code: int = 0,
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            error_code=ERROR_AUTH,
            cause=cause,
            context=context,
        )
        self.status_code = int(status_code or 0)


class SchemaError(DesktopError):  # noqa: A001
    """Schema version 不兼容。"""

    def __init__(
        self,
        message: str = "schema mismatch",
        expected: str = "",
        actual: str = "",
        reason: str = "",
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx = dict(context or {})
        if reason:
            ctx.setdefault("reason", str(reason))
        super().__init__(
            message,
            error_code=ERROR_SCHEMA,
            cause=cause,
            context=ctx,
        )
        self.expected = str(expected or "")
        self.actual = str(actual or "")
        self.reason = str(reason or "")

    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        d["expected"] = self.expected
        d["actual"] = self.actual
        d["reason"] = self.reason
        return d


class OfflineError(DesktopError):
    """服务离线(无可用数据,无缓存)。"""

    def __init__(
        self,
        message: str = "service offline",
        cause: Optional[BaseException] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            error_code=ERROR_OFFLINE,
            cause=cause,
            context=context,
        )


# ============================================================
# 分类器
# ============================================================
def classify_exception(exc: BaseException) -> str:
    """
    将 requests 异常或通用异常分类为错误码。

    Returns:
        error_code 字符串(ERROR_NETWORK / ERROR_TIMEOUT / ...)
    """
    try:
        import requests as _req  # type: ignore
        if isinstance(exc, _req.exceptions.Timeout):
            return ERROR_TIMEOUT
        if isinstance(exc, _req.exceptions.SSLError):
            return ERROR_SSL
        if isinstance(exc, _req.exceptions.ConnectionError):
            return ERROR_CONNECTION
        if isinstance(exc, _req.exceptions.RequestException):
            return ERROR_NETWORK
    except ImportError:
        pass
    return ERROR_UNKNOWN


def classify_error_code(error_str: str) -> str:
    """
    从 envelope 的 error 字符串中分类错误码。

    规则:
        "timeout:..." -> ERROR_TIMEOUT
        "connection_error:..." -> ERROR_CONNECTION
        "ssl_error:..." -> ERROR_SSL
        "auth_error:..." -> ERROR_AUTH
        "http_5xx" -> ERROR_SERVER
        "request_error:..." -> ERROR_NETWORK
        其它 -> ERROR_UNKNOWN
    """
    if not error_str:
        return ERROR_UNKNOWN
    s = str(error_str).strip().lower()
    if s.startswith("timeout"):
        return ERROR_TIMEOUT
    if s.startswith("connection_error"):
        return ERROR_CONNECTION
    if s.startswith("ssl_error"):
        return ERROR_SSL
    if s.startswith("auth_error"):
        return ERROR_AUTH
    if s.startswith("http_5") or "http_5" in s:
        return ERROR_SERVER
    if s.startswith("request_error"):
        return ERROR_NETWORK
    if s.startswith("server_"):
        return ERROR_SERVER
    if s.startswith("schema_"):
        return ERROR_SCHEMA
    return ERROR_UNKNOWN


__all__ = [
    # constants
    "ERROR_NETWORK",
    "ERROR_TIMEOUT",
    "ERROR_CONNECTION",
    "ERROR_SSL",
    "ERROR_SERVER",
    "ERROR_AUTH",
    "ERROR_SCHEMA",
    "ERROR_UNKNOWN",
    "ERROR_OFFLINE",
    # base
    "DesktopError",
    # subclasses
    "NetworkError",
    "TimeoutError",
    "ConnectionError_",
    "SSLError",
    "ServerError",
    "AuthError",
    "SchemaError",
    "OfflineError",
    # helpers
    "classify_exception",
    "classify_error_code",
]
