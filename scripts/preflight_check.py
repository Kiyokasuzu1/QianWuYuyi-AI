#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/preflight_check.py

Phase D.0 — 部署前 Preflight 检查

启动羽依前自动检查:
  1. Python 版本 (>= 3.11)
  2. 核心依赖 (openai, flask, yaml, chromadb, dotenv, requests, websockets)
  3. config.yaml 存在性
  4. .env 存在性 (可选, 缺失时警告但不阻断)
  5. 数据目录可写
  6. 日志目录可写
  7. 关键端口空闲 (默认 5000 / 8765)
  8. .env 中的 LLM key 状态 (有 key → 真实模式, 无 key → mock 模式)

退出码:
  0  全部通过
  1  关键检查失败 (Python 版本 / 核心依赖 / config / 数据目录)
  2  警告 (非关键项异常, 可继续部署)

可作为模块导入, 返回结构化结果 (供 first_run_test.py 复用)
"""
from __future__ import annotations

import os
import sys
import json
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_PYTHON = (3, 11)
REQUIRED_DEPS = [
    "openai",
    "flask",
    "yaml",
    "dotenv",
    "requests",
    "chromadb",
    "websockets",
]
DEFAULT_CHECK_PORTS = [5000, 8765]


def _check_python() -> Tuple[bool, str]:
    """检查 Python 版本是否满足最低要求"""
    v = sys.version_info
    if (v.major, v.minor) >= REQUIRED_PYTHON:
        return True, f"Python {v.major}.{v.minor}.{v.micro} (>= {'.'.join(map(str, REQUIRED_PYTHON))})"
    return False, f"Python {v.major}.{v.minor}.{v.micro} < {'.'.join(map(str, REQUIRED_PYTHON))}"


def _check_dependencies() -> Tuple[bool, str, List[str]]:
    """检查核心依赖是否安装"""
    missing = []
    for dep in REQUIRED_DEPS:
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
    if missing:
        return False, f"缺少依赖: {', '.join(missing)} (运行: pip install -r requirements.txt)", missing
    return True, f"全部 {len(REQUIRED_DEPS)} 个核心依赖已安装", []


def _check_config_yaml() -> Tuple[bool, str, Optional[Path]]:
    """检查 config.yaml 存在性; 缺失时尝试从 example 复制"""
    cfg = PROJECT_ROOT / "config.yaml"
    if cfg.exists():
        return True, f"config.yaml 已存在: {cfg}", cfg
    example = PROJECT_ROOT / "config.yaml.example"
    if example.exists():
        try:
            cfg.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            return True, f"config.yaml 缺失,已从 config.yaml.example 复制生成: {cfg}", cfg
        except Exception as e:
            return False, f"config.yaml 缺失且无法从 example 复制: {e}", None
    return False, "config.yaml 与 config.yaml.example 都不存在", None


def _check_dotenv() -> Tuple[bool, str]:
    """检查 .env 文件存在性 (非关键)"""
    env = PROJECT_ROOT / ".env"
    if env.exists():
        return True, f".env 已存在: {env}"
    example = PROJECT_ROOT / ".env.example"
    if example.exists():
        return True, f".env 缺失(非关键),.env.example 已存在,可手动复制: cp .env.example .env"
    return False, ".env 与 .env.example 都不存在"


def _check_writable_dir(path: Path) -> Tuple[bool, str]:
    """检查目录可写"""
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return False, f"无法创建目录 {path}: {e}"
    test_file = path / ".preflight_write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return True, f"目录可写: {path}"
    except Exception as e:
        return False, f"目录不可写 {path}: {e}"


def _check_data_dir() -> Tuple[bool, str]:
    return _check_writable_dir(PROJECT_ROOT / "data")


def _check_log_dir() -> Tuple[bool, str]:
    return _check_writable_dir(PROJECT_ROOT / "logs")


def _check_port_free(port: int, host: str = "0.0.0.0") -> Tuple[bool, str]:
    """检查端口是否空闲"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True, f"端口 {port} 空闲"
    except OSError as e:
        return False, f"端口 {port} 被占用: {e}"


def _check_ports(ports: List[int]) -> Tuple[bool, List[Tuple[int, bool, str]]]:
    """批量检查端口"""
    results = []
    all_free = True
    for p in ports:
        ok, msg = _check_port_free(p)
        results.append((p, ok, msg))
        if not ok:
            all_free = False
    return all_free, results


def _check_llm_key() -> Tuple[bool, str, str]:
    """检查 LLM 凭据状态"""
    # 1) 尝试从 .env 加载
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except Exception:
        pass
    ds = os.getenv("DEEPSEEK_API_KEY", "").strip()
    oa = os.getenv("OPENAI_API_KEY", "").strip()
    force_mock = os.getenv("YUYI_LLM_MOCK", "").strip().lower() in ("1", "true", "yes")

    if force_mock:
        return True, "YUYI_LLM_MOCK=1,显式进入 mock 模式", "mock"
    if ds:
        return True, f"DEEPSEEK_API_KEY 已设置 (长度 {len(ds)})", "deepseek"
    if oa:
        return True, f"OPENAI_API_KEY 已设置 (长度 {len(oa)})", "openai"
    return True, "未设置 LLM key, 将自动进入 mock 模式 (可正常启动)", "mock"


def run_all_checks(ports: Optional[List[int]] = None) -> Dict[str, Any]:
    """执行所有 preflight 检查

    Returns:
        {
            "passed": bool,        # 全部关键项通过
            "exit_code": int,      # 0/1/2
            "results": {           # 详细结果
                "python": {"ok": bool, "msg": str},
                "deps":   {"ok": bool, "msg": str, "missing": [...]},
                "config": {"ok": bool, "msg": str, "path": str|None},
                "dotenv": {"ok": bool, "msg": str},
                "data":   {"ok": bool, "msg": str},
                "log":    {"ok": bool, "msg": str},
                "ports":  {"ok": bool, "details": [(port, ok, msg)...]},
                "llm":    {"ok": bool, "msg": str, "mode": "mock|deepseek|openai"},
            },
            "summary": "..."
        }
    """
    ports = ports or DEFAULT_CHECK_PORTS

    py_ok, py_msg = _check_python()
    deps_ok, deps_msg, missing = _check_dependencies()
    cfg_ok, cfg_msg, cfg_path = _check_config_yaml()
    env_ok, env_msg = _check_dotenv()
    data_ok, data_msg = _check_data_dir()
    log_ok, log_msg = _check_log_dir()
    ports_ok, ports_details = _check_ports(ports)
    llm_ok, llm_msg, llm_mode = _check_llm_key()

    critical = [py_ok, deps_ok, cfg_ok, data_ok, log_ok]
    all_critical = all(critical)
    warnings = [not env_ok, not ports_ok]
    has_warnings = any(warnings)

    if not all_critical:
        exit_code = 1
    elif has_warnings:
        exit_code = 2
    else:
        exit_code = 0

    results = {
        "python": {"ok": py_ok, "msg": py_msg},
        "deps": {"ok": deps_ok, "msg": deps_msg, "missing": missing},
        "config": {"ok": cfg_ok, "msg": cfg_msg, "path": str(cfg_path) if cfg_path else None},
        "dotenv": {"ok": env_ok, "msg": env_msg},
        "data": {"ok": data_ok, "msg": data_msg},
        "log": {"ok": log_ok, "msg": log_msg},
        "ports": {"ok": ports_ok, "details": ports_details},
        "llm": {"ok": llm_ok, "msg": llm_msg, "mode": llm_mode},
    }

    passed_count = sum(1 for k in ("python", "deps", "config", "data", "log")
                       if results[k]["ok"])
    summary = f"通过 {passed_count}/5 项关键检查, 退出码 {exit_code}"

    return {
        "passed": exit_code == 0,
        "exit_code": exit_code,
        "results": results,
        "summary": summary,
    }


def print_report(report: Dict[str, Any]) -> None:
    """打印人类可读报告"""
    print("=" * 60)
    print("  Yuyi Preflight Check (Phase D.0)")
    print("=" * 60)
    r = report["results"]
    rows = [
        ("Python", r["python"]["ok"], r["python"]["msg"]),
        ("依赖", r["deps"]["ok"], r["deps"]["msg"]),
        ("config.yaml", r["config"]["ok"], r["config"]["msg"]),
        (".env", r["dotenv"]["ok"], r["dotenv"]["msg"]),
        ("data/", r["data"]["ok"], r["data"]["msg"]),
        ("logs/", r["log"]["ok"], r["log"]["msg"]),
        ("端口", r["ports"]["ok"],
         "; ".join(f"{p}:{'OK' if ok else 'BUSY'}" for p, ok, _ in r["ports"]["details"])),
        ("LLM", r["llm"]["ok"], r["llm"]["msg"]),
    ]
    for name, ok, msg in rows:
        mark = "[OK]" if ok else "[!!]"
        print(f"  {mark} {name}: {msg}")
    print("-" * 60)
    print(f"  摘要: {report['summary']}")
    print("=" * 60)


def main() -> int:
    """CLI 入口"""
    import argparse
    parser = argparse.ArgumentParser(description="Yuyi 部署前 Preflight 检查")
    parser.add_argument("--port", type=int, action="append", default=None,
                        help="要检查的端口 (可多次传, 默认 5000/8765)")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()
    report = run_all_checks(ports=args.port)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
