# -*- coding: utf-8 -*-
"""
src/admin/dashboard/_security.py

Phase 5.0 Dashboard Upgrade Step 8.4.7 —— Dashboard POST Security Middleware。

职责:
1. Local Only 检查(127.0.0.1 / localhost)
2. Token 验证(复用 admin auth 机制,默认 dev token 可配置)
3. Permission 检查(role → actions 映射)
4. 鉴权失败时,统一返回 401 / 403 响应
5. 任何异常隔离,绝不抛到 Router 外

架构约束:
- 禁止 import: memory / growth / emotion / personality / relationship / runtime /
  self_model / llm
- 不调用 LLM
- 不创建业务事件
- 不持有运行时状态(只缓存 role → permission 映射,可被测试覆盖)
- 严格只读,绝不在内部执行业务动作

数据流:
    Router (POST)
        ↓
    require_dashboard_post_auth(permission="...")
        ↓
    Auth (token) → Permission (role → action) → Principal
        ↓
    Router 继续处理(后续可接 Governance + Audit)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple

from flask import jsonify, request

logger = logging.getLogger(__name__)


# ============================================================
# 常量
# ============================================================

# 允许的本地主机
ALLOWED_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0"})

# 角色 → 权限 映射
DEFAULT_ROLE_PERMISSIONS: Dict[str, List[str]] = {
    "admin": ["*"],
    "operator": [
        "runtime.read",
        "runtime.control",
        "dashboard.read",
        "dashboard.control",
    ],
    "viewer": [
        "dashboard.read",
    ],
}

# Token 验证用默认 dev token(可通过环境变量 YUYI_DASHBOARD_TOKEN 覆盖)
DEFAULT_DEV_TOKEN = "yuyi-dashboard-dev-token-2026"


# ============================================================
# 内部:可注入的 role/permission / token / remote_host 覆盖
# ============================================================
_security_singleton: Dict[str, Any] = {}
_security_lock = threading.RLock()


def _get_role_permissions() -> Dict[str, List[str]]:
    with _security_lock:
        overridden = _security_singleton.get("role_permissions")
        if isinstance(overridden, dict) and overridden:
            return overridden
    return DEFAULT_ROLE_PERMISSIONS


def _get_token_resolver() -> Optional[Callable[[], Optional[str]]]:
    """返回一个 callable,用于获取"当前合法 token 集合"。

    允许返回:
        - None: 无 token 配置
        - 单个 str: 单 token
        - list[str]: 多 token

    测试可注入该 resolver 以控制 token 行为。
    """
    with _security_lock:
        resolver = _security_singleton.get("token_resolver")
        if resolver is not None:
            return resolver
    return None


def _get_extra_tokens() -> List[str]:
    """测试可注入额外合法 token(非破坏 DEFAULT_DEV_TOKEN)。"""
    with _security_lock:
        extra = _security_singleton.get("extra_tokens") or []
    return list(extra) if isinstance(extra, list) else []


def _resolve_token() -> List[str]:
    """解析当前所有合法 token(环境变量 / dev token / 注入)。"""
    tokens: List[str] = []
    try:
        env = os.environ.get("YUYI_DASHBOARD_TOKEN", "")
        if env:
            tokens.append(env)
    except Exception:  # noqa: BLE001
        pass
    tokens.append(DEFAULT_DEV_TOKEN)
    # 注入 resolver
    resolver = _get_token_resolver()
    if resolver is not None:
        try:
            v = resolver()
            if isinstance(v, str) and v:
                tokens.append(v)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, str) and x:
                        tokens.append(x)
        except Exception:  # noqa: BLE001
            pass
    # 注入额外 token
    for t in _get_extra_tokens():
        if isinstance(t, str) and t:
            tokens.append(t)
    # 去重
    seen = set()
    out: List[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# ============================================================
# Public Injection API(供测试 / 生产配置)
# ============================================================
def set_role_permissions_for_dashboard(perms: Optional[Dict[str, List[str]]]) -> None:
    """注入 role → permissions 映射(测试 / 高级配置)。"""
    with _security_lock:
        if perms is None:
            _security_singleton.pop("role_permissions", None)
        else:
            _security_singleton["role_permissions"] = dict(perms)


def set_token_resolver_for_dashboard(resolver: Optional[Callable[[], Optional[str]]]) -> None:
    """注入 token resolver(测试用)。"""
    with _security_lock:
        if resolver is None:
            _security_singleton.pop("token_resolver", None)
        else:
            _security_singleton["token_resolver"] = resolver


def set_extra_tokens_for_dashboard(tokens: Optional[List[str]]) -> None:
    """注入额外合法 token(测试用)。"""
    with _security_lock:
        if tokens is None:
            _security_singleton.pop("extra_tokens", None)
        else:
            _security_singleton["extra_tokens"] = list(tokens)


def reset_security_overrides_for_dashboard() -> None:
    """测试用:重置所有注入。"""
    with _security_lock:
        _security_singleton.clear()


# ============================================================
# Local-only check
# ============================================================
def is_local_request() -> bool:
    """判断当前请求是否来自本地。"""
    try:
        remote = (request.remote_addr or "").strip()
        if not remote:
            return False
        # 同时检查 X-Forwarded-For(本机回环测试场景)
        try:
            xff = (request.headers.get("X-Forwarded-For") or "").strip()
            if xff:
                remote = xff.split(",")[0].strip()
        except Exception:  # noqa: BLE001
            pass
        return remote in ALLOWED_LOCAL_HOSTS
    except Exception:  # noqa: BLE001
        return False


# ============================================================
# Token validation
# ============================================================
def _extract_bearer_token() -> Optional[str]:
    """从请求头 / query / form 提取 token(Authorization / X-Dashboard-Token / token)。"""
    try:
        # 1) Authorization: Bearer <token>
        auth = (request.headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            t = auth[7:].strip()
            if t:
                return t
        # 2) X-Dashboard-Token
        x = (request.headers.get("X-Dashboard-Token") or "").strip()
        if x:
            return x
        # 3) query / form: token=<...>
        try:
            q = request.args.get("token")
            if q:
                return q
        except Exception:  # noqa: BLE001
            pass
        try:
            f = request.form.get("token")
            if f:
                return f
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        return None
    return None


def validate_token(provided: Optional[str]) -> Tuple[bool, str]:
    """
    校验 token。

    Returns:
        (ok, reason)
        reason: ok | missing_token | invalid_token
    """
    if not provided or not isinstance(provided, str):
        return False, "missing_token"
    valid = _resolve_token()
    if not valid:
        return False, "invalid_token"
    for t in valid:
        if not t:
            continue
        # 用 hmac.compare_digest 防时序攻击
        try:
            if hmac.compare_digest(str(t), str(provided)):
                return True, "ok"
        except Exception:  # noqa: BLE001
            continue
    return False, "invalid_token"


# ============================================================
# Permission check
# ============================================================
def _extract_role_from_token(token: Optional[str]) -> str:
    """
    从 token 推断 role。

    协议:token 形如 "<role>:<secret>" → 解析出 role。
    否则默认 "operator"(dev 兼容)。
    """
    if not token or not isinstance(token, str):
        return "operator"
    if ":" in token:
        role = token.split(":", 1)[0].strip()
        if role:
            return role
    return "operator"


def role_has_permission(role: str, permission: str) -> bool:
    """判断 role 是否拥有指定 permission。"""
    if not role or not permission:
        return False
    perms = _get_role_permissions()
    role_perms = perms.get(role) or []
    if "*" in role_perms:
        return True
    if permission in role_perms:
        return True
    # 支持通配: "dashboard.*" 匹配 "dashboard.read"
    for p in role_perms:
        if not isinstance(p, str):
            continue
        if p.endswith(".*"):
            prefix = p[:-2]
            if permission.startswith(prefix + "."):
                return True
    return False


def check_permission(role: str, permission: str) -> Tuple[bool, str]:
    """
    检查 role 是否拥有指定 permission。

    Returns:
        (ok, reason)
        reason: ok | insufficient_role | role_not_found
    """
    perms = _get_role_permissions()
    if role not in perms:
        return False, "role_not_found"
    if role_has_permission(role, permission):
        return True, "ok"
    return False, "insufficient_role"


# ============================================================
# Principal
# ============================================================
class Principal:
    """已通过 Auth 的请求主体。"""

    __slots__ = ("role", "token_hash", "remote_addr", "raw_token")

    def __init__(self, role: str, token_hash: str, remote_addr: str, raw_token: str) -> None:
        self.role = role
        self.token_hash = token_hash
        self.remote_addr = remote_addr
        # raw_token 仅供审计使用 —— 严格不返回给客户端
        self.raw_token = raw_token

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "token_hash": self.token_hash,
            "remote_addr": self.remote_addr,
        }


# ============================================================
# Response helpers
# ============================================================
def _security_error(
    *,
    code: str,
    message: str,
    http_status: int,
    details: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, int]:
    payload: Dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
        },
        "timestamp": _now_iso(),
    }
    if details:
        payload["error"]["details"] = details
    return jsonify(payload), http_status


def _now_iso() -> str:
    try:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        try:
            return datetime.utcnow().isoformat() + "Z"  # noqa: F821
        except Exception:  # noqa: BLE001
            return ""


def _hash_token(token: str) -> str:
    """对 token 哈希后用于审计(避免明文落盘)。"""
    try:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "unknown"


# ============================================================
# Decorator
# ============================================================
def require_dashboard_post_auth(
    permission: str = "dashboard.read",
    *,
    allow_local_bypass: bool = False,
) -> Callable:
    """
    Dashboard POST 鉴权装饰器。

    链路:
        1) Local Only(本地访问限制)
        2) Token 校验
        3) Permission 校验(role → permission 映射)

    Args:
        permission: 需要的 permission(如 "runtime.control" / "dashboard.read")。
        allow_local_bypass: 本地访问是否绕过 token 校验(默认 False,生产环境严格)。
                           仅供 dev/测试场景使用,严禁默认开启。

    Returns:
        decorator —— 接受 Flask view function

    失败响应:
        - 401: missing_token / invalid_token
        - 403: dashboard_local_only / insufficient_role / role_not_found

    通过后:
        - 注入 `g.dashboard_principal` 与 `g.dashboard_role` 供后续 view 使用
        - 注入 `g.dashboard_raw_token`(供 audit 使用)
    """
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                # 1) Local Only
                if not is_local_request():
                    logger.warning("dashboard.post rejected: non-local request")
                    return _security_error(
                        code="dashboard_local_only",
                        message="Dashboard POST 仅允许本地访问",
                        http_status=403,
                        details={"remote_addr": "non-local"},
                    )

                # 2) Token 校验
                token = _extract_bearer_token()
                # 本地绕过(仅 dev)
                if not token and allow_local_bypass:
                    token = DEFAULT_DEV_TOKEN
                ok, reason = validate_token(token)
                if not ok:
                    logger.warning("dashboard.post rejected: token check failed (%s)", reason)
                    return _security_error(
                        code=reason,  # missing_token / invalid_token
                        message=("Token 缺失" if reason == "missing_token" else "Token 无效"),
                        http_status=401,
                        details={"reason": reason},
                    )

                # 3) Permission 校验
                role = _extract_role_from_token(token)
                ok2, reason2 = check_permission(role, permission)
                if not ok2:
                    logger.warning(
                        "dashboard.post rejected: role=%s permission=%s reason=%s",
                        role, permission, reason2,
                    )
                    return _security_error(
                        code="insufficient_role",
                        message=f"角色 {role} 缺少权限 {permission}",
                        http_status=403,
                        details={"role": role, "permission": permission, "reason": reason2},
                    )

                # 4) 注入 principal
                try:
                    from flask import g
                    g.dashboard_principal = Principal(
                        role=role,
                        token_hash=_hash_token(token) if token else "",
                        remote_addr=(request.remote_addr or ""),
                        raw_token=token or "",
                    )
                    g.dashboard_role = role
                    g.dashboard_permission = permission
                except Exception:  # noqa: BLE001
                    pass

                return view(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.warning("dashboard.post security middleware 异常: %s", exc)
                return _security_error(
                    code="security_middleware_error",
                    message="鉴权中间件异常",
                    http_status=500,
                    details={"error_name": type(exc).__name__},
                )

        return wrapper

    return decorator


# ============================================================
# Public utility:供 Router / Audit 使用
# ============================================================
def get_current_principal() -> Optional[Dict[str, Any]]:
    """从 flask.g 读取当前 principal(供 audit 记录)。"""
    try:
        from flask import g
        p = getattr(g, "dashboard_principal", None)
        if p is None:
            return None
        if isinstance(p, Principal):
            return p.to_dict()
        if isinstance(p, dict):
            return dict(p)
    except Exception:  # noqa: BLE001
        return None
    return None


def get_current_role() -> Optional[str]:
    """从 flask.g 读取当前 role。"""
    try:
        from flask import g
        r = getattr(g, "dashboard_role", None)
        return r if isinstance(r, str) else None
    except Exception:  # noqa: BLE001
        return None


__all__ = [
    "ALLOWED_LOCAL_HOSTS",
    "DEFAULT_ROLE_PERMISSIONS",
    "DEFAULT_DEV_TOKEN",
    "Principal",
    "is_local_request",
    "validate_token",
    "check_permission",
    "role_has_permission",
    "require_dashboard_post_auth",
    "set_role_permissions_for_dashboard",
    "set_token_resolver_for_dashboard",
    "set_extra_tokens_for_dashboard",
    "reset_security_overrides_for_dashboard",
    "get_current_principal",
    "get_current_role",
]
