# -*- coding: utf-8 -*-
"""
tests/test_memory_widget_phase_d2_10.py

Phase D.2.10 —— MemoryWidget 修复验证测试

覆盖目标:
1. ENDPOINT 常量:只保留 ENDPOINT_HEALTH = /health,旧 v2 端点已删除
2. _fetch_in_worker:不再调用 /api/dashboard/v2/*,只走 service + 轻量 /health
3. _on_data_ready:
   - service.available=True → service 路径(不再被 envelope_memory 403 污染)
   - service 不可用 + health 通 → health_only 路径
   - 都不可用 → error 路径
4. _safe_get 已删除(无残留静态方法)
5. __all__ 精简

约束(强):
- 不实例化 QWidget(避免 Qt 平台依赖)
- 不发起真实 HTTP(用 MagicMock 模拟)
- 保持向后兼容(只测行为变更,不动 widget UI)
"""
from __future__ import annotations

import os
import sys
import inspect
from pathlib import Path
from unittest.mock import MagicMock

# 测试期间强制 Qt offscreen
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================
# 1. 端点常量
# ============================================================
class TestEndpointConstants:
    """Phase D.2.10:端点定义已精简,删除 v2 路径,只保留 /health。"""

    def test_only_health_endpoint_exists(self):
        from yuyi_desktop.ui.widgets import memory_widget
        # ENDPOINT_HEALTH 保留
        assert memory_widget.ENDPOINT_HEALTH == "/health"

    def test_old_v2_endpoints_removed(self):
        from yuyi_desktop.ui.widgets import memory_widget
        for old in (
            "ENDPOINT_MEMORY_OVERVIEW",
            "ENDPOINT_MEMORY_SUMMARY",
            "ENDPOINT_MEMORY_RECENT",
            "ENDPOINT_MEMORY_STATS",
        ):
            assert not hasattr(memory_widget, old), (
                f"旧端点常量 {old} 应已删除"
            )

    def test_all_exports_cleaned(self):
        from yuyi_desktop.ui.widgets import memory_widget
        for old in (
            "ENDPOINT_MEMORY_OVERVIEW",
            "ENDPOINT_MEMORY_SUMMARY",
            "ENDPOINT_MEMORY_RECENT",
            "ENDPOINT_MEMORY_STATS",
        ):
            assert old not in memory_widget.__all__, (
                f"{old} 应从 __all__ 删除"
            )
        assert "ENDPOINT_HEALTH" in memory_widget.__all__
        assert "MemoryWidget" in memory_widget.__all__


# ============================================================
# 2. _fetch_in_worker:不再调用 v2 端点
# ============================================================
class TestFetchInWorker:
    """Phase D.2.10:Worker 线程只调 service + /health,不再调 v2 端点。"""

    @staticmethod
    def _code_only(src: str) -> str:
        """去除 docstring 和注释行,只保留实际代码。"""
        import ast
        tree = ast.parse(src.strip())
        fn = tree.body[0]
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)):
            fn.body = fn.body[1:]
        return ast.unparse(fn)

    def test_no_dashboard_v2_in_worker_code(self):
        """代码(去除 docstring/注释)中不应再出现 /api/dashboard/v2/*。"""
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._fetch_in_worker)
        code = self._code_only(src)
        assert "/api/dashboard/v2" not in code, (
            "_fetch_in_worker 代码中不应再调用 /api/dashboard/v2/* 端点"
        )
        for old in (
            "ENDPOINT_MEMORY_OVERVIEW",
            "ENDPOINT_MEMORY_SUMMARY",
            "ENDPOINT_MEMORY_RECENT",
            "ENDPOINT_MEMORY_STATS",
        ):
            assert old not in code, f"_fetch_in_worker 代码中不应有 {old}"

    def test_no_get_path_call(self):
        """不应使用 get_path(v2 端点专用前缀跳过),改用 get(走 api_prefix)。"""
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._fetch_in_worker)
        assert "get_path" not in src, (
            "_fetch_in_worker 不应再调用 _api.get_path"
        )
        assert "self._api.get(ENDPOINT_HEALTH)" in src, (
            "应使用 self._api.get(ENDPOINT_HEALTH) 探测 /api/v1/health"
        )

    def test_worker_returns_expected_keys(self):
        """result 应包含 overview / health_env / errors / _fetch_elapsed_ms,不含 envelope_memory。"""
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._fetch_in_worker)
        assert '"overview"' in src
        assert '"health_env"' in src
        assert '"errors"' in src
        # 不应再有 envelope_memory
        assert "envelope_memory" not in src
        assert "envelope_health" not in src


# ============================================================
# 3. _on_data_ready:关键回归测试 — 不再被 envelope_memory 403 污染
# ============================================================
class TestOnDataReadyPaths:
    """Phase D.2.10 关键修复:service 数据完整时,即使 v2 端点 403 也能正确渲染。"""

    def _make_widget_stub(self):
        """构造一个 MemoryWidget 桩,不实例化 QWidget,只测 _on_data_ready。

        直接给 MemoryWidget 类注入 MagicMock 化的 _service / _api,
        然后单独调用 _on_data_ready(传 mock data)。
        """
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget

        # 用 type() 创建一个绕过 __init__ 的实例
        # 避免触发 QWidget.__init__ + 启动 QTimer
        stub = MemoryWidget.__new__(MemoryWidget)
        stub._api = MagicMock()
        stub._service = MagicMock()
        stub._last_data = None
        stub._build_view_from_service = MemoryWidget._build_view_from_service
        stub._build_view_from_health = MemoryWidget._build_view_from_health
        stub._render_data = MagicMock()
        stub._render_error = MagicMock()
        stub._section_status = MagicMock()
        return stub

    def test_service_available_uses_service_path(self):
        """service.available=True → 走 service 路径,渲染 service 数据。"""
        stub = self._make_widget_stub()
        data = {
            "overview": {
                "available": True,
                "degraded": False,
                "total": 316,
                "important": 50,
                "v2": {
                    "total_count": 316,
                    "recent_count": 12,
                    "last_update": "2026-08-06T10:00:00",
                },
            },
            "health_env": {
                "success": True,
                "latency_ms": 80.0,
            },
        }
        stub._on_data_ready(data)
        # 应该调用 _render_data(source='service'),不调用 _render_error
        assert stub._render_data.called, "service 可用时应走 _render_data"
        assert not stub._render_error.called, (
            "service 可用时不应走 _render_error"
        )
        args, kwargs = stub._render_data.call_args
        view = args[0]
        assert view["source"] == "service"
        assert view["total"] == 316
        assert view["long_term"] == 50
        assert view["available"] is True

    def test_service_degraded_still_uses_service_path(self):
        """service.degraded=True 但 available=True → 仍走 service 路径。"""
        stub = self._make_widget_stub()
        data = {
            "overview": {
                "available": True,
                "degraded": True,
                "total": 100,
            },
            "health_env": {"success": True, "latency_ms": 50.0},
        }
        stub._on_data_ready(data)
        assert stub._render_data.called
        assert not stub._render_error.called

    def test_service_unavailable_health_ok_uses_health_only(self):
        """service 不可用 + health 通 → health_only 路径。"""
        stub = self._make_widget_stub()
        data = {
            "overview": {
                "available": False,
                "degraded": True,
                "total": 0,
            },
            "health_env": {
                "success": True,
                "latency_ms": 60.0,
                "schema_version": "1.0",
            },
        }
        stub._on_data_ready(data)
        assert stub._render_data.called
        args, kwargs = stub._render_data.call_args
        view = args[0]
        assert view["source"] == "health_only"
        assert view["online"] is True
        assert view["available"] is False
        assert view["degraded"] is True

    def test_both_unavailable_uses_error_path(self):
        """service + health 都不可用 → 走 _render_error。"""
        stub = self._make_widget_stub()
        data = {
            "overview": {
                "available": False,
                "degraded": True,
                "total": 0,
                "error": "connection_error: ConnectionError",
            },
            "health_env": {
                "success": False,
                "error": "timeout: Timeout",
                "latency_ms": 5000.0,
            },
        }
        stub._on_data_ready(data)
        assert stub._render_error.called, "双失败时必须走 _render_error"
        assert not stub._render_data.called, "双失败时不应走 _render_data"

    def test_no_any_403_any_404_in_on_data_ready_code(self):
        """关键回归测试:_on_data_ready 代码中不应再有 any_403/any_404 逻辑。"""
        import ast
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        src = inspect.getsource(MemoryWidget._on_data_ready)
        tree = ast.parse(src.strip())
        fn = tree.body[0]
        # 移除 docstring
        if (fn.body and isinstance(fn.body[0], ast.Expr)
                and isinstance(fn.body[0].value, ast.Constant)
                and isinstance(fn.body[0].value.value, str)):
            fn.body = fn.body[1:]
        code = ast.unparse(fn)
        for forbidden in ("any_403", "any_404", "envelope_memory", "envelope_health"):
            assert forbidden not in code, (
                f"_on_data_ready 代码中不应再有 {forbidden} 引用"
            )


# ============================================================
# 4. _safe_get 已删除
# ============================================================
class TestSafeGetRemoved:
    """Phase D.2.10:不再需要 _safe_get(v2 端点已删除)。"""

    def test_safe_get_attribute_missing(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        assert not hasattr(MemoryWidget, "_safe_get"), (
            "_safe_get 应已删除(v2 端点已下线)"
        )


# ============================================================
# 5. _build_view_from_service 仍保留并能正确处理 overview
# ============================================================
class TestBuildViewFromService:
    """回归测试:数据视图构建逻辑保持正确。"""

    def test_basic_view_construction(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        overview = {
            "total": 200,
            "important": 30,
            "v2": {
                "total_count": 200,
                "important_count": 30,
                "recent_count": 8,
                "last_update": "2026-08-05T12:00:00",
                "by_type": {
                    "user_fact": 50,
                    "user_milestone": 10,
                    "user_preference": 20,
                    "user_event": 30,
                    "user_experience": 15,
                    "user_relationship": 5,
                    "user_shared": 5,
                },
                "by_category": {
                    "normal_user": 60,
                    "system_pollution": 0,
                    "ai_internal_pollution": 0,
                    "invalid": 0,
                },
                "quality": {"status": "healthy"},
            },
            "available": True,
            "degraded": False,
        }
        health_env = {"success": True, "latency_ms": 100.0, "schema_version": "1.0"}
        view = MemoryWidget._build_view_from_service(overview, 100.0, health_env)
        assert view["total"] == 200
        assert view["long_term"] == 30
        assert view["recent_count"] == 8
        assert view["last_update"] == "2026-08-05T12:00:00"
        assert view["core_count"] == 60  # 来自 by_category.normal_user
        assert view["pref_count"] == 20
        assert view["event_count"] == 45  # 30 + 15
        assert view["relation_count"] == 20  # 5 + 5 + 10(milestone算relation)
        assert view["memory_health"] == "healthy"
        assert view["available"] is True
        assert view["source"] == "service"
        assert view["latency_ms"] == 100.0

    def test_empty_overview_returns_safe_defaults(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        view = MemoryWidget._build_view_from_service({}, 0.0, {})
        assert view["total"] == 0
        assert view["memory_health"] == "unknown"
        assert view["available"] is True  # service 路径默认 True


# ============================================================
# 6. _build_view_from_health 兜底视图
# ============================================================
class TestBuildViewFromHealth:
    """Phase D.2.10:health_only 兜底视图仍正确生成。"""

    def test_health_only_view(self):
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget
        health_env = {
            "success": True,
            "latency_ms": 80.0,
            "schema_version": "1.0",
            "timestamp": "2026-08-06T10:00:00",
        }
        view = MemoryWidget._build_view_from_health(health_env, 80.0)
        assert view["source"] == "health_only"
        assert view["online"] is True
        assert view["available"] is False
        assert view["degraded"] is True
        assert view["latency_ms"] == 80.0
        assert view["total"] == 0  # 兜底时无业务数据


# ============================================================
# 7. _friendly_error 兜底文案
# ============================================================
class TestFriendlyError:
    """错误文案保持原行为(不受 D.2.10 修复影响)。"""

    def test_403_message(self):
        from yuyi_desktop.ui.widgets.memory_widget import _friendly_error
        assert _friendly_error("http_403") == "服务暂不可用"
        assert _friendly_error("auth_error: HTTP 403") == "服务暂不可用"
        assert _friendly_error("forbidden") == "服务暂不可用"

    def test_404_message(self):
        from yuyi_desktop.ui.widgets.memory_widget import _friendly_error
        assert _friendly_error("http_404") == "服务暂未上线"
        assert _friendly_error("not_found") == "服务暂未上线"

    def test_connection_message(self):
        from yuyi_desktop.ui.widgets.memory_widget import _friendly_error
        assert _friendly_error("timeout: Timeout") == "等待核心服务响应"
        assert _friendly_error("connection_error: refused") == "等待核心服务响应"

    def test_empty_message(self):
        from yuyi_desktop.ui.widgets.memory_widget import _friendly_error
        assert _friendly_error("") == "数据暂未加载"


# ============================================================
# 8. 端到端回归:服务有数据时卡片能正常显示
# ============================================================
class TestRegressionServiceDataNotLost:
    """关键回归:即使 v2 端点 403,service 完整数据仍能渲染。"""

    def test_service_data_not_lost_on_v2_403(self):
        """模拟 Phase D.2.10 修复的核心场景。

        旧行为:
            - 同时调用 /api/dashboard/v2/memory/overview → 403
            - any_403 = True
            - 即使 service.get_overview() 返回 available=True,total=316
            - 仍跳到 health_only 路径 → 卡片全空

        新行为:
            - 不再调 v2 端点
            - service.get_overview() 返回 available=True,total=316
            - 走 service 路径 → 卡片正常显示
        """
        stub = TestOnDataReadyPaths()._make_widget_stub()
        data = {
            "overview": {
                "available": True,
                "degraded": False,
                "total": 316,
                "important": 50,
                "v2": {
                    "total_count": 316,
                    "important_count": 50,
                    "recent_count": 12,
                    "last_update": "2026-08-06T10:00:00",
                    "by_type": {
                        "user_fact": 100,
                        "user_preference": 30,
                        "user_event": 20,
                        "user_relationship": 10,
                        "user_milestone": 5,
                    },
                    "by_category": {
                        "normal_user": 150,
                    },
                    "quality": {"status": "healthy"},
                },
            },
            "health_env": {
                "success": True,
                "latency_ms": 80.0,
                "schema_version": "1.0",
            },
        }
        stub._on_data_ready(data)
        # 关键断言:走 service 路径,total=316 被保留
        assert stub._render_data.called
        assert not stub._render_error.called
        view = stub._render_data.call_args[0][0]
        assert view["source"] == "service"
        assert view["total"] == 316, (
            f"修复后 total 应为 316, 实际: {view['total']}"
        )
        assert view["long_term"] == 50
        assert view["available"] is True
        assert view["online"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
