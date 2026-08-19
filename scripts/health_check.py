#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/health_check.py

Phase D.0 — 部署后健康检查

羽依运行后, 验证:
  1. /health 端点 (api_server 存活)
  2. /admin 控制台 (Flask Blueprint 挂载)
  3. /v1/models (OpenAI 兼容接口)
  4. /v1/chat/completions 真实聊天链路 (mock 模式 OK)
  5. 远程 WebSocket 端口 8765 可达
  6. data/ 目录关键文件存在 (memory.json / relationship_state.json)
  7. logs/ 目录最近有写入

退出码:
  0  全部通过
  1  关键检查失败
  2  警告

可作为模块导入 (供 first_run_test.py 与 systemd watchdog 复用)
"""
from __future__ import annotations

import os
import sys
import json
import time
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    print("ERROR: requests 未安装, 请先: pip install requests", file=sys.stderr)
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 5000
DEFAULT_REMOTE_PORT = 8765


def _http_get(url: str, timeout: float = 5.0) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """HTTP GET 并尝试解析 JSON"""
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            try:
                return True, f"{url} → 200", r.json()
            except Exception:
                return True, f"{url} → 200 (non-JSON)", None
        return False, f"{url} → {r.status_code}: {r.text[:200]}", None
    except requests.exceptions.ConnectionError:
        return False, f"{url} → 连接失败 (服务可能未启动)", None
    except requests.exceptions.Timeout:
        return False, f"{url} → 超时 ({timeout}s)", None
    except Exception as e:
        return False, f"{url} → 错误: {e}", None


def _http_post_json(url: str, payload: Dict[str, Any], timeout: float = 30.0) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """HTTP POST JSON (用于 /v1/chat/completions)"""
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code == 200:
            try:
                return True, f"{url} → 200", r.json()
            except Exception:
                return True, f"{url} → 200 (non-JSON)", None
        return False, f"{url} → {r.status_code}: {r.text[:200]}", None
    except requests.exceptions.ConnectionError:
        return False, f"{url} → 连接失败", None
    except requests.exceptions.Timeout:
        return False, f"{url} → 超时 ({timeout}s)", None
    except Exception as e:
        return False, f"{url} → 错误: {e}", None


def _check_port_open(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> Tuple[bool, str]:
    """TCP 端口连通性"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"TCP {host}:{port} 可达"
    except (socket.timeout, ConnectionRefusedError) as e:
        return False, f"TCP {host}:{port} 不可达: {e}"
    except Exception as e:
        return False, f"TCP {host}:{port} 错误: {e}"


def _check_data_files() -> Tuple[bool, str, Dict[str, bool]]:
    """检查 data/ 关键文件"""
    files = {
        "memory.json": PROJECT_ROOT / "data" / "memory.json",
        "relationship_state.json": PROJECT_ROOT / "data" / "relationship_state.json",
        "emotion_state.json": PROJECT_ROOT / "data" / "emotion_state.json",
        "conversation_history.json": PROJECT_ROOT / "data" / "conversation_history.json",
    }
    details = {name: path.exists() for name, path in files.items()}
    missing = [n for n, ok in details.items() if not ok]
    if missing:
        return False, f"缺失: {', '.join(missing)} (首次启动后会自动生成)", details
    return True, "全部关键数据文件存在", details


def _check_log_recent() -> Tuple[bool, str]:
    """检查 logs/api.log 最近有写入 (10 分钟内)"""
    log = PROJECT_ROOT / "logs" / "api.log"
    if not log.exists():
        return True, "logs/api.log 不存在 (服务可能未启动, 或由 systemd 重定向到 journal)"
    try:
        mtime = log.stat().st_mtime
        age_s = time.time() - mtime
        if age_s < 600:
            return True, f"logs/api.log 最近 {int(age_s)}s 前有写入"
        return False, f"logs/api.log 已 {int(age_s)}s 未更新, 服务可能僵死"
    except Exception as e:
        return False, f"检查日志失败: {e}"


def run_all_checks(
    api_host: str = DEFAULT_API_HOST,
    api_port: Optional[int] = None,
    remote_port: Optional[int] = None,
    do_chat_probe: bool = True,
) -> Dict[str, Any]:
    """执行所有健康检查

    Args:
        api_host: API 主机
        api_port: API 端口 (默认从 .env 或 5000)
        remote_port: 远程 WS 端口 (默认 8765)
        do_chat_probe: 是否实际调用 /v1/chat/completions
    """
    # 从 .env 加载端口
    api_port = api_port or int(os.getenv("YUYI_API_PORT", DEFAULT_API_PORT))
    remote_port = remote_port or int(os.getenv("YUYI_REMOTE_PORT", DEFAULT_REMOTE_PORT))

    base = f"http://{api_host}:{api_port}"
    checks: Dict[str, Dict[str, Any]] = {}

    # 1. /health
    ok, msg, data = _http_get(f"{base}/health", timeout=3.0)
    checks["health_endpoint"] = {"ok": ok, "msg": msg, "data": data}

    # 2. /admin (HEAD or GET)
    ok, msg, data = _http_get(f"{base}/admin/", timeout=3.0)
    checks["admin_console"] = {"ok": ok, "msg": msg, "data": data}

    # 3. /v1/models
    ok, msg, data = _http_get(f"{base}/v1/models", timeout=3.0)
    checks["v1_models"] = {"ok": ok, "msg": msg, "data": data}

    # 4. /v1/chat/completions (真实链路探针)
    chat_ok = True
    chat_msg = "skipped (do_chat_probe=False)"
    chat_data: Optional[Dict[str, Any]] = None
    if do_chat_probe:
        chat_payload = {
            "model": "yuyi",
            "messages": [{"role": "user", "content": "你好, 你是谁?"}],
            "user": "healthcheck",
        }
        chat_ok, chat_msg, chat_data = _http_post_json(
            f"{base}/v1/chat/completions", chat_payload, timeout=30.0
        )
    checks["chat_completions"] = {"ok": chat_ok, "msg": chat_msg, "data": chat_data}

    # 5. 远程 WS 端口
    ok, msg = _check_port_open(remote_port)
    checks["remote_ws"] = {"ok": ok, "msg": msg}

    # 6. data/ 文件
    ok, msg, details = _check_data_files()
    checks["data_files"] = {"ok": ok, "msg": msg, "details": details}

    # 7. logs/ 最近写入
    ok, msg = _check_log_recent()
    checks["log_recent"] = {"ok": ok, "msg": msg}

    # 汇总
    critical_keys = ("health_endpoint", "v1_models", "chat_completions")
    critical_passed = all(checks[k]["ok"] for k in critical_keys)
    any_failed = any(not checks[k]["ok"] for k in checks)

    if not critical_passed:
        exit_code = 1
    elif any_failed:
        exit_code = 2
    else:
        exit_code = 0

    return {
        "passed": exit_code == 0,
        "exit_code": exit_code,
        "checks": checks,
        "summary": f"关键链路 {sum(1 for k in critical_keys if checks[k]['ok'])}/{len(critical_keys)} 通过, 退出码 {exit_code}",
        "api_base": base,
    }


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 60)
    print(f"  Yuyi Health Check (Phase D.0) — {report['api_base']}")
    print("=" * 60)
    for name, c in report["checks"].items():
        mark = "[OK]" if c["ok"] else "[!!]"
        print(f"  {mark} {name}: {c['msg']}")
    print("-" * 60)
    print(f"  摘要: {report['summary']}")
    print("=" * 60)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Yuyi 部署后健康检查")
    parser.add_argument("--host", default=DEFAULT_API_HOST, help="API 主机 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="API 端口 (默认 5000 或 YUYI_API_PORT)")
    parser.add_argument("--remote-port", type=int, default=None, help="远程 WS 端口 (默认 8765)")
    parser.add_argument("--no-chat-probe", action="store_true", help="跳过真实聊天探针")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args()

    report = run_all_checks(
        api_host=args.host,
        api_port=args.port,
        remote_port=args.remote_port,
        do_chat_probe=not args.no_chat_probe,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
