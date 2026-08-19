# -*- coding: utf-8 -*-
"""
tests/test_v2_health_fix_d2_10.py

Phase D.2.10 —— growth / initiative / personality widget v2 health 端点修复验证

覆盖目标:
1. 3 个 widget 的 ENDPOINT_HEALTH 全部统一为 /health
2. _fetch_in_worker 中调用 _api.get(ENDPOINT_HEALTH) (而非 _api.get_path)
3. _fetch_in_worker 代码中不再有 /api/dashboard/v2/* 实际调用
4. 模块级 docstring 已更新(D.2.10 修复说明)
5. _fetch_in_worker docstring 已更新

约束(强):
- 不实例化 QWidget(避免 Qt 平台依赖)
- 不发起真实 HTTP(用 MagicMock 模拟)
- 保持向后兼容
"""
from __future__ import annotations

import os
import sys
import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


WIDGETS = ("growth_widget", "initiative_widget", "personality_widget")


# ============================================================
# 工具:提取函数体代码(去除 docstring/注释)
# ============================================================
def _code_only(src: str) -> str:
    """解析源码,移除 docstring/常量字符串,只保留逻辑代码。"""
    tree = ast.parse(src.strip())
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:]
    return ast.unparse(fn)


def _module_code_without_docstring(path: str) -> str:
    """读取整个模块,移除模块级 docstring。"""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        tree.body = tree.body[1:]
    return ast.unparse(tree)


# ============================================================
# 1. 端点常量统一性
# ============================================================
class TestEndpointHealthUnified:
    """3 个 widget 的 ENDPOINT_HEALTH 应全部为 /health(走 api_prefix)。"""

    def test_growth_endpoint_health(self):
        from yuyi_desktop.ui.widgets.growth_widget import ENDPOINT_HEALTH
        assert ENDPOINT_HEALTH == "/health"

    def test_initiative_endpoint_health(self):
        from yuyi_desktop.ui.widgets.initiative_widget import ENDPOINT_HEALTH
        assert ENDPOINT_HEALTH == "/health"

    def test_personality_endpoint_health(self):
        from yuyi_desktop.ui.widgets.personality_widget import ENDPOINT_HEALTH
        assert ENDPOINT_HEALTH == "/health"

    def test_no_dashboard_v2_in_constant(self):
        """3 个 widget 的 ENDPOINT_HEALTH 不应是 v2 端点。"""
        for w in WIDGETS:
            mod = __import__(f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            assert "/api/dashboard/v2" not in mod.ENDPOINT_HEALTH, (
                f"{w}.ENDPOINT_HEALTH 仍包含 /api/dashboard/v2/"
            )


# ============================================================
# 2. _fetch_in_worker 不再调用 v2 端点,改用 _api.get
# ============================================================
class TestFetchInWorker:
    """3 个 widget 的 _fetch_in_worker 应使用 _api.get(ENDPOINT_HEALTH),不用 get_path。"""

    def test_uses_get_not_get_path(self):
        for w in WIDGETS:
            mod = __import__(
                f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            cls = getattr(mod, _widget_class_name(w))
            src = inspect.getsource(cls._fetch_in_worker)
            assert "_api.get(ENDPOINT_HEALTH)" in src, (
                f"{w}: _fetch_in_worker 应使用 _api.get(ENDPOINT_HEALTH)"
            )
            assert "_api.get_path(ENDPOINT_HEALTH)" not in src, (
                f"{w}: _fetch_in_worker 不应再使用 _api.get_path(ENDPOINT_HEALTH)"
            )

    def test_no_dashboard_v2_in_code(self):
        for w in WIDGETS:
            mod = __import__(
                f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            cls = getattr(mod, _widget_class_name(w))
            src = inspect.getsource(cls._fetch_in_worker)
            code = _code_only(src)
            assert "/api/dashboard/v2" not in code, (
                f"{w}: _fetch_in_worker 代码中不应再有 /api/dashboard/v2 引用"
            )

    def test_health_key_in_result(self):
        """result dict 应包含 'health' key(便于上层取 latency_ms)。"""
        for w in WIDGETS:
            mod = __import__(
                f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            cls = getattr(mod, _widget_class_name(w))
            src = inspect.getsource(cls._fetch_in_worker)
            assert '"health"' in src or "'health'" in src, (
                f"{w}: _fetch_in_worker 应在 result 中放入 'health' key"
            )


def _widget_class_name(widget_module: str) -> str:
    """growth_widget -> GrowthWidget (移除 _widget 后缀并首字母大写)。"""
    base = widget_module.replace("_widget", "")
    return base[0].upper() + base[1:] + "Widget"


# ============================================================
# 3. 模块级 docstring 已包含 D.2.10 修复说明
# ============================================================
class TestDocstringUpdated:
    """模块级 docstring 应提到 Phase D.2.10 修复 + 不再调 v2 health。"""

    def test_module_docstring_mentions_d2_10(self):
        for w in WIDGETS:
            path = f"yuyi_desktop/ui/widgets/{w}.py"
            src = open(path, encoding="utf-8").read()
            assert "D.2.10" in src, (
                f"{w}.py 模块级注释应包含 Phase D.2.10 字样"
            )

    def test_no_dashboard_v2_string_assignment(self):
        """3 个 widget 中不应再将 '/api/dashboard/v2/health' 赋值给变量。"""
        for w in WIDGETS:
            mod = __import__(
                f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            # 读取实际 ENDPOINT_HEALTH 值
            assert mod.ENDPOINT_HEALTH != "/api/dashboard/v2/health", (
                f"{w}.ENDPOINT_HEALTH 仍为 /api/dashboard/v2/health"
            )
            assert mod.ENDPOINT_HEALTH == "/health", (
                f"{w}.ENDPOINT_HEALTH 应为 /health, 实际: {mod.ENDPOINT_HEALTH}"
            )


# ============================================================
# 4. 端到端:3 个 widget 的 _on_data_ready 不受 v2 403 污染
# ============================================================
class TestNoV2Pollution:
    """3 个 widget 的 _on_data_ready 不应基于 health 的 any_403 拒绝 service 数据。

    注: personality widget 使用 _render_from_fetch_result 中转,
    其代码路径不直接用 service_available,但也不依赖 health 错误状态。
    这里只对 growth/initiative 做源码级检查。
    """

    def test_no_any_403_in_growth_initiative_on_data_ready(self):
        for w in ("growth_widget", "initiative_widget"):
            mod = __import__(
                f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            cls = getattr(mod, _widget_class_name(w))
            src = inspect.getsource(cls._on_data_ready)
            code = _code_only(src)
            # service_available 判定不应被 health 的 403 拦截
            assert "any_403" not in code, (
                f"{w}: _on_data_ready 代码中不应再有 any_403 拦截"
            )

    def test_growth_uses_service_available(self):
        from yuyi_desktop.ui.widgets.growth_widget import GrowthWidget
        src = inspect.getsource(GrowthWidget._on_data_ready)
        assert "service_available" in src

    def test_initiative_uses_service_available(self):
        from yuyi_desktop.ui.widgets.initiative_widget import InitiativeWidget
        src = inspect.getsource(InitiativeWidget._on_data_ready)
        assert "service_available" in src

    def test_personality_data_extraction_works(self):
        """personality widget 通过 _render_from_fetch_result 中转,
        验证数据能被正确提取到 instance 变量(无异常)。"""
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        stub = PersonalityWidget.__new__(PersonalityWidget)
        # 注入必要属性
        for attr in (
            "_traits_detail", "_evolution_detail", "_growth_detail", "_beliefs_detail",
            "_traits_group_expanded", "_beliefs_group_expanded",
            "_evolution_group_expanded", "_growth_group_expanded",
        ):
            setattr(stub, attr, False)
        # mock list 组件(避免 Qt widget 真实创建)
        for attr in ("_traits_list", "_beliefs_list", "_evolution_list", "_growth_list"):
            mock_list = MagicMock()
            mock_list.clear = MagicMock()
            mock_list.addItem = MagicMock()
            setattr(stub, attr, mock_list)
        stub._render_from_fetch_result = MagicMock()
        stub._render_traits_detail = MagicMock()
        stub._render_beliefs_detail = MagicMock()
        stub._render_evolution_detail = MagicMock()
        stub._render_growth_detail = MagicMock()
        stub._extract_growth_history = MagicMock(return_value=[])
        stub._extract_beliefs = MagicMock(return_value=[])
        stub._render_error = MagicMock()
        # mock PySide6 imports 在 _on_data_ready 中使用
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtGui import QColor
        # 调用
        data = {
            "overview": {"available": True, "selfmodel_v2": {}},
            "snapshot": {},
            "traits": [],
            "selfmodel": {},
            "evolution": [],
            "growth_history": [],
            "health": {"success": True, "latency_ms": 100.0},
        }
        stub._on_data_ready(data)
        # 关键: 没有触发 _render_error(说明数据流程通畅)
        assert not stub._render_error.called
        # 关键: _render_from_fetch_result 被调用(说明走了 service 路径)
        assert stub._render_from_fetch_result.called


# ============================================================
# 5. 关键回归:即使 v2 health 403,3 个 widget 仍走 service 路径
# ============================================================
class TestServicePathNotLost:
    """3 个 widget 的核心场景:service 有数据时不被 health 端点状态污染。"""

    def _make_stub(self, widget_name: str):
        """构造绕过 QWidget.__init__ 的 widget 桩。"""
        from yuyi_desktop.core.api_client import get_api_client
        from yuyi_desktop.services import (
            growth_service, initiative_service, personality_service,
        )
        from yuyi_desktop.ui.widgets.memory_widget import MemoryWidget  # placeholder
        # 每个 widget 都有自己的 Service
        cls = globals().get(_widget_class_name(widget_name))
        # 动态导入
        mod = __import__(
            f"yuyi_desktop.ui.widgets.{widget_name}", fromlist=[widget_name])
        cls = getattr(mod, _widget_class_name(widget_name))
        stub = cls.__new__(cls)
        stub._api = MagicMock()
        stub._api.get = MagicMock(return_value={
            "success": True, "latency_ms": 80.0, "schema_version": "1.0",
        })
        stub._service = MagicMock()
        stub._service.get_overview = MagicMock(return_value={
            "available": True, "degraded": False, "total": 100,
        })
        stub._last_data = None
        stub._render_data = MagicMock()
        stub._render_error = MagicMock()
        return stub

    def test_growth_service_path(self):
        stub = self._make_stub("growth_widget")
        result = {
            "overview": {"available": True, "degraded": False, "total": 100},
            "health": {"success": True, "latency_ms": 80.0},
        }
        stub._on_data_ready(result)
        assert stub._render_data.called
        assert not stub._render_error.called
        # 关键: 走 service 路径, _api.get 用于探测
        # (实际 _api.get 在 _fetch_in_worker 中调用, _on_data_ready 不直接调用)

    def test_initiative_service_path(self):
        stub = self._make_stub("initiative_widget")
        result = {
            "overview": {"available": True, "degraded": False, "total": 50},
            "health": {"success": True, "latency_ms": 60.0},
        }
        stub._on_data_ready(result)
        assert stub._render_data.called
        assert not stub._render_error.called

    def test_personality_service_path(self):
        """personality widget 数据流完整性测试(与 TestNoV2Pollution 互补)。"""
        from yuyi_desktop.ui.widgets.personality_widget import PersonalityWidget
        stub = PersonalityWidget.__new__(PersonalityWidget)
        # 注入所有必要属性
        for attr in (
            "_traits_detail", "_evolution_detail", "_growth_detail", "_beliefs_detail",
            "_traits_group_expanded", "_beliefs_group_expanded",
            "_evolution_group_expanded", "_growth_group_expanded",
        ):
            setattr(stub, attr, False)
        for attr in ("_traits_list", "_beliefs_list", "_evolution_list", "_growth_list"):
            mock_list = MagicMock()
            mock_list.clear = MagicMock()
            mock_list.addItem = MagicMock()
            setattr(stub, attr, mock_list)
        stub._render_from_fetch_result = MagicMock()
        stub._render_traits_detail = MagicMock()
        stub._render_beliefs_detail = MagicMock()
        stub._render_evolution_detail = MagicMock()
        stub._render_growth_detail = MagicMock()
        stub._extract_growth_history = MagicMock(return_value=[])
        stub._extract_beliefs = MagicMock(return_value=[])
        stub._render_error = MagicMock()
        # 调用
        data = {
            "overview": {"available": True, "selfmodel_v2": {}},
            "snapshot": {},
            "traits": [],
            "selfmodel": {},
            "evolution": [],
            "growth_history": [],
            "health": {"success": True, "latency_ms": 100.0},
        }
        stub._on_data_ready(data)
        # 关键: 走 service 路径, 没有错误
        assert not stub._render_error.called
        assert stub._render_from_fetch_result.called


# ============================================================
# 6. 5 个 widget 端点完全一致(交叉验证)
# ============================================================
class TestCrossWidgetConsistency:
    """5 个 widget 的 ENDPOINT_HEALTH 应全部为 /health(回归保护)。"""

    def test_all_5_widgets_use_same_health_endpoint(self):
        results = {}
        for w in (
            "growth_widget", "initiative_widget", "personality_widget",
            "memory_widget", "runtime_widget",
        ):
            mod = __import__(
                f"yuyi_desktop.ui.widgets.{w}", fromlist=[w])
            results[w] = mod.ENDPOINT_HEALTH
        assert all(v == "/health" for v in results.values()), (
            f"5 widget 端点不一致: {results}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
