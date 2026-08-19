"""
新版主动消息发送器（纯发送函数，由 Runtime InitiativeBridge 调用）

Phase 7.2 迁移：删除独立进程旧版（initiative_sender.py），将发送函数归并到
src/runtime/initiative_sender.py，由 InitiativeBridge 直接调用。旧版的
main_loop / Orchestrator 定时轮询由 Runtime InitiativeEngine 接管（事件
驱动 + 5 类智能触发器 + InitiativeLifecycleTask tick）。
"""

from __future__ import annotations

import logging
from typing import Optional

import requests  # noqa: F401  —— 被调用方依赖，如果 requests 缺失会报 ImportError

logger = logging.getLogger(__name__)


def send_private_msg_onebot(
    api_url: str,
    user_id: str,
    message: str,
    timeout: float = 8.0,
    token: str = "",
) -> bool:
    """通过 OneBot HTTP API 发送私聊消息。

    Args:
        api_url: OneBot 服务地址，例如 "http://127.0.0.1:3000"
        user_id: 目标 QQ 号（可为字符串或数字）
        message: 消息正文
        timeout: 请求超时秒数
        token: Bearer Token（可选）

    Returns:
        True 表示发送成功（HTTP 2xx 且 OneBot 没抛错），False 表示失败。
    """
    if not api_url or not user_id or not message:
        logger.warning(
            "send_private_msg_onebot: 缺少必要参数 (api_url=%r, user_id=%r, message_len=%d)",
            api_url, user_id, len(message) if message else 0,
        )
        return False

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"user_id": int(user_id), "message": message}
    try:
        url = api_url.rstrip("/")
        if not url.endswith("/send_private_msg"):
            url = f"{url}/send_private_msg"
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        # 尝试读取 OneBot 的返回体（retcode != 0 视为失败）
        try:
            body = r.json()
            retcode = body.get("retcode")
            if isinstance(retcode, int) and retcode != 0:
                logger.warning(
                    "send_private_msg_onebot: OneBot 返回非零 retcode=%s body=%s",
                    retcode, str(body)[:300],
                )
                return False
        except Exception:
            pass
        logger.info(
            "sent via OneBot to %s: %r",
            user_id, message[:60] + ("…" if len(message) > 60 else ""),
        )
        return True
    except Exception as e:
        logger.exception("send_private_msg_onebot failed: %s", e)
        return False


def send_private_msg_astrbot(
    api_url: str,
    user_id: str,
    message: str,
    timeout: float = 8.0,
) -> bool:
    """通过 AstrBot HTTP API 发送私聊消息。

    Args:
        api_url: AstrBot 服务地址，例如 "http://127.0.0.1:11451"
        user_id: 目标 QQ 号
        message: 消息正文
        timeout: 请求超时秒数

    Returns:
        True 表示发送成功，否则 False。
    """
    if not api_url or not user_id or not message:
        logger.warning(
            "send_private_msg_astrbot: 缺少必要参数 (api_url=%r, user_id=%r, message_len=%d)",
            api_url, user_id, len(message) if message else 0,
        )
        return False

    payload = {
        "action": "send_private_msg",
        "params": {"user_id": int(user_id), "message": message},
    }
    try:
        r = requests.post(api_url, json=payload, timeout=timeout)
        r.raise_for_status()
        logger.info(
            "sent via AstrBot to %s: %r",
            user_id, message[:60] + ("…" if len(message) > 60 else ""),
        )
        return True
    except Exception as e:
        logger.exception("send_private_msg_astrbot failed: %s", e)
        return False


def send_private_msg(
    *,
    api_type: str,
    user_id: str,
    message: str,
    onebot_url: Optional[str] = None,
    onebot_token: str = "",
    astrbot_url: Optional[str] = None,
    request_timeout: float = 8.0,
) -> bool:
    """统一入口：根据 api_type 发送私聊消息。

    Args:
        api_type: "onebot" 或 "astrbot"（大小写不敏感）
        user_id: 目标 QQ
        message: 消息正文
        onebot_url: OneBot API 地址（api_type=onebot 时必填）
        onebot_token: OneBot Bearer Token
        astrbot_url: AstrBot API 地址（api_type=astrbot 时必填）
        request_timeout: 超时秒数

    Returns:
        True 成功 / False 失败
    """
    api_type = (api_type or "onebot").lower()
    if api_type == "onebot":
        return send_private_msg_onebot(
            api_url=onebot_url or "",
            user_id=user_id,
            message=message,
            timeout=request_timeout,
            token=onebot_token,
        )
    if api_type == "astrbot":
        return send_private_msg_astrbot(
            api_url=astrbot_url or "",
            user_id=user_id,
            message=message,
            timeout=request_timeout,
        )
    logger.warning("send_private_msg: 未知 api_type=%r，已跳过发送", api_type)
    return False


def flatten_initiative_config(raw_cfg: dict) -> dict:
    """把 config.yaml 中的 initiative 子配置提升为顶层扁平 dict。

    与旧版 initiative_sender.load_config() 的扁平化行为保持一致，
    方便 api_server / 测试构造 send_config 时直接使用。

    环境变量覆盖保持相同：
      ASTRBOT_URL / ONEBOT_URL / ONEBOT_TOKEN / TARGET_USER_QQ
      MIN_CHECK_SECONDS / MAX_CHECK_SECONDS / API_TYPE
      MAX_BACKOFF_MULTIPLIER / REQUEST_TIMEOUT
    """
    import os

    cfg = dict(raw_cfg) if raw_cfg else {}
    if "initiative" in cfg and isinstance(cfg["initiative"], dict):
        init_cfg = cfg["initiative"]
        for key, value in init_cfg.items():
            cfg.setdefault(key, value)

    # env overrides（与旧版完全一致）
    _env = os.getenv
    cfg.setdefault("astrbot_url", _env("ASTRBOT_URL", _env("ONEBOT_URL", "http://127.0.0.1:11451")))
    cfg.setdefault("onebot_url", _env("ONEBOT_URL", "http://127.0.0.1:3000"))
    cfg.setdefault("onebot_token", _env("ONEBOT_TOKEN", ""))
    cfg.setdefault("target_user_qq", _env("TARGET_USER_QQ", "366648462"))
    cfg.setdefault(
        "min_check_seconds", int(_env("MIN_CHECK_SECONDS", str(5 * 60))),
    )
    cfg.setdefault(
        "max_check_seconds", int(_env("MAX_CHECK_SECONDS", str(15 * 60))),
    )
    cfg.setdefault("api_type", _env("API_TYPE", "onebot"))
    cfg.setdefault("max_backoff_multiplier", float(_env("MAX_BACKOFF_MULTIPLIER", "4.0")))
    cfg.setdefault("request_timeout", float(_env("REQUEST_TIMEOUT", "8.0")))
    return cfg


def build_send_config_from_flat(flat_cfg: dict) -> dict:
    """从 flatten 后的配置构造 InitiativeBridge 所需的 send_config。

    返回 dict 的键：api_type / onebot_url / onebot_token / astrbot_url /
    target_user / request_timeout。
    """
    return {
        "api_type": flat_cfg.get("api_type", "onebot"),
        "onebot_url": flat_cfg.get("onebot_url"),
        "onebot_token": flat_cfg.get("onebot_token", ""),
        "astrbot_url": flat_cfg.get("astrbot_url"),
        "target_user": flat_cfg.get("target_user_qq"),
        "request_timeout": flat_cfg.get("request_timeout", 8.0),
    }
