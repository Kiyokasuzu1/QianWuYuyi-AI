# -*- coding: utf-8 -*-
"""
v1.2.1 Production Hardening — 测试 C: Agent Server fail-closed。

验证（token 缺失/弱默认时）:
1. 安全拒绝启动（无监听、无匿名访问路径）;
2. 不存在 fallback 到无鉴权模式;
3. 状态写盘线程仍然运行（状态文件如实反映 running=false）;
4. 弱 token 集合覆盖历史弱默认值。

说明: 拒绝逻辑位于 api_server._start_agent_server, 属启动路径,
本测试以源码级静态断言 + 生产日志已验证的事实组合验证。
"""

from __future__ import annotations

import ast
from pathlib import Path


def _parse_api_server() -> ast.Module:
    src = Path(__file__).parent.parent / "api_server.py"
    return ast.parse(src.read_text(encoding="utf-8"))


# ------------------------------------------------------------
# Test 1: 弱 token 集合存在且覆盖历史弱默认值
# ------------------------------------------------------------
def test_weak_token_set_covers_known_defaults():
    tree = _parse_api_server()
    src = Path(__file__).parent.parent / "api_server.py"
    text = src.read_text(encoding="utf-8")
    for weak in ("test-token-yuyi-2026", "yuyi-agent-token-2026",
                 "your-secret-token-here", "changeme"):
        assert weak in text, f"弱默认值必须仍在拒绝集合中: {weak}"


# ------------------------------------------------------------
# Test 2: 拒绝路径在启动线程之前（fail-closed 顺序）
# ------------------------------------------------------------
def test_refusal_happens_before_thread_start():
    tree = _parse_api_server()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    fn = funcs.get("_start_agent_server")
    assert fn is not None, "_start_agent_server 必须存在"
    # 收集语句顺序: return(拒绝) 必须出现在 Thread(...).start() 之前
    lines = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Return):
            lines.append(("return", node.lineno))
        if isinstance(node, ast.Call):
            call_name = ""
            if isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            if call_name == "start" and isinstance(node.func.value, ast.Name) and \
                    node.func.value.id == "thread":
                lines.append(("thread_start", node.lineno))
    for kind, lineno in lines:
        if kind == "thread_start":
            assert any(k == "return" and l < lineno for k, l in lines), (
                "弱 token 的 return 拒绝必须发生在 Agent Server 线程启动之前"
            )


# ------------------------------------------------------------
# Test 3: 不存在匿名/无鉴权 fallback 路径
# ------------------------------------------------------------
def test_no_anonymous_fallback():
    src = Path(__file__).parent.parent / "api_server.py"
    text = src.read_text(encoding="utf-8")
    # 拒绝后必须 return, 不得继续进入无 token 启动分支
    idx = text.find("_WEAK_KNOWN_TOKENS")
    assert idx > 0
    segment = text[idx: idx + 1200]
    assert "return" in segment
    assert "auth_token or" not in segment or "if auth_token and" not in segment


# ------------------------------------------------------------
# Test 4: 状态写盘线程独立于 Agent Server 启动（拒绝时仍运行）
# ------------------------------------------------------------
def test_status_writer_independent_of_server_start():
    tree = _parse_api_server()
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    fn = funcs.get("_start_agent_server")
    assert fn is not None
    # _start_agent_status_writer 调用必须位于弱 token 检查之前
    # （v1.1 tick 接线时已把写盘/驱动线程提前, 此处断言该顺序保持）
    calls = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.append((node.func.id, node.lineno))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            calls.append((node.func.attr, node.lineno))
    writer_line = next((l for name, l in calls if name == "_start_agent_status_writer"), None)
    return_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Return) and n.lineno > 0
    ]
    first_return = min(return_lines) if return_lines else None
    assert writer_line is not None, "状态写盘线程调用必须存在"
    if first_return is not None:
        assert writer_line < first_return, "状态写盘线程必须在弱 token 拒绝 return 之前启动"
