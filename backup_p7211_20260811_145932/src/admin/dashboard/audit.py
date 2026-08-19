# -*- coding: utf-8 -*-
"""
src/admin/dashboard/audit.py

Phase 5.0 Dashboard Upgrade Step 8.4.7 —— Dashboard POST Audit 封装。

职责:
1. 包装 src.audit.storage.AuditStorage(不直接接管)
2. 提供 dashboard_audit() 入口
3. 任何 POST(成功 / 失败 / 拒绝)都必须记录
4. 异常隔离:Audit 写入失败不阻断业务流

约束:
- 禁止修改 src/audit/storage.py 与 src/audit/record.py
- 禁止 import 业务模块
- 严禁记录: 密码 / 完整 token / 敏感 PII
- 必须记录: who / action / payload_hash / timestamp / result / reason

数据流:
    Router (POST)
        ↓
    audit_record_post(who, action, payload, result, reason)
        ↓
    src.audit.storage.save_audit_record(record)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
# 注入 / 可替换
# ============================================================
_audit_singleton: Dict[str, Any] = {}
_audit_lock = threading.RLock()


def _get_audit_sink() -> Any:
    """获取底层 audit sink(可注入)。"""
    with _audit_lock:
        sink = _audit_singleton.get("sink")
        if sink is not None:
            return sink
    # 默认:用 src.audit.storage.get_audit_storage
    try:
        from src.audit.storage import get_audit_storage
        return get_audit_storage()
    except Exception as exc:  # noqa: BLE001
        logger.debug("dashboard.audit: 默认 sink 不可用: %s", exc)
        return None


def set_audit_sink_for_dashboard(sink: Any) -> None:
    """注入底层 audit sink(测试用,可传入任何 save(record) callable)。"""
    with _audit_lock:
        _audit_singleton["sink"] = sink


def reset_audit_sink_for_dashboard() -> None:
    """测试用:重置 audit sink。"""
    with _audit_lock:
        _audit_singleton.pop("sink", None)


# ============================================================
# 工具
# ============================================================
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_token", "refresh_token", "auth", "authorization",
})


def _sanitize_payload(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """对 payload 做脱敏:敏感字段替换为 '***'。"""
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        if not isinstance(k, str):
            out[str(k)] = v
            continue
        kl = k.lower()
        if kl in _SENSITIVE_KEYS or any(s in kl for s in ("password", "token", "secret", "api_key")):
            out[k] = "***"
        else:
            out[k] = v
    return out


def _hash_payload(payload: Optional[Dict[str, Any]]) -> str:
    """payload 哈希(用于审计,稳定 + 短)。"""
    if not isinstance(payload, dict):
        return ""
    try:
        # 脱敏后哈希,确保可复算
        sanitized = _sanitize_payload(payload)
        s = json.dumps(sanitized, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        try:
            s = repr(payload)
        except Exception:  # noqa: BLE001
            s = ""
    try:
        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    except Exception:  # noqa: BLE001
        return "0" * 16


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:  # noqa: BLE001
        try:
            return datetime.utcnow().isoformat() + "Z"  # noqa: F821
        except Exception:  # noqa: BLE001
            return ""


# ============================================================
# 主入口:record_dashboard_post
# ============================================================
def record_dashboard_post(
    *,
    who: str,
    role: str,
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    result: str = "success",
    reason: str = "",
    remote_addr: str = "",
    token_hash: str = "",
    permission: str = "",
    risk: str = "",
    correlation_id: str = "",
) -> bool:
    """
    记录一次 Dashboard POST 事件。

    Args:
        who: 操作者(role 或 username,默认 role)
        role: 角色
        action: 动作名
        payload: 原始 payload(会自动脱敏 + 哈希)
        result: success / failure / denied
        reason: 结果说明
        remote_addr: 客户端 IP
        token_hash: token 哈希(可选)
        permission: 校验的 permission
        risk: governance 风险分级
        correlation_id: 关联 ID

    Returns:
        bool 是否成功(不阻断业务流)
    """
    try:
        payload_hash = _hash_payload(payload if isinstance(payload, dict) else None)
        record = {
            "record_id": _gen_id(),
            "timestamp": _now_iso(),
            "who": str(who or role or "unknown"),
            "user_id": str(who or role or "unknown"),
            "user_name": str(who or role or "unknown"),
            "role": str(role or "unknown"),
            "operation_type": "dashboard.post",
            "source": "dashboard_v2",
            "action": str(action or ""),
            "payload_hash": payload_hash,
            "result": str(result or "success"),
            "reason": str(reason or ""),
            "remote_addr": str(remote_addr or ""),
            "token_hash": str(token_hash or ""),
            "permission": str(permission or ""),
            "risk": str(risk or ""),
            "correlation_id": str(correlation_id or ""),
            "detail": {
                "payload_hash": payload_hash,
                "permission": str(permission or ""),
                "risk": str(risk or ""),
                "remote_addr": str(remote_addr or ""),
            },
        }
        sink = _get_audit_sink()
        if sink is None:
            logger.debug("dashboard.audit: 无 sink,跳过记录")
            return False
        # 优先用 AuditRecord(如果 sink 是 AuditStorage)
        try:
            from src.audit.record import AuditRecord
            audit_record = AuditRecord(
                record_id=record["record_id"],
                timestamp=record["timestamp"],
                user_id=record["user_id"],
                user_name=record["user_name"],
                operation_type=record["operation_type"],
                source=record["source"],
                action=record["action"],
                detail=record["detail"],
                result=record["result"],
                error_message=record["reason"] if result != "success" else "",
                correlation_id=record["correlation_id"],
                metadata={
                    "who": record["who"],
                    "role": record["role"],
                    "reason": record["reason"],
                    "payload_hash": record["payload_hash"],
                    "remote_addr": record["remote_addr"],
                    "token_hash": record["token_hash"],
                    "permission": record["permission"],
                    "risk": record["risk"],
                },
            )
            try:
                sink.save(audit_record)
                return True
            except Exception:  # noqa: BLE001
                # 退回到 dict save
                pass
        except Exception:  # noqa: BLE001
            pass
        # 兜底:任何 callable sink(record)
        try:
            save_fn = getattr(sink, "save", None) or getattr(sink, "record", None) or getattr(sink, "write", None)
            if callable(save_fn):
                save_fn(record)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("dashboard.audit: 写入失败: %s", exc)
            return False
        return False
    except Exception as exc:  # noqa: BLE001
        # 任何异常都不能阻断业务流
        logger.warning("dashboard.audit.record_dashboard_post 异常: %s", exc)
        return False


def list_dashboard_audit(
    *,
    limit: int = 50,
    offset: int = 0,
    who: Optional[str] = None,
    action: Optional[str] = None,
    result: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    读取 Dashboard POST 审计日志。

    Returns:
        list[{record_id, timestamp, who, action, payload_hash, result, reason, ...}]
    """
    try:
        sink = _get_audit_sink()
        if sink is None:
            return []
        # 优先用 load
        try:
            load_fn = getattr(sink, "load", None)
            if callable(load_fn):
                # 加载所有(限制 1000),过滤 dashboard.post
                records = load_fn(limit=1000, offset=0) or []
                items: List[Dict[str, Any]] = []
                for r in records:
                    try:
                        if hasattr(r, "to_dict"):
                            d = r.to_dict()
                        elif isinstance(r, dict):
                            d = dict(r)
                        else:
                            d = {"raw": str(r)}
                    except Exception:  # noqa: BLE001
                        d = {"raw": str(r)}
                    # 过滤:仅 dashboard.post
                    op = d.get("operation_type", "")
                    if op and op != "dashboard.post":
                        continue
                    # 过滤条件
                    if who and d.get("user_id") != who and d.get("who") != who:
                        continue
                    if action and d.get("action") != action:
                        continue
                    if result and d.get("result") != result:
                        continue
                    items.append(_normalize_record(d))
                # 倒序(最新在前)
                items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                return items[offset: offset + limit]
        except Exception as exc:  # noqa: BLE001
            logger.debug("dashboard.audit.list: sink.load 失败: %s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning("dashboard.audit.list_dashboard_audit 异常: %s", exc)
        return []


def _normalize_record(d: Dict[str, Any]) -> Dict[str, Any]:
    """规范化输出字段。"""
    return {
        "record_id": d.get("record_id", ""),
        "timestamp": d.get("timestamp", ""),
        "who": d.get("who") or d.get("user_id") or d.get("user_name") or "unknown",
        "role": d.get("role", ""),
        "action": d.get("action", ""),
        "payload_hash": d.get("payload_hash", ""),
        "result": d.get("result", "success"),
        "reason": d.get("reason") or d.get("error_message") or "",
        "remote_addr": d.get("remote_addr", ""),
        "permission": d.get("permission", ""),
        "risk": d.get("risk", ""),
    }


def _gen_id() -> str:
    try:
        import uuid
        return "dash_" + uuid.uuid4().hex[:12]
    except Exception:  # noqa: BLE001
        import random
        return "dash_" + str(random.randint(0, 99999999))


__all__ = [
    "record_dashboard_post",
    "list_dashboard_audit",
    "set_audit_sink_for_dashboard",
    "reset_audit_sink_for_dashboard",
]
