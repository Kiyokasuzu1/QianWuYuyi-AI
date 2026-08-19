# -*- coding: utf-8 -*-
"""
src/control/api/routes.py

Phase C.10.3 — Yuyi Server API Gateway 路由

8 个只读 GET endpoint(供 Yuyi Desktop 调用):

    GET /api/v1/health
    GET /api/v1/runtime/status
    GET /api/v1/runtime/overview
    GET /api/v1/personality/status
    GET /api/v1/selfmodel/status
    GET /api/v1/memory/overview
    GET /api/v1/growth/status
    GET /api/v1/initiative/status
    GET /api/v1/audit/recent

所有方法:
- 仅 GET
- 仅消费已有 Provider 的只读快照
- 不直接修改任何 src.* 业务状态
- 异常一律降级为 degraded envelope,不抛 5xx 之外错误
"""

from __future__ import annotations

import dataclasses
import logging
import time
from datetime import date, datetime, time as _time
from enum import Enum
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

from .auth import require_auth
from .config import get_gateway_config
from .envelope import (
    API_SCHEMA_VERSION,
    make_error_envelope,
    make_success_envelope,
)

logger = logging.getLogger(__name__)


# ============================================================
# Blueprint
# ============================================================
gateway_bp = Blueprint(
    "yuyi_gateway",
    __name__,
    url_prefix="/api/v1",
)


# ============================================================
# 启动时间(用于 uptime)
# ============================================================
_GATEWAY_START_TIME = time.time()


# ============================================================
# Provider 懒加载(避免 import-time 副作用)
# ============================================================
def _get_runtime_provider():
    try:
        from src.admin.runtime_provider import get_runtime_provider
        return get_runtime_provider()
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway: runtime_provider 不可用: %s", exc)
        return None


def _get_self_model_provider():
    try:
        from src.admin.self_model_provider import get_self_model_provider
        return get_self_model_provider()
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway: self_model_provider 不可用: %s", exc)
        return None


def _get_governance_provider():
    try:
        from src.admin.governance_provider import get_governance_provider
        return get_governance_provider()
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway: governance_provider 不可用: %s", exc)
        return None


def _get_initiative_provider():
    try:
        from src.admin.initiative_dashboard_provider import (
            get_initiative_dashboard_provider,
        )
        return get_initiative_dashboard_provider()
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway: initiative_provider 不可用: %s", exc)
        return None


def _get_audit_storage():
    try:
        from src.audit.storage import get_audit_storage
        return get_audit_storage()
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway: audit_storage 不可用: %s", exc)
        return None


# ============================================================
# 工具
# ============================================================
def _server_version() -> str:
    return "0.10.3"


def _uptime_seconds() -> float:
    return max(0.0, time.time() - _GATEWAY_START_TIME)


def _ok(data: Dict[str, Any]):
    # P0-1 修复: 先经过 _to_json_safe 预处理,防止 provider 返回的 dataclass /
    # 自定义对象 / datetime / Enum 等不可 JSON 序列化类型导致 jsonify → 500。
    # _to_json_safe 对 dict/list/str/int/bool/None 原样返回,对已有接口无副作用。
    safe_data = _to_json_safe(data)
    return jsonify(make_success_envelope(data=safe_data, schema_version=API_SCHEMA_VERSION))


def _err(error: str, data: Optional[Dict[str, Any]] = None):
    body = make_error_envelope(
        error=error,
        data=data or {},
        degraded=True,
        schema_version=API_SCHEMA_VERSION,
    )
    resp = jsonify(body)
    # 即使失败也保持 200,让 Desktop 走 envelope
    # 只有 401/500 等不可恢复错误才用非 200
    return resp


# ============================================================
# JSON-safe 转换工具(防 500)
# ============================================================
# 防止 provider 返回的 snapshot/traits/state 等字段包含不可 JSON 序列化对象
# (dataclass / 自定义类 / datetime / Enum / Path / set 等),
# 导致 jsonify -> json.dumps 抛 TypeError, Gateway 返回 500。
#
# 策略: 递归把任意对象转换为 JSON 原生类型,失败字段降级为字符串。

def _to_json_safe(obj: Any, _depth: int = 0) -> Any:
    """递归将任意对象转为 JSON 可序列化的 Python 原生类型。

    支持:
    - dict / Mapping            -> dict (key 转 str, value 递归)
    - list / tuple / set / frozenset -> list (元素递归)
    - str / int / float / bool / None -> 原样返回
    - datetime / date / time    -> ISO 字符串
    - Enum                      -> .value
    - dataclass 实例            -> asdict 递归
    - 有 to_dict() 方法的对象    -> 调用 to_dict() 后递归
    - 有 __dict__ 的普通对象     -> vars(obj) 后递归
    - 其他                      -> 兜底 str(obj)

    深度上限: 20 层(防循环引用导致栈溢出)。
    """
    if _depth > 20:
        return "...max_depth_exceeded..."

    # 1) None & 基础类型(注意: bool 是 int 子类, 须先判断)
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str)):
        return obj

    # 2) datetime / date / time
    if isinstance(obj, (datetime, date, _time)):
        try:
            return obj.isoformat()
        except Exception:  # noqa: BLE001
            return str(obj)

    # 3) Enum
    if isinstance(obj, Enum):
        try:
            return obj.value
        except Exception:  # noqa: BLE001
            return str(obj)

    # 4) dict / Mapping
    if isinstance(obj, dict):
        try:
            return {str(k): _to_json_safe(v, _depth + 1) for k, v in obj.items()}
        except Exception:  # noqa: BLE001
            return str(obj)

    # 5) list / tuple / set / frozenset
    if isinstance(obj, (list, tuple, set, frozenset)):
        try:
            return [_to_json_safe(v, _depth + 1) for v in obj]
        except Exception:  # noqa: BLE001
            return str(obj)

    # 6) dataclass 实例(非类型)
    try:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return _to_json_safe(dataclasses.asdict(obj), _depth + 1)
    except Exception:  # noqa: BLE001
        pass

    # 7) 有 to_dict() 方法的对象(常见于 ORM / DTO)
    if hasattr(obj, "to_dict") and callable(getattr(obj, "to_dict", None)):
        try:
            return _to_json_safe(obj.to_dict(), _depth + 1)
        except Exception:  # noqa: BLE001
            pass

    # 8) 有 __dict__ 的普通对象
    if hasattr(obj, "__dict__"):
        try:
            return _to_json_safe(vars(obj), _depth + 1)
        except Exception:  # noqa: BLE001
            pass

    # 9) 兜底: str()
    try:
        return str(obj)
    except Exception:  # noqa: BLE001
        return f"<unserializable:{type(obj).__name__}>"


# ============================================================
# 拒绝所有写方法
# ============================================================
@gateway_bp.route(
    "/<path:any_path>",
    methods=["POST", "PUT", "PATCH", "DELETE"],
)
def _reject_write(any_path: str):
    """任何写方法一律拒绝(默认路由)。"""
    body = make_error_envelope(
        error="method_not_allowed: gateway is read-only",
        data={"path": f"/{any_path}"},
        degraded=False,
        schema_version=API_SCHEMA_VERSION,
    )
    resp = jsonify(body)
    resp.status_code = 405
    resp.headers["Allow"] = "GET"
    return resp


# ============================================================
# 1) GET /api/v1/health
# ============================================================
@gateway_bp.route("/health", methods=["GET"])
@require_auth
def api_health():
    """服务总健康状态。"""
    provider = _get_runtime_provider()
    runtime_status: Dict[str, Any] = {
        "initialized": False,
        "is_running": False,
    }
    authority: Dict[str, bool] = {}
    if provider is not None:
        try:
            runtime_status = provider.get_status().get("runtime", runtime_status)  # type: ignore[union-attr]
            authority = provider.get_authority_status()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.debug("gateway /health: provider 异常: %s", exc)

    runtime_ok = bool(runtime_status.get("initialized", False))
    data = {
        "server_status": "ok",
        "runtime_status": "running" if runtime_ok else "offline",
        "version": _server_version(),
        "schema_version": API_SCHEMA_VERSION,
        "uptime_seconds": _uptime_seconds(),
        "authority": authority,
        "runtime": runtime_status,
    }
    return _ok(data)


# ============================================================
# 2) GET /api/v1/runtime/status
# ============================================================
@gateway_bp.route("/runtime/status", methods=["GET"])
@require_auth
def api_runtime_status():
    """Runtime cycle 状态 + adapter 状态 + health。"""
    provider = _get_runtime_provider()
    if provider is None:
        return _err("runtime_provider_unavailable")

    try:
        status = provider.get_status()
        authority = provider.get_authority_status()
    except Exception as exc:  # noqa: BLE001
        return _err(f"runtime_status_error: {type(exc).__name__}: {exc}")

    runtime_info = status.get("runtime", {})
    online = bool(status.get("online", False))

    adapters = {k: bool(v) for k, v in (authority or {}).items()}

    data = {
        "online": online,
        "initialized": bool(runtime_info.get("initialized", False)),
        "is_running": bool(runtime_info.get("is_running", False)),
        "cycle_state": "running" if online else "offline",
        "adapters": adapters,
        "health": "healthy" if online and any(adapters.values()) else "degraded",
        "version": _server_version(),
    }
    return _ok(data)


# ============================================================
# 2.b) GET /api/v1/runtime/overview(可选扩展)
# ============================================================
@gateway_bp.route("/runtime/overview", methods=["GET"])
@require_auth
def api_runtime_overview():
    """Runtime 总览(包含 personality/selfmodel 摘要)。"""
    provider = _get_runtime_provider()
    if provider is None:
        return _err("runtime_provider_unavailable")
    data: Dict[str, Any] = {
        "runtime": {},
        "personality": None,
        "selfmodel": None,
        "emotion": None,
        "memory": None,
    }
    try:
        data["runtime"] = provider.get_status()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime overview status 异常: %s", exc)
    try:
        data["personality"] = provider.get_personality_summary()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime overview personality 异常: %s", exc)
    try:
        data["memory"] = provider.get_memory_summary()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime overview memory 异常: %s", exc)
    try:
        data["emotion"] = provider.get_emotion_summary()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime overview emotion 异常: %s", exc)
    try:
        sm = _get_self_model_provider()
        if sm is not None:
            data["selfmodel"] = sm.get_status()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        logger.debug("runtime overview selfmodel 异常: %s", exc)
    return _ok(data)


# ============================================================
# 3) GET /api/v1/personality/status
# ============================================================
@gateway_bp.route("/personality/status", methods=["GET"])
@require_auth
def api_personality_status():
    """Personality 状态(snapshot 摘要 + evolution 版本)。"""
    provider = _get_runtime_provider()
    if provider is None:
        return _err("runtime_provider_unavailable")
    try:
        summary = provider.get_personality_summary()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        return _err(f"personality_status_error: {type(exc).__name__}: {exc}")

    current = summary.get("current") if isinstance(summary, dict) else None
    evolution_version: Any = None
    if isinstance(current, dict):
        # 注意: 在拿 version 时, current 可能是 dict 或 dataclass / 自定义类,
        # 先对 current 做一次 json_safe 转换以兼容 dataclass 顶层场景。
        try:
            safe_current_for_version = _to_json_safe(current)
            if isinstance(safe_current_for_version, dict):
                evolution_version = safe_current_for_version.get("version") or safe_current_for_version.get("evolution_version")
        except Exception:  # noqa: BLE001
            pass

    # traits(若 resolver 有)
    traits: List[Any] = []
    try:
        if isinstance(current, dict):
            raw_traits = current.get("traits")
            if isinstance(raw_traits, dict):
                traits = [
                    {"name": str(k), "value": v}
                    for k, v in raw_traits.items()
                ]
            elif isinstance(raw_traits, list):
                traits = list(raw_traits)
    except Exception:  # noqa: BLE001
        pass

    # 对可能包含不可序列化对象(dataclass / datetime / Enum / set / 自定义类等)
    # 的字段统一做 JSON-safe 转换, 避免 jsonify -> json.dumps 抛 TypeError 返回 500。
    data = {
        "available": bool(summary.get("available", False)),
        "snapshot": _to_json_safe(current),
        "traits": _to_json_safe(traits),
        "evolution_version": evolution_version,
        "state": _to_json_safe(summary.get("state")),
    }
    return _ok(data)


# ============================================================
# 4) GET /api/v1/selfmodel/status
# ============================================================
@gateway_bp.route("/selfmodel/status", methods=["GET"])
@require_auth
def api_selfmodel_status():
    """SelfModel 当前版本 + health。"""
    provider = _get_self_model_provider()
    if provider is None:
        return _err("selfmodel_provider_unavailable")
    try:
        status = provider.get_status()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        return _err(f"selfmodel_status_error: {type(exc).__name__}: {exc}")

    version: Any = None
    if isinstance(status, dict):
        version = status.get("version") or status.get("schema_version")
        if not version:
            bootstrap = status.get("bootstrap")
            if isinstance(bootstrap, dict):
                version = bootstrap.get("version")

    # 优先尝试拿 identity_name
    identity_name: str = ""
    try:
        ident = provider.get_identity()  # type: ignore[union-attr]
        if isinstance(ident, dict):
            identity_name = str(
                ident.get("identity_name", "")
                or ident.get("name", "")
                or ""
            )
    except Exception:  # noqa: BLE001
        pass

    data = {
        "available": bool(status.get("available", False)),
        "version": version,
        "identity_name": identity_name,
        "bootstrap": status.get("bootstrap") if isinstance(status, dict) else None,
        "health": "healthy" if status.get("available", False) else "degraded",
        "bridge_error": status.get("bridge_error") if isinstance(status, dict) else None,
    }
    return _ok(data)


# ============================================================
# 5) GET /api/v1/memory/overview
# ============================================================
@gateway_bp.route("/memory/overview", methods=["GET"])
@require_auth
def api_memory_overview():
    """Memory 数量 + 最近记录摘要。"""
    provider = _get_runtime_provider()
    if provider is None:
        return _err("runtime_provider_unavailable")
    try:
        summary = provider.get_memory_summary()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        return _err(f"memory_overview_error: {type(exc).__name__}: {exc}")

    recent = summary.get("recent", [])
    # 只返回前 5 条,避免 payload 过大
    try:
        n = int(request.args.get("limit", 5))
    except (TypeError, ValueError):
        n = 5
    n = max(1, min(50, n))
    recent_preview = list(recent)[:n] if isinstance(recent, list) else []

    data = {
        "available": bool(summary.get("available", False)),
        "total_count": int(summary.get("total_count", 0) or 0),
        "important_count": int(summary.get("important_count", 0) or 0),
        "user_id": summary.get("user_id"),
        "recent": recent_preview,
    }
    return _ok(data)


# ============================================================
# 6) GET /api/v1/growth/status
# ============================================================
@gateway_bp.route("/growth/status", methods=["GET"])
@require_auth
def api_growth_status():
    """Growth proposal 数量 + evolution 状态。"""
    provider = _get_governance_provider()
    if provider is None:
        return _err("governance_provider_unavailable")
    try:
        snapshot = provider.get_growth_governance_snapshot()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        return _err(f"growth_status_error: {type(exc).__name__}: {exc}")

    data = {
        "available": bool(snapshot.get("available", False)),
        "section": snapshot.get("section", "growth"),
        "proposal_count": int(snapshot.get("proposal_count", 0) or 0),
        "pending_count": int(snapshot.get("pending_count", 0) or 0),
        "approved_count": int(snapshot.get("approved_count", 0) or 0),
        "rejected_count": int(snapshot.get("rejected_count", 0) or 0),
        "applied_count": int(snapshot.get("applied_count", 0) or 0),
        "evolution": snapshot.get("evolution") or snapshot.get("state", {}),
    }
    return _ok(data)


# ============================================================
# 7) GET /api/v1/initiative/status
# ============================================================
@gateway_bp.route("/initiative/status", methods=["GET"])
@require_auth
def api_initiative_status():
    """Initiative 状态 + 最近主动行为。"""
    provider = _get_initiative_provider()
    if provider is None:
        return _err("initiative_provider_unavailable")
    try:
        summary = provider.get_summary()  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001
        return _err(f"initiative_status_error: {type(exc).__name__}: {exc}")

    # 拉取最近 history(最多 5 条)
    recent: List[Dict[str, Any]] = []
    try:
        hist = provider.get_history(limit=5)  # type: ignore[union-attr]
        if isinstance(hist, dict):
            recent = list(hist.get("items", []) or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("initiative history 异常: %s", exc)

    data = {
        "available": bool(summary.get("available", False)),
        "interest_count": int(summary.get("interest_count", 0) or 0),
        "possible_action_count": int(summary.get("possible_action_count", 0) or 0),
        "filtered_count": int(summary.get("filtered_count", 0) or 0),
        "by_trend": summary.get("by_trend", {}),
        "by_action_status": summary.get("by_action_status", {}),
        "by_action_type": summary.get("by_action_type", {}),
        "last_interest_at": summary.get("last_interest_at"),
        "last_action_at": summary.get("last_action_at"),
        "recent": recent,
        "fallback": bool(summary.get("fallback", False)),
    }
    return _ok(data)


# ============================================================
# Phase D.6.0 —— SelfModel + Growth 列表只读端点(视神经扩展)
# 不改业务逻辑,仅把 RuntimeCore 已经存在的数据正式暴露给所有表现层。
# 所有 6 个端点:
#   * 只读 GET
#   * 用 @require_auth 保护
#   * 统一 envelope: make_success_envelope / make_error_envelope
#   * 失败保持 200, degraded=True, 客户端走 envelope 判断
# ============================================================

# 9) GET /api/v1/selfmodel/beliefs
@gateway_bp.route("/selfmodel/beliefs", methods=["GET"])
@require_auth
def api_selfmodel_beliefs():
    """SelfModel 核心信念列表。≠ Personality 即时切片。"""
    provider = _get_self_model_provider()
    if provider is None:
        return _err("selfmodel_provider_unavailable")
    try:
        domain = request.args.get("domain") or None
        try:
            min_conf = float(request.args.get("min_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_conf = 0.0
        include_inactive = str(request.args.get("include_inactive", "1")) not in ("0", "false", "False")
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(500, limit))
        payload = provider.list_beliefs(  # type: ignore[union-attr]
            domain=domain,
            min_confidence=min_conf,
            include_inactive=include_inactive,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"selfmodel_beliefs_error: {type(exc).__name__}: {exc}")
    # Provider 返回 dict envelope,直接透传
    if isinstance(payload, dict):
        return _ok(payload)
    return _ok({"items": list(payload or [])})


# 10) GET /api/v1/selfmodel/history
@gateway_bp.route("/selfmodel/history", methods=["GET"])
@require_auth
def api_selfmodel_history():
    """SelfModel 历史事件(pcr_applied / belief_formed / identity_updated 等)。"""
    provider = _get_self_model_provider()
    if provider is None:
        return _err("selfmodel_provider_unavailable")
    try:
        event_type = request.args.get("event_type") or None
        since = request.args.get("since") or None
        until = request.args.get("until") or None
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(500, limit))
        payload = provider.list_history(  # type: ignore[union-attr]
            event_type=event_type,
            since=since,
            until=until,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"selfmodel_history_error: {type(exc).__name__}: {exc}")
    if isinstance(payload, dict):
        return _ok(payload)
    return _ok({"items": list(payload or [])})


# 11) GET /api/v1/selfmodel/reflections
@gateway_bp.route("/selfmodel/reflections", methods=["GET"])
@require_auth
def api_selfmodel_reflections():
    """SelfModel 反思记录(每次 SelfPhase 内省产出)。"""
    provider = _get_self_model_provider()
    if provider is None:
        return _err("selfmodel_provider_unavailable")
    try:
        trigger_source = request.args.get("trigger_source") or None
        reflection_type = request.args.get("reflection_type") or None
        try:
            min_conf = float(request.args.get("min_confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_conf = 0.0
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(500, limit))
        payload = provider.list_reflections(  # type: ignore[union-attr]
            trigger_source=trigger_source,
            reflection_type=reflection_type,
            min_confidence=min_conf,
            limit=limit,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"selfmodel_reflections_error: {type(exc).__name__}: {exc}")
    if isinstance(payload, dict):
        return _ok(payload)
    return _ok({"items": list(payload or [])})


# 12) GET /api/v1/selfmodel/traits  —— StableTraits(≠ TraitState 当前人格)
@gateway_bp.route("/selfmodel/traits", methods=["GET"])
@require_auth
def api_selfmodel_stable_traits():
    """SelfModelV3 稳定特质。绝对不能从 Resolver.current 拿值!

    两个不同来源:
    GET /personality/status → traits → Dashboard 当前人格(抖动)
    GET /selfmodel/traits   → stable → Archive 稳定自我认知(慢变)
    """
    provider = _get_self_model_provider()
    if provider is None:
        return _err("selfmodel_provider_unavailable")
    try:
        try:
            min_stab = float(request.args.get("min_stability", 0.0) or 0.0)
        except (TypeError, ValueError):
            min_stab = 0.0
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(500, limit))
        try:
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)
        fn = getattr(provider, "list_stable_traits", None)
        if fn is None or not callable(fn):
            # 旧 Provider 没这个方法,返回空(优雅降级)
            return _ok({"available": False, "total": 0, "items": [],
                        "error": "provider_missing_list_stable_traits", "source": None})
        payload = fn(min_stability=min_stab, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001
        return _err(f"selfmodel_traits_error: {type(exc).__name__}: {exc}")
    if isinstance(payload, dict):
        return _ok(payload)
    return _ok({"items": list(payload or [])})


# 13) GET /api/v1/personality/evolution
@gateway_bp.route("/personality/evolution", methods=["GET"])
@require_auth
def api_personality_evolution():
    """人格演化时间线(每次 trait 变化事件)。D.6 Growth Timeline 主数据源之一。"""
    provider = _get_self_model_provider()
    if provider is None:
        return _err("selfmodel_provider_unavailable")
    try:
        start = request.args.get("start") or None
        end = request.args.get("end") or None
        try:
            limit = int(request.args.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(500, limit))
        sources_raw = request.args.get("sources") or ""
        sources = [s.strip() for s in sources_raw.split(",") if s.strip()] or None
        payload = provider.get_evolution_timeline(  # type: ignore[union-attr]
            start=start,
            end=end,
            limit=limit,
            sources=sources,
        )
    except Exception as exc:  # noqa: BLE001
        return _err(f"personality_evolution_error: {type(exc).__name__}: {exc}")
    if isinstance(payload, dict):
        return _ok(payload)
    return _ok({"items": list(payload or [])})


# 14) GET /api/v1/growth/proposals
@gateway_bp.route("/growth/proposals", methods=["GET"])
@require_auth
def api_growth_proposals():
    """Growth Proposal 列表(通过/否决/待审/已应用)。D.6 Growth Timeline 主数据源之二。"""
    provider = _get_governance_provider()
    if provider is None:
        return _err("governance_provider_unavailable")
    try:
        status = request.args.get("status") or None
        proposal_type = request.args.get("proposal_type") or None
        try:
            limit = int(request.args.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(500, limit))
        try:
            offset = int(request.args.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)
        fn = getattr(provider, "list_proposals", None)
        if fn is None or not callable(fn):
            return _ok({"available": False, "total": 0, "items": [],
                        "error": "governance_missing_list_proposals"})
        raw = fn(status=status, proposal_type=proposal_type, limit=limit, offset=offset)  # type: ignore[union-attr]
        if isinstance(raw, list):
            data = {
                "available": True,
                "total": len(raw),
                "status": status,
                "proposal_type": proposal_type,
                "items": raw,
            }
        else:
            data = raw if isinstance(raw, dict) else {"items": []}
        payload = data
    except Exception as exc:  # noqa: BLE001
        return _err(f"growth_proposals_error: {type(exc).__name__}: {exc}")
    if isinstance(payload, dict):
        return _ok(payload)
    return _ok({"items": list(payload or [])})


# ============================================================
# 8) GET /api/v1/audit/recent
# ============================================================
@gateway_bp.route("/audit/recent", methods=["GET"])
@require_auth
def api_audit_recent():
    """最近 audit 事件。"""
    storage = _get_audit_storage()
    if storage is None:
        return _err("audit_storage_unavailable")
    try:
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        limit = 20
    limit = max(1, min(100, limit))
    try:
        records = storage.load(limit=limit, offset=0)
    except Exception as exc:  # noqa: BLE001
        return _err(f"audit_recent_error: {type(exc).__name__}: {exc}")

    items: List[Dict[str, Any]] = []
    for r in records or []:
        try:
            if hasattr(r, "to_dict"):
                items.append(r.to_dict())  # type: ignore[union-attr]
            elif isinstance(r, dict):
                items.append(r)
        except Exception:  # noqa: BLE001
            continue

    data = {
        "available": True,
        "total": len(items),
        "limit": limit,
        "items": items,
    }
    return _ok(data)


# ============================================================
# 注册到 Flask app
# ============================================================
def register_gateway(app, url_prefix: Optional[str] = None) -> None:
    """
    将 gateway Blueprint 注册到 Flask app。

    Args:
        app: Flask 实例。
        url_prefix: 可选 url_prefix 覆盖(默认 Blueprint 自带 /api/v1)。
    """
    if app is None:
        return
    try:
        # Phase C.10.5.4: 先注册 Control API(具体路径,优先匹配)
        # 再注册 Gateway(包含 wildcard 拒绝写方法)
        try:
            from .control_routes import register_control_api
            register_control_api(app)
        except Exception as exc:  # noqa: BLE001
            logger.warning("register_control_api 失败: %s", exc)

        if url_prefix:
            app.register_blueprint(gateway_bp, url_prefix=url_prefix)
        else:
            app.register_blueprint(gateway_bp)
        logger.info(
            "Yuyi Server API Gateway registered: schema_version=%s",
            API_SCHEMA_VERSION,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("register_gateway 失败: %s", exc)


__all__ = [
    "gateway_bp",
    "register_gateway",
]
