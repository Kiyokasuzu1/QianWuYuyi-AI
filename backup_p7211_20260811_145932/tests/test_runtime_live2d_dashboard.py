# -*- coding: utf-8 -*-
"""
tests/test_runtime_live2d_dashboard.py

Phase 5.0 Dashboard Upgrade Step 8.4.6 —— Live2D Runtime Snapshot 联动测试。

覆盖:
  TestBasic          —— get_live2d_signal 基础结构
  TestReadonly       —— 只读,绝不修改 Runtime / Snapshot
  TestFallback       —— 各种 fallback 路径
  TestTrace          —— trace envelope 与 confidence
  TestIsolation      —— 不 import 业务模块 / 不调用 LLM
  TestAPI            —— /api/dashboard/v2/runtime/live2d 端点

合计:>= 12 个测试(>= 10 要求)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =====================================================================
# Helpers / Fakes
# =====================================================================

class _FakeLive2DStore:
    """可注入的 Live2D snapshot 读取器,模拟 Runtime Snapshot 的 live2d 字段。"""

    def __init__(self, payload: Any = None, raise_exc: Exception = None) -> None:
        self._payload = payload
        self._raise = raise_exc
        self._calls: List[str] = []

    def read_raw(self) -> Any:
        self._calls.append("read_raw")
        if self._raise is not None:
            raise self._raise
        return self._payload


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture
def reset_provider():
    from src.admin.runtime_dashboard_provider import (
        reset_runtime_dashboard_provider_for_testing,
    )
    reset_runtime_dashboard_provider_for_testing()
    yield
    reset_runtime_dashboard_provider_for_testing()


@pytest.fixture
def temp_snapshot_file(monkeypatch):
    """创建临时 snapshot 文件,覆盖 provider 的默认路径。"""
    tmpdir = tempfile.mkdtemp(prefix="yuyi_live2d_test_")
    tmpfile = os.path.join(tmpdir, "runtime_snapshot.json")
    yield tmpfile
    try:
        if os.path.exists(tmpfile):
            os.remove(tmpfile)
        os.rmdir(tmpdir)
    except Exception:
        pass


# =====================================================================
# 1. TestBasic —— 基础结构
# =====================================================================

class TestBasic:
    def test_live2d_signal_exists(self, reset_provider):
        """get_live2d_signal 必须存在并返回 dict。"""
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider
        provider = RuntimeDashboardProvider()
        # 默认无 store / 无文件 → fallback 但方法存在
        sig = provider.get_live2d_signal()
        assert isinstance(sig, dict)

    def test_signal_fields_complete(self, reset_provider):
        """正常路径:信号必须包含全部必填字段。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "schema_version": "1.0",
            "identity_id": "yuyi_default",
            "live2d": {
                "expression": "smile",
                "motion": "idle",
                "reason": "curiosity_high",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        provider = RuntimeDashboardProvider()
        sig = provider.get_live2d_signal()
        assert sig["available"] is True
        assert sig["expression"] == "smile"
        assert sig["motion"] == "idle"
        assert sig["reason"] == "curiosity_high"
        assert sig["timestamp"] == "2026-08-01T10:00:00Z"
        assert sig["readonly"] is True
        assert sig["fallback"] is False
        assert sig["fallback_reason"] is None

    def test_signal_does_not_inject_metadata(self, reset_provider):
        """信号内禁止出现 _meta / confidence / trace 等元数据。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "live2d": {
                "expression": "neutral",
                "motion": "idle",
                "reason": "runtime_snapshot",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        for forbidden in ("_meta", "source", "confidence", "trace", "timestamp_meta"):
            assert forbidden not in sig, f"signal 禁止: {forbidden}"


# =====================================================================
# 2. TestReadonly —— 只读
# =====================================================================

class TestReadOnly:
    def test_live2d_signal_readonly(self, reset_provider):
        """readonly 字段必须恒为 True(只读承诺)。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "live2d": {
                "expression": "happy",
                "motion": "wave",
                "reason": "test",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["readonly"] is True

    def test_no_runtime_mutation(self, reset_provider):
        """Provider 不应修改任何 snapshot / runtime 状态。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        # mock 一个 store,记录所有调用
        store = _FakeLive2DStore(
            payload={
                "live2d": {
                    "expression": "x",
                    "motion": "y",
                    "reason": "z",
                    "timestamp": "t",
                },
            },
        )
        set_live2d_snapshot_store_for_dashboard(store)
        provider = RuntimeDashboardProvider()
        provider.get_live2d_signal()
        # store 只被 read_raw,没有任何写入调用
        assert "read_raw" in store._calls
        forbidden = [c for c in store._calls if c.startswith(("write_", "save_", "update_", "set_", "delete_", "commit_"))]
        assert not forbidden, f"store 不应被写入: {forbidden}"

    def test_no_setters_exposed(self, reset_provider):
        """Provider 不得暴露任何修改 snapshot 的方法。"""
        from src.admin.runtime_dashboard_provider import RuntimeDashboardProvider
        provider = RuntimeDashboardProvider()
        forbidden = [m for m in dir(provider) if m.startswith(("set_", "save_", "update_", "commit_", "write_"))]
        # set_live2d_snapshot_store 允许(测试注入)
        forbidden = [m for m in forbidden if m != "set_live2d_snapshot_store"]
        assert not forbidden, f"Provider 不应暴露写入方法: {forbidden}"


# =====================================================================
# 3. TestFallback —— 各种 fallback 路径
# =====================================================================

class TestFallback:
    def test_runtime_unavailable_fallback(self, reset_provider):
        """Runtime 不存在时 → fallback runtime_snapshot_unavailable。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        # 注入返回 None 的 store
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=None))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["available"] is False
        assert sig["fallback"] is True
        assert sig["fallback_reason"] == "runtime_snapshot_unavailable"
        assert sig["expression"] is None
        assert sig["motion"] is None
        assert sig["readonly"] is True

    def test_store_raises_fallback(self, reset_provider):
        """Store 抛异常时 → fallback runtime_snapshot_unavailable。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        set_live2d_snapshot_store_for_dashboard(
            _FakeLive2DStore(raise_exc=RuntimeError("io_error"))
        )
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["available"] is False
        assert sig["fallback"] is True
        assert sig["fallback_reason"] == "runtime_snapshot_unavailable"

    def test_missing_signal_fallback(self, reset_provider):
        """Snapshot 没有 live2d 字段时 → fallback live2d_signal_not_found。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "schema_version": "1.0",
            "identity_id": "yuyi_default",
            "boot_count": 1,
            # 故意没有 live2d 字段
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["available"] is False
        assert sig["fallback"] is True
        assert sig["fallback_reason"] == "live2d_signal_not_found"

    def test_live2d_field_not_dict_fallback(self, reset_provider):
        """live2d 字段不是 dict 时 → fallback live2d_signal_not_found。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {"live2d": "not_a_dict"}
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["fallback"] is True
        assert sig["fallback_reason"] == "live2d_signal_not_found"

    def test_invalid_snapshot_fallback(self, reset_provider):
        """字段缺失 / 异常值 → fallback live2d_signal_invalid。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        # 缺 expression 和 motion
        payload = {
            "live2d": {
                "reason": "x",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["fallback"] is True
        assert sig["fallback_reason"] == "live2d_signal_invalid"
        assert "missing_fields" in sig

    def test_empty_string_field_fallback(self, reset_provider):
        """expression / motion 为空字符串 → fallback live2d_signal_invalid。"""
        from src.admin.runtime_dashboard_provider import (
            RuntimeDashboardProvider,
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "live2d": {
                "expression": "",
                "motion": "idle",
                "reason": "x",
                "timestamp": "t",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        sig = RuntimeDashboardProvider().get_live2d_signal()
        assert sig["fallback"] is True
        assert sig["fallback_reason"] == "live2d_signal_invalid"


# =====================================================================
# 4. TestTrace —— trace envelope 与 confidence
# =====================================================================

class TestTrace:
    def test_trace_source_runtime_provider(self, reset_provider):
        """trace.sources 必须包含 RuntimeDashboardProvider.get_live2d_signal。"""
        from src.admin.dashboard.response import build_dashboard_response, PROVIDER_RUNTIME
        assert PROVIDER_RUNTIME == "RuntimeDashboardProvider"
        env = build_dashboard_response(
            data={"available": True, "expression": "x", "motion": "y"},
            evidence_count=1,
            host_provider=PROVIDER_RUNTIME,
            host_method="get_live2d_signal",
        )
        # trace 至少有 host source
        assert "trace" in env
        assert "sources" in env["trace"]
        providers = [s["provider"] for s in env["trace"]["sources"]]
        assert "RuntimeDashboardProvider" in providers
        methods = [s["method"] for s in env["trace"]["sources"]]
        assert "get_live2d_signal" in methods

    def test_confidence_present(self, reset_provider):
        """响应必须包含 confidence 字段(0~1)。"""
        from src.admin.dashboard.response import build_dashboard_response, build_fallback_response
        env1 = build_dashboard_response(
            data={"available": True},
            host_provider="RuntimeDashboardProvider",
            host_method="get_live2d_signal",
        )
        assert "confidence" in env1
        assert 0.0 <= env1["confidence"] <= 1.0
        env2 = build_fallback_response(
            data={"available": False},
            reason="runtime_snapshot_unavailable",
            host_provider="RuntimeDashboardProvider",
            host_method="get_live2d_signal",
        )
        assert "confidence" in env2
        assert 0.0 <= env2["confidence"] <= 1.0
        # fallback confidence 应低于正常
        assert env2["confidence"] < env1["confidence"]

    def test_data_purity(self, reset_provider):
        """data 字段纯净:禁止出现 _meta / source / confidence / trace / fallback。"""
        from src.admin.dashboard.response import build_dashboard_response
        env = build_dashboard_response(
            data={"available": True, "expression": "x", "motion": "y"},
            evidence_count=1,
            host_provider="RuntimeDashboardProvider",
            host_method="get_live2d_signal",
        )
        for forbidden in ("_meta", "source", "confidence", "trace", "fallback", "fallback_reason", "timestamp", "ok"):
            assert forbidden not in env["data"], f"data 禁止: {forbidden}"


# =====================================================================
# 5. TestIsolation —— 不 import 业务模块 / 不调用 LLM
# =====================================================================

class TestIsolation:
    def test_no_llm_call(self, reset_provider):
        """Provider 不得调用 LLM。"""
        from src.admin import runtime_dashboard_provider as pmod
        from src.admin.dashboard import response as resp_mod
        from src.admin.dashboard import trace as tr_mod

        def _strip_docstring_and_comments(src: str) -> str:
            lines = []
            in_doc = False
            for line in src.splitlines():
                s = line.strip()
                if s.startswith('"""') or s.startswith("'''"):
                    in_doc = not in_doc
                    continue
                if in_doc:
                    continue
                if s.startswith("#"):
                    continue
                lines.append(line)
            return "\n".join(lines).lower()

        for mod in (pmod, resp_mod, tr_mod):
            src = open(mod.__file__, "r", encoding="utf-8").read()
            code = _strip_docstring_and_comments(src)
            forbidden = [
                "openai", "anthropic", "claude",
                "chat_completion", "completion(",
                "from src.llm", "import src.llm",
                ".invoke_llm", "call_llm",
            ]
            for f in forbidden:
                assert f not in code, f"{mod.__name__} 禁止 LLM 关键字: {f}"

    def test_no_business_module_import(self, reset_provider):
        """Provider 源码禁止 import 业务模块。"""
        from src.admin import runtime_dashboard_provider as pmod
        src = open(pmod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "from src.memory", "import src.memory",
            "from src.emotion", "import src.emotion",
            "from src.growth", "import src.growth",
            "from src.personality", "import src.personality",
            "from src.relationship", "import src.relationship",
            "from src.runtime.", "import src.runtime.",
            "from src.self_model", "import src.self_model",
            "from src.selfmodel", "import src.selfmodel",
            "from src.identity", "import src.identity",
            "from src.agreement", "import src.agreement",
        ]
        for f in forbidden:
            assert f not in src, f"禁止: {f}"

    def test_no_emotion_growth_personality_call(self, reset_provider):
        """Provider 不得显式调用 emotion / growth / personality 模块。"""
        from src.admin import runtime_dashboard_provider as pmod
        src = open(pmod.__file__, "r", encoding="utf-8").read()
        forbidden = [
            "emotion_manager", "EmotionManager",
            "growth_state", "GrowthState",
            "personality_resolver", "PersonalityResolver",
            "curiosity_engine",
            "emotion_belief",
            "emotion_engine",
        ]
        for f in forbidden:
            # 仅在源码字符串中检查,不在注释/docstring 中
            lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
            non_comment = "\n".join(lines)
            assert f not in non_comment, f"Provider 不得引用 {f}"


# =====================================================================
# 6. TestAPI —— /api/dashboard/v2/runtime/live2d 端点
# =====================================================================

class TestAPI:
    def _make_client(self):
        from src.admin.dashboard.runtime_router import runtime_v2_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(runtime_v2_bp)
        return app.test_client()

    def test_live2d_endpoint_available(self, reset_provider):
        """GET /api/dashboard/v2/runtime/live2d → 200,data 内含 live2d_signal 字段。"""
        from src.admin.runtime_dashboard_provider import (
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "live2d": {
                "expression": "smile",
                "motion": "idle",
                "reason": "test",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/live2d")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "data" in body
        # data 内含核心字段(不再叫 live2d_signal 包装,因为 router 拆开成扁平字段)
        for k in ("available", "expression", "motion", "reason", "readonly"):
            assert k in body["data"], f"data 缺字段: {k}"
        assert body["data"]["available"] is True
        assert body["data"]["expression"] == "smile"
        assert body["data"]["motion"] == "idle"

    def test_live2d_endpoint_envelope(self, reset_provider):
        """端点响应必须是标准 envelope(ok / trace / confidence / timestamp)。"""
        from src.admin.runtime_dashboard_provider import (
            set_live2d_snapshot_store_for_dashboard,
        )
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=None))
        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/live2d")
        body = resp.get_json()
        for k in ("ok", "data", "trace", "confidence", "fallback", "timestamp"):
            assert k in body, f"envelope 缺字段: {k}"
        # trace 至少有 source
        assert "sources" in body["trace"]
        assert len(body["trace"]["sources"]) >= 1
        # fallback 路径
        assert body["fallback"] is True
        assert body["fallback_reason"] == "runtime_snapshot_unavailable"

    def test_live2d_endpoint_fallback(self, reset_provider):
        """Runtime 不可用时 → fallback 响应,不抛 500。"""
        from src.admin.runtime_dashboard_provider import (
            set_live2d_snapshot_store_for_dashboard,
        )
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=None))
        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/live2d")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is False
        assert body["fallback"] is True
        assert body["fallback_reason"] == "runtime_snapshot_unavailable"

    def test_live2d_endpoint_trace_source(self, reset_provider):
        """端点 trace.sources[0] 必须是 RuntimeDashboardProvider.get_live2d_signal。"""
        from src.admin.runtime_dashboard_provider import (
            set_live2d_snapshot_store_for_dashboard,
        )
        payload = {
            "live2d": {
                "expression": "neutral",
                "motion": "idle",
                "reason": "test",
                "timestamp": "2026-08-01T10:00:00Z",
            },
        }
        set_live2d_snapshot_store_for_dashboard(_FakeLive2DStore(payload=payload))
        client = self._make_client()
        resp = client.get("/api/dashboard/v2/runtime/live2d")
        body = resp.get_json()
        sources = body["trace"]["sources"]
        # host source 在最前
        host = sources[0]
        assert host["provider"] == "RuntimeDashboardProvider"
        assert host["method"] == "get_live2d_signal"
        assert host["ok"] is True
