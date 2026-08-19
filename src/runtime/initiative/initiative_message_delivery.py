# -*- coding: utf-8 -*-
"""
src/runtime/initiative/initiative_message_delivery.py

Phase C.9.2 Initiative Message Delivery —— 主动消息投递层

职责:
  - 接收 Phase C.9.1 的 initiative_output
  - 走 InitiativeDeliveryPolicy 检查
  - 生成 MessagePayload
  - 调用已有 QQ / AstrBot 发送接口(initiative_sender.send_private_msg_*)
  - 记录 audit(runtime_initiative_message_sent / failed / blocked)

**复用现有**:
  - 已有 AstrBot / OneBot 发送接口(src/initiative_sender.py)
  - 已有 Runtime InitiativeBridge 架构(不重写,只包装)
  - 已有 Audit 接口

**绝不**:
  - 创建新 QQ 连接 / 新机器人 / 新 Web 接口
  - 修改 Personality / SelfModel / Growth / Relationship / Memory
  - 绕过 DeliveryPolicy 直接发送
  - 在 Policy blocked 时仍发送
  - 自行生成主动消息内容(必须来自 initiative_output.message_request)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.runtime.initiative.initiative_delivery_policy import (
    InitiativeDeliveryPolicy,
    create_initiative_delivery_policy,
    DECISION_ALLOW,
    DECISION_BLOCK,
    INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION,
    REASON_OK,
    REASON_ERROR,
    REASON_MISSING_CONTENT,
)

logger = logging.getLogger(__name__)


# ============================================================
# Schema + 常量
# ============================================================

INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION = "1.0"
INITIATIVE_MESSAGE_DELIVERY_NAME = "initiative_message_delivery"
INITIATIVE_MESSAGE_DELIVERY_VERSION = "1.0.0"

# Result labels
RESULT_SENT = "sent"
RESULT_FAILED = "failed"
RESULT_BLOCKED = "blocked"
RESULT_DEGRADED = "degraded"
ALL_RESULTS: List[str] = [RESULT_SENT, RESULT_FAILED, RESULT_BLOCKED, RESULT_DEGRADED]

# Channels
CHANNEL_QQ = "qq"
CHANNEL_ONEBOT = "onebot"
CHANNEL_ASTRBOT = "astrbot"
CHANNEL_DEFAULT = CHANNEL_QQ
ALL_CHANNELS: List[str] = [CHANNEL_QQ, CHANNEL_ONEBOT, CHANNEL_ASTRBOT]

# Audit actions
AUDIT_ACTION_SENT = "runtime_initiative_message_sent"
AUDIT_ACTION_FAILED = "runtime_initiative_message_failed"
AUDIT_ACTION_BLOCKED = "runtime_initiative_message_blocked"
AUDIT_COMPONENT = "runtime_initiative_delivery"

# Defaults
DEFAULT_API_TYPE = CHANNEL_ONEBOT
DEFAULT_REQUEST_TIMEOUT = 8.0
DEFAULT_ACTOR = "initiative_delivery"


# ============================================================
# 工具
# ============================================================


def _now_iso() -> str:
    try:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return "1970-01-01T00:00:00Z"


def _now_ts() -> float:
    try:
        return time.time()
    except Exception:
        return 0.0


def _new_id() -> str:
    try:
        return f"imd_{uuid.uuid4().hex[:16]}"
    except Exception:
        try:
            return f"imd_{int(_now_ts() * 1000):x}"
        except Exception:
            return "imd_0000000000000000"


def _safe_str(v: Any, default: str = "") -> str:
    try:
        if v is None:
            return default
        return str(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        f = float(v)
        if f != f:
            return default
        return f
    except (TypeError, ValueError):
        return default


def _safe_bool(v: Any, default: bool = False) -> bool:
    try:
        if v is None:
            return default
        return bool(v)
    except Exception:
        return default


def _safe_dict(v: Any) -> Dict[str, Any]:
    try:
        if isinstance(v, dict):
            return dict(v)
        return {}
    except Exception:
        return {}


def _safe_list(v: Any) -> List[Any]:
    try:
        if isinstance(v, list):
            return list(v)
        if isinstance(v, tuple):
            return list(v)
        return []
    except Exception:
        return []


def _record_audit_safely(
    audit: Any,
    action: str,
    detail: Dict[str, Any],
    result: str = "success",
) -> bool:
    """安全记录 audit(失败时静默返回 False)。"""
    try:
        if audit is None:
            return False
        d = dict(detail) if isinstance(detail, dict) else {}
        if hasattr(audit, "record") and callable(getattr(audit, "record")):
            try:
                audit.record(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=d,
                    result=result,
                )
                return True
            except Exception:
                pass
        if hasattr(audit, "save_audit_record") and callable(getattr(audit, "save_audit_record")):
            try:
                from src.audit.record import AuditRecord
                rec = AuditRecord(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=d,
                    result=result,
                )
                audit.save_audit_record(rec)
                return True
            except Exception:
                pass
        if hasattr(audit, "record_audit_log") and callable(getattr(audit, "record_audit_log")):
            try:
                audit.record_audit_log(
                    operation_type=action,
                    source=AUDIT_COMPONENT,
                    action=action,
                    detail=d,
                    result=result,
                )
                return True
            except Exception:
                pass
        return False
    except Exception:
        return False


def _build_degraded_result(error: str = "") -> Dict[str, Any]:
    return {
        "success": False,
        "degraded": True,
        "result": RESULT_DEGRADED,
        "error": _safe_str(error, "unknown"),
        "message_id": "",
        "channel": "",
        "sent_at": _now_iso(),
    }


# ============================================================
# 消息 payload 构造
# ============================================================


def build_message_payload(
    initiative_output: Dict[str, Any],
    policy_result: Dict[str, Any],
    channel: str = CHANNEL_DEFAULT,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 MessagePayload(只从 initiative_output 读取内容,绝不自行编造)。"""
    try:
        output = _safe_dict(initiative_output)
        policy = _safe_dict(policy_result)
        message_request = output.get("message_request")
        if not isinstance(message_request, dict):
            message_request = {}
        # 提取 content(三处兜底)
        content = _safe_str(
            message_request.get("content", "")
            or message_request.get("text", "")
            or message_request.get("template", "")
            or output.get("content", ""),
            "",
        )
        # 提取 reason / reasons
        reasons = _safe_list(output.get("reasons", []))
        # 合并 policy reasons
        policy_reasons = _safe_list(policy.get("reasons", []))
        for pr in policy_reasons:
            if isinstance(pr, str) and pr not in reasons:
                reasons.append(pr)

        payload: Dict[str, Any] = {
            "message_id": _safe_str(message_id, "") or _new_id(),
            "initiative_id": _safe_str(output.get("initiative_id", ""), ""),
            "user_id": _safe_str(output.get("user_id", ""), ""),
            "channel": _safe_str(channel, CHANNEL_DEFAULT) or CHANNEL_DEFAULT,
            "content": content,
            "reason": [str(r) for r in reasons],
            "priority": _safe_float(
                policy.get("adjusted_priority", output.get("priority", 0.0)),
                0.0,
            ),
            "trigger": _safe_str(output.get("trigger", "none"), "none") or "none",
            "confidence": _safe_float(output.get("confidence", 0.0), 0.0),
            "timestamp": _now_iso(),
            "schema_version": INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION,
            "actor": DEFAULT_ACTOR,
        }
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[phase_c92] build_message_payload 异常(已隔离): {exc}")
        return {
            "message_id": _safe_str(message_id, "") or _new_id(),
            "initiative_id": "",
            "user_id": "",
            "channel": CHANNEL_DEFAULT,
            "content": "",
            "reason": [f"build_error:{exc}"],
            "priority": 0.0,
            "trigger": "none",
            "confidence": 0.0,
            "timestamp": _now_iso(),
            "schema_version": INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION,
            "actor": DEFAULT_ACTOR,
        }


# ============================================================
# 已有发送接口包装(duck-type)
# ============================================================


def _call_existing_sender(
    channel: str,
    user_id: str,
    content: str,
    *,
    api_type: str = DEFAULT_API_TYPE,
    api_url: str = "",
    token: str = "",
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
) -> bool:
    """调用已有 initiative_sender.send_private_msg_* 接口。

    严格复用现有 AstrBot / OneBot 发送层,不创建新连接。

    Returns:
        True 发送成功(返回 2xx),False 失败
    """
    if not content:
        return False
    if not user_id:
        return False
    if not api_url:
        # 没有 api_url → 拒绝发送(必须有配置)
        return False
    try:
        # 复用现有发送函数
        from initiative_sender import (
            send_private_msg_onebot,
            send_private_msg_astrbot,
        )
        # 优先用 channel,其次用 api_type
        ch = _safe_str(channel, "").strip().lower()
        at = _safe_str(api_type, DEFAULT_API_TYPE).strip().lower()
        if ch == CHANNEL_ASTRBOT or at == CHANNEL_ASTRBOT:
            return bool(send_private_msg_astrbot(api_url, user_id, content, timeout))
        # 默认 OneBot
        return bool(send_private_msg_onebot(api_url, user_id, content, timeout, token))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[phase_c92] _call_existing_sender 异常(已隔离): {exc}")
        return False


# ============================================================
# InitiativeMessageDelivery(主类)
# ============================================================


class InitiativeMessageDelivery:
    """
    Initiative Message Delivery (Phase C.9.2 / v1.0)

    接收 Phase C.9.1 adapter 的 initiative_output,
    走 DeliveryPolicy,生成 MessagePayload,调用已有 QQ/AstrBot 发送接口,
    记录 audit。

    任何异常 fail-soft:返回 degraded result,Runtime 不中断。

    全部 write-only:
      - 写 Audit(sent / failed / blocked)
      - 写 DeliveryPolicy 内部 cooldown 时间戳
      - 调用已有 QQ/AstrBot 发送接口(只在 policy allow 时)

    绝不:
      - 写 Personality / SelfModel / Growth / Relationship / Memory
      - 创建新 QQ 连接
      - 自行生成主动消息内容
      - 绕过 DeliveryPolicy 直接发送
    """

    SCHEMA_VERSION = INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION
    NAME = INITIATIVE_MESSAGE_DELIVERY_NAME
    VERSION = INITIATIVE_MESSAGE_DELIVERY_VERSION

    def __init__(
        self,
        policy: Any = None,
        audit: Any = None,
        channel: str = CHANNEL_DEFAULT,
        api_type: str = DEFAULT_API_TYPE,
        api_url: str = "",
        token: str = "",
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
        sender: Any = None,
    ) -> None:
        self._policy = policy if isinstance(policy, InitiativeDeliveryPolicy) else (
            policy if policy is not None and hasattr(policy, "evaluate") else create_initiative_delivery_policy()
        )
        self._audit = audit
        self._channel = _safe_str(channel, CHANNEL_DEFAULT) or CHANNEL_DEFAULT
        self._api_type = _safe_str(api_type, DEFAULT_API_TYPE) or DEFAULT_API_TYPE
        self._api_url = _safe_str(api_url, "")
        self._token = _safe_str(token, "")
        try:
            self._timeout = float(timeout)
        except (TypeError, ValueError):
            self._timeout = DEFAULT_REQUEST_TIMEOUT

        # 允许注入自定义 sender(用于测试 / 替代实现)
        self._sender = sender

        # 内部状态
        self._lock = threading.RLock()
        self._deliver_count: int = 0
        self._sent_count: int = 0
        self._failed_count: int = 0
        self._blocked_count: int = 0
        self._degraded_count: int = 0
        self._last_result: Optional[Dict[str, Any]] = None
        self._last_error: Optional[str] = None
        self._last_audit: Optional[Dict[str, Any]] = None

    # --------------------------------------------------------
    # 配置
    # --------------------------------------------------------

    def set_api_config(
        self,
        api_type: Optional[str] = None,
        api_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[float] = None,
        channel: Optional[str] = None,
    ) -> None:
        with self._lock:
            if api_type is not None:
                self._api_type = _safe_str(api_type, self._api_type)
            if api_url is not None:
                self._api_url = _safe_str(api_url, "")
            if token is not None:
                self._token = _safe_str(token, "")
            if timeout is not None:
                try:
                    self._timeout = float(timeout)
                except (TypeError, ValueError):
                    pass
            if channel is not None:
                self._channel = _safe_str(channel, self._channel) or self._channel

    def get_api_config(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "channel": self._channel,
                "api_type": self._api_type,
                "api_url": self._api_url,
                "token": self._token,
                "timeout": self._timeout,
            }

    def set_sender(self, sender: Any) -> None:
        """注入自定义 sender(供测试用)。"""
        with self._lock:
            self._sender = sender

    # --------------------------------------------------------
    # 投递流程
    # --------------------------------------------------------

    def deliver(
        self,
        initiative_output: Any,
        relationship_snapshot: Any = None,
        metadata: Any = None,
        actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """投递一条 initiative 消息。

        Args:
            initiative_output:     C.9.1 adapter 产出
            relationship_snapshot: 关系快照(只读,用于 policy 校验)
            metadata:              补充元数据(只读)
            actor:                 投递 actor(用于 audit)

        Returns:
            {
                "success": bool,
                "result":  "sent" | "failed" | "blocked" | "degraded",
                "message_id": str,
                "channel": str,
                "policy_result": dict,
                "message_payload": dict | None,
                "audit_action": str,
                "timestamp": str,
                "error": str,
                "degraded": bool,
            }
        """
        with self._lock:
            self._deliver_count += 1
            act = _safe_str(actor, DEFAULT_ACTOR) or DEFAULT_ACTOR

        try:
            # 1) DeliveryPolicy 校验
            policy_raised = False
            try:
                policy_result = self._policy.evaluate(
                    initiative_output=initiative_output,
                    relationship_snapshot=relationship_snapshot,
                    metadata=metadata,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_c92] policy.evaluate 异常(已隔离): {exc}")
                policy_raised = True
                # policy 异常 → 视为 block(保守,不发送)
                policy_result = {
                    "decision": DECISION_BLOCK,
                    "reasons": [REASON_ERROR],
                    "adjusted_priority": 0.0,
                    "cooldown_remaining_seconds": 0.0,
                    "schema_version": INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION,
                    "timestamp": _now_iso(),
                }
            policy_decision = _safe_str(policy_result.get("decision", ""), "")
            if policy_decision != DECISION_ALLOW:
                # 2a) blocked
                return self._record_blocked(
                    initiative_output=initiative_output,
                    policy_result=policy_result,
                    actor=act,
                )

            # 2b) 生成 MessagePayload
            message_id = _new_id()
            payload = build_message_payload(
                initiative_output=_safe_dict(initiative_output),
                policy_result=policy_result,
                channel=self._channel,
                message_id=message_id,
            )
            content = _safe_str(payload.get("content", ""), "")
            if not content:
                # policy 已检查,但双保险:这里也拦
                blocked_result = {
                    "decision": DECISION_BLOCK,
                    "reasons": [REASON_MISSING_CONTENT],
                    "adjusted_priority": _safe_float(policy_result.get("adjusted_priority", 0.0), 0.0),
                    "cooldown_remaining_seconds": _safe_float(
                        policy_result.get("cooldown_remaining_seconds", 0.0), 0.0,
                    ),
                    "schema_version": INITIATIVE_DELIVERY_POLICY_SCHEMA_VERSION,
                    "timestamp": _now_iso(),
                }
                return self._record_blocked(
                    initiative_output=initiative_output,
                    policy_result=blocked_result,
                    actor=act,
                )

            user_id = _safe_str(payload.get("user_id", ""), "")

            # 3) 调用发送(可注入 sender / 已有 AstrBot-OneBot)
            sent_ok = self._send_to_channel(
                channel=_safe_str(payload.get("channel", self._channel), self._channel),
                user_id=user_id,
                content=content,
            )

            if sent_ok:
                # 4a) 成功
                # 记录 cooldown
                try:
                    self._policy.record_delivery()
                except Exception:  # noqa: BLE001
                    pass
                return self._record_sent(
                    initiative_output=initiative_output,
                    policy_result=policy_result,
                    payload=payload,
                    actor=act,
                )
            # 4b) 失败
            return self._record_failed(
                initiative_output=initiative_output,
                policy_result=policy_result,
                payload=payload,
                actor=act,
                error="send_failed",
            )

        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[phase_c92] deliver 异常(已隔离): {exc}")
            with self._lock:
                self._degraded_count += 1
                self._last_error = repr(exc)
            # 记录 audit failed
            try:
                _record_audit_safely(
                    audit=self._audit,
                    action=AUDIT_ACTION_FAILED,
                    detail={
                        "error": repr(exc),
                        "actor": act,
                        "timestamp": _now_iso(),
                    },
                    result="degraded",
                )
            except Exception:  # noqa: BLE001
                pass
            return _build_degraded_result(repr(exc))

    def _send_to_channel(
        self,
        channel: str,
        user_id: str,
        content: str,
    ) -> bool:
        """统一发送入口:优先用注入的 sender,否则调用已有 AstrBot/OneBot。"""
        # 1) 注入的 sender(测试用 / 自定义实现)
        if self._sender is not None:
            try:
                fn = self._sender
                if callable(fn):
                    result = fn(user_id=user_id, content=content, channel=channel)
                    return bool(result)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[phase_c92] 自定义 sender 异常(已隔离): {exc}")
                return False
        # 2) 已有 AstrBot/OneBot
        return _call_existing_sender(
            channel=channel,
            user_id=user_id,
            content=content,
            api_type=self._api_type,
            api_url=self._api_url,
            token=self._token,
            timeout=self._timeout,
        )

    # --------------------------------------------------------
    # 结果记录
    # --------------------------------------------------------

    def _record_sent(
        self,
        initiative_output: Any,
        policy_result: Dict[str, Any],
        payload: Dict[str, Any],
        actor: str,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "success": True,
            "result": RESULT_SENT,
            "message_id": _safe_str(payload.get("message_id", ""), ""),
            "channel": _safe_str(payload.get("channel", ""), ""),
            "policy_result": dict(policy_result),
            "message_payload": dict(payload),
            "audit_action": AUDIT_ACTION_SENT,
            "timestamp": _now_iso(),
            "error": "",
            "degraded": False,
        }
        with self._lock:
            self._sent_count += 1
            self._last_result = result
            self._last_audit = {"action": AUDIT_ACTION_SENT, "result": "success"}
        # audit
        try:
            _record_audit_safely(
                audit=self._audit,
                action=AUDIT_ACTION_SENT,
                detail={
                    "message_id": result["message_id"],
                    "initiative_id": _safe_str(payload.get("initiative_id", ""), ""),
                    "user_id": _safe_str(payload.get("user_id", ""), ""),
                    "channel": result["channel"],
                    "reason": _safe_list(payload.get("reason", [])),
                    "timestamp": result["timestamp"],
                    "result": RESULT_SENT,
                    "actor": actor,
                },
                result="success",
            )
        except Exception:  # noqa: BLE001
            pass
        return result

    def _record_failed(
        self,
        initiative_output: Any,
        policy_result: Dict[str, Any],
        payload: Dict[str, Any],
        actor: str,
        error: str,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "success": False,
            "result": RESULT_FAILED,
            "message_id": _safe_str(payload.get("message_id", ""), ""),
            "channel": _safe_str(payload.get("channel", ""), ""),
            "policy_result": dict(policy_result),
            "message_payload": dict(payload),
            "audit_action": AUDIT_ACTION_FAILED,
            "timestamp": _now_iso(),
            "error": _safe_str(error, "send_failed"),
            "degraded": False,
        }
        with self._lock:
            self._failed_count += 1
            self._last_result = result
            self._last_audit = {"action": AUDIT_ACTION_FAILED, "result": "failed"}
            self._last_error = result["error"]
        # audit
        try:
            _record_audit_safely(
                audit=self._audit,
                action=AUDIT_ACTION_FAILED,
                detail={
                    "message_id": result["message_id"],
                    "initiative_id": _safe_str(payload.get("initiative_id", ""), ""),
                    "user_id": _safe_str(payload.get("user_id", ""), ""),
                    "channel": result["channel"],
                    "reason": _safe_list(payload.get("reason", [])),
                    "timestamp": result["timestamp"],
                    "result": RESULT_FAILED,
                    "error": result["error"],
                    "actor": actor,
                },
                result="failed",
            )
        except Exception:  # noqa: BLE001
            pass
        return result

    def _record_blocked(
        self,
        initiative_output: Any,
        policy_result: Dict[str, Any],
        actor: str,
    ) -> Dict[str, Any]:
        output = _safe_dict(initiative_output)
        message_id = _new_id()
        result: Dict[str, Any] = {
            "success": False,
            "result": RESULT_BLOCKED,
            "message_id": message_id,
            "channel": self._channel,
            "policy_result": dict(policy_result),
            "message_payload": None,
            "audit_action": AUDIT_ACTION_BLOCKED,
            "timestamp": _now_iso(),
            "error": "",
            "degraded": False,
        }
        with self._lock:
            self._blocked_count += 1
            self._last_result = result
            self._last_audit = {"action": AUDIT_ACTION_BLOCKED, "result": "blocked"}
        # audit
        try:
            _record_audit_safely(
                audit=self._audit,
                action=AUDIT_ACTION_BLOCKED,
                detail={
                    "message_id": message_id,
                    "initiative_id": _safe_str(output.get("initiative_id", ""), ""),
                    "user_id": _safe_str(output.get("user_id", ""), ""),
                    "channel": self._channel,
                    "reason": _safe_list(policy_result.get("reasons", [])),
                    "timestamp": result["timestamp"],
                    "result": RESULT_BLOCKED,
                    "actor": actor,
                },
                result="blocked",
            )
        except Exception:  # noqa: BLE001
            pass
        return result

    # --------------------------------------------------------
    # 状态
    # --------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "deliver_count": int(self._deliver_count),
                "sent_count": int(self._sent_count),
                "failed_count": int(self._failed_count),
                "blocked_count": int(self._blocked_count),
                "degraded_count": int(self._degraded_count),
                "channel": str(self._channel),
                "api_type": str(self._api_type),
                "last_result": dict(self._last_result) if self._last_result else None,
                "last_error": self._last_error,
                "policy": self._policy.get_stats() if hasattr(self._policy, "get_stats") else {},
                "schema_version": self.schema_version,
            }

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return dict(self._last_result) if self._last_result else None

    @property
    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    @property
    def schema_version(self) -> str:
        return INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION

    @property
    def policy(self) -> Any:
        return self._policy


# ============================================================
# 工厂
# ============================================================


def create_initiative_message_delivery(
    policy: Any = None,
    audit: Any = None,
    channel: str = CHANNEL_DEFAULT,
    api_type: str = DEFAULT_API_TYPE,
    api_url: str = "",
    token: str = "",
    timeout: float = DEFAULT_REQUEST_TIMEOUT,
    sender: Any = None,
) -> InitiativeMessageDelivery:
    return InitiativeMessageDelivery(
        policy=policy,
        audit=audit,
        channel=channel,
        api_type=api_type,
        api_url=api_url,
        token=token,
        timeout=timeout,
        sender=sender,
    )


# ============================================================
# 公共 API
# ============================================================


__all__ = [
    "INITIATIVE_MESSAGE_DELIVERY_SCHEMA_VERSION",
    "INITIATIVE_MESSAGE_DELIVERY_NAME",
    "INITIATIVE_MESSAGE_DELIVERY_VERSION",
    "RESULT_SENT",
    "RESULT_FAILED",
    "RESULT_BLOCKED",
    "RESULT_DEGRADED",
    "ALL_RESULTS",
    "CHANNEL_QQ",
    "CHANNEL_ONEBOT",
    "CHANNEL_ASTRBOT",
    "CHANNEL_DEFAULT",
    "ALL_CHANNELS",
    "AUDIT_ACTION_SENT",
    "AUDIT_ACTION_FAILED",
    "AUDIT_ACTION_BLOCKED",
    "AUDIT_COMPONENT",
    "DEFAULT_API_TYPE",
    "DEFAULT_REQUEST_TIMEOUT",
    "DEFAULT_ACTOR",
    "build_message_payload",
    "InitiativeMessageDelivery",
    "create_initiative_message_delivery",
]
