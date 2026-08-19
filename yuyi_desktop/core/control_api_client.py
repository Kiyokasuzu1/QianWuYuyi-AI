# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/control_api_client.py

Phase C.10.5.6 —— Desktop Control API Client

专用 Client,只用于访问 Yuyi Control API(/api/v1/control/*)。
不与 RemoteProviderBridge 共享(后者仍只读)。

支持:
- GET  /api/v1/control/status
- GET  /api/v1/control/modules
- GET  /api/v1/control/audit
- POST /api/v1/control/module/<name>/enable
- POST /api/v1/control/module/<name>/disable
- POST /api/v1/control/module/<name>/toggle
- POST /api/v1/control/safe_mode/enable
- POST /api/v1/control/safe_mode/disable
- POST /api/v1/control/maintenance/enable
- POST /api/v1/control/maintenance/disable

约束(强):
- 不允许 import 任何 src.* 业务模块
- 仅通过 requests.Session 发起 HTTP
- 失败一律返回标准 envelope,不抛错
- Bearer Token 通过 ApiClient.token_provider 注入
- 可注入 session(测试用)
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


CONTROL_SCHEMA_VERSION = "1.0"


# ============================================================
# 配置
# ============================================================
class ControlApiConfig:
    """ControlApiClient 不可变配置。"""

    __slots__ = (
        "base_url",
        "api_prefix",
        "timeout_seconds",
        "connect_timeout_seconds",
        "max_retries",
        "retry_backoff_seconds",
        "verify_ssl",
        "user_agent",
    )

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        api_prefix: str = "/api/v1",
        timeout_seconds: float = 5.0,
        connect_timeout_seconds: float = 2.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        verify_ssl: bool = True,
        user_agent: str = "YuyiDesktop/0.10.5 (Phase-C.10.5)",
    ) -> None:
        self.base_url = str(base_url or "http://127.0.0.1:8000")
        self.api_prefix = str(api_prefix or "/api/v1")
        self.timeout_seconds = float(timeout_seconds)
        self.connect_timeout_seconds = float(connect_timeout_seconds)
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self.verify_ssl = bool(verify_ssl)
        self.user_agent = str(user_agent or "YuyiDesktop/0.10.5")

    def full_base(self) -> str:
        base = (self.base_url or "").rstrip("/")
        prefix = (self.api_prefix or "").rstrip("/")
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return base + prefix

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "api_prefix": self.api_prefix,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "user_agent": self.user_agent,
        }


# ============================================================
# Envelope
# ============================================================
def make_envelope(
    success: bool,
    data: Any = None,
    error: str = "",
    degraded: bool = False,
    latency_ms: float = 0.0,
) -> Dict[str, Any]:
    return {
        "success": bool(success),
        "data": data if data is not None else {},
        "error": str(error or ""),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "schema_version": CONTROL_SCHEMA_VERSION,
        "degraded": bool(degraded),
        "latency_ms": float(latency_ms),
    }


def make_error_envelope(
    error: str,
    degraded: bool = True,
    data: Any = None,
) -> Dict[str, Any]:
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
    return make_envelope(
        success=True,
        data=data if data is not None else {},
        error="",
        degraded=False,
        latency_ms=latency_ms,
    )


# ============================================================
# ControlApiClient
# ============================================================
class ControlApiClient:
    """
    桌面端 Control API 客户端。

    支持 GET / POST。
    任何异常返回标准 envelope,不抛错。
    """

    def __init__(
        self,
        config: Optional[ControlApiConfig] = None,
        session: Optional[requests.Session] = None,
        token_provider: Optional[Any] = None,
    ) -> None:
        self._config = config if config is not None else ControlApiConfig()
        self._lock = threading.RLock()
        self._session = session if session is not None else requests.Session()
        self._token_provider = token_provider

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------
    def get_status(self) -> Dict[str, Any]:
        return self._request("GET", "/control/status")

    def get_modules(self) -> Dict[str, Any]:
        return self._request("GET", "/control/modules")

    def get_audit(self, limit: int = 20, field: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": int(limit)}
        if field:
            params["field"] = str(field)
        return self._request("GET", "/control/audit", params=params)

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------
    def enable_module(
        self,
        name: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._post_module(name, "enable", operator=operator, reason=reason)

    def disable_module(
        self,
        name: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._post_module(name, "disable", operator=operator, reason=reason)

    def toggle_module(
        self,
        name: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._post_module(name, "toggle", operator=operator, reason=reason)

    def enter_safe_mode(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/control/safe_mode/enable",
            json={"operator": operator, "reason": reason},
            extra_headers={"X-Operator": operator},
        )

    def exit_safe_mode(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/control/safe_mode/disable",
            json={"operator": operator, "reason": reason},
            extra_headers={"X-Operator": operator},
        )

    def enter_maintenance(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/control/maintenance/enable",
            json={"operator": operator, "reason": reason},
            extra_headers={"X-Operator": operator},
        )

    def exit_maintenance(
        self,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/control/maintenance/disable",
            json={"operator": operator, "reason": reason},
            extra_headers={"X-Operator": operator},
        )

    def _post_module(
        self,
        name: str,
        action: str,
        operator: str = "desktop",
        reason: str = "",
    ) -> Dict[str, Any]:
        return self._request(
            "POST",
            f"/control/module/{name}/{action}",
            json={"operator": operator, "reason": reason},
            extra_headers={"X-Operator": operator},
        )

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        extra_headers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        method = (method or "").upper()
        if method not in ("GET", "POST"):
            return make_error_envelope(
                f"method_not_allowed: {method}",
                degraded=True,
            )
        url = self._build_url(path)
        headers = self._build_headers(extra_headers=extra_headers)

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
                    json=json,
                    headers=headers,
                    timeout=(
                        self._config.connect_timeout_seconds,
                        self._config.timeout_seconds,
                    ),
                    verify=self._config.verify_ssl,
                )
                attempt_latency_ms = (time.monotonic() - attempt_start) * 1000.0
                if 200 <= response.status_code < 300:
                    body = self._safe_json(response)
                    return self._wrap_success(body, attempt_latency_ms)
                if response.status_code in (401, 403):
                    return make_error_envelope(
                        f"auth_error: HTTP {response.status_code}",
                        degraded=False,
                    )
                if response.status_code == 404:
                    return make_error_envelope(
                        "not_found",
                        degraded=False,
                    )
                last_error = f"http_{response.status_code}"
                logger.debug(
                    "ControlApiClient: HTTP %s failed, attempt=%d url=%s",
                    response.status_code, attempt, url,
                )
            except requests.exceptions.Timeout as exc:
                last_error = f"timeout: {type(exc).__name__}"
                logger.debug("ControlApiClient: Timeout attempt=%d url=%s", attempt, url)
            except requests.exceptions.ConnectionError as exc:
                last_error = f"connection_error: {type(exc).__name__}"
                logger.debug("ControlApiClient: ConnectionError attempt=%d url=%s", attempt, url)
            except requests.exceptions.SSLError as exc:
                last_error = f"ssl_error: {type(exc).__name__}"
                logger.debug("ControlApiClient: SSLError attempt=%d url=%s", attempt, url)
            except requests.exceptions.RequestException as exc:
                last_error = f"request_error: {type(exc).__name__}"
                logger.debug("ControlApiClient: RequestException attempt=%d url=%s", attempt, url)
            except Exception as exc:  # noqa: BLE001
                last_error = f"unexpected: {type(exc).__name__}: {exc}"
                logger.debug("ControlApiClient: Unknown attempt=%d url=%s err=%s", attempt, url, exc)
            if attempt < max_attempts:
                backoff = self._config.retry_backoff_seconds * attempt
                time.sleep(backoff)

        total_latency_ms = (time.monotonic() - total_start) * 1000.0
        return make_error_envelope(last_error or "request_failed", degraded=True)

    def _wrap_success(self, body: Any, latency_ms: float) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        if isinstance(body, dict):
            if "success" in body and "data" in body:
                body.setdefault("schema_version", CONTROL_SCHEMA_VERSION)
                body.setdefault("timestamp", now)
                body.setdefault("error", "")
                body.setdefault("degraded", False)
                body["latency_ms"] = float(latency_ms)
                return body
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

    def _build_headers(
        self,
        extra_headers: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        h: Dict[str, str] = {
            "User-Agent": self._config.user_agent,
            "Accept": "application/json",
            "X-Client": "YuyiDesktop",
            "X-Schema-Version": CONTROL_SCHEMA_VERSION,
        }
        if self._token_provider is not None:
            try:
                token = self._token_provider()
                if token:
                    h["Authorization"] = f"Bearer {token}"
            except Exception as exc:  # noqa: BLE001
                logger.debug("ControlApiClient: token_provider 失败: %s", exc)
        if extra_headers:
            for k, v in extra_headers.items():
                if v is None:
                    continue
                h[str(k)] = str(v)
        return h

    @staticmethod
    def _safe_json(response: requests.Response) -> Any:
        try:
            return response.json()
        except Exception:
            try:
                return {"_text": (response.text or "")[:2000]}
            except Exception:
                return None


# ============================================================
# 模块级单例
# ============================================================
_client_instance: Optional[ControlApiClient] = None
_client_lock = threading.Lock()


def get_control_api_client() -> ControlApiClient:
    """获取 ControlApiClient 单例(懒加载)。"""
    global _client_instance
    if _client_instance is None:
        with _client_lock:
            if _client_instance is None:
                _client_instance = ControlApiClient()
    return _client_instance


def reset_control_api_client_for_testing(
    config: Optional[ControlApiConfig] = None,
    session: Optional[requests.Session] = None,
    token_provider: Optional[Any] = None,
) -> ControlApiClient:
    """测试用:重置并返回新实例。"""
    global _client_instance
    with _client_lock:
        _client_instance = ControlApiClient(
            config=config,
            session=session,
            token_provider=token_provider,
        )
    return _client_instance


__all__ = [
    "CONTROL_SCHEMA_VERSION",
    "ControlApiConfig",
    "ControlApiClient",
    "make_envelope",
    "make_error_envelope",
    "make_success_envelope",
    "get_control_api_client",
    "reset_control_api_client_for_testing",
]
