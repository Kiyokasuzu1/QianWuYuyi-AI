# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/desktop_context.py

Phase C.10.2 —— Desktop Context(远程桥接版)

桌面端全局上下文,统一管理:
- RemoteProviderBridge(远程 API 桥接,主用)
- ConnectionManager(连接状态/心跳)
- AuthClient(本地 token)
- DesktopConfig(只读配置)
- 运行状态标记

约束:
- 单例,线程安全
- 不可变 API,所有修改通过 reset_for_testing 完成
- 不影响主项目 Runtime
- Phase C.10.2:仅通过 RemoteProviderBridge 访问 Server
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from yuyi_desktop.config.desktop_config import (
    DesktopConfig,
    get_desktop_config,
)
from yuyi_desktop.core.api_client import (
    ApiClient,
    get_api_client,
    reset_api_client_for_testing,
)
from yuyi_desktop.core.auth_client import (
    AuthClient,
    get_auth_client,
    reset_auth_client_for_testing,
)
from yuyi_desktop.core.connection_manager import (
    ConnectionManager,
    get_connection_manager,
    reset_connection_manager_for_testing,
)
from yuyi_desktop.core.remote_provider_bridge import (
    RemoteProviderBridge,
    get_remote_provider_bridge,
    reset_remote_provider_bridge_for_testing,
)

logger = logging.getLogger(__name__)


class DesktopContext:
    """
    Yuyi Desktop 全局上下文(Phase C.10.2)。

    提供:
        - config: DesktopConfig
        - bridge: RemoteProviderBridge(主用)
        - connection: ConnectionManager
        - auth: AuthClient
        - api: ApiClient
        - is_ready(): bool

    Usage:
        ctx = get_desktop_context()
        status = ctx.bridge.get_runtime_snapshot()
    """

    def __init__(
        self,
        config: Optional[DesktopConfig] = None,
        bridge: Optional[RemoteProviderBridge] = None,
        connection: Optional[ConnectionManager] = None,
        auth: Optional[AuthClient] = None,
        api: Optional[ApiClient] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._config = config if config is not None else get_desktop_config()
        # 重要: 让 AuthClient 优先使用 DesktopConfig.auth_token(injected),
        # 保留 AuthClient 原有优先级: injected > env > file
        if auth is None:
            if self._config.has_auth_token():
                auth = AuthClient(token=self._config.auth_token)
                logger.info(
                    "DesktopContext: AuthClient 由 DesktopConfig.auth_token 注入"
                )
            else:
                auth = get_auth_client()
        self._auth = auth
        # 重要:让 ApiClient 持有 auth.token_provider,使所有 /api/v1/* 请求自动带 token
        if api is None:
            api = get_api_client()
        # Phase D.2.3 P1: 实际绑定 token_provider (此前注释承诺但未生效)
        # 兼容单例模式: 仅在 token_provider 为空时绑定
        try:
            if getattr(api, "_token_provider", None) is None:
                api._token_provider = self._auth.token_provider  # type: ignore[attr-defined]
                logger.info(
                    "DesktopContext: token_provider 已绑定 (source=%s)",
                    self._auth.get_state().get("source", "unknown"),
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("DesktopContext: 绑定 token_provider 失败: %s", exc)
        self._api = api
        # 让 bridge 持有我们注入的 api client
        if bridge is None:
            bridge = RemoteProviderBridge(api_client=self._api)
        self._bridge = bridge
        # ConnectionManager 持有同一个 api client
        if connection is None:
            connection = ConnectionManager(api_client=self._api)
        self._connection = connection
        self._ready = False

    @property
    def config(self) -> DesktopConfig:
        return self._config

    @property
    def bridge(self) -> RemoteProviderBridge:
        return self._bridge

    @property
    def connection(self) -> ConnectionManager:
        return self._connection

    @property
    def auth(self) -> AuthClient:
        return self._auth

    @property
    def api(self) -> ApiClient:
        return self._api

    def mark_ready(self) -> None:
        with self._lock:
            self._ready = True

    def mark_not_ready(self) -> None:
        with self._lock:
            self._ready = False

    def is_ready(self) -> bool:
        with self._lock:
            return self._ready

    def health_snapshot(self) -> Dict[str, Any]:
        """返回 health 概览(dict,纯本地状态)。"""
        with self._lock:
            return {
                "ready": self._ready,
                "tab_count": self._config.get_tab_count(),
                "auth": self._auth.get_state(),
                "connection": self._connection.get_status(),
                "provider_health": {},
            }

    def full_health_snapshot(self) -> Dict[str, Any]:
        """返回 health 概览(含远程 Provider 分组,会发请求)。"""
        with self._lock:
            snap = self.health_snapshot()
            try:
                snap["provider_health"] = self._bridge.health_check()
            except Exception as exc:  # noqa: BLE001
                logger.debug("DesktopContext.full_health_snapshot 异常: %s", exc)
                snap["provider_health"] = {}
            return snap


# ============================================================
# 模块级单例
# ============================================================
_ctx_instance: Optional[DesktopContext] = None
_ctx_lock = threading.Lock()


def get_desktop_context() -> DesktopContext:
    """获取 DesktopContext 单例(懒加载)。"""
    global _ctx_instance
    if _ctx_instance is None:
        with _ctx_lock:
            if _ctx_instance is None:
                _ctx_instance = DesktopContext()
    return _ctx_instance


def reset_desktop_context_for_testing(
    config: Optional[DesktopConfig] = None,
    bridge: Optional[RemoteProviderBridge] = None,
    connection: Optional[ConnectionManager] = None,
    auth: Optional[AuthClient] = None,
    api: Optional[ApiClient] = None,
) -> DesktopContext:
    """
    测试用:重置 DesktopContext 单例,并可选注入 bridge/connection/auth/api。
    返回新建的 context。
    """
    global _ctx_instance
    with _ctx_lock:
        reset_api_client_for_testing()
        reset_auth_client_for_testing()
        reset_connection_manager_for_testing()
        reset_remote_provider_bridge_for_testing()
        _ctx_instance = DesktopContext(
            config=config,
            bridge=bridge,
            connection=connection,
            auth=auth,
            api=api,
        )
    return _ctx_instance


__all__ = [
    "DesktopContext",
    "get_desktop_context",
    "reset_desktop_context_for_testing",
]
