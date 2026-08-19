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


def _guess_port(send_config: Dict[str, Any]) -> str:
    """Phase 7.2.1-p3：日志里友好提示用户 OneBot/AstrBot 预期监听端口。"""
    from urllib.parse import urlparse
    api_type = str(send_config.get("api_type") or "onebot").lower()
    url = send_config.get("onebot_url") if api_type == "onebot" else send_config.get("astrbot_url")
    try:
        if url:
            p = urlparse(str(url))
            if p.port:
                return str(p.port)
    except Exception:
        pass
    return "3000" if api_type == "onebot" else "11451"


def _callable_accepts_arg(fn: Callable[..., Any], nargs: int) -> bool:
    """检测回调是否至少接受 nargs 个位置参数（用于判断 send_callback(user_id) 新旧签名兼容）。"""
    import inspect
    try:
        sig = inspect.signature(fn)
        # 位置参数（排除 *args/**kwargs）
        pos = 0
        for p in sig.parameters.values():
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
                pos += 1
        return pos >= nargs
    except (TypeError, ValueError):
        return False


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

    # ── 公共方法 ──────────────────────────────────────────

    def set_orchestrator(self, orchestrator: Any) -> None:
        """Phase 7.2.1-p3-final：允许运行时注入 Orchestrator。

        典型场景：api_server.py 中 RuntimeBridge 必须先于 Orchestrator 初始化（因为
        Orchestrator.__init__ 内部会 get_runtime_bridge()），导致 InitiativeBridge
        创建时只能传 orchestrator=None。等 Orchestrator 构造完成后，再调用本方法
        把真实对象注入，后续 payload.message 为空时才会走 generate_initiative()。
        """
        old_none = self._orchestrator is None
        self._orchestrator = orchestrator
        if old_none and orchestrator is not None:
            logger.info(
                "InitiativeBridge: 已注入 Orchestrator（类型=%s），后续 payload 无 message 时可调用 generate_initiative",
                type(orchestrator).__name__,
            )

    @property
    def send_config_snapshot(self) -> Dict[str, Any]:
        """只读快照，用于 api_server 里 debug 用，不会改内部状态。"""
        return {
            "api_type": self._send_config.get("api_type"),
            "target_user": self._send_config.get("target_user"),
            "initiative_cooldown_seconds": self._get_cooldown_seconds(),
            "onebot_url": self._send_config.get("onebot_url"),
            "astrbot_url": self._send_config.get("astrbot_url"),
            "has_send_callback": bool(self._send_config.get("send_callback")),
            "orchestrator_type": None if self._orchestrator is None else type(self._orchestrator).__name__,
        }

    # ── Action Handler ────────────────────────────────────

    def handle_send_message(self, action: Action) -> Dict[str, Any]:
        """ActionDispatcher 回调：处理 send_message 行动。

        Phase 7.2.1-p3：所有提前 return 分支都必须打 INFO 级日志，
        否则 RuntimeCore.tick 里 dispatched=1，但用户从 logs/yuyi-api.log
        完全无法判断卡在哪一步（target_user 未配置？消息为空？发送失败？）。
        """
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
            logger.error(
                "InitiativeBridge: 无法发送主动消息 action=%s：未解析到 target_user。"
                "payload.target_user/user_id=%r send_config.target_user=%r orchestrator.target_user_id=%r。"
                "请检查 config.yaml initiative.target_user_qq 或环境变量 TARGET_USER_QQ。",
                action.action_id,
                payload.get("target_user") or payload.get("user_id"),
                self._send_config.get("target_user"),
                getattr(self._orchestrator, "target_user_id", None),
            )
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
            logger.warning(
                "InitiativeBridge: 无消息内容 action=%s user=%s。"
                "payload.message=%r orchestrator=%r generate_initiative 结果为空字符串。",
                action.action_id, target_user,
                payload.get("message"),
                bool(self._orchestrator),
            )
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

        sent = self._send_message(message, target_user=target_user)
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
            # Phase 7.2.1-p3：发送失败必须打 ERROR 日志（之前静默 return）
            logger.error(
                "InitiativeBridge: 发送失败 action=%s user=%s msg_len=%d reason=%r。"
                "请检查 OneBot/NapCat 是否在 127.0.0.1:%s 可用，以及 ONEBOT_TOKEN / target_user 是否正确。",
                action.action_id, target_user, len(message), result["reason"],
                _guess_port(self._send_config),
            )
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
            "InitiativeBridge: send_message OK action=%s user=%s sent=True msg=%r",
            action.action_id, target_user,
            message[:60] + ("…" if len(message) > 60 else ""),
        )
        return result

    # ── 发送实现 ──────────────────────────────────────────

    def _send_message(self, message: str, target_user: Optional[str] = None) -> bool:
        """实际发送（callback / onebot / astrbot / 仅日志）。

        Phase 7.2.1-p3：target_user 参数支持直接由 handle_send_message 传入，
        避免二次 _resolve_target_user 而打印不精确的 warning。
        """
        cfg = self._send_config

        callback: Optional[Callable[[str], Any]] = cfg.get("send_callback")
        if callback:
            try:
                if _callable_accepts_arg(callback, 2):
                    result = callback(message, target_user) if target_user else callback(message)
                else:
                    result = callback(message)
                return bool(result)
            except Exception as exc:
                logger.exception("InitiativeBridge: send_callback 异常: %s", exc)
                return False

        api_type = str(cfg.get("api_type") or "onebot").lower()
        resolved_user = str(target_user or cfg.get("target_user") or "")
        if not resolved_user:
            logger.warning("InitiativeBridge: 未配置 target_user，跳过发送")
            return False

        try:
            from src.runtime.initiative_sender import send_private_msg
            ok = send_private_msg(
                api_type=api_type,
                user_id=resolved_user,
                message=message,
                onebot_url=cfg.get("onebot_url"),
                onebot_token=str(cfg.get("onebot_token") or ""),
                astrbot_url=cfg.get("astrbot_url"),
                request_timeout=float(cfg.get("request_timeout") or 8.0),
            )
            if not ok:
                logger.warning(
                    "InitiativeBridge: send_private_msg 返回 False api_type=%s user=%s url=%s",
                    api_type, resolved_user,
                    cfg.get(f"{api_type}_url"),
                )
            return bool(ok)
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
