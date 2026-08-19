#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/verify_phase_p0_gateway.py

Phase P0-P1 验收脚本:
  1. 导入 api_server 的 Flask app
  2. 打印 url_map, 确认 /api/v1/* 端点存在
  3. 使用 Flask test_client 模拟 curl 调用, 检查状态码 / JSON / 认证

不修改后端, 不修改 Desktop, 只做只读验证。
"""

from __future__ import annotations

import json
import sys
import os
import io
from pathlib import Path

# 把项目根目录加进 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 抑制 import 期间的 stderr 噪音 (Orchestrator / Bridge 等)
import logging
logging.basicConfig(level=logging.WARNING)


# ===================== 1. 导入 app =====================
print("=" * 70)
print("[P0-P1 验证] 导入 api_server.app")
print("=" * 70)

try:
    import api_server
except Exception as exc:  # noqa: BLE001
    print(f"[FAIL] 导入 api_server 失败: {type(exc).__name__}: {exc}")
    sys.exit(2)

app = getattr(api_server, "app", None)
if app is None:
    print("[FAIL] api_server.app 不存在")
    sys.exit(2)

print(f"[OK] api_server.app 导入成功: {app}")


# ===================== 2. 打印 /api/v1 路由 =====================
print()
print("=" * 70)
print("[P0-P1 验证] Flask 路由表 (筛选 /api/v1)")
print("=" * 70)

EXPECTED_ROUTES = [
    "/api/v1/health",
    "/api/v1/runtime/status",
    "/api/v1/runtime/overview",
    "/api/v1/personality/status",
    "/api/v1/selfmodel/status",
    "/api/v1/memory/overview",
    "/api/v1/growth/status",
    "/api/v1/initiative/status",
    "/api/v1/audit/recent",
]

registered = {}
for rule in app.url_map.iter_rules():
    methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
    if "/api/v1" in rule.rule:
        registered[rule.rule] = methods
        print(f"  {','.join(methods):>10s}  {rule.rule}  -> {rule.endpoint}")

# 检查
print()
print("-" * 70)
print("[期望] 上述列表至少应包含以下 9 个 GET 端点:")
missing = []
for path in EXPECTED_ROUTES:
    if path not in registered:
        missing.append(path)
        print(f"  [MISSING] {path}")
    else:
        methods = registered[path]
        if "GET" not in methods:
            print(f"  [WRONG METHOD] {path}: {methods}")
            missing.append(path)
        else:
            print(f"  [OK] GET {path}")

if missing:
    print()
    print(f"[FAIL] 缺失 {len(missing)} 个端点: {missing}")
    sys.exit(3)

print()
print("[OK] 所有 9 个 /api/v1/* 端点已注册")


# ===================== 3. 打印所有 url_map 概览 =====================
print()
print("=" * 70)
print("[P0-P1 验证] 完整 url_map (按 endpoint 排序)")
print("=" * 70)

rules_sorted = sorted(app.url_map.iter_rules(), key=lambda r: (r.rule, r.endpoint))
for rule in rules_sorted:
    methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
    print(f"  {','.join(methods):>10s}  {rule.rule}  -> {rule.endpoint}")


# ===================== 4. 使用 test_client 模拟 curl =====================
print()
print("=" * 70)
print("[P0-P1 验证] 使用 Flask test_client 模拟 curl (无 token)")
print("=" * 70)

client = app.test_client()

# 4.1 无 token 请求 - 期望 401 (require_auth 装饰器)
print()
print("[4.1] 无 token 请求 - 期望 401")
NO_TOKEN_RESULTS = {}
for path in EXPECTED_ROUTES:
    resp = client.get(path)
    is_json = resp.is_json if hasattr(resp, "is_json") else False
    body_preview = ""
    if is_json:
        try:
            data = resp.get_json()
            body_preview = json.dumps(data, ensure_ascii=False)[:120]
        except Exception:
            body_preview = "(unparseable json)"
    else:
        body_preview = resp.data[:80].decode("utf-8", errors="replace")
    NO_TOKEN_RESULTS[path] = {
        "status": resp.status_code,
        "is_json": is_json,
        "body_preview": body_preview,
    }
    print(f"  {resp.status_code:>4d}  {path:<35s}  json={is_json}  body={body_preview}")

# 4.2 读 config.yaml 取 admin token
print()
print("[4.2] 读 config.yaml 提取 token")
config_path = ROOT / "config.yaml"
TOKEN = ""
if config_path.exists():
    try:
        import yaml  # type: ignore
        cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        # 优先级 1: control_api.auth.token
        ca = cfg.get("control_api") or {}
        if isinstance(ca, dict):
            auth_raw = ca.get("auth") or {}
            if isinstance(auth_raw, dict) and auth_raw.get("token"):
                TOKEN = str(auth_raw.get("token", ""))
        # 优先级 2: remote.auth_token
        if not TOKEN:
            remote = cfg.get("remote") or {}
            if isinstance(remote, dict) and remote.get("auth_token"):
                TOKEN = str(remote.get("auth_token", ""))
        # 兜底: 其他常见 key
        if not TOKEN:
            TOKEN = (
                cfg.get("admin", {}).get("token", "")
                or cfg.get("api", {}).get("admin_token", "")
                or cfg.get("desktop", {}).get("auth_token", "")
                or cfg.get("control", {}).get("admin_token", "")
                or cfg.get("admin_token", "")
                or ""
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] 读 config.yaml 失败: {exc}")

# 也尝试 .env
env_path = ROOT / ".env"
if not TOKEN and env_path.exists():
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip().lower() in ("admin_token", "yuyi_admin_token", "api_admin_token"):
                    TOKEN = v.strip().strip('"').strip("'")
                    break
    except Exception:
        pass

print(f"  token 长度: {len(TOKEN)}  (预览: {TOKEN[:6] + '***' if TOKEN else '(空)'})")

# 4.3 带 token 请求 - 期望 200 + JSON envelope
print()
print("[4.3] 带 token 请求 - 期望 200 + JSON envelope")
WITH_TOKEN_RESULTS = {}
for path in EXPECTED_ROUTES:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    resp = client.get(path, headers=headers)
    is_json = resp.is_json if hasattr(resp, "is_json") else False
    body_preview = ""
    if is_json:
        try:
            data = resp.get_json()
            body_preview = json.dumps(data, ensure_ascii=False)[:140]
        except Exception:
            body_preview = "(unparseable json)"
    else:
        body_preview = resp.data[:80].decode("utf-8", errors="replace")
    WITH_TOKEN_RESULTS[path] = {
        "status": resp.status_code,
        "is_json": is_json,
        "body_preview": body_preview,
    }
    print(f"  {resp.status_code:>4d}  {path:<35s}  json={is_json}  body={body_preview}")


# ===================== 5. 汇总 =====================
print()
print("=" * 70)
print("[P0-P1 验证] 汇总")
print("=" * 70)

# 5.1 无 token 应该是 401 (未授权) 或 403 (禁止)
all_no_token_401_or_403 = all(
    r["status"] in (401, 403) for r in NO_TOKEN_RESULTS.values()
)
all_no_token_json = all(r["is_json"] for r in NO_TOKEN_RESULTS.values())

# 5.2 有 token 应该都是 200 (envelope) 或 404
# 注意: 即便 token 通过, Provider 不可用也可能返回 envelope(200 + success=false)
all_with_token_200 = all(
    r["status"] == 200 for r in WITH_TOKEN_RESULTS.values()
) if TOKEN else False
all_with_token_json = all(r["is_json"] for r in WITH_TOKEN_RESULTS.values())

print(f"  无 token 一律 401/403:        {'OK' if all_no_token_401_or_403 else 'NO'}")
print(f"  无 token 一律 JSON 响应:      {'OK' if all_no_token_json else 'NO'}")
print(f"  有 token 一律 200:            {'OK' if all_with_token_200 else '(token 缺失或部分异常)'}")
print(f"  有 token 一律 JSON 响应:      {'OK' if all_with_token_json else 'NO'}")
print()
print("[P0-P1 验证] 路由表与 curl 行为核对完毕")
