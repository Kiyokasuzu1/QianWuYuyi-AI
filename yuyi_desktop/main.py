# -*- coding: utf-8 -*-
"""
yuyi_desktop/main.py

Phase C.10.1 —— Desktop 启动入口

提供:
- 命令行入口:`python -m yuyi_desktop.main`
- 启动 QApplication + MainWindow + 主循环

启动流程(Phase C.10.x auth_token 注入):
    1. 通过 AuthClient 加载 token(env YUYI_DESKTOP_TOKEN / ~/.yuyi_desktop/token.json)
    2. 用该 token 构造 DesktopConfig(auth_token=...)
    3. 用 config 构造 DesktopContext(config=cfg),确保 AuthClient
       以 injected 模式生效,所有 /api/v1/* 请求自动带 Authorization header

约束:
- 不修改主项目 main.py
- 不影响 Runtime 启动
- 不读取/写入主项目 config.yaml
- token 加载复用 AuthClient 已有机制(env > file),不硬编码
- 测试可显式注入 auth_token / config / ctx 覆盖默认行为
"""
from __future__ import annotations

import logging
import sys
from typing import Optional

from yuyi_desktop.app import run_desktop
from yuyi_desktop.config.desktop_config import DesktopConfig
from yuyi_desktop.core.auth_client import AuthClient
from yuyi_desktop.core.desktop_context import DesktopContext

logger = logging.getLogger(__name__)


def _load_auth_token() -> str:
    """通过现有 AuthClient 机制加载 token(env > file 兜底)。

    说明:
        - 复用 AuthClient 已有优先级: env > file(已实现,避免重复)
        - 不硬编码 token,不读取主项目 config.yaml
        - 任何异常返回空串(由 DesktopContext 决定是否降级)
    """
    try:
        auth = AuthClient()
        if auth.is_authenticated():
            return auth.get_token()
    except Exception as exc:  # noqa: BLE001
        logger.debug("main: 加载 auth token 失败: %s", exc)
    return ""


def _build_desktop_config(
    auth_token: Optional[str] = None,
) -> DesktopConfig:
    """构造 DesktopConfig;若未显式提供 auth_token,则通过 AuthClient 加载。

    Args:
        auth_token: 显式 token。None 表示自动从 AuthClient(env/file)加载。
                    空串表示强制不注入(走 env/file 兜底)。
                    非空表示直接以 injected 模式注入。
    """
    if auth_token is None:
        auth_token = _load_auth_token()
    return DesktopConfig(auth_token=auth_token)


def main() -> int:
    """Desktop 启动入口。

    启动流程:
        1. basicConfig + 启动日志
        2. 加载 token(通过 AuthClient 既有机制 env/file)
        3. 用 token 构造 DesktopConfig
        4. 用 config 构造 DesktopContext(injected 模式生效)
        5. 启动 QApplication + MainWindow + 主循环
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Yuyi Desktop 启动中... (Phase C.10.1)")
    try:
        # 1) 加载 token(自动模式: 复用 AuthClient env > file)
        cfg = _build_desktop_config()
        if cfg.has_auth_token():
            logger.info(
                "Yuyi Desktop: 启动加载 auth_token(由 AuthClient 机制)"
            )
        else:
            logger.info(
                "Yuyi Desktop: 未发现 auth_token,后续请求将不带 Authorization header"
            )
        # 2) 用 config 构造 DesktopContext(injected 模式生效)
        ctx = DesktopContext(config=cfg)
        # 3) 启动 Qt 主循环(注入 cfg/ctx)
        return run_desktop(config=cfg, ctx=ctx)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Yuyi Desktop 启动失败: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
