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


# ============================================================
# V1.0-OPT: 统一测试隔离机制（Post-Release Optimization Phase 2）
#
# 目标：让测试默认不污染真实 data/、不泄漏单例状态、不泄漏环境变量。
#
# 设计（保守、自动）：
#   - 单例清理（RuntimeBridge / MemoryProvider / personality_state global）
#     对全部测试 autouse，开销极小。
#   - tmp cwd 隔离只自动应用于「引用了有状态单例」的测试文件
#     （MemoryStore/RuntimeCore/RuntimeBridge/GrowthState/SelfModelStore/
#     MemoryProvider/personality_state），避免影响依赖仓库根目录的
#     其余测试。可用 @pytest.mark.keep_repo_cwd 显式退出。
#   - 环境变量 per-test 快照恢复。
#   - real_api 标记：默认跳过，--run-real-api 启用（真实 API 测试禁止默认运行）。
# ============================================================

import shutil as _shutil
import tempfile as _tempfile
import re as _re

_SINGLETON_PATTERN = _re.compile(
    r"MemoryStore\(|RuntimeCore\(|RuntimeBridge|get_runtime_bridge|"
    r"GrowthState\(|SelfModelStore\(|MemoryProvider|get_personality_state|"
    r"Orchestrator\(|MemorySystem\(|YuyiCore\(|EmotionRepository\(|"
    r"EmotionTraceRepository\(|EmotionManager\(|PersistenceManager\(|"
    r"RuntimeController\(|RelationshipRepository\(|PersonalityResolver\(|"
    r"InteractionRecorder\(|ExperienceExtractor\(|ProposalStore\(|"
    r"ProposalStorage\(|GrowthEngine\(|VectorMemory\(|EventMemory\(|"
    r"save_personality_state|"
    # V1.1 (F3): 单例/有状态访问器函数——此前仅覆盖构造器,漏掉通过
    # get_* 访问器触达单例的测试文件,导致其写操作落在真实 cwd。
    r"get_self_model_store|get_proposal_storage|get_growth_state|"
    r"get_emotion_repository|get_memory_store|get_runtime_core|"
    r"get_orchestrator\b|get_growth_proposal_mirror_storage"
)
_STATEFUL_CACHE: dict = {}


def _file_references_stateful_singletons(filename: str) -> bool:
    """按文件缓存：该测试文件是否引用了有状态单例。"""
    if filename not in _STATEFUL_CACHE:
        try:
            with open(filename, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            _STATEFUL_CACHE[filename] = bool(_SINGLETON_PATTERN.search(text))
        except OSError:
            _STATEFUL_CACHE[filename] = False
    return _STATEFUL_CACHE[filename]


def pytest_addoption(parser):
    parser.addoption(
        "--run-real-api",
        action="store_true",
        default=False,
        help="允许运行标记为 real_api 的测试（默认跳过）",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_api: 会调用真实 LLM API 的测试，默认禁止运行（--run-real-api 启用）",
    )
    config.addinivalue_line(
        "markers",
        "keep_repo_cwd: 需要仓库根目录作为工作目录的测试（退出自动 tmp cwd 隔离）",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-real-api"):
        return
    skip_marker = pytest.mark.skip(reason="真实 API 测试默认跳过（--run-real-api 启用）")
    for item in items:
        if "real_api" in item.keywords:
            item.add_marker(skip_marker)


@pytest.fixture(scope="session", autouse=True)
def _yuyi_env_session_guard():
    """会话级环境变量快照：会话结束后恢复启动时环境。"""
    env_snapshot = dict(os.environ)
    yield
    try:
        os.environ.clear()
        os.environ.update(env_snapshot)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _yuyi_runtime_isolation(request):
    """V1.0-OPT 统一隔离（per-test）：单例清理 + tmp cwd + 环境变量恢复。

    单例清理与 env 恢复对全部测试生效；tmp cwd 只对引用有状态单例的
    测试文件生效，可用 keep_repo_cwd 标记退出。
    """
    env_before = dict(os.environ)

    # --- 单例清理 ---
    try:
        from src.memory.memory_provider import MemoryProvider
        MemoryProvider.reset_for_testing()
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.runtime.runtime_bridge import RuntimeBridge
        RuntimeBridge.reset_for_testing()
    except Exception:  # noqa: BLE001
        pass
    try:
        from src.personality import personality_state as _ps
        _ps._global_state = None
    except Exception:  # noqa: BLE001
        pass
    try:
        # V1.1: ProposalStorage 单例锚定首次构造时的绝对路径,
        # 每测试重置防止跨测试 cwd 漂移污染。
        from src.growth.proposal.storage import ProposalStorage
        ProposalStorage.reset_for_testing()
    except Exception:  # noqa: BLE001
        pass

    # --- tmp cwd（仅有状态测试文件）---
    needs_workspace = (
        "keep_repo_cwd" not in request.keywords
        and _file_references_stateful_singletons(str(request.fspath))
    )
    cwd_before = os.getcwd()
    tmp_dir = None
    if needs_workspace:
        tmp_dir = _tempfile.mkdtemp(prefix="yuyi_conftest_")
        os.chdir(tmp_dir)
        Path("data").mkdir(parents=True, exist_ok=True)
        # cwd 相对路径写入者需要的嵌套目录（ProposalStorage / AuditStorage 等
        # 以相对路径构造目录且只在 __init__ 时 mkdir，跨测试 cwd 切换后若
        # 目录缺失会导致保存静默失败）——仅在临时工作区预建，绝不影响真实 data/。
        for _subdir in ("growth/proposals", "audit"):
            Path("data", *_subdir.split("/")).mkdir(parents=True, exist_ok=True)

    yield

    if tmp_dir is not None:
        os.chdir(cwd_before)
        _shutil.rmtree(tmp_dir, ignore_errors=True)
    # --- 环境变量恢复 ---
    try:
        os.environ.clear()
        os.environ.update(env_before)
    except Exception:  # noqa: BLE001
        pass


# ============================================================
# V1.1 (F3): 会话级真实数据污染探针（非阻断 WARNING）
# ============================================================
# 目标: 测试默认不得污染仓库真实 data/。本探针在会话结束时对比
# 仓库根关键数据文件的 sha256,发现差异输出 WARNING 让污染可见。
# 不阻断运行(keep_repo_cwd 例外用例可能合法写入),仅作为告警证据。

_REPO_DATA_SNAPSHOT_FILES = [
    "data/memory.json",
    "data/self_model.json",
    "data/growth_state.json",
    "data/runtime_state.json",
    "data/personality_state.json",
    "data/relationship_state.json",
    "data/emotion_state.json",
    "data/emotional_traces.json",
    "data/experience_journal.jsonl",
    "data/conversation_history.json",
    "data/growth/proposals/proposals.json",
    "data/emotion_patterns.json",
]


@pytest.fixture(scope="session", autouse=True)
def _yuyi_repo_data_tripwire():
    """会话级真实数据快照对比探针（V1.1 Foundation Hardening F3）。"""
    import hashlib
    import warnings

    repo_root = Path(__file__).resolve().parent.parent

    def _snapshot():
        out = {}
        for rel in _REPO_DATA_SNAPSHOT_FILES:
            p = repo_root / rel
            out[rel] = (
                hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
            )
        return out

    before = _snapshot()
    yield
    polluted = [
        rel for rel, prev in before.items() if _snapshot().get(rel) != prev
    ]
    if polluted:
        warnings.warn(
            "V1.1 数据污染告警: 本测试会话修改了真实数据文件: "
            + ", ".join(polluted)
            + "。默认隔离要求测试不得污染真实 data/;keep_repo_cwd 用例需自查。",
            UserWarning,
            stacklevel=2,
        )
