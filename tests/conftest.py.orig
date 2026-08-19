# -*- coding: utf-8 -*-
"""
tests/conftest.py

Yuyi 测试全局 fixtures。

提供:
- QT_QPA_PLATFORM=offscreen 自动设置
- mock_yuyi_server:启动一个 in-process 模拟 Yuyi Server
- repo_root:仓库根路径
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 测试期间强制 Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 仓库根路径加入 sys.path
_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


import pytest


# ============================================================
# 仓库根路径
# ============================================================
@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ============================================================
# Mock Yuyi Server(基于 werkzeug 直接启动,可控端口)
# ============================================================
@pytest.fixture
def mock_yuyi_server():
    """
    启动一个 in-process werkzeug mock server,模拟 Yuyi Server API。

    提供:
        base_url: "http://127.0.0.1:<port>/api/v1"
        port: 端口号
        set_response(path, response): 自定义 endpoint 响应
        get_calls(): 获取请求日志
        stop(): 关闭 server
    """
    try:
        from flask import Flask, jsonify  # type: ignore
        from werkzeug.serving import make_server  # type: ignore
    except ImportError:
        pytest.skip("Flask/Werkzeug 未安装,无法启动 mock server")

    app = Flask("mock_yuyi_server")
    responses: Dict[str, Dict[str, Any]] = {}
    call_log: List[Dict[str, Any]] = []
    call_lock = threading.Lock()

    # 默认数据
    DEFAULT_DATA = {
        "/health/ping": {
            "success": True,
            "data": {"status": "ok", "timestamp": "2026-08-04T00:00:00Z"},
        },
        "/system/info": {
            "success": True,
            "data": {
                "version": "0.9.0",
                "server_version": "0.9.0",
                "schema_version": "1.0",
            },
        },
        # C.10.3 新增端点(供新契约使用)
        "/health": {
            "success": True,
            "data": {
                "server_status": "ok",
                "runtime_status": "running",
                "version": "0.10.3",
                "schema_version": "1.0",
                "uptime_seconds": 123.4,
                "authority": {"runtime": True, "personality": True, "memory": True},
                "runtime": {"initialized": True, "is_running": True},
            },
        },
        "/runtime/overview": {
            "success": True,
            "data": {
                "runtime": {"online": True, "initialized": True, "is_running": True},
                "personality": {
                    "available": True,
                    "current": {
                        "identity_name": "浅雾羽依",
                        "version": "1.0.0",
                    },
                },
                "selfmodel": {"available": True, "version": "2.0"},
                "emotion": {"available": True, "state": "calm"},
                "memory": {"available": True, "total_count": 100},
            },
        },
        "/personality/status": {
            "success": True,
            "data": {
                "available": True,
                "snapshot": {
                    "identity_name": "浅雾羽依",
                    "version": "1.0.0",
                    "stable": True,
                    "traits": {"温柔": 0.8, "理性": 0.7},
                },
                "traits": [{"name": "温柔", "value": 0.8}],
                "evolution_version": "1.0.0",
                "state": "stable",
            },
        },
        "/selfmodel/status": {
            "success": True,
            "data": {
                "available": True,
                "version": "2.0",
                "identity_name": "浅雾羽依",
                "bootstrap": {"version": "2.0"},
                "health": "healthy",
                "bridge_error": None,
            },
        },
        "/memory/overview": {
            "success": True,
            "data": {
                "available": True,
                "total_count": 100,
                "important_count": 12,
                "user_id": "user-1",
                "recent": [
                    {"memory_id": "m1", "summary": "first meeting"},
                    {"memory_id": "m2", "summary": "second meeting"},
                ],
            },
        },
        "/growth/status": {
            "success": True,
            "data": {
                "available": True,
                "section": "growth",
                "proposal_count": 10,
                "pending_count": 3,
                "approved_count": 5,
                "rejected_count": 2,
                "applied_count": 0,
                "evolution": {"stage": "stable", "version": "1.0.0"},
            },
        },
        "/initiative/status": {
            "success": True,
            "data": {
                "available": True,
                "interest_count": 5,
                "possible_action_count": 3,
                "filtered_count": 1,
                "by_trend": {"curiosity": 3, "care": 2},
                "by_action_status": {"pending": 2, "delivered": 1},
                "by_action_type": {"speak": 2, "observe": 1},
                "last_interest_at": "2026-08-04T00:00:00Z",
                "last_action_at": "2026-08-04T00:01:00Z",
                "recent": [
                    {"action_id": "a1", "type": "speak", "status": "delivered"},
                ],
                "fallback": False,
            },
        },
        "/audit/recent": {
            "success": True,
            "data": {
                "available": True,
                "total": 2,
                "limit": 20,
                "items": [
                    {"event_id": "ae1", "event_type": "runtime.tick", "ts": "2026-08-04T00:00:00Z"},
                    {"event_id": "ae2", "event_type": "memory.write", "ts": "2026-08-04T00:00:01Z"},
                ],
            },
        },
        # C.10.2 兼容端点(保留,确保 desktop smoke 仍然可用)
        "/runtime/status": {
            "success": True,
            "data": {
                "initialized": True,
                "running": True,
                "uptime": 120.5,
                "current_state": "idle",
                "online": True,
                "tick_count": 100,
                # 兼容 C.10.3 server gateway 字段
                "is_running": True,
                "adapters": {"runtime": True, "personality": True, "memory": True},
                "cycle_state": "running",
                "health": "healthy",
                "version": "0.10.3",
            },
        },
        "/runtime/tasks": {
            "success": True,
            "data": {
                "tasks": [
                    {"task_name": "PerceptionAdapter", "status": "running"},
                    {"task_name": "MemoryAdapter", "status": "running"},
                ],
                "available": True,
            },
        },
        "/runtime/ticks": {
            "success": True,
            "data": [
                {"event_id": "e1", "event_type": "tick", "timestamp": "2026-08-04T00:00:00Z"},
            ],
        },
        "/memory/summary": {
            "success": True,
            "data": {"total_count": 100, "important_count": 12, "available": True},
        },
        "/memory/recent": {
            "success": True,
            "data": [
                {"memory_id": "m1", "summary": "first meeting"},
            ],
        },
        "/personality/snapshot": {
            "success": True,
            "data": {
                "identity_name": "浅雾羽依",
                "version": "1.0.0",
                "stable": True,
            },
        },
        "/personality/traits": {
            "success": True,
            "data": [
                {"trait": "温柔", "value": 0.8},
            ],
        },
        "/personality/evolution": {
            "success": True,
            "data": [],
        },
        "/selfmodel/snapshot": {
            "success": True,
            "data": {
                "identity_name": "浅雾羽依",
                "version": "2.0",
                "stable": True,
            },
        },
        "/growth/summary": {
            "success": True,
            "data": {
                "total": 10,
                "pending": 3,
                "approved": 5,
                "rejected": 2,
                "applied": 0,
                "available": True,
            },
        },
        "/growth/recent": {
            "success": True,
            "data": [],
        },
        "/growth/proposals": {
            "success": True,
            "data": [],
        },
        "/initiative/summary": {
            "success": True,
            "data": {
                "interest_count": 5,
                "possible_action_count": 3,
                "filtered_count": 1,
                "available": True,
            },
        },
        "/initiative/actions": {
            "success": True,
            "data": [],
        },
        "/life/state": {
            "success": True,
            "data": {"stage": "mature", "available": True},
        },
        "/life/timeline": {
            "success": True,
            "data": [],
        },
        "/life/graph": {
            "success": True,
            "data": {"nodes": [], "edges": []},
        },
    }

    def _make_response(path: str):
        payload = responses.get(path, DEFAULT_DATA.get(path, {
            "success": False,
            "data": {},
            "error": "not_found",
        }))
        return jsonify(payload), 200

    @app.route("/api/v1/health/ping", methods=["GET"])
    def _ping():
        with call_lock:
            call_log.append({"method": "GET", "path": "/health/ping"})
        return _make_response("/health/ping")

    @app.route("/api/v1/system/info", methods=["GET"])
    def _info():
        with call_lock:
            call_log.append({"method": "GET", "path": "/system/info"})
        return _make_response("/system/info")

    @app.route("/api/v1/runtime/status", methods=["GET"])
    def _runtime_status():
        with call_lock:
            call_log.append({"method": "GET", "path": "/runtime/status"})
        return _make_response("/runtime/status")

    # C.10.3 新增端点
    @app.route("/api/v1/health", methods=["GET"])
    def _c103_health():
        with call_lock:
            call_log.append({"method": "GET", "path": "/health"})
        return _make_response("/health")

    @app.route("/api/v1/runtime/overview", methods=["GET"])
    def _c103_runtime_overview():
        with call_lock:
            call_log.append({"method": "GET", "path": "/runtime/overview"})
        return _make_response("/runtime/overview")

    @app.route("/api/v1/personality/status", methods=["GET"])
    def _c103_personality_status():
        with call_lock:
            call_log.append({"method": "GET", "path": "/personality/status"})
        return _make_response("/personality/status")

    @app.route("/api/v1/selfmodel/status", methods=["GET"])
    def _c103_selfmodel_status():
        with call_lock:
            call_log.append({"method": "GET", "path": "/selfmodel/status"})
        return _make_response("/selfmodel/status")

    @app.route("/api/v1/memory/overview", methods=["GET"])
    def _c103_memory_overview():
        with call_lock:
            call_log.append({"method": "GET", "path": "/memory/overview"})
        return _make_response("/memory/overview")

    @app.route("/api/v1/growth/status", methods=["GET"])
    def _c103_growth_status():
        with call_lock:
            call_log.append({"method": "GET", "path": "/growth/status"})
        return _make_response("/growth/status")

    @app.route("/api/v1/initiative/status", methods=["GET"])
    def _c103_initiative_status():
        with call_lock:
            call_log.append({"method": "GET", "path": "/initiative/status"})
        return _make_response("/initiative/status")

    @app.route("/api/v1/audit/recent", methods=["GET"])
    def _c103_audit_recent():
        with call_lock:
            call_log.append({"method": "GET", "path": "/audit/recent"})
        return _make_response("/audit/recent")

    @app.route("/api/v1/runtime/tasks", methods=["GET"])
    def _runtime_tasks():
        with call_lock:
            call_log.append({"method": "GET", "path": "/runtime/tasks"})
        return _make_response("/runtime/tasks")

    @app.route("/api/v1/runtime/ticks", methods=["GET"])
    def _runtime_ticks():
        with call_lock:
            call_log.append({"method": "GET", "path": "/runtime/ticks"})
        return _make_response("/runtime/ticks")

    @app.route("/api/v1/memory/summary", methods=["GET"])
    def _memory_summary():
        with call_lock:
            call_log.append({"method": "GET", "path": "/memory/summary"})
        return _make_response("/memory/summary")

    @app.route("/api/v1/memory/recent", methods=["GET"])
    def _memory_recent():
        with call_lock:
            call_log.append({"method": "GET", "path": "/memory/recent"})
        return _make_response("/memory/recent")

    @app.route("/api/v1/personality/snapshot", methods=["GET"])
    def _personality_snapshot():
        with call_lock:
            call_log.append({"method": "GET", "path": "/personality/snapshot"})
        return _make_response("/personality/snapshot")

    @app.route("/api/v1/personality/traits", methods=["GET"])
    def _personality_traits():
        with call_lock:
            call_log.append({"method": "GET", "path": "/personality/traits"})
        return _make_response("/personality/traits")

    @app.route("/api/v1/personality/evolution", methods=["GET"])
    def _personality_evolution():
        with call_lock:
            call_log.append({"method": "GET", "path": "/personality/evolution"})
        return _make_response("/personality/evolution")

    @app.route("/api/v1/selfmodel/snapshot", methods=["GET"])
    def _selfmodel_snapshot():
        with call_lock:
            call_log.append({"method": "GET", "path": "/selfmodel/snapshot"})
        return _make_response("/selfmodel/snapshot")

    @app.route("/api/v1/growth/summary", methods=["GET"])
    def _growth_summary():
        with call_lock:
            call_log.append({"method": "GET", "path": "/growth/summary"})
        return _make_response("/growth/summary")

    @app.route("/api/v1/growth/recent", methods=["GET"])
    def _growth_recent():
        with call_lock:
            call_log.append({"method": "GET", "path": "/growth/recent"})
        return _make_response("/growth/recent")

    @app.route("/api/v1/growth/proposals", methods=["GET"])
    def _growth_proposals():
        with call_lock:
            call_log.append({"method": "GET", "path": "/growth/proposals"})
        return _make_response("/growth/proposals")

    @app.route("/api/v1/initiative/summary", methods=["GET"])
    def _initiative_summary():
        with call_lock:
            call_log.append({"method": "GET", "path": "/initiative/summary"})
        return _make_response("/initiative/summary")

    @app.route("/api/v1/initiative/actions", methods=["GET"])
    def _initiative_actions():
        with call_lock:
            call_log.append({"method": "GET", "path": "/initiative/actions"})
        return _make_response("/initiative/actions")

    @app.route("/api/v1/life/state", methods=["GET"])
    def _life_state():
        with call_lock:
            call_log.append({"method": "GET", "path": "/life/state"})
        return _make_response("/life/state")

    @app.route("/api/v1/life/timeline", methods=["GET"])
    def _life_timeline():
        with call_lock:
            call_log.append({"method": "GET", "path": "/life/timeline"})
        return _make_response("/life/timeline")

    @app.route("/api/v1/life/graph", methods=["GET"])
    def _life_graph():
        with call_lock:
            call_log.append({"method": "GET", "path": "/life/graph"})
        return _make_response("/life/graph")

    # 任何 POST/PUT/PATCH/DELETE 都被 405 拒绝
    @app.route("/api/v1/<path:any_path>", methods=["POST", "PUT", "PATCH", "DELETE"])
    def _reject_write(any_path: str):
        with call_lock:
            call_log.append({"method": "REJECT", "path": any_path})
        return jsonify({
            "success": False,
            "data": {},
            "error": "method_not_allowed",
        }), 405

    # 启动可控端口的 server
    port = _find_free_port()
    server = make_server("127.0.0.1", port, app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # 等待 server 就绪
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.05)

    base_url = f"http://127.0.0.1:{port}/api/v1"

    class _MockServerHandle:
        def __init__(self):
            self.base_url = base_url
            self.port = port
            self._responses = responses
            self._call_log = call_log
            self._lock = call_lock
            self._server = server

        def set_response(self, path: str, response: Dict[str, Any]) -> None:
            self._responses[path] = response

        def get_calls(self) -> list:
            with self._lock:
                return list(self._call_log)

        def stop(self) -> None:
            try:
                self._server.shutdown()
            except Exception:  # noqa: BLE001
                pass

    handle = _MockServerHandle()
    yield handle
    handle.stop()
