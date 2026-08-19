# -*- coding: utf-8 -*-
"""
yuyi_desktop/core/auth_client.py

Phase C.10.2 —— Yuyi Desktop Auth Client

负责:
- 本地保存/读取 Auth Token
- 提供给 ApiClient 注入(只读 token 字符串)
- 不发起任何写请求(POST /auth/login 等)
- 全部在 Desktop 本地完成

设计原则:
- Phase C.10.2 阶段:仅支持"已注入 token"模式(由部署者预置)
- 不实现登录流程(避免在 Desktop 端写鉴权副作用)
- 任何 token 错误返回空字符串 + 上报错误

约束:
- 不 import src.*
- 任何 IO 异常都安全降级(返回空 token)
- 文件权限:仅 0600(由调用者保证)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Token 状态
# ============================================================
@dataclass
class AuthState:
    """Auth 状态(线程安全快照)。"""

    has_token: bool = False
    token_preview: str = ""
    source: str = "none"  # none | env | file | injected
    loaded_at: str = ""
    last_error: str = ""

    def snapshot(self) -> Dict[str, Any]:
        return {
            "has_token": bool(self.has_token),
            "token_preview": str(self.token_preview or ""),
            "source": str(self.source or "none"),
            "loaded_at": str(self.loaded_at or ""),
            "last_error": str(self.last_error or ""),
        }


# ============================================================
# Auth Client
# ============================================================
class AuthClient:
    """
    Yuyi Desktop 鉴权客户端(只读 token)。

    优先级:
        1. 注入(injected)
        2. 环境变量 YUYI_DESKTOP_TOKEN
        3. 本地文件 ~/.yuyi_desktop/token.json
    """

    ENV_TOKEN_KEY = "YUYI_DESKTOP_TOKEN"
    DEFAULT_TOKEN_FILE = ".yuyi_desktop/token.json"

    def __init__(
        self,
        token: Optional[str] = None,
        token_file: Optional[str] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._state = AuthState()
        # 显式注入优先
        if token:
            self._apply_token(token, source="injected")
        else:
            # 尝试环境变量
            env_token = os.environ.get(self.ENV_TOKEN_KEY, "")
            if env_token:
                self._apply_token(env_token, source="env")
            else:
                # 尝试文件
                file_path = token_file or self._default_token_path()
                if file_path:
                    self._try_load_file(file_path)

    # --------------------------------------------------------
    # 对外 API
    # --------------------------------------------------------
    def get_token(self) -> str:
        """获取当前 token(可能为空)。"""
        with self._lock:
            if self._state.has_token:
                return self._peek_token()
            return ""

    def token_provider(self) -> str:
        """作为 callable 供 ApiClient 注入。"""
        return self.get_token()

    def is_authenticated(self) -> bool:
        with self._lock:
            return bool(self._state.has_token)

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self._state.snapshot()

    def clear(self) -> None:
        """清空 token(仅清状态,不动文件)。"""
        with self._lock:
            self._token = ""
            self._state = AuthState()

    # --------------------------------------------------------
    # 内部
    # --------------------------------------------------------
    def _apply_token(self, token: str, source: str) -> None:
        token = (token or "").strip()
        if not token:
            return
        with self._lock:
            self._token = token
            self._state.has_token = True
            self._state.source = source
            self._state.loaded_at = datetime.now(timezone.utc).isoformat()
            self._state.last_error = ""
            # preview: 头 4 + 尾 4
            if len(token) > 8:
                self._state.token_preview = f"{token[:4]}...{token[-4:]}"
            else:
                self._state.token_preview = "***"

    def _peek_token(self) -> str:
        # 假设已加锁
        return getattr(self, "_token", "")

    def _try_load_file(self, file_path: str) -> None:
        try:
            path = Path(file_path)
            if not path.exists():
                self._record_error(f"file_not_found: {file_path}")
                return
            # Phase D.2.9 修复: 兼容 UTF-8 BOM (PowerShell Set-Content 默认会写 BOM)
            # 优先用 utf-8-sig 自动剥离 BOM,失败再回退 utf-8
            data = None
            for enc in ("utf-8-sig", "utf-8"):
                try:
                    with open(path, "r", encoding=enc) as f:
                        data = json.load(f)
                    break
                except UnicodeDecodeError:
                    continue
                except json.JSONDecodeError as exc:
                    self._record_error(f"json_decode_error: {exc}")
                    return
            if data is None:
                self._record_error("decode_failed")
                return
            if not isinstance(data, dict):
                self._record_error("invalid_format")
                return
            token = data.get("token", "")
            if not token:
                self._record_error("empty_token")
                return
            self._apply_token(str(token), source="file")
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"load_failed: {type(exc).__name__}")

    def _record_error(self, msg: str) -> None:
        with self._lock:
            self._state.last_error = str(msg)
            logger.debug("AuthClient: %s", msg)

    @staticmethod
    def _default_token_path() -> Optional[str]:
        try:
            home = Path.home()
            return str(home / AuthClient.DEFAULT_TOKEN_FILE)
        except Exception:  # noqa: BLE001
            return None


# ============================================================
# 模块级单例
# ============================================================
_auth_instance: Optional[AuthClient] = None
_auth_lock = threading.Lock()


def get_auth_client() -> AuthClient:
    """获取 AuthClient 单例(懒加载)。"""
    global _auth_instance
    if _auth_instance is None:
        with _auth_lock:
            if _auth_instance is None:
                _auth_instance = AuthClient()
    return _auth_instance


def reset_auth_client_for_testing() -> None:
    """测试用:重置单例。"""
    global _auth_instance
    with _auth_lock:
        _auth_instance = None


__all__ = [
    "AuthState",
    "AuthClient",
    "get_auth_client",
    "reset_auth_client_for_testing",
]
