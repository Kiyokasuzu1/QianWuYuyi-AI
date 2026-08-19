# -*- coding: utf-8 -*-
"""
src/control/api/control_routes.py

Phase C.10.5.4 + C.10.5.5 — Yuyi Server Control API

独立的 namespace:
    /api/v1/control/

提供:
    GET  /api/v1/control/status         # 状态总览
    GET  /api/v1/control/modules        # 模块列表
    GET  /api/v1/control/audit          # 审计查询

    POST /api/v1/control/module/<name>/enable   # 启用模块
    POST /api/v1/control/module/<name>/disable  # 禁用模块
    POST /api/v1/control/module/<name>/toggle   # 切换模块

    POST /api/v1/control/safe_mode/enable
    POST /api/v1/control/safe_mode/disable

    POST /api/v1/control/maintenance/enable
    POST /api/v1/control/maintenance/disable

所有写接口:
- 强制 Bearer Token 认证(@require_auth)
- 自动追加 audit
- 内部使用 ControlManager(只改 ControlState,不直接操作业务对象)

约束:
- 不直接 import 任何 src.runtime.* / src.memory.* / src.growth.* / ...
- 不引入新的 Provider
- 不破坏现有 routes.py 的只读语义(GET-only)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from .auth import require_auth
from .envelope import (
    API_SCHEMA_VERSION,
    make_error_envelope,
    make_success_envelope,
)
from src.control.manager.control_manager import (
    ControlManager,
    get_control_manager,
)

logger = logging.getLogger(__name__)


# ============================================================
# Blueprint(独立 namespace)
# ============================================================
control_bp = Blueprint(
    "yuyi_control",
    __name__,
    url_prefix="/api/v1/control",
)


_CONTROL_START_TIME = time.time()


# ============================================================
# 工具
# ============================================================
def _ok(data: Dict[str, Any]):
    return jsonify(make_success_envelope(data=data, schema_version=API_SCHEMA_VERSION))


def _err(error: str, status_code: int = 200, data: Optional[Dict[str, Any]] = None):
    body = make_error_envelope(
        error=error,
        data=data or {},
        degraded=False,
        schema_version=API_SCHEMA_VERSION,
    )
    resp = jsonify(body)
    if status_code != 200:
        resp.status_code = status_code
    return resp


def _get_operator() -> str:
    """从 header 抽取 operator(默认 'desktop')。"""
    try:
        op = request.headers.get("X-Operator", "") or ""
    except Exception:
        op = ""
    op = op.strip()
    if not op:
        # 尝试从 query 拿
        try:
            op = (request.args.get("operator") or "").strip()
        except Exception:
            op = ""
    if not op:
        op = "desktop"
    # 限制长度
    if len(op) > 64:
        op = op[:64]
    return op


def _get_reason() -> str:
    """从 body / query 抽取 reason。"""
    reason = ""
    try:
        if request.method == "POST" and request.is_json:
            body = request.get_json(silent=True) or {}
            if isinstance(body, dict):
                reason = str(body.get("reason", "") or "")
    except Exception:
        reason = ""
    if not reason:
        try:
            reason = str(request.args.get("reason", "") or "")
        except Exception:
            reason = ""
    if len(reason) > 256:
        reason = reason[:256]
    return reason.strip()


def _get_manager() -> ControlManager:
    """获取 ControlManager 单例(失败时使用 lazy init)。"""
    try:
        return get_control_manager()
    except Exception as exc:  # noqa: BLE001
        logger.error("control_api: ControlManager 不可用: %s", exc)
        raise


# ============================================================
# 1) GET /api/v1/control/status
# ============================================================
@control_bp.route("/status", methods=["GET"])
@require_auth
def api_control_status():
    """获取控制平面状态总览(模块 + 系统模式 + 元信息)。"""
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)

    try:
        overview = manager.get_overview()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_status_error: {type(exc).__name__}: {exc}")

    data = dict(overview)
    data["server_status"] = "ok"
    data["version"] = "0.10.5"
    data["schema_version"] = API_SCHEMA_VERSION
    data["uptime_seconds"] = max(0.0, time.time() - _CONTROL_START_TIME)
    return _ok(data)


# ============================================================
# 2) GET /api/v1/control/modules
# ============================================================
@control_bp.route("/modules", methods=["GET"])
@require_auth
def api_control_modules():
    """获取所有模块的元信息 + 当前启用状态。"""
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)

    try:
        from src.control.registry.module_registry import get_module_registry
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        registry = get_module_registry()
        state = get_control_state_persistence().get_state()
        modules = registry.get_status(state_provider=state)
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_modules_error: {type(exc).__name__}: {exc}")

    return _ok({
        "available": True,
        "count": len(modules),
        "modules": modules,
        "schema_version": API_SCHEMA_VERSION,
    })


# ============================================================
# 3) GET /api/v1/control/audit
# ============================================================
@control_bp.route("/audit", methods=["GET"])
@require_auth
def api_control_audit():
    """查询控制平面审计日志。"""
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(200, limit))

    field_filter = request.args.get("field", None) or None
    try:
        from src.control.state.control_state import (
            get_control_state_persistence,
        )
        persistence = get_control_state_persistence()
        items = persistence.list_audit(limit=limit, field=field_filter)
        count = persistence.audit_count()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_audit_error: {type(exc).__name__}: {exc}")

    return _ok({
        "available": True,
        "total": int(count),
        "limit": int(limit),
        "filter_field": field_filter,
        "items": items,
        "schema_version": API_SCHEMA_VERSION,
    })


# ============================================================
# 4) POST /api/v1/control/module/<name>/enable
# ============================================================
@control_bp.route("/module/<name>/enable", methods=["POST"])
@require_auth
def api_control_module_enable(name: str):
    """启用某个模块。"""
    return _do_module_op(name, "enable", new_value=True)


# ============================================================
# 5) POST /api/v1/control/module/<name>/disable
# ============================================================
@control_bp.route("/module/<name>/disable", methods=["POST"])
@require_auth
def api_control_module_disable(name: str):
    """禁用某个模块。"""
    return _do_module_op(name, "disable", new_value=False)


# ============================================================
# 6) POST /api/v1/control/module/<name>/toggle
# ============================================================
@control_bp.route("/module/<name>/toggle", methods=["POST"])
@require_auth
def api_control_module_toggle(name: str):
    """切换模块状态。"""
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)

    operator = _get_operator()
    reason = _get_reason()
    result = manager.toggle_module(
        name=name,
        operator=operator,
        reason=reason,
    )
    if not result.success:
        status = 403 if "readonly" in result.error or "not_controllable" in result.error else 400
        if "not_found" in result.error:
            status = 404
        return _err(result.error or "control_failed", status_code=status, data=result.to_dict())
    return _ok({
        "action": result.action,
        "module": result.module,
        "old_value": result.old_value,
        "new_value": result.new_value,
        "operator": operator,
        "reason": reason,
        "change": result.change,
        "schema_version": API_SCHEMA_VERSION,
    })


def _do_module_op(name: str, action: str, new_value: bool):
    """统一的模块 enable/disable 处理器。"""
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)

    operator = _get_operator()
    reason = _get_reason()
    if action == "enable":
        result = manager.enable_module(
            name=name,
            operator=operator,
            reason=reason,
        )
    elif action == "disable":
        result = manager.disable_module(
            name=name,
            operator=operator,
            reason=reason,
        )
    else:
        return _err(f"unsupported_action: {action}", status_code=400)

    if not result.success:
        err = result.error or "control_failed"
        if "not_found" in err:
            status = 404
        elif "readonly" in err or "not_controllable" in err:
            status = 403
        else:
            status = 400
        return _err(err, status_code=status, data=result.to_dict())

    return _ok({
        "action": result.action,
        "module": result.module,
        "old_value": result.old_value,
        "new_value": result.new_value,
        "operator": operator,
        "reason": reason,
        "change": result.change,
        "schema_version": API_SCHEMA_VERSION,
    })


# ============================================================
# 7) POST /api/v1/control/safe_mode/enable
# ============================================================
@control_bp.route("/safe_mode/enable", methods=["POST"])
@require_auth
def api_control_safe_mode_enable():
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)
    operator = _get_operator()
    reason = _get_reason()
    result = manager.enter_safe_mode(operator=operator, reason=reason)
    return _system_result(result, operator=operator, reason=reason)


# ============================================================
# 8) POST /api/v1/control/safe_mode/disable
# ============================================================
@control_bp.route("/safe_mode/disable", methods=["POST"])
@require_auth
def api_control_safe_mode_disable():
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)
    operator = _get_operator()
    reason = _get_reason()
    result = manager.exit_safe_mode(operator=operator, reason=reason)
    return _system_result(result, operator=operator, reason=reason)


# ============================================================
# 9) POST /api/v1/control/maintenance/enable
# ============================================================
@control_bp.route("/maintenance/enable", methods=["POST"])
@require_auth
def api_control_maintenance_enable():
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)
    operator = _get_operator()
    reason = _get_reason()
    result = manager.enter_maintenance(operator=operator, reason=reason)
    return _system_result(result, operator=operator, reason=reason)


# ============================================================
# 10) POST /api/v1/control/maintenance/disable
# ============================================================
@control_bp.route("/maintenance/disable", methods=["POST"])
@require_auth
def api_control_maintenance_disable():
    try:
        manager = _get_manager()
    except Exception as exc:  # noqa: BLE001
        return _err(f"control_manager_unavailable: {exc}", status_code=503)
    operator = _get_operator()
    reason = _get_reason()
    result = manager.exit_maintenance(operator=operator, reason=reason)
    return _system_result(result, operator=operator, reason=reason)


def _system_result(result, operator: str, reason: str):
    if not result.success:
        return _err(result.error or "control_failed", status_code=400, data=result.to_dict())
    return _ok({
        "action": result.action,
        "old_value": result.old_value,
        "new_value": result.new_value,
        "operator": operator,
        "reason": reason,
        "change": result.change,
        "schema_version": API_SCHEMA_VERSION,
    })


# ============================================================
# 拒绝所有其他写方法
# ============================================================
@control_bp.route(
    "/<path:any_path>",
    methods=["PUT", "PATCH", "DELETE"],
)
def _reject_other_writes(any_path: str):
    body = make_error_envelope(
        error=f"method_not_allowed: only POST/GET are supported on /api/v1/control/*, got control-{any_path}",
        data={"path": f"/api/v1/control/{any_path}"},
        degraded=False,
        schema_version=API_SCHEMA_VERSION,
    )
    resp = jsonify(body)
    resp.status_code = 405
    resp.headers["Allow"] = "GET, POST"
    return resp


# ============================================================
# 注册
# ============================================================
def register_control_api(app, url_prefix: Optional[str] = None) -> None:
    """
    将 control Blueprint 注册到 Flask app。

    Args:
        app: Flask 实例
        url_prefix: 可选 url_prefix 覆盖
    """
    if app is None:
        return
    try:
        if url_prefix:
            app.register_blueprint(control_bp, url_prefix=url_prefix)
        else:
            app.register_blueprint(control_bp)
        logger.info(
            "Yuyi Control API registered: namespace=/api/v1/control schema_version=%s",
            API_SCHEMA_VERSION,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("register_control_api 失败: %s", exc)


__all__ = [
    "control_bp",
    "register_control_api",
]
