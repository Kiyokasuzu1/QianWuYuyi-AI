# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/api_client.py

Phase C.10.2 —— Yuyi Desktop API Client

负责:
- Server 连接(base_url / timeout / retry)
- 请求封装(统一返回 schema)
- FailSafe(任何异常返回 degraded 响应)

约束(强):
- 仅使用 HTTP/HTTPS GET
- 禁止任何写方法(POST / PUT / PATCH / DELETE)
- 禁止引入 src.* 任何业务模块
- 任何异常必须返回标准 envelope,不抛错

标准响应 schema:
    {
        "success": true/false,
        "data": {},
        "error": "",
        "timestamp": "...",
        "schema_version": "1.0"
    }

降级响应:
    {
        "success": false,
        "degraded": true,
        "data": {},
        "error": "...",
        "timestamp": "...",
        "schema_version": "1.0"
    }
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)


# ============================================================
# 响应 Schema 常量
# ============================================================
API_SCHEMA_VERSION = "1.0"

# 默认 HTTP method 白名单(只读)
_ALLOWED_METHODS = frozenset({"GET"})


# ============================================================
# 配置
# ============================================================
# Phase D.1.1: 默认 base_url 改为用户服务器地址,可通过环境变量覆盖
DEFAULT_API_BASE = "http://198.44.178.195:5000"
DEFAULT_API_PREFIX = "/api/v1"


def _resolve_env(name: str, default: str) -> str:
    """读取环境变量,空值或缺失时返回 default。"""
    try:
        import os
        v = os.environ.get(name)
        if v is None or str(v).strip() == "":
            return default
        return str(v).strip()
    except Exception:
        return default


@dataclass(frozen=True)
class ApiClientConfig:
    """API Client 不可变配置。

    Phase D.1.1 新增:
        - 默认 base_url 指向用户服务器
        - 提供 from_env() 工厂方法,优先读取环境变量
            YUYI_API_BASE     覆盖 base_url
            YUYI_API_PREFIX   覆盖 api_prefix
    """

    base_url: str = DEFAULT_API_BASE
    api_prefix: str = DEFAULT_API_PREFIX
    timeout_seconds: float = 5.0
    connect_timeout_seconds: float = 2.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    verify_ssl: bool = True
    user_agent: str = "YuyiDesktop/0.1.0 (Phase-C.10.2)"

    def full_base(self) -> str:
        base = (self.base_url or "").rstrip("/")
        prefix = (self.api_prefix or "").rstrip("/")
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return base + prefix

    @staticmethod
    def from_env(
        base_default: str = DEFAULT_API_BASE,
        prefix_default: str = DEFAULT_API_PREFIX,
    ) -> "ApiClientConfig":
        """从环境变量构造配置(用于本地开发 / 服务器切换)。"""
        return ApiClientConfig(
            base_url=_resolve_env("YUYI_API_BASE", base_default),
            api_prefix=_resolve_env("YUYI_API_PREFIX", prefix_default),
        )


# ============================================================
# 标准响应
# ============================================================
def make_envelope(
    success: bool,
    data: Any = None,
    error: str = "",
    degraded: bool = False,
    latency_ms: float = 0.0,
) -> Dict[str, Any]:
    """构造标准 API 响应 envelope。"""
    return {
        "success": bool(success),
        "data": data if data is not None else {},
        "error": str(error or ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": API_SCHEMA_VERSION,
        "degraded": bool(degraded),
        "latency_ms": float(latency_ms),
    }


def make_error_envelope(
    error: str,
    degraded: bool = True,
    data: Any = None,
) -> Dict[str, Any]:
    """构造错误响应 envelope。"""
    return make_envelope(
        success=False,
        data=data if data is not None else {},
        error=error,
        degraded=degraded,
        latency_ms=0.0,
    )


def make_success_envelope(
    data: Any = None,
    latency_ms: float = 0.0,
) -> Dict[str, Any]:
    """构造成功响应 envelope。"""
    return make_envelope(
        success=True,
        data=data if data is not None else {},
        error="",
        degraded=False,
        latency_ms=latency_ms,
    )


# ============================================================
# API Client
# ============================================================
class ApiClient:
    """
    Yuyi Desktop 端 API 客户端。

    特点:
    - 只允许 GET(写方法在调用层就被拒绝)
    - 统一返回 envelope
    - 内置 retry + backoff
    - 任何异常返回 degraded 响应,绝不抛错到 UI 层
    - 可注入 session(测试用)
    """

    def __init__(
        self,
        config: Optional[ApiClientConfig] = None,
        session: Optional[requests.Session] = None,
        token_provider: Optional[Any] = None,
    ) -> None:
        self._config = config if config is not None else ApiClientConfig()
        self._lock = threading.RLock()
        self._session = session if session is not None else requests.Session()
        # token_provider 是可选的可调用对象,返回 token str
        self._token_provider = token_provider

    # --------------------------------------------------------
    # 公开 API
    # --------------------------------------------------------
    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        GET 请求,统一返回 envelope。

        永不抛错。任何异常都返回 degraded envelope。
        """
        return self._request("GET", path, params=params)

    def get_path(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Phase D.1.1: 跳过 api_prefix,直接用 base_url + path 发起 GET。

        用于访问非 /api/v1 前缀的端点(例如 /api/dashboard/v2/overview)。
        """
        try:
            base = (self._config.base_url or "").rstrip("/")
            p = path or ""
            if not p.startswith("/"):
                p = "/" + p
            return self._request("GET", "", params=params, _full_url=base + p)
        except Exception as exc:  # noqa: BLE001
            return make_error_envelope(f"absolute_request_failed: {exc}", degraded=True)

    def ping(self) -> Dict[str, Any]:
        """探活请求。

        说明:
            - 当前 server 注册的是 /health(不是 /health/ping)
            - 探活失败不应该误判 server 离线
        """
        return self.get("/health")

    def server_info(self) -> Dict[str, Any]:
        """获取服务器信息(版本等)。"""
        return self.get("/system/info")

    # --------------------------------------------------------
    # 内部实现
    # --------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        _full_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1) method 白名单检查
        if method not in _ALLOWED_METHODS:
            return make_error_envelope(
                f"method_not_allowed: {method} (Desktop 仅支持 GET)",
                degraded=True,
            )

        url = _full_url if _full_url else self._build_url(path)
        headers = self._build_headers()

        # 2) 发起请求(带 retry)
        max_attempts = max(1, int(self._config.max_retries) + 1)
        last_error: str = ""
        total_start = time.monotonic()
        for attempt in range(1, max_attempts + 1):
            attempt_start = time.monotonic()
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    params=params or {},
                    headers=headers,
                    timeout=(
                        self._config.connect_timeout_seconds,
                        self._config.timeout_seconds,
                    ),
                    verify=self._config.verify_ssl,
                )
                attempt_latency_ms = (time.monotonic() - attempt_start) * 1000.0
                # 状态码处理
                if 200 <= response.status_code < 300:
                    body = self._safe_json(response)
                    return self._wrap_success(body, attempt_latency_ms)
                if response.status_code in (401, 403):
                    # 鉴权问题不算 degraded(用户必须处理)
                    return make_error_envelope(
                        f"auth_error: HTTP {response.status_code}",
                        degraded=False,
                    )
                if response.status_code == 404 or response.status_code == 405:
                    # 端点不存在:不是 server 故障,而是 Desktop 端 URL 与 server 不匹配
                    # degraded=False 让 health_check 能区分"端点不存在"与"server 故障"
                    return make_error_envelope(
                        f"endpoint_not_available: HTTP {response.status_code}",
                        degraded=False,
                    )
                # 其它 HTTP 错误(5xx 等)
                last_error = f"http_{response.status_code}"
                logger.debug(
                    "ApiClient: HTTP %s 失败,attempt=%d url=%s",
                    response.status_code,
                    attempt,
                    url,
                )
            except requests.exceptions.Timeout as exc:
                last_error = f"timeout: {type(exc).__name__}"
                logger.debug("ApiClient: Timeout attempt=%d url=%s", attempt, url)
            except requests.exceptions.ConnectionError as exc:
                last_error = f"connection_error: {type(exc).__name__}"
                logger.debug("ApiClient: ConnectionError attempt=%d url=%s", attempt, url)
            except requests.exceptions.SSLError as exc:
                last_error = f"ssl_error: {type(exc).__name__}"
                logger.debug("ApiClient: SSLError attempt=%d url=%s", attempt, url)
            except requests.exceptions.RequestException as exc:
                last_error = f"request_error: {type(exc).__name__}"
                logger.debug("ApiClient: RequestException attempt=%d url=%s", attempt, url)
            except Exception as exc:  # noqa: BLE001
                last_error = f"unexpected: {type(exc).__name__}: {exc}"
                logger.debug("ApiClient: Unknown attempt=%d url=%s err=%s", attempt, url, exc)
            # 退避
            if attempt < max_attempts:
                backoff = self._config.retry_backoff_seconds * attempt
                time.sleep(backoff)

        total_latency_ms = (time.monotonic() - total_start) * 1000.0
        return make_error_envelope(last_error or "request_failed", degraded=True)

    def _wrap_success(self, body: Any, latency_ms: float) -> Dict[str, Any]:
        """包装成功响应为 envelope,补全所有标准字段。"""
        now = datetime.now(timezone.utc).isoformat()
        if isinstance(body, dict):
            # 已是 envelope 形式(包含 success/data/error/timestamp)
            if "success" in body and "data" in body:
                # 补全缺失字段(以符合标准 envelope)
                body.setdefault("schema_version", API_SCHEMA_VERSION)
                body.setdefault("timestamp", now)
                body.setdefault("error", "")
                body.setdefault("degraded", False)
                body["latency_ms"] = float(latency_ms)
                return body
            # 普通 dict data
            return make_envelope(
                success=True,
                data=body,
                error="",
                degraded=False,
                latency_ms=latency_ms,
            )
        if isinstance(body, list):
            return make_envelope(
                success=True,
                data=body,
                error="",
                degraded=False,
                latency_ms=latency_ms,
            )
        # 标量/其它
        return make_envelope(
            success=True,
            data={"value": body},
            error="",
            degraded=False,
            latency_ms=latency_ms,
        )

    def _build_url(self, path: str) -> str:
        base = self._config.full_base()
        p = path or ""
        if not p.startswith("/"):
            p = "/" + p
        return base + p

    def _build_headers(self) -> Dict[str, str]:
        h = {
            "User-Agent": self._config.user_agent,
            "Accept": "application/json",
            "X-Client": "YuyiDesktop",
            "X-Schema-Version": API_SCHEMA_VERSION,
        }
        # 注入 token(若有)
        if self._token_provider is not None:
            try:
                token = self._token_provider()
                if token:
                    h["Authorization"] = f"Bearer {token}"
            except Exception as exc:  # noqa: BLE001
                logger.debug("ApiClient: token_provider 失败: %s", exc)
        return h

    @staticmethod
    def _safe_json(response: requests.Response) -> Any:
        try:
            return response.json()
        except Exception:  # noqa: BLE001
            try:
                return {"_text": (response.text or "")[:2000]}
            except Exception:  # noqa: BLE001
                return None


# ============================================================
# 模块级单例
# ============================================================
_client_instance: Optional[ApiClient] = None
_client_lock = threading.Lock()


def get_api_client() -> ApiClient:
    """获取 ApiClient 单例(懒加载)。

    Phase D.1.1: 优先从环境变量读取 YUYI_API_BASE / YUYI_API_PREFIX,
    未设置时回退到 ApiClientConfig 默认值。
    """
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = ApiClient(config=ApiClientConfig.from_env())
    return _client_instance


def reset_api_client_for_testing() -> None:
    """测试用:重置单例。"""
    global _client_instance
    with _client_lock:
        _client_instance = None


__all__ = [
    "API_SCHEMA_VERSION",
    "ApiClientConfig",
    "ApiClient",
    "make_envelope",
    "make_error_envelope",
    "make_success_envelope",
    "get_api_client",
    "reset_api_client_for_testing",
]
