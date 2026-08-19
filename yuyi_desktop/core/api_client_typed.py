# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/api_client_typed.py

Phase C.10.4.4 —— Typed API Client

在原 ApiClient(envelope-based)之上提供严格类型化结果:
- 显式 success / data / error_code / error_class / error_message / latency_ms
- 任何错误都转换为 DesktopError 子类(或 TypedResponse.error_class)
- 禁止吞掉异常:不期望的异常直接 raise DesktopError

错误分类:
    NetworkError  - 连接 / 超时 / SSL
    ServerError   - 5xx / envelope.success=False
    AuthError     - 401 / 403
    SchemaError   - schema_version 不兼容
    TimeoutError  - (NetworkError 子类)超时
"""
from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from yuyi_desktop.core.api_client import (
    API_SCHEMA_VERSION,
    ApiClient,
    ApiClientConfig,
    get_api_client,
    make_envelope,
)
from yuyi_desktop.core.errors import (
    AuthError,
    DesktopError,
    ERROR_AUTH,
    ERROR_NETWORK,
    ERROR_SCHEMA,
    ERROR_SERVER,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
    NetworkError,
    SchemaError,
    ServerError,
    TimeoutError,
    classify_error_code,
)
from yuyi_desktop.core.schema_validator import (
    SchemaValidator,
    get_schema_validator,
)

logger = logging.getLogger(__name__)


# ============================================================
# Typed Response
# ============================================================
@dataclass
class TypedResponse:
    """
    类型化 API 响应。

    字段:
        success:        bool
        data:           dict(业务数据)
        error_code:     str(错误码,例如 ERROR_NETWORK)
        error_class:    str(异常类名,例如 "NetworkError")
        error_message:  str(可读错误描述)
        http_status:    int(HTTP 状态码,0 表示未发起)
        schema_version: str(响应 schema 版本)
        latency_ms:     float(请求耗时)
        timestamp:      str(ISO8601)
        degraded:       bool
        raw_envelope:   dict(原始响应 envelope)
    """

    success: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_class: str = ""
    error_message: str = ""
    http_status: int = 0
    schema_version: str = ""
    latency_ms: float = 0.0
    timestamp: str = ""
    degraded: bool = True
    raw_envelope: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": bool(self.success),
            "data": dict(self.data) if isinstance(self.data, dict) else {},
            "error_code": str(self.error_code or ""),
            "error_class": str(self.error_class or ""),
            "error_message": str(self.error_message or ""),
            "http_status": int(self.http_status),
            "schema_version": str(self.schema_version or ""),
            "latency_ms": float(self.latency_ms),
            "timestamp": str(self.timestamp or ""),
            "degraded": bool(self.degraded),
        }

    def to_envelope(self) -> Dict[str, Any]:
        """转换为标准 envelope(供老代码使用)。"""
        return make_envelope(
            success=bool(self.success),
            data=dict(self.data) if isinstance(self.data, dict) else {},
            error=str(self.error_message or self.error_code or ""),
            degraded=bool(self.degraded),
            latency_ms=float(self.latency_ms),
        )

    def raise_for_error(self) -> "TypedResponse":
        """
        若失败,raise DesktopError 子类。
        """
        if self.success:
            return self
        cls_name = self.error_class or "DesktopError"
        code = self.error_code or ERROR_UNKNOWN
        msg = self.error_message or code
        ctx = {
            "http_status": int(self.http_status),
            "latency_ms": float(self.latency_ms),
        }
        # 分类到具体子类
        if code == ERROR_NETWORK or cls_name in (
            "NetworkError", "TimeoutError", "ConnectionError_", "SSLError"
        ):
            if cls_name == "TimeoutError" or code == ERROR_TIMEOUT:
                raise TimeoutError(message=msg, context=ctx)
            raise NetworkError(message=msg, error_code=code, context=ctx)
        if code == ERROR_AUTH or cls_name == "AuthError":
            raise AuthError(
                message=msg,
                status_code=int(self.http_status),
                context=ctx,
            )
        if code == ERROR_SCHEMA or cls_name == "SchemaError":
            raise SchemaError(message=msg, context=ctx)
        if code == ERROR_SERVER or cls_name == "ServerError":
            raise ServerError(
                message=msg,
                status_code=int(self.http_status),
                context=ctx,
            )
        raise DesktopError(message=msg, error_code=code, context=ctx)

    @property
    def is_network_error(self) -> bool:
        return self.error_code in (
            ERROR_NETWORK, ERROR_TIMEOUT, "connection_error", "ssl_error",
        )

    @property
    def is_auth_error(self) -> bool:
        return self.error_code == ERROR_AUTH

    @property
    def is_server_error(self) -> bool:
        return self.error_code == ERROR_SERVER

    @property
    def is_schema_error(self) -> bool:
        return self.error_code == ERROR_SCHEMA


# ============================================================
# TypedApiClient
# ============================================================
class TypedApiClient:
    """
    类型化 API 客户端(Phase C.10.4.4)。

    包装 ApiClient(只读 envelope),返回 TypedResponse。
    所有失败均分类为 DesktopError 子类,严禁静默吞掉。
    """

    def __init__(
        self,
        api_client: Optional[ApiClient] = None,
        config: Optional[ApiClientConfig] = None,
        schema_validator: Optional[SchemaValidator] = None,
        enable_schema_check: bool = True,
    ) -> None:
        if api_client is not None:
            self._api_client = api_client
        elif config is not None:
            self._api_client = ApiClient(config=config)
        else:
            self._api_client = get_api_client()
        self._enable_schema_check = bool(enable_schema_check)
        self._schema_validator = (
            schema_validator if schema_validator is not None
            else get_schema_validator()
        )

    # --------------------------------------------------------
    # 核心方法
    # --------------------------------------------------------
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> TypedResponse:
        """
        类型化 GET。

        流程:
        1) 调用 ApiClient.get() 拿 envelope
        2) 分类 envelope.success=False 时的 error_code
        3) 分类 HTTP 状态码
        4) 可选:校验 schema_version
        5) 返回 TypedResponse
        """
        start = time.monotonic()
        try:
            envelope = self._api_client.get(path, params=params)
        except DesktopError:
            # 上层已规范化,直接 raise
            raise
        except requests.exceptions.Timeout as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            raise TimeoutError(
                message=f"timeout: {type(exc).__name__}",
                cause=exc,
                context={"path": str(path)},
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            raise NetworkError(
                message=f"connection_error: {type(exc).__name__}",
                error_code="connection_error",
                cause=exc,
                context={"path": str(path)},
            ) from exc
        except requests.exceptions.SSLError as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            raise NetworkError(
                message=f"ssl_error: {type(exc).__name__}",
                error_code="ssl_error",
                cause=exc,
                context={"path": str(path)},
            ) from exc
        except requests.exceptions.RequestException as exc:
            latency_ms = (time.monotonic() - start) * 1000.0
            raise NetworkError(
                message=f"request_error: {type(exc).__name__}",
                cause=exc,
                context={"path": str(path)},
            ) from exc
        except Exception as exc:  # noqa: BLE001
            # 非网络异常:不吞掉,转 DesktopError
            latency_ms = (time.monotonic() - start) * 1000.0
            raise DesktopError(
                message=f"unexpected: {type(exc).__name__}: {exc}",
                error_code=ERROR_UNKNOWN,
                cause=exc,
                context={"path": str(path), "latency_ms": float(latency_ms)},
            ) from exc

        latency_ms = float(envelope.get("latency_ms", 0.0) or 0.0)
        return self._envelope_to_typed(envelope, latency_ms=latency_ms)

    def ping(self) -> TypedResponse:
        return self.get("/health/ping")

    def server_info(self) -> TypedResponse:
        return self.get("/system/info")

    # --------------------------------------------------------
    # 内部:envelope -> TypedResponse
    # --------------------------------------------------------
    def _envelope_to_typed(
        self,
        envelope: Any,
        latency_ms: float = 0.0,
    ) -> TypedResponse:
        if not isinstance(envelope, dict):
            return TypedResponse(
                success=False,
                error_code=ERROR_UNKNOWN,
                error_class="DesktopError",
                error_message=f"envelope_not_dict: {type(envelope).__name__}",
                latency_ms=float(latency_ms),
                timestamp=datetime.now(timezone.utc).isoformat(),
                raw_envelope=None,
            )

        success = bool(envelope.get("success", False))
        data = envelope.get("data", {}) or {}
        if not isinstance(data, dict):
            data = {"value": data}
        else:
            data = copy.deepcopy(data)
        error_message = str(envelope.get("error", "") or "")
        schema_version = str(envelope.get("schema_version", "") or "")
        degraded = bool(envelope.get("degraded", False))
        ts = str(envelope.get("timestamp", "") or datetime.now(timezone.utc).isoformat())

        if success:
            # Schema 校验(可选)
            if self._enable_schema_check and schema_version:
                result = self._schema_validator.validate(envelope)
                if not result.ok:
                    return TypedResponse(
                        success=False,
                        data={},
                        error_code=ERROR_SCHEMA,
                        error_class="SchemaError",
                        error_message=(
                            f"schema_mismatch: expected={result.expected} "
                            f"actual={result.actual!r} reason={result.reason}"
                        ),
                        schema_version=schema_version,
                        latency_ms=float(latency_ms),
                        timestamp=ts,
                        degraded=True,
                        raw_envelope=copy.deepcopy(envelope),
                    )
            return TypedResponse(
                success=True,
                data=data,
                error_code="",
                error_class="",
                error_message="",
                schema_version=schema_version,
                latency_ms=float(latency_ms),
                timestamp=ts,
                degraded=False,
                raw_envelope=copy.deepcopy(envelope),
            )

        # 失败路径:分类 error_code
        code = classify_error_code(error_message)
        cls_name = self._error_class_for_code(code)
        # 推断 http_status(degraded envelope 不一定含)
        http_status = 0
        if code == ERROR_AUTH:
            http_status = 401
        return TypedResponse(
            success=False,
            data=data if isinstance(data, dict) else {},
            error_code=code,
            error_class=cls_name,
            error_message=error_message or code,
            http_status=int(http_status),
            schema_version=schema_version,
            latency_ms=float(latency_ms),
            timestamp=ts,
            degraded=degraded,
            raw_envelope=copy.deepcopy(envelope),
        )

    @staticmethod
    def _error_class_for_code(code: str) -> str:
        mapping = {
            ERROR_NETWORK: "NetworkError",
            ERROR_TIMEOUT: "TimeoutError",
            "connection_error": "ConnectionError_",
            "ssl_error": "SSLError",
            ERROR_SERVER: "ServerError",
            ERROR_AUTH: "AuthError",
            ERROR_SCHEMA: "SchemaError",
            ERROR_UNKNOWN: "DesktopError",
        }
        return mapping.get(code, "DesktopError")


# ============================================================
# 模块级单例
# ============================================================
_typed_instance: Optional[TypedApiClient] = None
_typed_lock = __import__("threading").Lock()


def get_typed_api_client() -> TypedApiClient:
    """获取 TypedApiClient 单例(懒加载)。"""
    global _typed_instance
    if _typed_instance is None:
        with _typed_lock:
            if _typed_instance is None:
                _typed_instance = TypedApiClient()
    return _typed_instance


def reset_typed_api_client_for_testing() -> TypedApiClient:
    """测试用:重置单例。"""
    global _typed_instance
    with _typed_lock:
        _typed_instance = TypedApiClient()
    return _typed_instance


__all__ = [
    "TypedResponse",
    "TypedApiClient",
    "get_typed_api_client",
    "reset_typed_api_client_for_testing",
]
