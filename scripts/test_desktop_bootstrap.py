#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟 Desktop 完整启动流程，验证 token 注入 + 端点连接。

流程:
  1. 通过 main._build_desktop_config() 加载 token
  2. 构造 DesktopContext(config=cfg)
  3. 验证 api._token_provider 绑定正确
  4. 通过 RemoteProviderBridge 探测所有端点
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 强制环境变量
os.environ["YUYI_API_BASE"] = "http://127.0.0.1:5001"
os.environ["YUYI_DESKTOP_TOKEN"] = "test-token-yuyi-2026"

from yuyi_desktop.config.desktop_config import DesktopConfig
from yuyi_desktop.core.desktop_context import DesktopContext
from yuyi_desktop.main import _build_desktop_config
from yuyi_desktop.core.remote_provider_bridge import (
    ENDPOINTS,
    PROVIDER_GROUPS,
    RemoteProviderBridge,
)


def main():
    print("=" * 80)
    print("完整 Desktop 启动流程测试")
    print("=" * 80)

    # 1. 通过 main._build_desktop_config() 加载 token
    cfg = _build_desktop_config()
    print(f"\n[1] DesktopConfig: {cfg.safe_repr()}")
    print(f"    has_auth_token: {cfg.has_auth_token()}")
    print(f"    auth_token preview: {cfg.auth_token[:8]}...{cfg.auth_token[-4:]}")

    # 2. 构造 DesktopContext
    ctx = DesktopContext(config=cfg)
    print(f"\n[2] DesktopContext.bridge: {type(ctx.bridge).__name__}")
    print(f"    api._token_provider: {ctx.api._token_provider}")
    if ctx.api._token_provider:
        token = ctx.api._token_provider()
        print(f"    Token via provider: {token[:8]}...{token[-4:] if len(token) > 4 else '***'}")

    # 3. 验证 headers
    headers = ctx.api._build_headers()
    auth_header = headers.get("Authorization", "NONE")
    print(f"    Authorization header: {auth_header[:40]}{'...' if len(auth_header) > 40 else ''}")

    # 4. 探测所有端点
    print("\n[3] 端点连接诊断 (使用 bridge)")
    print("-" * 80)
    success = 0
    fail = 0
    for name, ep in ENDPOINTS.items():
        env = ctx.bridge._get(ep)
        ok = env.get("success", False)
        err = env.get("error", "")
        latency = env.get("latency_ms", 0.0)
        data_keys = list((env.get("data", {}) or {}).keys())[:5] if ok else []
        status = "OK" if ok else "FAIL"
        print(f"  {name:<25} {ep:<25} [{status}] {latency:6.1f}ms err={err[:40]!r:<42} keys={data_keys}")
        if ok:
            success += 1
        else:
            fail += 1

    # 5. 检查 health_check
    print("\n[4] bridge.health_check (按分组)")
    print("-" * 80)
    health = ctx.bridge.health_check()
    for group, ok in health.items():
        print(f"  {group:<15} {'OK' if ok else 'FAIL'}")

    print("\n" + "=" * 80)
    print(f"端点成功: {success}/{len(ENDPOINTS)}, 失败: {fail}")
    print("=" * 80)

    if fail > 0:
        print("❌ 有端点失败,需要修复")
        sys.exit(1)
    else:
        print("✅ 所有端点连接成功")
        sys.exit(0)


if __name__ == "__main__":
    main()
