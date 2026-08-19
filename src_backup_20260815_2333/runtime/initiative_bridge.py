"""
InitiativeBridge —— 将发送函数注册为 Runtime ActionDispatcher 的处理器

Phase 7.2 迁移后，主动消息只有单一来源：
  RuntimeCore.tick → InitiativeLifecycleTask → InitiativeAdapter → InitiativeEngine
    → InitiativeQueue → ActionDispatcher → InitiativeBridge.handle_send_message → send_private_msg

不再存在独立进程（initiative_sender.py），也不再有旧版定时轮询。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from src.runtime.action_dispatcher import Action
from src.runtime.runtime_bridge import get_runtime_bridge

logger = logging.getLogger(__name__)


# 默认的全局主动消息 cooldown 秒数（避免短时间连续触发重复发送）。
# 可通过 send_config["initiative_cooldown_seconds"] 覆盖。
_DEFAULT_INITIATIVE_COOLDOWN_SECONDS: int = 60


class InitiativeBridge:
    """
    将 RuntimeCore 的 send_message 决策适配到主动消息发送函数。

    设计目标：
      - 发送失败隔离（不影响 Runtime 主链路）
      - 防重复发送（cooldown + dedup）
      - 可选 callback / 真实 HTTP 发送 / 仅记录日志 三档
    """

    def __init__(
        self,
        orchestrator: Any = None,
        send_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._send_config = dict(send_config) if send_config else {}
        self._registered = False
        self._lock = threading.Lock()

        # 去重：同一目标用户在 cooldown 内不重复发送
        self._last_sent_at: Dict[str, float] = {}
        # 最近一次发送的消息指纹（目标用户 + 消息前 100 char hash），秒级去重
        self._last_msg_fingerprint: Dict[str, str] = {}

    # ── 公共属性 ──────────────────────────────────────────

    @property
    def is_registered(self) -> bool:
        return self._registered

    # ── Action Handler ────────────────────────────────────

    def handle_send_message(self, action: Action) -> Dict[str, Any]:
        """ActionDispatcher 回调：处理 send_message 行动。"""
        payload = action.payload or {}
        result: Dict[str, Any] = {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "sent": False,
            "message": None,
            "reason": action.reason,
        }

        target_user = self._resolve_target_user(payload)
        if not target_user:
            result["reason"] = "no target_user configured"
            return result

        # 1) cooldown 检查（防重复）
        skip_reason = self._check_cooldown(target_user)
        if skip_reason:
            result["reason"] = skip_reason
            result["skipped_by_cooldown"] = True
            logger.info(
                "InitiativeBridge: 跳过主动消息 action=%s user=%s reason=%s",
                action.action_id, target_user, skip_reason,
            )
            return result

        # 2) payload 已有消息 → 直接用；否则用 orchestrator 生成
        message: Optional[str] = payload.get("message")
        if not message and self._orchestrator:
            try:
                gen_result = self._orchestrator.generate_initiative(target_user)
                # generate_initiative 既可能返回 str，也可能返回 dict（取决于版本）
                if isinstance(gen_result, dict):
                    message = str(gen_result.get("message") or gen_result.get("content") or "")
                elif gen_result:
                    message = str(gen_result)
            except Exception as exc:
                logger.exception("InitiativeBridge: 生成主动消息失败: %s", exc)
                result["error"] = f"generate_initiative failed: {exc}"
                result["reason"] = "generate_initiative raised"
                return result

        if not message:
            result["reason"] = "no message generated; skipped"
            return result

        result["message"] = message

        # 3) 内容指纹去重：同用户 30s 内完全相同的消息再次触发就跳过
        dedup_key = f"{target_user}|{message[:100]}"
        now = time.monotonic()
        with self._lock:
            last_fingerprint = self._last_msg_fingerprint.get(target_user)
            last_time = self._last_sent_at.get(target_user, 0.0)
            if last_fingerprint == dedup_key and (now - last_time) < max(
                30, self._get_cooldown_seconds(),
            ):
                result["skipped_by_duplicate"] = True
                result["reason"] = "duplicate message within cooldown"
                logger.info(
                    "InitiativeBridge: 跳过重复主动消息 action=%s user=%s len=%d",
                    action.action_id, target_user, len(message),
                )
                return result
            # 先记录，发送失败时回滚
            prev_sent_at = last_time
            prev_finger = last_fingerprint
            self._last_sent_at[target_user] = now
            self._last_msg_fingerprint[target_user] = dedup_key

        sent = self._send_message(message)
        if sent:
            result["sent"] = True
        else:
            # 发送失败：回滚 cooldown 记录，让下一个触发有机会重试
            with self._lock:
                if prev_sent_at <= 0:
                    self._last_sent_at.pop(target_user, None)
                else:
                    self._last_sent_at[target_user] = prev_sent_at
                if prev_finger is None:
                    self._last_msg_fingerprint.pop(target_user, None)
                else:
                    self._last_msg_fingerprint[target_user] = prev_finger
            result["reason"] = "send_message returned False"
            return result

        # 4) 回写 RuntimeCore 事件
        try:
            bridge = get_runtime_bridge()
            core = bridge.get_runtime_core() if bridge else None
            if core is not None:
                core.inject_event(
                    "action.proactive_executed",
                    {
                        "action_id": action.action_id,
                        "action_content": message,
                        "user_response": None,
                    },
                )
        except Exception:
            pass

        logger.info(
            "InitiativeBridge: send_message %s sent=True msg=%r",
            action.action_id, message[:60] + ("…" if len(message) > 60 else ""),
        )
        return result

    # ── 发送实现 ──────────────────────────────────────────

    def _send_message(self, message: str) -> bool:
        """实际发送（callback / onebot / astrbot / 仅日志）。"""
        cfg = self._send_config

        callback: Optional[Callable[[str], Any]] = cfg.get("send_callback")
        if callback:
            try:
                result = callback(message)
                return bool(result)
            except Exception as exc:
                logger.exception("InitiativeBridge: send_callback 异常: %s", exc)
                return False

        api_type = str(cfg.get("api_type") or "onebot").lower()
        target_user = cfg.get("target_user")
        if not target_user:
            logger.warning("InitiativeBridge: 未配置 target_user，跳过发送")
            return False

        try:
            from src.runtime.initiative_sender import send_private_msg
            return send_private_msg(
                api_type=api_type,
                user_id=str(target_user),
                message=message,
                onebot_url=cfg.get("onebot_url"),
                onebot_token=str(cfg.get("onebot_token") or ""),
                astrbot_url=cfg.get("astrbot_url"),
                request_timeout=float(cfg.get("request_timeout") or 8.0),
            )
        except Exception as exc:
            logger.exception("InitiativeBridge: 发送消息异常: %s", exc)
            return False

    # ── 注册 ──────────────────────────────────────────────

    def register(self) -> bool:
        """将 handle_send_message 注册到 ActionDispatcher。"""
        if self._registered:
            return True
        try:
            bridge = get_runtime_bridge()
            bridge.register_action_handler("send_message", self.handle_send_message)
            bridge.mark_action_handlers_ready()
            self._registered = True
            logger.info(
                "InitiativeBridge: send_message handler 已注册（cooldown=%ss）",
                self._get_cooldown_seconds(),
            )
            return True
        except Exception as exc:
            logger.exception("InitiativeBridge: 注册处理器失败: %s", exc)
            return False

    # ── 内部工具 ──────────────────────────────────────────

    def _get_cooldown_seconds(self) -> int:
        raw = self._send_config.get("initiative_cooldown_seconds")
        try:
            val = int(raw) if raw is not None else _DEFAULT_INITIATIVE_COOLDOWN_SECONDS
        except (TypeError, ValueError):
            val = _DEFAULT_INITIATIVE_COOLDOWN_SECONDS
        return val if val >= 0 else 0

    def _resolve_target_user(self, payload: Dict[str, Any]) -> Optional[str]:
        # payload 里的 target_user 优先；其次 send_config；最后 orchestrator
        candidate = payload.get("target_user") or payload.get("user_id")
        if candidate:
            return str(candidate)
        candidate = self._send_config.get("target_user")
        if candidate:
            return str(candidate)
        if self._orchestrator is not None:
            candidate = getattr(self._orchestrator, "target_user_id", None)
            if candidate:
                return str(candidate)
        return None

    def _check_cooldown(self, target_user: str) -> Optional[str]:
        cd = self._get_cooldown_seconds()
        if cd <= 0:
            return None
        now = time.monotonic()
        last = self._last_sent_at.get(target_user, 0.0)
        if last and (now - last) < cd:
            return (
                f"cooldown active: last_sent {int(now - last)}s ago < limit {cd}s"
            )
        return None
