# -*- coding: utf-8 -*-
"""
src/control/api/__init__.py

Phase C.10.3 — Yuyi Server API Gateway

提供 Yuyi Desktop 通过 HTTPS/WebSocket 访问的服务器 API 入口。
本层为隔离层,仅消费已有 Provider 的只读快照,绝不直接修改
src/runtime/** / src/memory/** / src/growth/** / src/personality/**
/ src/self_model/** / src/relationship/** 等业务核心。

设计原则:
- 只读优先
- 8 个 GET endpoint 覆盖 Runtime / Memory / Personality / SelfModel
  / Growth / Initiative / Audit / Health
- Bearer Token 认证(config.yaml)
- 统一 envelope 响应
- 异常降级,绝不向 Desktop 抛 5xx 之外的不可控错误
"""

from .config import GatewayAuthConfig, GatewayConfig, get_gateway_config
from .envelope import (
    API_SCHEMA_VERSION,
    is_envelope,
    make_envelope,
    make_error_envelope,
    make_success_envelope,
)
from .auth import (
    AuthResult,
    check_bearer_token,
    require_auth,
)
from .routes import gateway_bp, register_gateway
from .control_routes import control_bp, register_control_api

__all__ = [
    # config
    "GatewayAuthConfig",
    "GatewayConfig",
    "get_gateway_config",
    # envelope
    "API_SCHEMA_VERSION",
    "is_envelope",
    "make_envelope",
    "make_error_envelope",
    "make_success_envelope",
    # auth
    "AuthResult",
    "check_bearer_token",
    "require_auth",
    # routes
    "gateway_bp",
    "register_gateway",
    # control routes (C.10.5.4)
    "control_bp",
    "register_control_api",
]
