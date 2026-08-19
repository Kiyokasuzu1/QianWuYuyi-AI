# -*- coding: utf-8 -*-
"""
src/control/api/auth.py

Phase C.10.3 — Bearer Token 认证

认证模式:
  - "disabled":   任何请求都允许(仅用于测试)
  - "development": token 匹配即通过(token 从 config.yaml 读取)
  - "production":  token 必须存在且匹配(required=True)

不存任何敏感信息到日志。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

from flask import Request, jsonify

from .config import GatewayAuthConfig, get_gateway_config
from .envelope import make_error_envelope

logger = logging.getLogger(__name__)


# ============================================================
# 认证结果
# ============================================================
@dataclass
class AuthResult:
    """认证结果。"""

    ok: bool
    reason: str = ""
    mode: str = "unknown"

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "mode": self.mode}


# ============================================================
# 内部状态(允许测试时注入)
# ============================================================
_auth_override: Optional[GatewayAuthConfig] = None
_auth_lock = threading.Lock()


def set_auth_override(cfg: Optional[GatewayAuthConfig]) -> None:
    """测试用:覆盖 auth 配置。"""
    global _auth_override
    with _auth_lock:
        _auth_override = cfg


def get_effective_auth_config() -> GatewayAuthConfig:
    """获取当前生效的 auth 配置(允许测试覆盖)。"""
    if _auth_override is not None:
        return _auth_override
    try:
        return get_gateway_config().auth
    except Exception:  # noqa: BLE001
        return GatewayAuthConfig()


# ============================================================
# Token 解析与校验
# ============================================================
def _extract_bearer_token(request: Request) -> str:
    """从 Flask Request 抽取 Bearer token。"""
    try:
        header = request.headers.get("Authorization", "") or ""
    except Exception:  # noqa: BLE001
        header = ""
    header = header.strip()
    if not header:
        return ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def _safe_compare(a: str, b: str) -> bool:
    """常量时间字符串比较(防 timing attack)。"""
    if not a or not b:
        return False
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0


def check_bearer_token(request: Request) -> AuthResult:
    """
    校验请求是否带合法 Bearer token。

    Returns:
        AuthResult(ok/reason/mode)
    """
    cfg = get_effective_auth_config()
    mode = cfg.mode
    if mode == "disabled":
        return AuthResult(ok=True, reason="auth_disabled", mode=mode)

    provided = _extract_bearer_token(request)
    if not provided:
        if cfg.required:
            return AuthResult(ok=False, reason="missing_token", mode=mode)
        # 非强制模式:允许无 token 访问
        return AuthResult(ok=True, reason="no_token_optional", mode=mode)

    if not cfg.token:
        # 没有配置 token,但客户端提供了 token:在 development 模式放行
        if mode == "development":
            return AuthResult(ok=True, reason="dev_mode_no_server_token", mode=mode)
        return AuthResult(ok=False, reason="server_token_not_configured", mode=mode)

    if _safe_compare(provided, cfg.token):
        return AuthResult(ok=True, reason="ok", mode=mode)

    return AuthResult(ok=False, reason="invalid_token", mode=mode)


# ============================================================
# Flask 装饰器/钩子
# ============================================================
def _unauthorized_response(reason: str, mode: str):
    """构造 401 响应。"""
    body = make_error_envelope(
        error=f"unauthorized: {reason}",
        data={"auth_mode": mode},
        degraded=False,
    )
    resp = jsonify(body)
    resp.status_code = 401
    return resp


def require_auth(view_func):
    """
    Flask 视图装饰器:要求请求带合法 Bearer token。

    Usage:
        @gateway_bp.route("/api/v1/runtime/status")
        @require_auth
        def api_runtime_status():
            ...
    """
    def wrapper(*args, **kwargs):
        from flask import request  # 延迟 import,便于测试
        result = check_bearer_token(request)
        if not result.ok:
            logger.info(
                "Gateway auth rejected: reason=%s mode=%s path=%s",
                result.reason,
                result.mode,
                getattr(request, "path", "<unknown>"),
            )
            return _unauthorized_response(result.reason, result.mode)
        return view_func(*args, **kwargs)

    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


__all__ = [
    "AuthResult",
    "check_bearer_token",
    "get_effective_auth_config",
    "require_auth",
    "set_auth_override",
]
